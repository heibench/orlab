"""Parallel simulation running and the declarative options contract.

``SimulationPool`` runs dispersion studies across worker processes — one
JVM plus one loaded ``.ork`` per worker (JPype allows one JVM per process,
so parallelism is multi-process by construction). Tasks are plain
mappings of ``DECLARATIVE_KEYS`` (portable, validated, notebook-safe) or an
importable ``worker_fn`` for anything the whitelist doesn't cover. Results
come back as plain-Python records; per-task failures are data, and every
abort path preserves the partial results collected so far.

``DECLARATIVE_KEYS`` is the curated set of ``SimulationOptions`` knobs that
are verified to apply-and-read-back identically on every supported
OpenRocket version (15.03, 22.02, 23.09, 24.12 — probed per release, and
re-checked by the profile contract manifest and an all-version integration
case).

One interaction to know: OpenRocket launches into the wind by default, and
while ``launch_into_wind`` is true the rod-direction getter reports the
into-wind direction — ``launch_rod_direction`` only takes effect together
with ``launch_into_wind: False`` (verified on all four versions; the pool
refuses a task set that varies the direction without pinning the flag).
"""

import multiprocessing
import os
import pickle
import random
import signal
import sys
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NamedTuple

from ._enums import OrLogLevel
from .errors import OrlabError, StudyAborted

__all__ = [
    "DECLARATIVE_KEYS",
    "DeclarativeKey",
    "SimError",
    "SimResult",
    "SimulationPool",
    "StudyResult",
]


class DeclarativeKey(NamedTuple):
    """How one declarative key maps onto SimulationOptions."""

    setter: str
    getter: str
    kind: type  # value type: float or bool
    unit: str  # SI unit for float keys ("" for bool/dimensionless)


# launch_into_wind precedes launch_rod_direction on purpose: appliers that
# set keys in this order get the verified round-trip semantics. Read-only:
# the whitelist changes by release, never at runtime.
DECLARATIVE_KEYS: MappingProxyType[str, DeclarativeKey] = MappingProxyType(
    {
        "launch_rod_length": DeclarativeKey("setLaunchRodLength", "getLaunchRodLength", float, "m"),
        "launch_rod_angle": DeclarativeKey("setLaunchRodAngle", "getLaunchRodAngle", float, "rad"),
        "launch_into_wind": DeclarativeKey("setLaunchIntoWind", "getLaunchIntoWind", bool, ""),
        "launch_rod_direction": DeclarativeKey(
            "setLaunchRodDirection", "getLaunchRodDirection", float, "rad"
        ),
        "launch_altitude": DeclarativeKey("setLaunchAltitude", "getLaunchAltitude", float, "m"),
        "launch_latitude": DeclarativeKey("setLaunchLatitude", "getLaunchLatitude", float, "°"),
        "launch_longitude": DeclarativeKey("setLaunchLongitude", "getLaunchLongitude", float, "°"),
        "wind_speed_average": DeclarativeKey(
            "setWindSpeedAverage", "getWindSpeedAverage", float, "m/s"
        ),
        "wind_direction": DeclarativeKey("setWindDirection", "getWindDirection", float, "rad"),
    }
)


_SEED_MIN, _SEED_MAX = -(2**31), 2**31 - 1


@dataclass(frozen=True)
class SimResult:
    """One successful simulation in a study."""

    index: int
    """Submission order (results are returned sorted by this)."""
    task: dict
    """The task mapping as submitted."""
    seed: int
    """The seed the simulation actually used, read back after the run."""
    seed_reassigned: bool
    """True when the readback differs from the pool-assigned seed — e.g. a
    worker_fn that called run_simulation with randomize_seed=True. The
    recorded seed is still the authoritative one for replay."""
    payload: Any
    """The default extractor's summary fields, or worker_fn's return value.
    Always plain-Python data (pickle-checked in the worker)."""


@dataclass(frozen=True)
class SimError:
    """One failed simulation in a study — failure as data, not an abort."""

    index: int
    task: dict
    seed: int
    error_type: str
    """Exception type name; Java exceptions keep their class name."""
    message: str
    traceback: str
    """Python traceback, plus the Java stack when one exists."""


