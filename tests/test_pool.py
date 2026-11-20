"""SimulationPool, jar-free: pure validation/seed logic, constructor
checks, and behavioral tests through the _test_init seam under a REAL spawn
pool (worker processes, no JVM)."""

import sys
import zipfile
from types import SimpleNamespace

import _pool_stubs
import pytest

import orlab.parallel as parallel
from orlab import SimulationPool
from orlab.errors import OrlabError, StudyAborted


@pytest.fixture
def ork(tmp_path):
    path = tmp_path / "rocket.ork"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("rocket.ork", "<openrocket><simulation name='a'></simulation></openrocket>")
    return path


@pytest.fixture
def jar(tmp_path):
    path = tmp_path / "OpenRocket-24.12.jar"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("build.properties", "build.version=24.12\n")
    return path


def _pool(ork, jar, **kwargs):
    kwargs.setdefault("max_workers", 2)
    kwargs.setdefault("_test_init", _pool_stubs.fake_init)
    return SimulationPool(ork, str(jar), **kwargs)


# --- pure validation ---


def test_pool_exposes_whether_the_profile_is_exact(ork, tmp_path):
    """A pool running on stale enums is exactly what a caller must branch on.

    The pool builds an OpenRocketInstance to validate the jar and fire the
    fallback warning once in the parent. That instance's verdict is the
    pool's verdict, and a log line is not something a caller can act on (#61).
    """
    newer = tmp_path / "OpenRocket-26.xx-SNAPSHOT.jar"
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("build.properties", "build.version=26.xx-SNAPSHOT\n")
    assert _pool(ork, newer).profile_exact is False


def test_pool_profile_exact_is_true_on_a_profiled_jar(ork, jar):
    assert _pool(ork, jar).profile_exact is True


def test_heterogeneous_key_sets_rejected(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="share one key set"):
        pool.run([{"wind_speed_average": 1.0}, {"launch_rod_angle": 0.1}])


def test_unknown_keys_rejected_naming_whitelist(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="worker_fn"):
        pool.run([{"wind_speed": 1.0}])


def test_rod_direction_without_into_wind_rejected(ork, jar):
    """The silent no-op the declarative contract exists to catch: rod
    direction is hijacked by the into-wind default."""
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="launch_into_wind"):
        pool.run([{"launch_rod_direction": 1.2}])


@pytest.mark.parametrize("seed", [2**31, -(2**31) - 1, 1.5, "42", True])
def test_bad_task_seed_rejected(ork, jar, seed):
    pool = _pool(ork, jar)
    with pytest.raises((OverflowError, TypeError)):
        pool.run([{"wind_speed_average": 1.0, "seed": seed}])


def test_bool_for_float_key_rejected(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(TypeError, match="number"):
        pool.run([{"wind_speed_average": True}])


def test_lambda_worker_fn_rejected_with_guidance(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="module level"):
        pool.run(1, worker_fn=lambda h, s, t: None)


def test_notebook_main_worker_fn_rejected(ork, jar, monkeypatch):
    """A function from an interactive __main__ pickles fine but dies opaquely
    after N JVM boots — reject it up front."""

    def fn(helper, sim, task):
        return None

    fn.__module__ = "__main__"
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace())  # no __file__
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="notebook"):
        pool.run(1, worker_fn=fn)


def test_seed_derivation_deterministic_and_deduplicated():
    tasks = [{} for _ in range(50)] + [{"seed": 7}, {"seed": -3}]
    a = SimulationPool._derive_seeds(tasks, seed=123)
    b = SimulationPool._derive_seeds(tasks, seed=123)
    assert a == b  # study seed reproduces the derivation
    assert len(set(a)) == len(a)  # no collisions
    assert a[-2:] == [7, -3]  # task overrides win
    c = SimulationPool._derive_seeds(tasks, seed=124)
    assert c != a


# --- constructor validation ---


def test_missing_ork_rejected(tmp_path, jar):
    with pytest.raises(FileNotFoundError, match="ork"):
        SimulationPool(tmp_path / "missing.ork", str(jar))


def test_bad_jar_rejected(ork, tmp_path):
    from orlab.errors import NotAnOpenRocketJar

    bad = tmp_path / "bad.jar"
    bad.write_text("not a jar")
    with pytest.raises(NotAnOpenRocketJar):
        SimulationPool(ork, str(bad))


def test_simulation_index_bounds_checked_without_jvm(ork, jar):
    with pytest.raises(IndexError, match="simulation_index"):
        SimulationPool(ork, str(jar), simulation_index=3)


def test_max_tasks_per_child_needs_311(ork, jar, monkeypatch):
    monkeypatch.setattr(parallel.sys, "version_info", (3, 10, 8))
    with pytest.raises(OrlabError, match="3.11"):
        SimulationPool(ork, str(jar), max_tasks_per_child=5)


def test_bad_worker_stdout_rejected(ork, jar):
    with pytest.raises(ValueError, match="worker_stdout"):
        SimulationPool(ork, str(jar), worker_stdout="tee")


