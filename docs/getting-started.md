# Getting started

## Install

1. **The package** (Python 3.11+):

    ```
    pip install orlab
    ```

    `pip install 'orlab[pandas]'` adds DataFrame export
    (`Helper.get_dataframe`); everything else works without it.

2. **A JDK** — 17 or 21, [Adoptium Temurin](https://adoptium.net/) tested.
   Let the installer set `JAVA_HOME`; JPype finds the JVM through it. If it
   can't, either export `JAVA_HOME` yourself or pass
   `OpenRocketInstance(jvm_path="/path/to/libjvm.so")`.

3. **An OpenRocket jar** — let orlab fetch and verify one into its cache:

    ```
    python -m orlab fetch
    ```

    After that, `OpenRocketInstance()` needs no configuration. To use a jar
    you already have instead, pass `jar_path=` to `OpenRocketInstance(...)`
    or set `ORLAB_JAR`; the [jar guide](guides/jars.md) covers the full
    resolution order, the cache, and fetching versions orlab doesn't pin.

## First simulation

```python
import orlab

with orlab.OpenRocketInstance() as instance:
    orl = orlab.Helper(instance)
    doc = orl.load_doc("rocket.ork")
    sim = doc.getSimulation(0)
    orl.run_simulation(sim)

    data = orl.get_timeseries(
        sim, [orlab.FlightDataType.TYPE_TIME, orlab.FlightDataType.TYPE_ALTITUDE]
    )
    events = orl.get_events(sim)
    apogee_time = events[orlab.FlightEvent.APOGEE][0]
    finals = orl.get_final_values(sim, [orlab.FlightDataType.TYPE_ALTITUDE])
```

`sim` and `doc` are live Java objects — anything OpenRocket can do to them,
you can too (`sim.getOptions().setLaunchRodAngle(...)`,
`orl.get_component_named(sim.getRocket(), "Nose cone").setOverrideMass(...)`,
…). The `Helper` methods cover the common paths and convert results to
Python/numpy types.

## Worth knowing

- **One OpenRocket jar per process.** JPype cannot restart a JVM: the JVM
  starts on first use and stays up until the interpreter exits. Sequential
  `with` blocks and notebook re-runs on the same jar reuse it; a different
  jar raises `OrlabError`. Compare versions with subprocesses.
- **Seeds**: `run_simulation` randomizes the simulation seed on every call by
  default — what monte-carlo loops want. Pass `randomize_seed=False` to keep
  a seed you set via `sim.getOptions().setRandomSeed(...)`. On OpenRocket
  24.12, a fixed seed reproduces results within one process, but not across
  processes when wind is enabled (the wind model draws its own entropy at
  startup).
- **Memory**: large studies may need a bigger heap —
  `OpenRocketInstance(jvm_args=("-Xmx4g",))`.
- **Version differences**: requesting a flight-data constant the loaded
  OpenRocket version does not have raises
  `orlab.errors.UnsupportedFlightDataType` naming the versions that have it.
  Constants newer than orlab's enum can be passed as strings:
  `orl.get_timeseries(sim, ["TYPE_SOME_NEW_TYPE"])`.
- **Listeners**: subclass `orlab.AbstractSimulationListener` and pass
  instances via `run_simulation(sim, listeners=[...])`. OpenRocket clones
  listeners (shallow-copy) before the run — mutate shared state (append to a
  list you keep a reference to) rather than rebinding attributes on `self`
  and reading them back afterwards. Exceptions raised in a listener
  propagate out of `run_simulation` intact.
