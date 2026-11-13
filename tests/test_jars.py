"""fetch_jar, the jar cache, default-jar resolution, and the CLI."""

import hashlib
import logging
from pathlib import Path

import pytest

import orlab.jars as jars
from orlab.__main__ import main
from orlab._pins import DEFAULT_VERSION, PINNED_SHA256
from orlab.core.openrocket_instance import _resolve_default_jar
from orlab.errors import JarVerificationError

JAR_BYTES = b"fake jar bytes"
JAR_SHA = hashlib.sha256(JAR_BYTES).hexdigest()
OTHER_BYTES = b"different bytes entirely"


@pytest.fixture(autouse=True)
def _no_network_by_default(monkeypatch):
    """The unit suite must never download. Tests that exercise the download
    path re-patch the seam via _hook."""
    monkeypatch.setattr(
        jars, "_download", lambda url, dest: pytest.fail(f"unexpected download of {url}")
    )


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    monkeypatch.setenv("ORLAB_JAR_CACHE", str(path))
    return path


@pytest.fixture
def hermetic(tmp_path, monkeypatch, cache):
    """Resolution-chain tests must not see this machine's env, cwd, or real
    jar cache."""
    monkeypatch.delenv("ORLAB_JAR", raising=False)
    monkeypatch.delenv("CLASSPATH", raising=False)
    monkeypatch.setenv("ORLAB_OR_INSTALL_DIR", "")  # disable install discovery
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _hook(monkeypatch, data=JAR_BYTES):
    """Replaces the network seam; everything above it (temp file, verify,
    replace) runs real."""
    calls = []

    def _download(url, dest):
        calls.append(url)
        Path(dest).write_bytes(data)

    monkeypatch.setattr(jars, "_download", _download)
    return calls


def _no_download(monkeypatch):
    """Re-arms the autouse guard after a test used _hook."""
    monkeypatch.setattr(
        jars, "_download", lambda url, dest: pytest.fail(f"unexpected download of {url}")
    )


def test_default_version_is_pinned():
    assert DEFAULT_VERSION in PINNED_SHA256


def test_fetch_unpinned_with_sha256(cache, monkeypatch):
    calls = _hook(monkeypatch)
    path = jars.fetch_jar("99.99", sha256=JAR_SHA)
    assert path == cache / "OpenRocket-99.99.jar"
    assert path.read_bytes() == JAR_BYTES
    assert calls == [jars.RELEASE_URL.format(v="99.99")]


def test_cache_hit_needs_no_download(cache, monkeypatch):
    _hook(monkeypatch)
    jars.fetch_jar("99.99", sha256=JAR_SHA)
    _no_download(monkeypatch)
    assert jars.fetch_jar("99.99", sha256=JAR_SHA).read_bytes() == JAR_BYTES


def test_fetch_pinned_uses_pin(cache, monkeypatch):
    monkeypatch.setitem(jars.PINNED_SHA256, "99.99", JAR_SHA)
    _hook(monkeypatch)
    assert jars.fetch_jar("99.99").read_bytes() == JAR_BYTES


def test_default_version_used_when_none(cache, monkeypatch):
    monkeypatch.setattr(jars, "DEFAULT_VERSION", "99.99")
    monkeypatch.setitem(jars.PINNED_SHA256, "99.99", JAR_SHA)
    _hook(monkeypatch)
    assert jars.fetch_jar().name == "OpenRocket-99.99.jar"


def test_corrupt_cache_entry_evicted_and_redownloaded(cache, monkeypatch):
    cache.mkdir(parents=True)
    (cache / "OpenRocket-99.99.jar").write_bytes(OTHER_BYTES)
    calls = _hook(monkeypatch)
    assert jars.fetch_jar("99.99", sha256=JAR_SHA).read_bytes() == JAR_BYTES
    assert len(calls) == 1


def test_mismatched_download_never_enters_cache(cache, monkeypatch):
    _hook(monkeypatch, data=OTHER_BYTES)
    with pytest.raises(JarVerificationError, match="mismatch"):
        jars.fetch_jar("99.99", sha256=JAR_SHA)
    assert not (cache / "OpenRocket-99.99.jar").exists()
    assert list(cache.glob("*.part")) == []


def test_corrupt_cache_then_bad_download_raises_clean(cache, monkeypatch):
    cache.mkdir(parents=True)
    (cache / "OpenRocket-99.99.jar").write_bytes(OTHER_BYTES)
    _hook(monkeypatch, data=OTHER_BYTES)
    with pytest.raises(JarVerificationError, match="mismatch"):
        jars.fetch_jar("99.99", sha256=JAR_SHA)
    assert list(cache.iterdir()) == []


def test_unpinned_without_sha256_refused_with_guidance(cache, monkeypatch):
    _no_download(monkeypatch)
    cache.mkdir(parents=True)
    (cache / "OpenRocket-99.99.jar").write_bytes(JAR_BYTES)
    with pytest.raises(JarVerificationError) as exc:
        jars.fetch_jar("99.99")
    message = str(exc.value)
    assert jars.RELEASE_URL.format(v="99.99") in message
    assert "sha256sum" in message
    assert JAR_SHA in message  # digest of the already-cached file


def test_sha256_conflicting_with_pin_rejected(cache, monkeypatch):
    _no_download(monkeypatch)
    monkeypatch.setitem(jars.PINNED_SHA256, "99.99", JAR_SHA)
    with pytest.raises(ValueError, match="contradicts"):
        jars.fetch_jar("99.99", sha256="0" * 64)


