"""Flight-event translation against fake Java objects — no jar, no JVM."""

import logging

import pytest

from orlab import FlightEvent, Helper


class FakeJavaEventType:
    """Mimics a Java FlightEvent.Type enum constant (has .name())."""

    def __init__(self, name: str):
        self._name = name

    def name(self) -> str:
        return self._name


class FakeEvent:
    def __init__(self, type_name: str, time: float):
        self._type = FakeJavaEventType(type_name)
        self._time = time

    def getType(self):
        return self._type

    def getTime(self) -> float:
        return self._time


class FakeBranch:
    def __init__(self, events):
        self._events = events

    def getEvents(self):
        return self._events


class FakeSimulation:
    def __init__(self, events):
        self._branch = FakeBranch(events)

    def getSimulatedData(self):
        return self

    def getBranch(self, n):
        return self._branch


class FakeInstance:
    started = True
    openrocket = None


@pytest.fixture
def helper():
    return Helper(FakeInstance())


@pytest.mark.parametrize("name", ["LAUNCH", "APOGEE", "GROUND_HIT", "SIM_WARN", "SIM_ABORT"])
def test_translate_known_event(helper, name):
    assert helper.translate_flight_event(FakeJavaEventType(name)) is FlightEvent[name]


def test_translate_unknown_event_raises_value_error(helper):
    with pytest.raises(ValueError, match="SOME_FUTURE_EVENT"):
        helper.translate_flight_event(FakeJavaEventType("SOME_FUTURE_EVENT"))


def test_get_events_collects_times_per_type(helper):
    sim = FakeSimulation(
        [
            FakeEvent("LAUNCH", 0.0),
            FakeEvent("SIM_WARN", 0.5),
            FakeEvent("SIM_WARN", 1.2),
            FakeEvent("APOGEE", 3.4),
            FakeEvent("GROUND_HIT", 9.9),
        ]
    )
    events = helper.get_events(sim)
    assert events[FlightEvent.LAUNCH] == [0.0]
    assert events[FlightEvent.SIM_WARN] == [0.5, 1.2]
    assert events[FlightEvent.APOGEE] == [3.4]
    assert events[FlightEvent.GROUND_HIT] == [9.9]


def test_get_events_skips_unknown_types_with_one_warning(helper, caplog):
    sim = FakeSimulation(
        [
            FakeEvent("LAUNCH", 0.0),
            FakeEvent("SOME_FUTURE_EVENT", 1.0),
            FakeEvent("SOME_FUTURE_EVENT", 2.0),
            FakeEvent("APOGEE", 3.0),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="orlab.core.helper"):
        events = helper.get_events(sim)

    assert events == {FlightEvent.LAUNCH: [0.0], FlightEvent.APOGEE: [3.0]}
    warnings = [r for r in caplog.records if "SOME_FUTURE_EVENT" in r.message]
    assert len(warnings) == 1


def test_get_events_reports_the_types_it_could_not_interpret(helper):
    """The dropped names must be readable from the result, not only from a log.

    An application that raises its log level sees nothing at all otherwise, and a
    caller iterating the mapping cannot tell "no such event in this simulation"
    from "orlab did not recognise the event and dropped it" (issue #63).
    """
    sim = FakeSimulation(
        [
            FakeEvent("LAUNCH", 0.0),
            FakeEvent("SOME_FUTURE_EVENT", 1.0),
            FakeEvent("ANOTHER_FUTURE_EVENT", 2.0),
            FakeEvent("APOGEE", 3.0),
        ]
    )
    events = helper.get_events(sim)
    assert events.unknown_event_types == frozenset({"SOME_FUTURE_EVENT", "ANOTHER_FUTURE_EVENT"})


def test_get_events_reports_nothing_dropped_when_all_types_are_known(helper):
    """Guard against the field always looking like something went missing."""
    sim = FakeSimulation([FakeEvent("LAUNCH", 0.0), FakeEvent("APOGEE", 3.0)])
    assert helper.get_events(sim).unknown_event_types == frozenset()


def test_get_events_is_still_a_plain_mapping(helper):
    """Existing callers index the result and compare it to dicts; that must hold."""
    sim = FakeSimulation([FakeEvent("LAUNCH", 0.0), FakeEvent("SOME_FUTURE_EVENT", 1.0)])
    events = helper.get_events(sim)
    assert isinstance(events, dict)
    assert events == {FlightEvent.LAUNCH: [0.0]}
    assert dict(events) == {FlightEvent.LAUNCH: [0.0]}


def test_a_silenced_log_still_leaves_the_drop_visible(helper, caplog):
    """The exact failure mode: the warning is suppressed, the result must still say so."""
    sim = FakeSimulation([FakeEvent("LAUNCH", 0.0), FakeEvent("SOME_FUTURE_EVENT", 1.0)])
    with caplog.at_level(logging.CRITICAL, logger="orlab.core.helper"):
        events = helper.get_events(sim)
    assert not [r for r in caplog.records if "SOME_FUTURE_EVENT" in r.message]
    assert "SOME_FUTURE_EVENT" in events.unknown_event_types
