# Changelog

All notable changes to orlab are documented here. Follows
[Keep a Changelog](https://keepachangelog.com) and
[Semantic Versioning](https://semver.org). History before 0.3.0 was
reconstructed from the git log.

## [Unreleased]

### Added

- `Helper.get_events()` now returns an `EventTimes` mapping carrying
  `unknown_event_types`: the Java event-type names this orlab version has no
  enum member for and therefore left out. Previously they were dropped with
  only a log line, so a caller could not tell "this simulation had no such
  event" from "orlab did not recognise the event", and an application that
  raised its log level saw nothing at all. Likeliest exactly when
  `OpenRocketInstance.profile_exact` is `False`. `EventTimes` is a `dict`
  subclass, so indexing and comparison are unchanged.

- `OpenRocketInstance.profile_exact`: `True` when a profile for the jar's
  exact version is checked in, `False` when orlab fell back to the nearest
  older profile because it does not know that release. The fallback was
  previously reported only through a log warning, which an application that
  raises its log level silences entirely — leaving nothing on the object to
  branch on. Comparing `or_version` to `profile.version_string` is not a
  substitute: `24.12.RC.01` is an exact match whose strings differ, so that
  reconstruction reports a fallback that is not one (#61).
- `SimulationPool.profile_exact`: the same verdict on the pool, which builds
  an instance to validate the jar and previously discarded it. A pool running
  on stale enums is the case where it matters most (#61).

## [0.9.0] — 2026-07-17

### Added

- Dispersion listeners (`orlab.listeners`): `WindProfile` — deterministic
  altitude-dependent wind on every supported version via the wind-model
  override hook (replaces built-in wind including turbulence;
  meteorological direction convention matching `setWindDirection`,
  verified by identical drift; component-wise vector interpolation, so
  359°→1° blends through north) — and `ThrustFactor`, the motor
  batch-variation knob. Both hold plain-Python state only: they pickle
  into `SimulationPool` workers and survive OpenRocket's listener
  cloning.
- `Helper.get_components_of_type(root, "BodyTube")`: typed component
  lookup by class name (resolved against the loaded version), superclass,
  or interface — `"MotorMount"` finds every mount.
- The simulation-setup guide covers layered wind, the native 24.12
  multi-level model (version-gated, stochastic) vs `WindProfile`
  (cross-version, deterministic), and the listener-cloning caveats; the
  manifest checks the wind-override surface and the canary runs the wind
  case.

- Motor selection and swapping: `Helper.set_motor(sim, motor)` changes the
  motor a simulation actually flies — by database designation
  (`find_motor`, which requires `manufacturer=` for the common hobby
  designations that exist from several makers), from a `.eng`/`.rse`/`.zip`
  thrust-curve file (`load_motor`, no database involved), or as a motor
  object — always keyed on the simulation's own flight configuration and
  read back afterwards. Raw assignment to the rocket's *selected*
  configuration succeeds and silently does nothing to the simulation (the
  ecosystem's classic dispersion bug — now pinned by an integration test
  on every version); `get_motor(sim)` reports the flying designation.
  `delay=` sets the ejection delay in seconds. Works on all four
  supported versions including 24.12 headless (the motor database loads
  fully there; only component presets are affected by the upstream gap).
- Docs: "Simulation setup" guide page (motor swapping, the verified
  one-liner setters, SI units).
- The profile contract manifest checks the motor surface per version —
  including the MotorConfiguration package move after 15.03 and the two
  mount-accessor eras — and the monthly canary runs the motor case.

## [0.8.0] — 2026-07-17

### Added

- Parallel dispersion runner: `orlab.SimulationPool(ork_file, jar_path)`
  runs studies across spawn-based worker processes — one JVM and one
  loaded document per worker. Tasks are plain mappings of the declarative
  keys (validated up front, including the rod-direction/into-wind
  interaction) plus an optional pinned `seed`, or an importable
  `worker_fn(helper, sim, task)` for anything beyond the whitelist.
  Results are plain-Python `SimResult`/`StudyResult` records
  (`to_records()` feeds `pandas.DataFrame` directly); per-task failures
  are `SimError` data carrying the Python traceback and the Java stack;
  interrupts, worker crashes, and worker-boot failures raise
  `orlab.errors.StudyAborted` with the partial results preserved. Every
  task gets a unique pool-assigned 31-bit seed (Java's own randomization
  collides at 10k-run scale), read back after the run so any result can
  be replayed. Progress is a `(done, total)` callback, tqdm-compatible
  without a tqdm dependency. Workers restore the document's option
  baseline between tasks, ignore SIGINT (the parent owns ^C — verified
  live against running JVM workers), and discard stdout on POSIX by
  default (`worker_stdout="inherit"` to keep it; best-effort on Windows).

- `orlab.parallel.DECLARATIVE_KEYS`: the curated set of `SimulationOptions`
  knobs verified to apply-and-read-back identically on every supported
  OpenRocket version — launch rod length/angle/direction, launch-into-wind,
  launch altitude/latitude/longitude, wind speed average and direction —
  each mapped to its setter/getter with value type and unit. Notable
  verified interaction: OpenRocket launches into the wind by default, and
  `launch_rod_direction` only takes effect together with
  `launch_into_wind: False`; the mapping's declaration order encodes that.
  The profile contract manifest checks every pair (plus
  `setRandomSeed`/`getRandomSeed`) on regeneration, an integration case
  round-trips them all on every matrix version, and the monthly canary
  runs the same case against the newest upstream release.

## [0.7.0] — 2026-07-17

### Added

- Flight summaries: `Helper.get_summary(sim, branch_number=0)` returns a
  `FlightSummary` — apogee, max velocity/acceleration/Mach, rail-exit and
  deployment velocities, stability windowed from rod departure to apogee
  (OpenRocket 24.12 computes stability through post-apogee tumble, where
  unwindowed minima are meaningless), descent rate, flat-earth landing
  position/distance/compass bearing, flight time, optimum delay (23.09+),
  and warnings. All values are builtin Python types (never Java/numpy), so
  summaries pickle cleanly into JVM-less processes; missing values are NaN,
  with one warning per process when the cause is the loaded OpenRocket
  version rather than the flight. `print(summary)` renders a sectioned
  report; `to_dict()` is a dispersion-table row.
- `Helper.get_events(sim, branch_number=0)`: events per stage branch
  (default unchanged).
- Docs: "Flight summaries" guide page; `monte_carlo.py` example reads
  landing points through `get_summary` instead of a hand-rolled listener
  and flat-earth math.
- The profile contract manifest now checks the FlightData summary getters
  and branch accessors on every profile regeneration, and the monthly
  canary also runs the summary case against the newest upstream release.
- Tabular export: `Helper.export_csv(sim, path)` writes the branch's full
  timeseries as UTF-8 CSV (stdlib-only; NaN as empty cells, lossless
  through `pandas.read_csv`), and `Helper.get_dataframe(sim)` returns the
  same columns as a pandas DataFrame. Column labels carry the jar's own SI
  units (`TYPE_ACCELERATION_TOTAL (m/s²)`); dimensionless types get no
  suffix. pandas ships as orlab's first optional extra —
  `pip install orlab[pandas]` (or `orlab[all]`) — and is lazy-imported
  with an error naming the extra; nothing else in orlab needs it.

## [0.6.0] — 2026-07-17

### Added

- Jar management: `orlab.fetch_jar(version)` downloads an OpenRocket release
  jar into a local cache (`ORLAB_JAR_CACHE`, else `$XDG_CACHE_HOME/orlab-jars`,
  else `~/.cache/orlab-jars`) and verifies its sha256 against pins shipped
  with orlab. Unpinned versions
  require an explicit `sha256=`; there is no way to skip verification
  (`orlab.errors.JarVerificationError`). A `python -m orlab` CLI wraps it:
  `fetch [version] [--sha256 HEX]` (stable, scriptable stdout: the jar path)
  and `which` (shows the jar the default resolution would use, and why).
- Default jar resolution now ends at the fetch cache: `ORLAB_JAR` →
  `CLASSPATH` → newest supported `OpenRocket-*.jar` in the current
  directory → newest verified jar in the cache. `OpenRocketInstance()`
  works with zero configuration after one `python -m orlab fetch`.
  `OpenRocketInstance` itself never downloads anything.
- `orlab.jars.find_installed()`: locates a desktop OpenRocket installation
  (jar, version, and — when the install bundles a Java 17+ runtime — a JVM
  to run it with), so a machine with the app needs no separate JDK or jar.
  Deliberately opt-in: the default jar resolution never selects a desktop
  install (a self-updating app could silently switch scripts to an
  unverified version); use the explicit
  `OpenRocketInstance(str(inst.jar), jvm_path=inst.jvm)` two-liner.
  `ORLAB_OR_INSTALL_DIR` overrides the search; empty string disables it.
- Docs: "Getting an OpenRocket jar" guide page.
- Guides on the docs site: simulation listeners (hooks, the clone/shared-state
  contract), monte-carlo studies (the one-instance-many-sims pattern, seeds),
  and working across OpenRocket versions (profiles, fallback, the
  `TYPE_PROPELLANT_MASS`→`TYPE_MOTOR_MASS` rename, string escape hatch).
  Every code block executed against a real jar.
- Documentation site at <https://heibench.github.io/orlab/>
  (mkdocs-material + mkdocstrings API reference, deployed from main by CI);
  `just docs` builds it locally. The maintainer release notes moved there
  from `docs/pypi_usage.md`.

### Changed

- The current-directory jar fallback picks the newest jar with an exact
  version profile instead of the hardcoded `./OpenRocket-23.09.jar`, and
  skips unprofiled or unparseable jar names (a `26.xx-SNAPSHOT` build in
  the cwd no longer shadows — nor is shadowed by — a supported release;
  pass such jars explicitly). With several `OpenRocket-*.jar` files in the
  cwd, the newest supported one now wins.
- The integration suite downloads its matrix jars through `orlab.fetch_jar`
  itself instead of a private copy of the download/verify logic.
- CI runs the unit suite on Windows (one `windows-latest` cell, Python
  3.14, jar-free) — the first automated check of orlab's path, cache, and
  file handling on a platform the README documents.
- README rewritten: version-support matrix (all four OpenRocket versions
  CI-tested), working quickstart, lifecycle/seed/JVM-options notes, trimmed
  JDK setup, uv/just development workflow.
- Examples resolve `simple.ork` relative to the script (they previously only
  worked from the repository root), and their third-party dependencies are
  declared as the `examples` dependency group.

### Fixed

- `examples/simple_ork/lazy.py` works with modern numpy (`fmin` passes a
  1-element array that `math.radians` no longer coerces).
- `examples/simple_ork/monte_carlo.py`: landing bearing uses `atan2` (the
  `atan` form was wrong for westward drift and divided by zero for pure
  north-south), and the reported mean bearing is a circular mean.

## [0.5.0] — 2026-07-17

### Added

- `OpenRocketInstance(jvm_path=..., jvm_args=(...))`: choose the JVM library
  and pass launch arguments (e.g. `("-Xmx4g",)` for large monte-carlo runs).
  The `MANUAL_JVM_PATH` class attribute is deprecated and honored with a
  warning for one release.
- `Helper.run_simulation(..., randomize_seed=False)` respects a seed already
  set on the simulation options; the default still randomizes before every
  run (previously unconditional and undocumented). On 24.12 a fixed seed
  reproduces results within one process, not across processes with wind
  enabled.
- `py.typed` marker: consumer type checkers now see orlab's annotations.
- The release workflow refuses to publish if checks or tests fail.

### Changed

- The JVM now outlives the `with OpenRocketInstance(...)` block (JPype cannot
  restart a JVM, so shutting it down made any second instance in the same
  process fail with a confusing JPype error). Sequential blocks and notebook
  re-runs on the same jar reuse the running OpenRocket; a different jar path
  raises `OrlabError` explaining the one-jar-per-process constraint, and a
  startup failure after JVM launch raises `OrlabError` instead of poisoning
  retries silently. Helpers and listeners stay usable after the block; the
  JVM ends with the interpreter.

- `import orlab` no longer configures the root logger; consumers that relied
  on orlab's implicit `logging.basicConfig` must configure logging themselves
  (orlab's own INFO messages are otherwise hidden by Python's defaults).
- The jar path defaults to `$ORLAB_JAR`, then the first existing jar on the
  legacy `$CLASSPATH` (list-aware), then `./OpenRocket-23.09.jar` — resolved
  when an instance is created, not at import.
- Bad jars raise `orlab.errors.NotAnOpenRocketJar` (previously `BadZipFile`,
  `KeyError` or `ValueError` depending on how the jar was bad), and using a
  `Helper` on an unstarted instance raises `OrlabError` (previously bare
  `Exception`).

## [0.4.0] — 2026-07-17

### Added

- Version profiles: per-OpenRocket-version facts (package roots, startup path,
  `FlightDataType`/`FlightEvent` constants) generated from the jars by
  `tools/generate_profile.py` and checked in for 15.03, 22.02, 23.09 and
  24.12. Unknown newer versions fall back to the nearest older profile with a
  warning, and a startup drift alarm warns when the loaded jar's constants
  differ from its profile.
- OpenRocket 24.12+ starts fully headless via the official
  `OpenRocketCore.initialize()` bootstrap — no window disposal, no display
  needed, and no private-field reflection on that path.
- `orlab.errors` with `OrlabError`, `UnsupportedFlightDataType` and
  `UnsupportedOpenRocketVersion`.
- Per-version integration harness (`just test-integration`):
  subprocess-per-version cases against sha256-pinned OpenRocket jars
  (downloaded on demand) covering flight sanity, the enum surface against the
  profile, warning/abort events, listener exception propagation, and
  cross-version apogee agreement. CI runs the full matrix — all four
  OpenRocket versions on JDK 17 and 21, no display server — and a monthly
  canary runs the newest upstream release against the newest profile, opening
  an issue on failure.

### Changed

- `FlightDataType`/`FlightEvent` are now generated as the union of constants
  across all profiled versions (four 24.12-only data types added); requesting
  a constant the loaded OpenRocket version does not expose raises
  `UnsupportedFlightDataType` naming the versions that have it (previously a
  raw JPype `AttributeError`). Enum members are now sorted by name — their
  numeric `.value`s shifted and remain non-stable identifiers; use names.
- Snapshot version strings (`26.xx-SNAPSHOT`) parse instead of erroring.

## [0.3.1] — 2026-07-17

### Added

- `FlightEvent.SIM_WARN` and `FlightEvent.SIM_ABORT` (emitted by OpenRocket
  24.12+) and `FlightDataType.TYPE_MOTOR_MASS` (the 22.02+ name for the
  propellant-mass series; `TYPE_PROPELLANT_MASS` remains for 15.03).

### Changed

- Packaging migrated to `pyproject.toml` (hatchling) with a `src/` layout and
  uv-managed environments; `setup.py` removed. Public import surface unchanged.
- `requires-python` raised to `>=3.10`; license metadata now correctly
  declares GPL-2.0-only (matching the LICENSE file).
- Quality gate: `justfile` (fmt / lint / typecheck / check / test through uv),
  ruff + mypy clean, pre-commit with gitleaks, `AGENTS.md`, and a jar-free
  unit-test suite (version detection, package-root selection, iterator
  bridging, API-surface freeze).
- CI: checks and a Python 3.10/3.14 unit-test matrix on every push and PR;
  releases publish to PyPI via Trusted Publishing on the `pypi` environment.

### Fixed

- `Helper.get_events()` no longer crashes on flight event types missing from
  the Python enum — on 24.12, simulation warnings are recorded as `SIM_WARN`
  events and previously raised a bare `KeyError`. Unknown event types from
  future OpenRocket versions are now skipped with a logged warning, and
  `translate_flight_event` raises a clear `ValueError` instead.
- `Helper.get_events()` return annotation matches its actual behavior
  (`Dict[FlightEvent, List[float]]` — a list of times per event).

### Removed

- Dead `orlab._orhelper` shim module and `scripts/debug_jvm.py`.

## [0.3.0] — 2026-07-17

### Added

- OpenRocket **24.12** support (#2): the jar's version is read from its
  `build.properties` before the JVM starts and the Java package roots
  (`net.sf.openrocket` vs `info.openrocket.core`/`info.openrocket.swing`)
  are selected automatically. `instance.or_version` and
  `instance.openrocket_swing` exposed; public API otherwise unchanged.
- OpenRocket 15.03 works on modern JVMs (`--add-opens` at JVM start).
  Verified by real simulations against 15.03, 22.02, 23.09, and 24.12.

## [0.2.x] — 2025

Initial orlab releases (0.2.3–0.2.7): evolution of
[orhelper](https://github.com/SilentSys/orhelper) — driver reorganization into
`core/`/`utils/`, PyPI publication, docs and examples for OpenRocket 23.09.