@dataclass(frozen=True)
class StudyResult:
    """Everything a study produced, in submission order."""

    results: tuple[SimResult, ...]
    errors: tuple[SimError, ...]

    def to_records(self) -> list[dict]:
        """Flat dicts (task keys + payload + seed/index) — a dispersion
        table is ``pandas.DataFrame(study.to_records())``. Column precedence
        on name collisions: the reserved ``index``/``seed``/
        ``seed_reassigned`` columns override task keys, and payload keys
        override both (the declarative path is collision-free by
        construction)."""
        records = []
        for r in self.results:
            record = dict(r.task)
            # reserved columns win over task keys; payload keys win last
            record.update(index=r.index, seed=r.seed, seed_reassigned=r.seed_reassigned)
            if isinstance(r.payload, dict):
                record.update(r.payload)
            else:
                record["payload"] = r.payload
            records.append(record)
        return records

    def raise_if_errors(self) -> "StudyResult":
        """Returns self, or raises OrlabError describing the first failure
        when any task errored."""
        if self.errors:
            first = self.errors[0]
            raise OrlabError(
                f"{len(self.errors)} of {len(self.results) + len(self.errors)} "
                f"simulations failed; first: task {first.index} "
                f"({first.error_type}: {first.message})"
            )
        return self


# --- worker side: module-level so spawn can pickle them by reference ---

_WORKER: dict = {}  # per-worker-process state, set up once by _worker_init


