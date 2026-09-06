# orlab

**orlab** scripts [OpenRocket](https://openrocket.info/) from Python via
[JPype](https://jpype.readthedocs.io/): load `.ork` files, run simulations
(optionally with custom listeners), and extract time series, flight
summaries, and events as Python/numpy data — up to parallel dispersion
studies with one verified jar fetch and a worker pool.

```python
import orlab

with orlab.OpenRocketInstance(jar_path="OpenRocket-24.12.jar") as instance:
    orl = orlab.Helper(instance)
    doc = orl.load_doc("rocket.ork")
    sim = doc.getSimulation(0)
    orl.run_simulation(sim)

    data = orl.get_timeseries(
        sim, [orlab.FlightDataType.TYPE_TIME, orlab.FlightDataType.TYPE_ALTITUDE]
    )
    events = orl.get_events(sim)  # {FlightEvent.APOGEE: [3.51], ...}
```

The project is an evolution of [orhelper](https://github.com/SilentSys/orhelper).
Beyond the basics, orlab ships the plumbing you'd otherwise hand-roll
for dispersion studies:

- `python -m orlab fetch` — sha256-verified jar download and cache;
  `OpenRocketInstance()` then boots with zero configuration.
- `orl.get_summary(sim)` — the scalar flight report (apogee, stability,
  descent, landing distance/bearing, …) as plain Python;
  `orl.export_csv` / `orl.get_dataframe` for full timeseries tables.
- `orlab.SimulationPool` — parallel monte-carlo across JVM worker
  processes, with replayable seeds and progress reporting.
- `orl.set_motor(sim, "C6", manufacturer="Estes")` — swap the motor the
  simulation actually flies, from the motor database or a `.eng` file.

The [guides](https://heibench.com/orlab/) cover each.

Where orhelper targets a single OpenRocket version, orlab detects the jar's
version before the JVM starts and adapts to it — package roots, startup path,
and available flight-data constants all come from checked-in, generated
version profiles.

**Documentation: <https://heibench.com/orlab/>** — getting
started, API reference, and maintainer procedures.

## Supported OpenRocket versions

| OpenRocket | Status | Notes |
| ---------- | ------ | ----- |
| 24.12 | CI-tested (JDK 17, 21) | official headless bootstrap |
| 23.09 | CI-tested (JDK 17, 21) | |
| 22.02 | CI-tested (JDK 17, 21) | |
| 15.03 | CI-tested (JDK 17, 21) | |
| newer releases | forward fallback | run day-one on the nearest older profile (`profile_exact` is `False`); full support is one profile-regeneration PR |

Every version in the table runs real simulations in CI (no display server
needed on any of them) on every pull request and push to main, and a monthly
canary checks the newest upstream release against the newest profile.

Version differences are enforced with clear errors: requesting a constant the
loaded version does not have raises `UnsupportedFlightDataType` naming the
versions that have it. Constants newer than the enum can be passed as strings,
e.g. `orl.get_timeseries(sim, ["TYPE_SOME_NEW_TYPE"])`.

## Installation

1. **Install the package** (Python 3.10+)

   ```
   pip install orlab
   ```

2. **Install a JDK** (17 or 21, [Adoptium Temurin](https://adoptium.net/)
   tested). Let the installer set `JAVA_HOME` — JPype finds the JVM through
   it. See [Setting up the JDK](#setting-up-the-jdk) if it doesn't.

3. **Fetch an OpenRocket jar** — orlab downloads it into a local cache and
   verifies its sha256:

   ```
   python -m orlab fetch
   ```

   After that, `OpenRocketInstance()` finds the cached jar with no further
   configuration. To use a jar you already have instead, pass `jar_path=`
   to `OpenRocketInstance(...)` or set `ORLAB_JAR`:

   ```
   export ORLAB_JAR=/path/to/OpenRocket-24.12.jar
   ```

   The full resolution order is `ORLAB_JAR`, the legacy `CLASSPATH`, the
   newest supported `OpenRocket-*.jar` in the current directory, then the
   cache; `python -m orlab which` shows what would be used. See
   [Getting an OpenRocket jar](https://heibench.com/orlab/guides/jars/)
   for details.

## Usage

The [`examples/`](examples/simple_ork) directory demonstrates the main
workflows against a bundled `.ork` file:

- [`simple_plot.py`](examples/simple_ork/simple_plot.py) — run one simulation, plot altitude and vertical velocity
- [`advanced_plot.py`](examples/simple_ork/advanced_plot.py) — multiple series, events annotated on the plot
- [`monte_carlo.py`](examples/simple_ork/monte_carlo.py) — dispersion study with randomized parameters and custom listeners (landing-point capture, air start)
- [`lazy.py`](examples/simple_ork/lazy.py) — optimize a design parameter against simulation output

The plotting examples need `matplotlib` (and `lazy.py` needs `scipy`) —
`pip install matplotlib scipy`, or `uv sync --group examples` in a clone.

Worth knowing:

- **One OpenRocket jar per process** (JPype cannot restart a JVM). The JVM
  starts on first use and stays up until the interpreter exits; sequential
  `with` blocks and notebook re-runs on the same jar reuse it, a different
  jar raises `OrlabError`. Use subprocesses to compare versions.
- `run_simulation` randomizes the simulation seed by default (what
  monte-carlo loops want); pass `randomize_seed=False` to keep a seed you
  set yourself.
- JVM options: `OpenRocketInstance(jvm_args=("-Xmx4g",))` for large runs;
  `jvm_path=` selects a specific JVM.
- Exceptions raised inside your `AbstractSimulationListener` subclass
  propagate out of `run_simulation` intact.

For background, see the
[OpenRocket wiki on scripting with Python and JPype](https://github.com/openrocket/openrocket/wiki/Scripting-with-Python-and-JPype).

## Setting up the JDK

JPype locates the JVM via `JAVA_HOME`. If startup fails with a JVM-not-found
error:

- **Linux**: `export JAVA_HOME=/usr/lib/jvm/<your-jdk>` (add to `~/.bashrc`),
  or install via your package manager (`temurin-21-jdk`, `openjdk-21-jdk`).
- **Windows**: set `JAVA_HOME` under *System Properties → Environment
  Variables* to e.g. `C:\Program Files\Eclipse Adoptium\jdk-21`, and add
  `%JAVA_HOME%\bin` to `Path`.
- Any platform: pass the JVM library path directly —
  `OpenRocketInstance(jvm_path="/path/to/libjvm.so")`.

## Development

The toolchain is [uv](https://docs.astral.sh/uv/) +
[just](https://github.com/casey/just); working agreements live in
[AGENTS.md](AGENTS.md).

```
git clone https://github.com/heibench/orlab.git
cd orlab
just setup     # uv sync
just check     # format check + lint + mypy
just test      # unit tests (no jar or JVM needed)
```

`just test-integration` runs real simulations against every supported
OpenRocket version (jars are downloaded and cached on first run). Releases
are tagged from `main` and published to PyPI by CI — see
[CHANGELOG.md](CHANGELOG.md).

## Credits

- The original [orhelper](https://github.com/SilentSys/orhelper) project by **SilentSys**
  - **Richard Graham** for the original script: [Source](https://sourceforge.net/p/openrocket/mailman/openrocket-devel/thread/4F17AA0C.1040002@rdg.cc/)
  - **@not7cd** for initial organization and cleanup: [Source](https://github.com/not7cd/orhelper)
- All contributors to the [OpenRocket](https://openrocket.info/) project over the years
