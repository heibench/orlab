import logging
import math
import os
from collections.abc import Iterable

import jpype
import numpy as np

from .._enums import FlightDataType, FlightEvent
from ..errors import OrlabError, UnsupportedFlightDataType
from ..profiles import versions_with
from .jiterator import JIterator
from .openrocket_instance import OpenRocketInstance
from .simulation_listener import AbstractSimulationListener
from .summary import FlightSummary, _bearing_deg, _mean_descent_rate, _value_at, _window_stats

__all__ = ["EventTimes", "Helper"]

logger = logging.getLogger(__name__)


class EventTimes(dict[FlightEvent, list[float]]):
    """Event times keyed by type, plus the types this orlab could not interpret.

    A plain ``dict`` cannot tell "the simulation had no such event" from "orlab
    did not recognise the event and dropped it". The second is likeliest exactly
    when :attr:`OpenRocketInstance.profile_exact` is ``False`` -- a newer
    OpenRocket on a nearest-older profile is where unknown event types appear --
    so it has to be readable from the result, not only from a log line an
    application can silence by raising its level.

    This is a ``dict``, so callers that only index it are unaffected.
    """

    def __init__(
        self,
        events: dict[FlightEvent, list[float]] | None = None,
        unknown: Iterable[str] = (),
    ) -> None:
        super().__init__(events or {})
        self.unknown_event_types: frozenset[str] = frozenset(unknown)
        """Java event-type names dropped because this orlab has no enum member for them."""


# names already warned about in this process: version-absence warnings fire
# once, not once per summary, so dispersion loops stay readable
_absence_warned: set[str] = set()


def _warn_absent_once(what: str, version: str) -> None:
    if what not in _absence_warned:
        _absence_warned.add(what)
        logger.warning(
            "%s is not available in OpenRocket %s; the corresponding summary field(s) will be NaN",
            what,
            version,
        )