def _worker_init(cfg: dict) -> None:
    """Boots the worker: JVM + OpenRocket + loaded document, once. Never
    raises — an initializer exception surfaces as an opaque
    BrokenProcessPool, so failures are captured and reported through the
    first task result instead."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent owns ^C
    try:
        if cfg.get("_test_init") is not None:
            # unit-test seam: a picklable hook builds the worker state with
            # fakes instead of booting a JVM
            cfg["_test_init"](_WORKER)
            return
        if cfg["worker_stdout"] == "discard":
            # POSIX: silences everything including the JVM's native stdout.
            # Windows: silences Python/CRT writes; the JVM's native handle
            # is unreachable (documented best-effort).
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.close(devnull)
        import orlab

        instance = orlab.OpenRocketInstance(
            cfg["jar_path"],
            log_level=cfg["log_level"],
            jvm_path=cfg["jvm_path"],
            jvm_args=cfg["jvm_args"],
        )
        instance.__enter__()  # never exited: the JVM lives with the process
        helper = orlab.Helper(instance)
        doc = helper.load_doc(cfg["ork_file"])
        sim = doc.getSimulation(cfg["simulation_index"])
        opts = sim.getOptions()
        baseline = {}
        for key, spec in DECLARATIVE_KEYS.items():
            baseline[key] = spec.kind(getattr(opts, spec.getter)())
        _WORKER.update(helper=helper, sim=sim, baseline=baseline, init_error=None)
    except BaseException:
        _WORKER["init_error"] = traceback.format_exc()


def _flatten_exception(e: BaseException) -> tuple[str, str, str]:
    """(type, message, traceback+java stack): a raw Java exception must
    never reach the pickler — it would destroy the message."""
    tb = traceback.format_exc()
    java_stack = getattr(e, "stacktrace", None)
    if callable(java_stack):
        try:
            tb = f"{tb}\n{java_stack()}"
        except Exception:
            pass
    return (type(e).__name__, str(e), tb)


def _default_extract(helper, sim) -> dict:
    summary = helper.get_summary(sim)
    return {
        name: getattr(summary, name)
        for name in (
            "apogee",
            "max_velocity",
            "max_acceleration",
            "flight_time",
            "landing_x",
            "landing_y",
        )
    }


def _worker_run(index: int, task: dict, seed: int, worker_fn) -> tuple:
    """Runs one task. Returns a plain tuple:
    ("ok", index, payload, actual_seed, reassigned) |
    ("error", index, type, message, traceback) |
    ("init_error", index, traceback)."""
    if _WORKER.get("init_error") or "sim" not in _WORKER:
        return ("init_error", index, _WORKER.get("init_error") or "worker never initialized")
    try:
        helper, sim = _WORKER["helper"], _WORKER["sim"]
        opts = sim.getOptions()
        # restore the document's own baseline first: without this a reused
        # pool carries run A's last task's options into run B
        for key, spec in DECLARATIVE_KEYS.items():
            getattr(opts, spec.setter)(_WORKER["baseline"][key])
        opts.setRandomSeed(seed)
        if worker_fn is None:
            for key, spec in DECLARATIVE_KEYS.items():  # whitelist order
                if key in task:
                    getattr(opts, spec.setter)(spec.kind(task[key]))
            helper.run_simulation(sim, randomize_seed=False)
            payload = _default_extract(helper, sim)
        else:
            payload = worker_fn(helper, sim, task)
        actual_seed = int(opts.getRandomSeed())
        try:  # fail HERE, with task context, not in the pool
            pickle.dumps(payload)
        except Exception as e:
            # Python's own message names pickling on some versions only
            # (3.12 and 3.13 say "Can't get local object"), so say it here (#91)
            raise ValueError(
                f"worker_fn returned a payload that is not picklable ({e}); "
                "return plain-Python data"
            ) from e
        return ("ok", index, payload, actual_seed, actual_seed != seed)
    except BaseException as e:
        return ("error", index, *_flatten_exception(e))


# --- parent side ---


def _count_simulations(ork_file: str) -> int | None:
    """The number of <simulation> elements in a .ork, without a JVM. None
    when the file layout defeats the scan (never a hard failure — the
    worker's getSimulation would raise the real error)."""
    try:
        if zipfile.is_zipfile(ork_file):
            with zipfile.ZipFile(ork_file) as z:
                name = next((n for n in z.namelist() if n.endswith(".ork")), None)
                data = z.read(name) if name else b""
        else:
            with open(ork_file, "rb") as fh:
                data = fh.read()
        return data.count(b"<simulation ") + data.count(b"<simulation>")
    except Exception:
        return None


class SimulationPool:
    """A spawn-based worker pool for dispersion studies: each worker boots
    one JVM, loads the ``.ork`` once, and runs many simulations against it.

    Constructing the pool validates everything cheap (jar, document,
    simulation index); workers boot lazily on the first :meth:`run`. Use
    from a script's ``if __name__ == "__main__":`` block — a requirement of
    multiprocessing's spawn start method, which is the only start method
    that is safe with a JVM and uniform across platforms.
    """

    def __init__(
        self,
        ork_file,
        jar_path=None,
        *,
        simulation_index: int = 0,
        max_workers: int | None = None,
        log_level: OrLogLevel | str = OrLogLevel.ERROR,
        jvm_path=None,
        jvm_args=(),
        max_tasks_per_child: int | None = None,
        worker_stdout: str = "discard",
        _test_init=None,
    ):
        """ork_file/jar_path resolve exactly like OpenRocketInstance (the
        default jar resolution chain applies) but are pinned to absolute
        paths here — workers never re-run environment-dependent resolution.
        max_workers defaults to min(4, cpu_count), clamped to the first
        run's task count; each worker holds a JVM (hundreds of MB — size
        jvm_args=("-Xmx512m",) accordingly). max_tasks_per_child recycles
        workers (Python 3.11+). worker_stdout='discard' silences worker
        stdout including the JVM's native writes on POSIX (best-effort on
        Windows); stderr is always inherited.
        """
        from .core.openrocket_instance import OpenRocketInstance, _default_jar_path

        if worker_stdout not in ("discard", "inherit"):
            raise ValueError("worker_stdout must be 'discard' or 'inherit'")
        if max_tasks_per_child is not None and sys.version_info < (3, 11):
            raise OrlabError("max_tasks_per_child needs Python 3.11+")
        ork = os.path.abspath(os.fspath(ork_file))
        if not os.path.exists(ork):
            raise FileNotFoundError(f"No such .ork file: {ork}")
        jar = (
            os.path.abspath(os.fspath(jar_path))
            if jar_path is not None
            else os.path.abspath(_default_jar_path())
        )
        # validates the jar and fires the fallback-profile warning once,
        # in the parent, instead of once per worker. The instance is discarded
        # but its verdict is not: a pool running on stale enums is exactly the
        # case a caller needs to branch on, and a log line is not branchable.
        self.profile_exact = OpenRocketInstance(
            jar, log_level=log_level, jvm_path=jvm_path, jvm_args=tuple(jvm_args)
        ).profile_exact
        count = _count_simulations(ork)
        if count is not None and not 0 <= simulation_index < max(count, 1):
            raise IndexError(
                f"simulation_index {simulation_index} out of range: {ork} defines {count}"
            )
        self._cfg = {
            "ork_file": ork,
            "jar_path": jar,
            "simulation_index": simulation_index,
            "log_level": log_level,
            "jvm_path": os.fspath(jvm_path) if jvm_path is not None else None,
            "jvm_args": tuple(jvm_args),
            "worker_stdout": worker_stdout,
            "_test_init": _test_init,
        }
        self._max_workers = max_workers
        self._max_tasks_per_child = max_tasks_per_child
        self._executor: ProcessPoolExecutor | None = None
        self._broken = False

    # -- validation helpers (pure; unit-tested directly) --

    @staticmethod
    def _validate_declarative(tasks: list[dict]) -> None:
        keysets = {frozenset(t) - {"seed"} for t in tasks}
        if len(keysets) > 1:
            union = frozenset().union(*keysets)
            common = frozenset(union)
            for keys in keysets:
                common &= keys
            raise ValueError(
                "declarative tasks must share one key set; "
                f"{sorted(union - common)} appear in some tasks but not all"
            )
        keys = keysets.pop() if keysets else frozenset()
        unknown = keys - set(DECLARATIVE_KEYS)
        if unknown:
            raise ValueError(
                f"unknown declarative keys {sorted(unknown)}; the verified "
                f"set is {sorted(DECLARATIVE_KEYS)} (use worker_fn= for "
                "anything else)"
            )
        if "launch_rod_direction" in keys and "launch_into_wind" not in keys:
            raise ValueError(
                "launch_rod_direction has no effect while OpenRocket's "
                "launch-into-wind default is active — include "
                "launch_into_wind: False in the tasks"
            )
        for task in tasks:
            if "seed" in task:
                seed = task["seed"]
                if (
                    not isinstance(seed, int)
                    or isinstance(seed, bool)
                    or not _SEED_MIN <= seed <= _SEED_MAX
                ):
                    raise OverflowError(
                        f"task seed {seed!r} is not a signed 32-bit int "
                        "(OpenRocket seeds are Java ints)"
                    )
            for key, value in task.items():
                if key == "seed":
                    continue
                spec = DECLARATIVE_KEYS[key]
                if spec.kind is float and isinstance(value, bool):
                    raise TypeError(f"{key} expects a number, got a bool")
                if not isinstance(value, (int, float) if spec.kind is float else bool):
                    raise TypeError(
                        f"{key} expects {spec.kind.__name__}, got {type(value).__name__}"
                    )

    @staticmethod
    def _validate_worker_fn(worker_fn) -> None:
        try:
            pickle.dumps(worker_fn)
        except Exception as e:
            raise ValueError(
                f"worker_fn is not picklable ({e}); define it at module "
                "level in an importable module — lambdas, closures, and "
                "locally defined functions cannot cross the spawn boundary"
            ) from e
        module = getattr(worker_fn, "__module__", "")
        if module in ("__main__", "__mp_main__"):
            main = sys.modules.get("__main__")
            main_file = getattr(main, "__file__", None)
            if not (main_file and os.path.exists(main_file)):
                raise ValueError(
                    "worker_fn is defined in an interactive __main__ "
                    "(notebook/REPL) — spawn workers cannot re-import it. "
                    "Save it to a .py module and import it (in a notebook: "
                    "%%writefile workers.py, then from workers import ...)"
                )

    @staticmethod
    def _derive_seeds(tasks: list[dict], seed) -> list[int]:
        """Unique 31-bit seeds per task (Java's own randomizeSeed draws a
        full 32-bit space where 10k-run studies collide by birthday
        statistics); task['seed'] overrides."""
        # note: several tasks pinning the SAME seed are honored verbatim —
        # uniqueness is guaranteed only for derived seeds
        rng = random.Random(seed) if seed is not None else random.SystemRandom()
        used: set[int] = {t["seed"] for t in tasks if "seed" in t}
        seeds = []
        for task in tasks:
            if "seed" in task:
                seeds.append(task["seed"])
                continue
            while True:
                candidate = rng.getrandbits(31)
                if candidate not in used:
                    used.add(candidate)
                    seeds.append(candidate)
                    break
        return seeds

    def _ensure_executor(self, task_count: int) -> ProcessPoolExecutor:
        if self._broken:
            raise OrlabError(
                "this SimulationPool is dead after a worker crash or interrupt; create a new one"
            )
        if self._executor is None:
            workers = self._max_workers
            if workers is None:
                # the default is sized to the first run; an explicit value is
                # respected verbatim (idle workers just idle)
                workers = max(1, min(min(4, os.cpu_count() or 1), task_count))
            kwargs: dict = {
                "max_workers": workers,
                "mp_context": multiprocessing.get_context("spawn"),
                "initializer": _worker_init,
                "initargs": (self._cfg,),
            }
            if self._max_tasks_per_child is not None:
                kwargs["max_tasks_per_child"] = self._max_tasks_per_child
            self._executor = ProcessPoolExecutor(**kwargs)
        return self._executor

    def run(
        self,
        tasks,
        *,
        worker_fn=None,
        seed=None,
        progress=None,
        on_error: str = "collect",
    ) -> StudyResult:
        """Runs one task per simulation and collects the results.

        tasks: an iterable of mappings (DECLARATIVE_KEYS names, plus an
        optional reserved 'seed'), or an int meaning that many runs of the
        document as-is. worker_fn(helper, sim, task): an importable,
        module-level callable taking over the whole task body — set options
        yourself (everything you vary), run the simulation, return
        plain-Python data. seed: study seed for reproducible seed
        derivation; default draws from SystemRandom. progress(done, total):
        called with (0, total) up front — JVM boot takes seconds and bars
        should render — then once per completion; wrap
        ``pool.run(...)``'s completions with tqdm by passing
        ``progress=lambda done, total: bar.update(...)`` or iterate
        to_records afterwards. on_error: 'collect' (failures are SimError
        data) or 'abort' (first failure cancels the study).

        :raises StudyAborted: interrupt / worker-crash / worker-init /
            task-error (with ``.partial`` holding everything collected).
        """
        if on_error not in ("collect", "abort"):
            raise ValueError("on_error must be 'collect' or 'abort'")
        if isinstance(tasks, bool):
            raise TypeError("tasks must be an iterable of mappings or an int")
        if isinstance(tasks, int):
            if tasks < 0:
                raise ValueError(f"cannot run {tasks} simulations")
            task_list: list[dict] = [{} for _ in range(tasks)]
        else:
            try:
                task_list = [dict(t) for t in tasks]
            except (TypeError, ValueError) as e:
                raise TypeError(
                    "tasks must be an iterable of mappings or an int "
                    f"(got {type(tasks).__name__}); did you pass a single "
                    "mapping instead of a list?"
                ) from e
        if worker_fn is None:
            self._validate_declarative(task_list)
        else:
            for task in task_list:
                if "seed" in task:
                    self._validate_declarative([{"seed": task["seed"]}])
            self._validate_worker_fn(worker_fn)
            try:
                pickle.dumps(task_list)
            except Exception as e:
                raise ValueError(
                    f"tasks are not picklable ({e}); worker_fn tasks must "
                    "hold plain-Python data only"
                ) from e
        if not task_list:
            return StudyResult((), ())
        seeds = self._derive_seeds(task_list, seed)

        results: list[SimResult] = []
        errors: list[SimError] = []
        total = len(task_list)

        def partial() -> StudyResult:
            return StudyResult(
                tuple(sorted(results, key=lambda r: r.index)),
                tuple(sorted(errors, key=lambda e: e.index)),
            )

        def kill_pool() -> None:
            self._broken = True
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

        futures: list = []
        try:
            executor = self._ensure_executor(total)
            for index, task in enumerate(task_list):
                futures.append(executor.submit(_worker_run, index, task, seeds[index], worker_fn))
            if progress is not None:
                try:
                    progress(0, total)
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise
            for done, future in enumerate(as_completed(futures), start=1):
                outcome = future.result()
                kind = outcome[0]
                if kind == "init_error":
                    for f in futures:
                        f.cancel()
                    kill_pool()  # init config is identical across workers:
                    # every worker holds an unusable JVM — release them
                    raise StudyAborted(
                        "worker-init",
                        partial(),
                        f"a worker failed to initialize:\n{outcome[2]}",
                    )
                index = outcome[1]
                if kind == "ok":
                    _, _, payload, actual_seed, reassigned = outcome
                    results.append(
                        SimResult(index, task_list[index], actual_seed, reassigned, payload)
                    )
                else:
                    _, _, error_type, message, tb = outcome
                    errors.append(
                        SimError(index, task_list[index], seeds[index], error_type, message, tb)
                    )
                    if on_error == "abort":
                        for f in futures:
                            f.cancel()
                        raise StudyAborted(
                            "task-error",
                            partial(),
                            f"task {index} failed ({error_type}: {message}) and on_error='abort'",
                        )
                if progress is not None:
                    try:
                        progress(done, total)
                    except BaseException:
                        for f in futures:
                            f.cancel()
                        raise
            return partial()
        except KeyboardInterrupt:
            kill_pool()
            raise StudyAborted(
                "interrupt", partial(), "interrupted; partial results preserved"
            ) from None
        except BrokenProcessPool as e:
            kill_pool()
            raise StudyAborted(
                "worker-crash",
                partial(),
                "a worker process died (JVM crash or out-of-memory?) — "
                "consider jvm_args=('-Xmx512m',) per worker, fewer workers, "
                "or max_tasks_per_child",
            ) from e

    def shutdown(self) -> None:
        """Stops the workers (their JVMs end with their processes). The pool
        cannot be reused afterwards."""
        self._broken = True
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
