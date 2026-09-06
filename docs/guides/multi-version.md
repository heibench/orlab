# Working across OpenRocket versions

orlab reads the jar's version from its `build.properties` before the JVM
starts and selects a **version profile** — package roots, startup path, and
the flight-data/event constants that version exposes. Profiles are generated
from the jars themselves and checked in for 15.03, 22.02, 23.09, and 24.12.

```python
instance = orlab.OpenRocketInstance(jar_path="OpenRocket-24.12.jar")
print(instance.or_version)  # "24.12" — read from the jar, no JVM needed yet
```

## Constants differ between versions

`FlightDataType` and `FlightEvent` are the union across all supported
versions. Requesting a constant the loaded jar doesn't have raises a
precise error:

```python
orl.get_timeseries(sim, [orlab.FlightDataType.TYPE_WIND_DIRECTION])
# on a 23.09 jar:
# UnsupportedFlightDataType: TYPE_WIND_DIRECTION is not available in
# OpenRocket 23.09 (available in: 24.12)
```

Known renames are represented as-is — `TYPE_PROPELLANT_MASS` exists only on
15.03; from 22.02 the same series is `TYPE_MOTOR_MASS`. Catch
`orlab.errors.UnsupportedFlightDataType` to handle either.

Constants newer than orlab's enum (a brand-new OpenRocket release) can be
passed as strings; they resolve against the live jar:

```python
orl.get_timeseries(sim, ["TYPE_MOTOR_MASS"])
```

Flight *events* degrade gracefully in the other direction: event types
orlab doesn't know are left out of `get_events` rather than crashing. They
are not dropped in silence -- their Java names are on the result, so a
caller can tell "this simulation had no such event" from "orlab could not
interpret it":

```python
events = orl.get_events(sim)
if events.unknown_event_types:
    print("not interpreted:", sorted(events.unknown_event_types))
```

The result is still a plain mapping, so indexing it is unchanged. A warning
is logged once per name too, but the log can be silenced and the field
cannot. On 24.12, expect `SIM_WARN` events for simulation warnings and
`SIM_ABORT` where older versions raised exceptions.

## Newer releases than orlab knows

A jar newer than the newest profile runs immediately on the **nearest older
profile**. `OpenRocketInstance.profile_exact` is `False` in that case, and
`instance.profile` names the profile actually in use:

```python
instance = OpenRocketInstance(jar_path="OpenRocket-99.99.jar")
if not instance.profile_exact:
    print(f"running {instance.or_version} on {instance.profile.version_string} facts")
```

A warning is logged too, but branch on the flag rather than the log line — an
application that raises its log level silences the warning, and comparing
`or_version` to `profile.version_string` is **not** equivalent: `24.12.RC.01`
is an exact match whose strings differ. At startup, orlab also compares the
live jar's constants against the profile and logs any drift. Full support
for a new release is one PR — see the
[maintainer notes](../maintainers.md#supporting-a-new-openrocket-release).
A jar older than 15.03 raises `UnsupportedOpenRocketVersion`.

## Comparing versions

One process drives one jar (the JVM cannot be restarted), so comparisons are
subprocess-per-version. orlab's own integration harness is the reference
implementation:
[`tests/integration/`](https://github.com/heibench/orlab/tree/main/tests/integration)
spawns a fresh Python per version and asserts, among other things, that the
same rocket's apogee agrees across all four supported versions within 5 %.

OpenRocket logs to stdout, so don't parse a case's whole output as JSON —
print one marked line and scan for it (the harness does the same):

```python
import json
import subprocess
import sys

CASE = "case.py"  # takes the jar as argv[1], simulates, prints "RESULT " + json.dumps(...)


def run_case(jar):
    out = subprocess.run(
        [sys.executable, CASE, jar], capture_output=True, text=True, check=True
    ).stdout
    line = next(ln for ln in out.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT ") :])


results = {jar: run_case(jar) for jar in ["OpenRocket-23.09.jar", "OpenRocket-24.12.jar"]}
```
