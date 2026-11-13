# Getting an OpenRocket jar

orlab drives a real OpenRocket jar; you need one on disk (plus a JDK 17 or
21 — that part stays a prerequisite, see
[Getting started](../getting-started.md)). The fastest path on a bare
machine:

```
pip install orlab
python -m orlab fetch
```

`fetch` downloads the default OpenRocket release from GitHub into a local
cache, verifies its sha256 against a pin shipped with orlab, and prints the
jar's path — the path is the only stable, scriptable output of the CLI.
After that, `OpenRocketInstance()` finds the cached jar on its own.

The same thing in Python:

```python
import orlab

jar = orlab.fetch_jar()  # -> Path to the verified, cached jar
jar = orlab.fetch_jar("22.02")  # any pinned version
```

Fetching is always explicit. `OpenRocketInstance` itself never touches the
network — it only ever reads jars that are already on disk.

## How the default jar is resolved

When `OpenRocketInstance()` is constructed without `jar_path=`, the first
hit wins:

1. `ORLAB_JAR` — a path to a specific jar.
2. The legacy `CLASSPATH` variable (first existing `.jar` entry).
3. The newest supported `OpenRocket-*.jar` in the current directory.
   Only versions with an exact checked-in profile count here, so a stray
   `OpenRocket-26.xx-SNAPSHOT.jar` never outranks a supported release —
   name such a jar explicitly to use it.
4. The newest pinned version already in the fetch cache, re-verified.
   Nothing is downloaded at this step.

`python -m orlab which` prints the jar this chain would pick and which step
found it (the output is informational — script against `fetch`, not
`which`). It exits 0 when that jar exists and 4 when there is no jar to use,
including an `ORLAB_JAR` that names a missing file: a missing engine is an
environment fault, not a finding.

## The cache

Jars land in `$ORLAB_JAR_CACHE` if set, else `$XDG_CACHE_HOME/orlab-jars`,
else `~/.cache/orlab-jars`. Every use re-verifies the file against its pin;
a corrupt entry is evicted and re-downloaded. The cache is just files —
delete the directory (or single jars) to reclaim space; the next `fetch`
restores what you need.

## Versions orlab doesn't pin

orlab ships sha256 pins for the versions it supports (see the
[version matrix](../index.md#supported-openrocket-versions)). For any other
version, `fetch_jar` refuses to download unverified — there is no bypass
flag. Compute the digest yourself from a source you trust and pass it:

```python
orlab.fetch_jar("26.00", sha256="...")
```

```
python -m orlab fetch 26.00 --sha256 ...
```

The refusal error includes the release URL, how to compute the digest, and
the digest of any file already cached for that version. A newer orlab
release may simply pin the version — upgrading is usually the easier fix.

One caveat: the zero-config resolution chain only auto-selects **pinned**
versions from the cache (it has no digest to re-verify anything else
against). A jar you fetched with `sha256=` stays cached, but you point at
it explicitly — capture the printed path into `ORLAB_JAR` or pass it as
`jar_path=`.

## Using a desktop OpenRocket install

If the OpenRocket desktop app is installed, `orlab.jars.find_installed()`
locates its jar — and, when the install bundles a Java 17+ runtime, a JVM
to run it with, so a machine with the app needs no separate JDK or jar:

```python
import orlab
from orlab.jars import find_installed

inst = find_installed()  # Installed(jar=..., jvm=..., version=...) or None
if inst is None:
    raise SystemExit("no desktop OpenRocket found — see `python -m orlab fetch`")
with orlab.OpenRocketInstance(str(inst.jar), jvm_path=inst.jvm) as instance:
    ...
```

Discovery is deliberately **not** part of the default resolution chain —
a desktop app that updates itself could silently switch your scripts to an
unverified version, so selecting a discovered install is always this
explicit two-liner. `find_installed` never raises and never downloads; it
returns `None` when nothing usable is found.

Where it looks:

| OS | Location | Status |
| -- | -------- | ------ |
| Linux | install root from the installer's per-user `.desktop` entry | verified against a real install |
| macOS | `/Applications/OpenRocket.app/Contents/Resources/app` | config-derived, best-effort |
| Windows | `%ProgramFiles%\OpenRocket` | config-derived, best-effort |

`ORLAB_OR_INSTALL_DIR` overrides the search with an explicit install root;
setting it to an empty string disables discovery entirely. Within a root,
both the modern `jar/OpenRocket-*.jar` layout and the older root-level
`OpenRocket.jar` are recognized, and the version is always read from the
jar itself. `inst.jvm` is only set when the bundled JRE is Java 17+ —
older installs bundle Java 8/11, which cannot run orlab; their jar is
still usable with your own JDK.

## Worth knowing about the default version

`python -m orlab fetch` currently fetches OpenRocket 24.12. On its headless
startup path, OpenRocket 24.12 does not load the *component preset*
database (an upstream gap in `OpenRocketCore`); simulations, motors, and
everything orlab's API touches are unaffected, but code reaching into
component presets via raw Java will find them empty.
