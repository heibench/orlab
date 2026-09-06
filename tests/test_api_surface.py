def test_public_surface_stable():
    import orlab

    assert sorted(orlab.__all__) == [
        "AbstractSimulationListener",
        "EventTimes",
        "FlightDataType",
        "FlightEvent",
        "FlightSummary",
        "Helper",
        "JIterator",
        "OpenRocketInstance",
        "OrLogLevel",
        "SimulationPool",
        "fetch_jar",
    ]
    for name in orlab.__all__:
        assert getattr(orlab, name) is not None


def test_errors_surface_stable():
    """Exception types escape through public entry points — they are public API."""
    import orlab.errors

    assert sorted(orlab.errors.__all__) == [
        "JarVerificationError",
        "NotAnOpenRocketJar",
        "OrlabError",
        "StudyAborted",
        "UnsupportedFlightDataType",
        "UnsupportedOpenRocketVersion",
    ]
    for name in orlab.errors.__all__:
        assert issubclass(getattr(orlab.errors, name), Exception)


def test_jars_surface_stable():
    import orlab.jars

    assert sorted(orlab.jars.__all__) == [
        "Installed",
        "fetch_jar",
        "find_installed",
        "jar_cache_dir",
    ]
    for name in orlab.jars.__all__:
        assert getattr(orlab.jars, name) is not None


def test_parallel_surface_stable():
    """The whole verified contract is frozen — names, setters, getters,
    kinds, units, and the into-wind-before-rod-direction apply order."""
    import orlab.parallel
    from orlab.parallel import DECLARATIVE_KEYS, DeclarativeKey

    assert sorted(orlab.parallel.__all__) == [
        "DECLARATIVE_KEYS",
        "DeclarativeKey",
        "SimError",
        "SimResult",
        "SimulationPool",
        "StudyResult",
    ]
    expected = {
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
    assert dict(DECLARATIVE_KEYS) == expected
    keys = list(DECLARATIVE_KEYS)
    assert keys.index("launch_into_wind") < keys.index("launch_rod_direction")


def test_declarative_case_values_match_contract():
    """The integration case must exercise exactly the whitelist — a key
    added to either side without the other is a unit-suite failure, not a
    surprise a month later on the canary."""
    import importlib.util
    from pathlib import Path

    from orlab.parallel import DECLARATIVE_KEYS

    case = Path(__file__).parent / "integration" / "cases" / "declarative_keys.py"
    spec = importlib.util.spec_from_file_location("declarative_keys_case", case)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.VALUES) == set(DECLARATIVE_KEYS)


def test_listeners_surface_stable():
    import orlab.listeners

    assert sorted(orlab.listeners.__all__) == ["ThrustFactor", "WindProfile"]
    for name in orlab.listeners.__all__:
        assert getattr(orlab.listeners, name) is not None


def test_enums_are_coherent():
    from orlab import FlightDataType, FlightEvent, OrLogLevel

    assert all(m.name.startswith("TYPE_") for m in FlightDataType)
    assert len({m.name for m in FlightDataType}) == len(list(FlightDataType))
    assert {"LAUNCH", "APOGEE", "GROUND_HIT"} <= {m.name for m in FlightEvent}
    assert {"ERROR", "WARN", "INFO", "DEBUG"} <= {m.name for m in OrLogLevel}