@pytest.mark.parametrize("version", ["../../x", "24.12/evil", "a b", ""])
def test_malformed_version_rejected_before_any_io(cache, monkeypatch, version):
    _no_download(monkeypatch)
    with pytest.raises(ValueError, match="version"):
        jars.fetch_jar(version, sha256=JAR_SHA)


@pytest.mark.parametrize("digest", ["", "nothex", "abc123", JAR_SHA[:-1], JAR_SHA + "0"])
def test_malformed_sha256_rejected_before_any_io(cache, digest):
    """An empty or typo'd digest must fail fast — not skip the no-pin
    refusal, and not cost a full download that ends in a mismatch error."""
    with pytest.raises(ValueError, match="sha256"):
        jars.fetch_jar("99.99", sha256=digest)


def test_cache_dir_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("ORLAB_JAR_CACHE", str(tmp_path / "explicit"))
    assert jars.jar_cache_dir() == tmp_path / "explicit"
    monkeypatch.delenv("ORLAB_JAR_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert jars.jar_cache_dir() == tmp_path / "xdg" / "orlab-jars"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert jars.jar_cache_dir() == Path.home() / ".cache" / "orlab-jars"


# --- default-jar resolution chain ---


def _touch_jars(directory, *names):
    for name in names:
        (directory / name).write_bytes(b"")


def test_chain_cwd_picks_newest_profiled(hermetic):
    _touch_jars(
        hermetic,
        "OpenRocket-23.09.jar",
        "OpenRocket-24.12.jar",
        "OpenRocket-26.xx-SNAPSHOT.jar",  # no exact profile: must not win
        "OpenRocket-banana.jar",  # unparseable: must not crash the scan
    )
    path, source = _resolve_default_jar()
    assert path == "OpenRocket-24.12.jar"
    assert source == "current directory"


def test_chain_cwd_matches_lone_2309(hermetic):
    _touch_jars(hermetic, "OpenRocket-23.09.jar")
    assert _resolve_default_jar()[0] == "OpenRocket-23.09.jar"


def test_chain_skips_unprofiled_cwd_jar_entirely(hermetic):
    _touch_jars(hermetic, "OpenRocket-26.xx-SNAPSHOT.jar")
    with pytest.raises(FileNotFoundError):
        _resolve_default_jar()


def test_chain_cwd_ignores_directories(hermetic):
    (hermetic / "OpenRocket-24.12.jar").mkdir()
    with pytest.raises(FileNotFoundError):
        _resolve_default_jar()


def test_chain_cache_picks_newest_pinned_verified(hermetic, cache, monkeypatch, caplog):
    _no_download(monkeypatch)
    cache.mkdir(parents=True)
    monkeypatch.setitem(jars.PINNED_SHA256, "98.98", JAR_SHA)
    monkeypatch.setitem(jars.PINNED_SHA256, "99.99", JAR_SHA)
    (cache / "OpenRocket-98.98.jar").write_bytes(JAR_BYTES)
    (cache / "OpenRocket-99.99.jar").write_bytes(OTHER_BYTES)  # corrupt: skip + evict
    with caplog.at_level(logging.WARNING):
        path, source = _resolve_default_jar()
    assert path == str(cache / "OpenRocket-98.98.jar")
    assert source == "orlab jar cache"
    assert not (cache / "OpenRocket-99.99.jar").exists()
    assert "verification" in caplog.text


def test_chain_env_wins_over_cwd_and_cache(hermetic, monkeypatch):
    _touch_jars(hermetic, "OpenRocket-24.12.jar")
    monkeypatch.setenv("ORLAB_JAR", "/some/explicit.jar")
    assert _resolve_default_jar() == ("/some/explicit.jar", "ORLAB_JAR")


def test_chain_miss_names_fetch_jar(hermetic):
    with pytest.raises(FileNotFoundError, match=r"fetch_jar|python -m orlab"):
        _resolve_default_jar()


# --- CLI ---


def test_cli_fetch_prints_only_path(cache, monkeypatch, capsys):
    _hook(monkeypatch)
    assert main(["fetch", "99.99", "--sha256", JAR_SHA]) == 0
    out = capsys.readouterr().out
    assert out == f"{cache / 'OpenRocket-99.99.jar'}\n"


def test_cli_fetch_failure_exit_1_message_on_stderr(cache, monkeypatch, capsys):
    _no_download(monkeypatch)
    assert main(["fetch", "99.99"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err and "sha256" in captured.err


def test_cli_which_reports_path_and_source(hermetic, monkeypatch, capsys):
    jar = hermetic / "real.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv("ORLAB_JAR", str(jar))
    assert main(["which"]) == 0
    assert capsys.readouterr().out == f"{jar} (via ORLAB_JAR)\n"


def test_cli_which_flags_dangling_orlab_jar(hermetic, monkeypatch, capsys):
    """A dangling ORLAB_JAR resolves (env wins) but would fail at boot —
    which must say so rather than report success silently: in the output,
    and in the exit code, which was 0 until #71."""
    monkeypatch.setenv("ORLAB_JAR", "/gone/OpenRocket.jar")
    assert main(["which"]) == 4
    assert capsys.readouterr().out == "/gone/OpenRocket.jar (via ORLAB_JAR) — does not exist\n"


def test_cli_which_miss_is_an_environment_fault(hermetic, capsys):
    """No jar is an environment fault, exit 4 (org AGENTS.md 6.2), not 1,
    which reads as a finding (#71)."""
    assert main(["which"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No OpenRocket jar found" in captured.err