def test_empty_tasks_no_pool(ork, jar):
    pool = _pool(ork, jar)
    study = pool.run([])
    assert study.results == () and study.errors == ()
    assert pool._executor is None  # no workers were ever started


# --- behavioral: real spawn pool, fake workers ---


def test_declarative_run_results_ordered_and_seeded(ork, jar):
    pool = _pool(ork, jar)
    calls = []
    study = pool.run(
        [{"wind_speed_average": float(i)} for i in range(6)],
        seed=42,
        progress=lambda done, total: calls.append((done, total)),
    )
    assert [r.index for r in study.results] == list(range(6))
    assert len({r.seed for r in study.results}) == 6
    assert not any(r.seed_reassigned for r in study.results)
    assert all(r.payload["apogee"] == 51.0 for r in study.results)
    assert calls[0] == (0, 6) and calls[-1] == (6, 6)
    assert len(calls) == 7
    records = study.to_records()
    assert records[3]["wind_speed_average"] == 3.0 and "apogee" in records[3]


def test_worker_fn_error_is_data_and_pool_survives(ork, jar):
    pool = _pool(ork, jar)
    study = pool.run(
        [{"x": 1}, {"x": 2}, {"x": 3}],
        worker_fn=_pool_stubs.value_error_fn,
    )
    assert study.results == ()
    assert len(study.errors) == 3
    assert study.errors[0].error_type == "ValueError"
    assert "deliberate failure" in study.errors[0].message
    with pytest.raises(OrlabError, match="3 of 3"):
        study.raise_if_errors()
    # same pool immediately reusable
    again = pool.run([{"x": 5}], worker_fn=_pool_stubs.ok_fn)
    assert again.results[0].payload["value"] == 10