class Helper:
    """This class contains a variety of useful helper functions and wrapper for using
    openrocket via jpype. These are intended to take care of some of the more
    cumbersome aspects of calling methods, or provide more 'pythonic' data structures
    for general use.
    """

    def __init__(self, open_rocket_instance: OpenRocketInstance):
        if not open_rocket_instance.started:
            raise OrlabError(
                "OpenRocketInstance not started — enter it first "
                "('with OpenRocketInstance(...) as instance:')"
            )

        self._instance = open_rocket_instance
        self.openrocket = open_rocket_instance.openrocket

    def load_doc(self, or_filename):
        """Loads a .ork file and returns the corresponding openrocket document"""

        or_java_file = jpype.java.io.File(or_filename)
        loader = self.openrocket.file.GeneralRocketLoader(or_java_file)
        doc = loader.load()
        return doc

    def save_doc(self, or_filename, doc):
        """Saves an openrocket document to a .ork file"""

        or_java_file = jpype.java.io.File(or_filename)
        saver = self.openrocket.file.GeneralRocketSaver()
        saver.save(or_java_file, doc)

    def run_simulation(
        self,
        sim,
        listeners: list[AbstractSimulationListener] | None = None,
        *,
        randomize_seed: bool = True,
    ):
        """This is a wrapper to the Simulation.simulate() for running a simulation
        The optional listeners parameter is a sequence of objects which extend orl.AbstractSimulationListener.

        By default the simulation's random seed is randomized before each run
        (identical repeated runs would otherwise produce identical numbers —
        the behavior naive monte-carlo loops rely on). Pass
        randomize_seed=False to respect the seed already set on the options.
        Note: on OpenRocket 24.12 the wind model draws additional per-process
        entropy, so a fixed seed reproduces results within one process but not
        across processes when wind is enabled.
        """

        if listeners is None:
            # this method takes in a vararg of SimulationListeners, which is just a fancy way of passing in an array, so
            # we have to pass in an array of length 0 ..
            listener_array = jpype.JArray(
                self.openrocket.simulation.listeners.AbstractSimulationListener, 1
            )(0)
        else:
            listener_array = [
                jpype.JProxy(
                    (
                        self.openrocket.simulation.listeners.SimulationListener,
                        self.openrocket.simulation.listeners.SimulationEventListener,
                        self.openrocket.simulation.listeners.SimulationComputationListener,
                        jpype.java.lang.Cloneable,
                    ),
                    inst=c,
                )
                for c in listeners
            ]

        if randomize_seed:
            sim.getOptions().randomizeSeed()
        sim.simulate(listener_array)

    def translate_flight_data_type(self, flight_data_type: FlightDataType | str):
        """Translates a FlightDataType (or constant name) to the Java constant.
        Raises UnsupportedFlightDataType when the loaded OpenRocket version
        does not expose it (constants differ across versions).
        """
        if isinstance(flight_data_type, FlightDataType):
            name = flight_data_type.name
        elif isinstance(flight_data_type, str):
            name = flight_data_type
        else:
            raise TypeError("Invalid type for flight_data_type")

        java_type = getattr(self.openrocket.simulation.FlightDataType, name, None)
        if java_type is None:
            loaded = self._instance.or_version
            available = versions_with(name)
            detail = f"available in: {', '.join(available)}" if available else "unknown constant"
            raise UnsupportedFlightDataType(
                f"{name} is not available in OpenRocket {loaded} ({detail})"
            )
        return java_type

    def get_timeseries(
        self, simulation, variables: Iterable[FlightDataType | str], branch_number=0
    ) -> dict[FlightDataType | str, np.ndarray]:
        """
        Gets a dictionary of timeseries data (as numpy arrays) from a simulation given specific variable names.

        :param simulation: An openrocket simulation object.
        :param variables: A sequence of FlightDataType or strings representing the desired variables
        :param branch_number: Stage branch to read (0 = sustainer).
        :return: Dict keyed by the requested variables; values are numpy arrays.
        """

        branch = simulation.getSimulatedData().getBranch(branch_number)
        output: dict[FlightDataType | str, np.ndarray] = {}
        for v in variables:
            output[v] = np.array(branch.get(self.translate_flight_data_type(v)))

        return output

    def get_final_values(
        self, simulation, variables: Iterable[FlightDataType | str], branch_number=0
    ) -> dict[FlightDataType | str, float]:
        """
        Gets a the final value in the time series from a simulation given variable names.

        :param simulation: An openrocket simulation object.
        :param variables: A sequence of FlightDataType or strings representing the desired variables
        :param branch_number: Stage branch to read (0 = sustainer).
        :return: Dict keyed by the requested variables; values are the final samples.
        """

        branch = simulation.getSimulatedData().getBranch(branch_number)
        output: dict[FlightDataType | str, float] = {}
        for v in variables:
            output[v] = branch.get(self.translate_flight_data_type(v))[-1]

        return output

    def translate_flight_event(self, flight_event) -> FlightEvent:
        """Translates a Java FlightEvent.Type constant to the FlightEvent enum.
        Raises ValueError for event types this orlab version does not know
        (newer OpenRocket releases add types; get_events skips them instead).
        """
        name = str(flight_event.name())
        try:
            return FlightEvent[name]
        except KeyError:
            raise ValueError(
                f"Unknown flight event type {name!r} from the loaded OpenRocket version"
            ) from None

    def get_events(self, simulation, branch_number: int = 0) -> EventTimes:
        """Returns the flight events in a given simulation, keyed by FlightEvent,
        with a list of all the times at which each event occurs.

        Event types this orlab version does not know are not in the mapping. Their
        Java names are on :attr:`EventTimes.unknown_event_types`, so a caller can
        tell a simulation that had no such event from one whose event orlab could
        not interpret. A warning is also logged once per name.

        :param branch_number: Stage branch to read (0 = sustainer).
        """
        branch = simulation.getSimulatedData().getBranch(branch_number)

        output: dict[FlightEvent, list[float]] = {}
        unknown: set[str] = set()
        for name, times in self._events_by_name(branch).items():
            try:
                event = FlightEvent[name]
            except KeyError:
                if name not in unknown:
                    unknown.add(name)
                    logger.warning("Skipping unknown flight event type %s", name)
                continue
            output[event] = times

        return EventTimes(output, unknown)

    @staticmethod
    def _events_by_name(branch) -> dict[str, list[float]]:
        """One traversal of a branch's events, keyed by stable string names —
        shared by get_events (enum-gated view) and get_summary (which must
        see every event regardless of orlab's enum vintage)."""
        output: dict[str, list[float]] = {}
        for ev in branch.getEvents():
            output.setdefault(str(ev.getType().name()), []).append(float(ev.getTime()))
        return output

    def get_summary(self, simulation, branch_number: int = 0) -> FlightSummary:
        """Returns the scalar flight report for one branch as a
        :class:`~orlab.FlightSummary` — plain-Python values only, safe to
        pickle into JVM-less processes.

        Branch 0 uses OpenRocket's own FlightData summary getters verbatim;
        other branches derive the same numbers from their own time series.
        Missing/inapplicable values are NaN (see the field docs). Degenerate
        flights degrade to NaN; an unsimulated simulation raises OrlabError.

        :param branch_number: Stage branch to summarize (0 = sustainer).
        """
        branch_number = int(branch_number)  # builtin-types contract incl. numpy ints
        flight_data = simulation.getSimulatedData()
        if flight_data is None or int(flight_data.getBranchCount()) == 0:
            raise OrlabError("Simulation has no flight data; call run_simulation first")
        branch_count = int(flight_data.getBranchCount())
        if not 0 <= branch_number < branch_count:
            raise IndexError(f"branch {branch_number} out of range (simulation has {branch_count})")
        branch = flight_data.getBranch(branch_number)
        version = self._instance.or_version

        times = self._branch_series(branch, "TYPE_TIME")
        altitude = self._branch_series(branch, "TYPE_ALTITUDE")
        stability = self._branch_series(branch, "TYPE_STABILITY")
        velocity_z = self._branch_series(branch, "TYPE_VELOCITY_Z")
        velocity_total = self._branch_series(branch, "TYPE_VELOCITY_TOTAL")
        events = self._events_by_name(branch)

        def at_event(name: str, series: np.ndarray, which: int = 0) -> float:
            if name not in events:
                return math.nan
            return _value_at(times, series, events[name][which])

        # stability window: launch rod departure -> apogee. Boosters have no
        # LAUNCHROD (window starts at their first sample, off-rod values NaN);
        # a flight without an APOGEE event windows to its highest sample.
        t_rod = events["LAUNCHROD"][0] if "LAUNCHROD" in events else None
        if "APOGEE" in events:
            t_apogee = events["APOGEE"][0]
        elif len(altitude) and not np.isnan(altitude).all():
            t_apogee = float(times[int(np.nanargmax(altitude))])
        else:
            t_apogee = None
        window_start = t_rod if t_rod is not None else (float(times[0]) if len(times) else None)
        if window_start is not None and t_apogee is not None:
            min_stab, max_stab = _window_stats(times, stability, window_start, t_apogee)
        else:
            min_stab, max_stab = math.nan, math.nan

        t_deploy = (
            events["RECOVERY_DEVICE_DEPLOYMENT"][-1]
            if "RECOVERY_DEVICE_DEPLOYMENT" in events
            else None
        )
        t_ground = (
            events["GROUND_HIT"][0]
            if "GROUND_HIT" in events
            else (float(times[-1]) if len(times) else None)
        )
        if t_deploy is not None and t_ground is not None:
            descent_rate = _mean_descent_rate(times, velocity_z, t_deploy, t_ground)
        else:
            descent_rate = math.nan

        if branch_number == 0:

            def scalar(getter: str) -> float:
                fn = getattr(flight_data, getter, None)
                if fn is None:
                    _warn_absent_once(f"FlightData.{getter}", version)
                    return math.nan
                return float(fn())

            apogee = scalar("getMaxAltitude")
            time_to_apogee = scalar("getTimeToApogee")
            max_velocity = scalar("getMaxVelocity")
            max_acceleration = scalar("getMaxAcceleration")
            max_mach = scalar("getMaxMachNumber")
            velocity_off_rod = scalar("getLaunchRodVelocity")
            velocity_at_deployment = scalar("getDeploymentVelocity")
            ground_hit_velocity = scalar("getGroundHitVelocity")
            flight_time = scalar("getFlightTime")
            optimum_delay = scalar("getOptimumDelay")
        else:
            acceleration = self._branch_series(branch, "TYPE_ACCELERATION_TOTAL")
            mach = self._branch_series(branch, "TYPE_MACH_NUMBER")

            def series_max(series: np.ndarray) -> float:
                if len(series) == 0 or np.isnan(series).all():
                    return math.nan
                return float(np.nanmax(series))

            apogee = series_max(altitude)
            time_to_apogee = t_apogee if t_apogee is not None else math.nan
            max_velocity = series_max(velocity_total)
            max_acceleration = series_max(acceleration)
            max_mach = series_max(mach)
            velocity_off_rod = at_event("LAUNCHROD", velocity_total)
            velocity_at_deployment = at_event("RECOVERY_DEVICE_DEPLOYMENT", velocity_total, -1)
            ground_hit_velocity = at_event("GROUND_HIT", velocity_total)
            flight_time = float(times[-1]) if len(times) else math.nan
            # policy: reported for the sustainer only, where FlightData's own
            # figure is authoritative
            optimum_delay = math.nan

        pos_x = self._branch_series(branch, "TYPE_POSITION_X")
        pos_y = self._branch_series(branch, "TYPE_POSITION_Y")
        landing_x = float(pos_x[-1]) if len(pos_x) else math.nan
        landing_y = float(pos_y[-1]) if len(pos_y) else math.nan
        landing_distance = math.hypot(landing_x, landing_y)

        if hasattr(branch, "getName"):
            branch_name = str(branch.getName())
        elif hasattr(branch, "getBranchName"):
            branch_name = str(branch.getBranchName())
        else:
            branch_name = ""

        # bounded at apogee: the NaN-skip must not report a post-apogee
        # (tumble-regime) sample as the off-rod margin
        off_rod_stability = math.nan
        if t_rod is not None:
            off_rod_stability = _value_at(
                times, stability, t_rod, t_max=t_apogee if t_apogee is not None else math.inf
            )

        return FlightSummary(
            velocity_off_rod=velocity_off_rod,
            stability_off_rod_cal=off_rod_stability,
            apogee=apogee,
            time_to_apogee=time_to_apogee,
            max_velocity=max_velocity,
            max_acceleration=max_acceleration,
            max_mach=max_mach,
            min_stability_cal=min_stab,
            max_stability_cal=max_stab,
            velocity_at_deployment=velocity_at_deployment,
            descent_rate=descent_rate,
            ground_hit_velocity=ground_hit_velocity,
            landing_x=landing_x,
            landing_y=landing_y,
            landing_distance=landing_distance,
            landing_bearing_deg=_bearing_deg(landing_x, landing_y),
            flight_time=flight_time,
            optimum_delay=optimum_delay,
            branch_number=branch_number,
            branch_name=branch_name,
            branch_count=branch_count,
            warnings=tuple(str(w) for w in flight_data.getWarningSet()),
        )

    def _tabular_columns(self, branch, variables):
        """Resolves (label, java_type) columns for tabular export. Default:
        every profile data type populated on the branch, TYPE_TIME first,
        the rest in name order."""
        if variables is None:
            names = sorted(self._instance.profile.flight_data_types)
            if "TYPE_TIME" in names:
                names.remove("TYPE_TIME")
                names.insert(0, "TYPE_TIME")
            columns = []
            for name in names:
                try:
                    java_type = self.translate_flight_data_type(name)
                except UnsupportedFlightDataType:
                    # nearest-older fallback profile on a future jar that
                    # dropped a constant: skip it, never abort the export
                    _warn_absent_once(name, self._instance.or_version)
                    continue
                if branch.get(java_type) is not None:
                    columns.append((self._column_label(java_type, name), java_type))
            return columns
        columns = []
        for v in variables:
            java_type = self.translate_flight_data_type(v)
            name = v.name if isinstance(v, FlightDataType) else str(v)
            if branch.get(java_type) is None:
                raise ValueError(f"{name} is not populated on this branch — nothing to export")
            columns.append((self._column_label(java_type, name), java_type))
        return columns

    @staticmethod
    def _column_label(java_type, name: str) -> str:
        """ "NAME (unit)" from the jar's own SI unit string; units that are
        only whitespace/zero-width-space (OpenRocket's dimensionless marker)
        get no suffix."""
        unit = str(java_type.getUnitGroup().getSIUnit().getUnit())
        if not unit.replace("\u200b", "").strip():
            return name
        return f"{name} ({unit})"

    def get_dataframe(self, simulation, variables=None, branch_number: int = 0):
        """The branch's timeseries as a pandas DataFrame, one column per
        variable, labeled ``NAME (SI unit)``. Requires the ``orlab[pandas]``
        extra; everything else in orlab works without pandas.

        :param variables: FlightDataTypes or constant names; default: every
            profile data type populated on the branch.
        :param branch_number: Stage branch to read (0 = sustainer).
        """
        try:
            import pandas  # type: ignore[import-untyped]  # optional extra, no stubs
        except ImportError as e:
            raise ImportError(
                "pandas is required for get_dataframe — pip install orlab[pandas]"
            ) from e
        branch = simulation.getSimulatedData().getBranch(branch_number)
        columns = self._tabular_columns(branch, variables)
        return pandas.DataFrame(
            {label: np.asarray(branch.get(java_type), dtype=float) for label, java_type in columns}
        )

    def export_csv(self, simulation, path, variables=None, branch_number: int = 0) -> None:
        """Writes the branch's timeseries as UTF-8 CSV with ``NAME (SI
        unit)`` headers — stdlib only, no pandas needed. NaN samples become
        empty cells (``pandas.read_csv`` reads them back as NaN).

        :param variables: FlightDataTypes or constant names; default: every
            profile data type populated on the branch.
        :param branch_number: Stage branch to read (0 = sustainer).
        """
        import csv

        branch = simulation.getSimulatedData().getBranch(branch_number)
        columns = self._tabular_columns(branch, variables)
        series = [np.asarray(branch.get(java_type), dtype=float) for _, java_type in columns]
        # newline='' and explicit utf-8: text-mode newline translation would
        # write \r\r\n on Windows, and locale encodings choke on m/s²
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([label for label, _ in columns])
            try:
                for row in zip(*series, strict=True):
                    writer.writerow(
                        ["" if math.isnan(value) else repr(float(value)) for value in row]
                    )
            except ValueError as e:  # pragma: no cover - can't-happen invariant
                raise ValueError(f"branch series lengths differ: {e}") from e

    def _branch_series(self, branch, type_name: str) -> np.ndarray:
        """A branch's series for a FlightDataType constant name as a float
        array; empty when the loaded version lacks the type (warned once per
        process) or the branch holds no such series."""
        try:
            java_type = self.translate_flight_data_type(type_name)
        except UnsupportedFlightDataType:
            _warn_absent_once(type_name, self._instance.or_version)
            return np.array([])
        values = branch.get(java_type)
        if values is None:
            return np.array([])
        return np.asarray(values, dtype=float)

    # --- motor selection and swapping ---

    def _motor_mount_interface(self):
        return jpype.JClass(f"{self._instance.profile.core_root}.rocketcomponent.MotorMount")

    def _sim_fcid(self, sim):
        """The simulation's OWN flight-configuration id — never the rocket's
        selected/default config, which the sim may not fly at all (verified:
        simple.ork's sim flies A8 while the selected config shows C6, and
        assigning to the selected config is a silent no-op)."""
        if hasattr(sim, "getFlightConfigurationId"):
            return sim.getFlightConfigurationId()
        if hasattr(sim.getOptions(), "getMotorConfigurationID"):  # 15.03
            return sim.getOptions().getMotorConfigurationID()
        raise OrlabError(
            "cannot determine this simulation's flight configuration id on "
            f"OpenRocket {self._instance.or_version} — the motor API drifted "
            "beyond both known forms"
        )

    def _motor_config(self, mount, fcid):
        """The mount's MotorConfiguration at an fcid. Two accessor eras
        (probed): getMotorConfig(fcid) on 22.02+, the
        getMotorConfiguration() map on 15.03. The returned object's own API
        (getMotor/setMotor/get/setEjectionDelay) is identical on all four
        versions."""
        if hasattr(mount, "getMotorConfig"):
            return mount.getMotorConfig(fcid)
        if hasattr(mount, "getMotorConfiguration"):
            return mount.getMotorConfiguration().get(fcid)
        raise OrlabError(
            "no known motor-configuration accessor on this mount "
            f"(OpenRocket {self._instance.or_version})"
        )

    def _resolve_mount(self, sim, mount_name):
        """The motor mount to operate on: by name, else the unique mount
        carrying a motor in the sim's config, else the unique MotorMount.
        Candidate enumeration filters via the MotorMount INTERFACE before
        calling isMotorMount() — most 15.03 components lack the method
        entirely while all 24.12 components have it (probed)."""
        interface = self._motor_mount_interface()
        rocket = sim.getRocket()
        if mount_name is not None:
            if isinstance(mount_name, str):
                component = self.get_component_named(rocket, mount_name)
            else:
                component = mount_name  # a mount object works directly
            if not isinstance(component, interface) or not component.isMotorMount():
                raise ValueError(f"{mount_name} is not an active motor mount")
            return component
        candidates = [c for c in JIterator(rocket) if isinstance(c, interface) and c.isMotorMount()]
        if not candidates:
            raise ValueError("this rocket has no active motor mount")
        if len(candidates) == 1:
            return candidates[0]
        fcid = self._sim_fcid(sim)
        bearing = []
        for candidate in candidates:
            config = self._motor_config(candidate, fcid)
            if config is not None and config.getMotor() is not None:
                bearing.append(candidate)
        if len(bearing) == 1:
            return bearing[0]
        names = sorted(str(c.getName()) for c in candidates)
        raise ValueError(f"ambiguous motor mount — pass mount= (candidates: {', '.join(names)})")

    def get_motor(self, sim, mount=None) -> str | None:
        """The designation of the motor the simulation would fly on the
        given (or auto-resolved) mount, as a plain string; None when the
        sim's configuration has no motor there.
        """
        resolved = self._resolve_mount(sim, mount)
        config = self._motor_config(resolved, self._sim_fcid(sim))
        motor = config.getMotor() if config is not None else None
        return str(motor.getDesignation()) if motor is not None else None

    def find_motor(self, designation: str, manufacturer: str | None = None):
        """A motor from OpenRocket's own database by designation
        (case-insensitive exact). Common hobby designations (A8, B6, C6…)
        exist from several manufacturers — those lookups require
        ``manufacturer=`` (motor choice is safety-relevant; orlab refuses to
        guess); manufacturer matching uses OpenRocket's own alias machinery,
        so "CTI" finds Cesaroni. One manufacturer's designation can span
        several motor sets (different diameters/lengths): sets are ordered
        by diameter, then length, and the first variant of the first set is
        returned — OpenRocket keeps each set's variants deterministically
        sorted, so 'the' motor is stable across runs.

        :raises ValueError: no match (message lists near-matches), a
            designation that exists but not from the given manufacturer, or
            an ambiguous designation without ``manufacturer=``.
        """
        wanted = designation.strip().lower()
        database = self.openrocket.startup.Application.getMotorSetDatabase()
        matches = [
            s for s in database.getMotorSets() if str(s.getDesignation()).strip().lower() == wanted
        ]
        if manufacturer is not None and matches:
            # OpenRocket's Manufacturer.matches handles display/simple names
            # and aliases (CTI, CES, ...) on every supported version
            filtered = [s for s in matches if s.getManufacturer().matches(manufacturer)]
            if not filtered:
                available = sorted({str(s.getManufacturer().getDisplayName()) for s in matches})
                raise ValueError(
                    f"{designation!r} exists, but not from {manufacturer!r} "
                    f"(available: {', '.join(available)})"
                )
            matches = filtered
        if not matches:
            near = sorted(
                {
                    str(s.getDesignation())
                    for s in database.getMotorSets()
                    if wanted in str(s.getDesignation()).lower()
                    or str(s.getDesignation()).lower() in wanted
                }
            )[:10]
            hint = f"; near matches: {', '.join(near)}" if near else ""
            raise ValueError(f"no motor matches designation {designation!r}{hint}")
        makers = sorted({str(s.getManufacturer().getDisplayName()) for s in matches})
        if len(makers) > 1:
            raise ValueError(
                f"{designation!r} exists from several manufacturers "
                f"({', '.join(makers)}) — pass manufacturer="
            )
        # deterministic set choice by intrinsic geometry; the set's own
        # variant list is already deterministically sorted by OpenRocket
        chosen = min(
            matches,
            key=lambda s: (float(s.getDiameter()), float(s.getLength()), str(s.getType())),
        )
        return chosen.getMotors()[0]

    def load_motor(self, motor_file, designation: str | None = None):
        """A motor loaded from a thrust-curve file (.eng/.rse/.zip) via
        OpenRocket's own loader — no database needed, works on every
        supported version and startup path. Files holding several motors
        need ``designation=`` to pick one.

        :raises OrlabError: the file doesn't parse as a motor file.
        """
        path = os.fspath(motor_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"No such motor file: {path}")
        loader = self.openrocket.file.motor.GeneralMotorLoader()
        stream = jpype.java.io.FileInputStream(path)
        try:
            loaded = list(loader.load(stream, os.path.basename(path)))
        except Exception as e:
            raise OrlabError(f"{path} did not parse as a motor file ({e})") from e
        finally:
            stream.close()
        motors = [m.build() if hasattr(m, "build") else m for m in loaded]
        if not motors:
            raise OrlabError(f"{path} holds no motors")
        if designation is not None:
            wanted = designation.strip().lower()
            selected = [m for m in motors if str(m.getDesignation()).strip().lower() == wanted]
            if not selected:
                names = sorted(str(m.getDesignation()) for m in motors)
                raise ValueError(
                    f"{path} holds no motor designated {designation!r} "
                    f"(it holds: {', '.join(names)})"
                )
            motors = selected
        elif len(motors) > 1:
            names = sorted(str(m.getDesignation()) for m in motors)
            raise ValueError(
                f"{path} holds {len(names)} motors ({', '.join(names)}) — pass designation="
            )
        return motors[0]

    def set_motor(
        self,
        sim,
        motor,
        *,
        mount=None,
        manufacturer: str | None = None,
        designation: str | None = None,
        delay: float | None = None,
    ) -> None:
        """Sets the motor the simulation will actually fly — always keyed on
        the sim's own flight configuration (assigning to the rocket's
        *selected* config is the ecosystem's classic silent failure). The
        motor argument is a designation string (database lookup, see
        :meth:`find_motor`), a ``.eng``/``.rse``/``.zip`` path (file load,
        see :meth:`load_motor`), or a ThrustCurveMotor object. ``delay=``
        sets the ejection delay in seconds; None preserves the existing one.
        The assignment is read back and verified — a mismatch raises instead
        of failing silently.
        """
        if isinstance(motor, (str, os.PathLike)):
            text = os.fspath(motor)
            if text.lower().endswith((".eng", ".rse", ".zip")):
                if manufacturer is not None:
                    raise ValueError("manufacturer= does not apply to a motor file")
                motor = self.load_motor(text, designation=designation)
            else:
                if designation is not None:
                    raise ValueError(
                        "designation= selects a motor from a multi-motor "
                        "FILE; for database lookups the designation IS the "
                        "motor argument"
                    )
                motor = self.find_motor(text, manufacturer=manufacturer)
        target_designation = str(motor.getDesignation())

        resolved = self._resolve_mount(sim, mount)
        fcid = self._sim_fcid(sim)
        config = self._motor_config(resolved, fcid)
        if config is None:
            raise OrlabError(
                f"mount {resolved.getName()} has no motor configuration for "
                "this simulation's flight configuration"
            )
        config.setMotor(motor)
        if delay is not None:
            config.setEjectionDelay(float(delay))

        readback = self.get_motor(sim, mount=mount)
        if readback != target_designation:
            raise OrlabError(
                f"motor assignment did not stick: set {target_designation}, "
                f"read back {readback} (wrong mount or flight configuration?)"
            )

    def get_components_of_type(self, root, component_type) -> list:
        """Every component under root of a given type — a class name string
        ("BodyTube", "TrapezoidFinSet", "MotorMount", …) resolved against
        the loaded OpenRocket, or a JClass. Concrete classes, superclasses,
        and interfaces all match; an empty list is a normal answer.

        :raises ValueError: an unknown class name (named with the loaded
            version — component classes differ across OpenRocket releases).
        """
        if isinstance(component_type, str):
            java_class = getattr(self.openrocket.rocketcomponent, component_type, None)
            if not isinstance(java_class, jpype.JClass):
                raise ValueError(
                    f"{component_type!r} is not a rocket-component class in "
                    f"OpenRocket {self._instance.or_version}"
                )
            component_type = java_class
        elif not isinstance(component_type, jpype.JClass):
            # a Python class would silently match nothing; a component
            # instance would crash isinstance — refuse both clearly
            raise TypeError(
                "component_type must be a class-name string or a JClass, "
                f"got {type(component_type).__name__}"
            )
        return [c for c in JIterator(root) if isinstance(c, component_type)]

    def get_component_named(self, root, name):
        """Finds and returns the first rocket component with the given name.
        Requires a root RocketComponent, usually this will be a RocketComponent.rocket instance.
        Raises a ValueError if no component found.
        """

        for component in JIterator(root):
            if component.getName() == name:
                return component
        raise ValueError(root.toString() + " has no component named " + name)

    def get_all_components(self, root) -> list[jpype.JObject]:
        """Returns a list of all rocket components in the loaded OpenRocket file.

        :param root: The root RocketComponent (usually obtained from the simulation)
        :return: List of all component objects
        """
        components = []
        for component in JIterator(root):
            components.append(component)
        return components