def test_on_error_abort(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(StudyAborted) as exc:
        pool.run([{"x": 1}] * 3, worker_fn=_pool_stubs.value_error_fn, on_error="abort")
    assert exc.value.reason == "task-error"


def test_unpicklable_payload_is_task_error_with_context(ork, jar):
    pool = _pool(ork, jar)
    study = pool.run([{}], worker_fn=_pool_stubs.unpicklable_payload_fn)
    assert study.errors
    err = study.errors[0]
    # orlab's message, not Python's, which lacks "pickle" on 3.12 and 3.13 (#91)
    assert err.error_type == "ValueError"
    assert "worker_fn returned a payload that is not picklable" in err.message


def test_worker_crash_aborts_with_partial(ork, jar):
    pool = _pool(ork, jar, max_workers=1)
    tasks = [{"x": 1}, {"crash": True}]
    with pytest.raises(StudyAborted) as exc:
        pool.run(tasks, worker_fn=_dispatch_fn)
    assert exc.value.reason == "worker-crash"
    assert "-Xmx" in str(exc.value)
    # pool is dead afterwards
    with pytest.raises(OrlabError, match="dead"):
        pool.run(1)


def test_init_failure_single_report(ork, jar):
    pool = _pool(ork, jar, _test_init=_pool_stubs.failing_init)
    with pytest.raises(StudyAborted) as exc:
        pool.run(4, worker_fn=_pool_stubs.ok_fn)
    assert exc.value.reason == "worker-init"
    assert "deliberate init failure" in str(exc.value)


def test_progress_exception_cancels_run_pool_reusable(ork, jar):
    pool = _pool(ork, jar)

    def bad_progress(done, total):
        if done >= 1:
            raise RuntimeError("bar exploded")

    with pytest.raises(RuntimeError, match="bar exploded"):
        pool.run(4, worker_fn=_pool_stubs.ok_fn, progress=bad_progress)
    study = pool.run([{"x": 2}], worker_fn=_pool_stubs.ok_fn)
    assert study.results[0].payload["value"] == 4


def test_state_bleed_snapshot_restore(ork, jar):
    """run() with wind varied, then a second run() that doesn't set wind:
    the second run must see the document baseline, not run 1's last task."""
    pool = _pool(ork, jar, max_workers=1)
    pool.run([{"wind_speed_average": 9.9}])
    study = pool.run([{"launch_rod_angle": 0.2}], worker_fn=_pool_stubs.echo_options_fn)
    assert study.results[0].payload["wind"] == 2.0  # the fake baseline value


def _dispatch_fn(helper, sim, task):
    if task.get("crash"):
        return _pool_stubs.crash_fn(helper, sim, task)
    return _pool_stubs.ok_fn(helper, sim, task)


def test_closure_and_local_class_worker_fns_rejected(ork, jar):
    pool = _pool(ork, jar)
    captured = 41

    def closure_fn(helper, sim, task):
        return captured

    class LocalRunner:
        def __call__(self, helper, sim, task):
            return None

    for fn in (closure_fn, LocalRunner()):
        with pytest.raises(ValueError, match="module level"):
            pool.run(1, worker_fn=fn)


def test_unpicklable_task_values_rejected_up_front(ork, jar):
    """worker_fn tasks carrying unpicklable values would otherwise fail in
    the executor's feeder thread as a raw PicklingError, discarding every
    completed result."""
    pool = _pool(ork, jar)
    with pytest.raises(ValueError, match="not picklable"):
        pool.run([{"x": 1}, {"bad": lambda: None}], worker_fn=_pool_stubs.ok_fn)


def test_interrupt_during_submit_and_collection(ork, jar, monkeypatch):
    pool = _pool(ork, jar)

    class InterruptingExecutor:
        def __init__(self, real):
            self._real = real
            self._count = 0

        def submit(self, *args, **kwargs):
            self._count += 1
            if self._count == 2:
                raise KeyboardInterrupt
            return self._real.submit(*args, **kwargs)

    real_ensure = pool._ensure_executor
    monkeypatch.setattr(pool, "_ensure_executor", lambda n: InterruptingExecutor(real_ensure(n)))
    with pytest.raises(StudyAborted) as exc:
        pool.run(4, worker_fn=_pool_stubs.ok_fn)
    assert exc.value.reason == "interrupt"
    with pytest.raises(OrlabError, match="dead"):
        pool.run(1)


def test_interrupt_during_collection(ork, jar, monkeypatch):
    pool = _pool(ork, jar)

    def interrupting_as_completed(futures):
        raise KeyboardInterrupt

    monkeypatch.setattr(parallel, "as_completed", interrupting_as_completed)
    with pytest.raises(StudyAborted) as exc:
        pool.run(2, worker_fn=_pool_stubs.ok_fn)
    assert exc.value.reason == "interrupt"


def test_pool_uses_spawn_context(ork, jar):
    pool = _pool(ork, jar)
    executor = pool._ensure_executor(1)
    assert executor._mp_context.get_start_method() == "spawn"
    pool.shutdown()


def test_seed_dedup_avoids_pinned_collision():
    """Pin a task seed equal to the rng's own first draw: derivation must
    steer around it (this is the collision the dedup exists for)."""
    import random

    first_draw = random.Random(123).getrandbits(31)
    seeds = SimulationPool._derive_seeds([{"seed": first_draw}, {}], seed=123)
    assert seeds[0] == first_draw
    assert seeds[1] != first_draw


def test_crash_partial_contents(ork, jar):
    pool = _pool(ork, jar, max_workers=1)
    with pytest.raises(StudyAborted) as exc:
        pool.run([{"x": 4}, {"crash": True}], worker_fn=_dispatch_fn)
    partial = exc.value.partial
    assert len(partial.results) == 1
    assert partial.results[0].payload["value"] == 8


def test_worker_init_abort_releases_workers(ork, jar):
    """A worker-init abort must not leak N idle JVM processes."""
    pool = _pool(ork, jar, _test_init=_pool_stubs.failing_init)
    with pytest.raises(StudyAborted):
        pool.run(2, worker_fn=_pool_stubs.ok_fn)
    assert pool._executor is None and pool._broken


def test_initial_progress_exception_cancels(ork, jar):
    pool = _pool(ork, jar)

    def bad_progress(done, total):
        raise RuntimeError("dies at zero")

    with pytest.raises(RuntimeError, match="dies at zero"):
        pool.run(3, worker_fn=_pool_stubs.ok_fn, progress=bad_progress)
    # pool stays reusable
    assert pool.run([{"x": 1}], worker_fn=_pool_stubs.ok_fn).results


def test_explicit_max_workers_not_clamped_by_first_run(ork, jar):
    pool = _pool(ork, jar, max_workers=3)
    executor = pool._ensure_executor(1)  # 1-task warm-up must not shrink it
    assert executor._max_workers == 3
    pool.shutdown()


def test_default_extract_builtin_floats():
    payload = parallel._default_extract(
        _pool_stubs.FakeHelper(_pool_stubs.FakeSim()), _pool_stubs.FakeSim()
    )
    assert set(payload) == {
        "apogee",
        "max_velocity",
        "max_acceleration",
        "flight_time",
        "landing_x",
        "landing_y",
    }
    assert all(type(v) is float for v in payload.values())


@pytest.mark.parametrize("bad", [True, -3])
def test_int_task_edge_cases_rejected(ork, jar, bad):
    pool = _pool(ork, jar)
    with pytest.raises((TypeError, ValueError)):
        pool.run(bad)


def test_single_mapping_tasks_rejected_clearly(ork, jar):
    pool = _pool(ork, jar)
    with pytest.raises(TypeError, match="single"):
        pool.run({"wind_speed_average": 1.0})


def test_study_aborted_pickle_round_trip():
    import pickle

    from orlab.parallel import StudyResult

    err = StudyAborted("interrupt", StudyResult((), ()), "test message")
    loaded = pickle.loads(pickle.dumps(err))
    assert loaded.reason == "interrupt" and "test message" in str(loaded)
