"""``python -m orlab`` — jar management from the command line.

``fetch [version] [--sha256 HEX]`` downloads and verifies a jar into the
cache and prints its path to stdout (the path is the only stable, scriptable
output of this CLI). ``which`` reports the jar ``OpenRocketInstance()``
would use and where the resolution chain found it; its output format is
informational, not a contract. Its exit code is: 0 when the jar it reports
exists, and 4 when there is no jar to use (none found, or ``ORLAB_JAR``
names a file that is not there). A missing engine is an environment fault,
not a finding, and 4 is the org's code for one (heibench/.github AGENTS.md
6.2; #71).

``fetch`` exits 0 with the path, 4 when the download or the cache fails or
the jar does not match its digest, and 64 on a usage error, which includes
a version orlab does not pin given without ``--sha256`` (#88).
"""

import argparse
import http.client
import logging
import sys
from typing import NoReturn

EXIT_ENVIRONMENT = 4
"""Could not run or read its input: no jar, a failed download or a digest
mismatch. An environment fault, not a verdict (#71, #88)."""

EXIT_USAGE = 64
"""Bad arguments (EX_USAGE), including no digest to verify a fetch (#88)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _fail(e: BaseException, code: int) -> int:
    print(f"error: {e}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="python -m orlab", description="OpenRocket jar management")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download and verify an OpenRocket jar")
    fetch.add_argument(
        "version", nargs="?", default=None, help="e.g. 24.12 (default: orlab's default version)"
    )
    fetch.add_argument(
        "--sha256", default=None, help="expected digest, required for versions orlab does not pin"
    )
    sub.add_parser("which", help="show the jar OpenRocketInstance() would use")
    args = parser.parse_args(argv)

    # the library never configures logging; the CLI is an application and
    # does, on stderr, so download progress doesn't pollute scriptable stdout
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

    if args.command == "fetch":
        from .errors import JarVerificationError
        from .jars import DEFAULT_VERSION, PINNED_SHA256, fetch_jar

        # fetch_jar raises the same error for "nothing to verify against",
        # which is the caller's to fix, and for a download that fails its digest
        no_digest = args.sha256 is None and (args.version or DEFAULT_VERSION) not in PINNED_SHA256
        try:
            print(fetch_jar(args.version, sha256=args.sha256))
        except ValueError as e:
            return _fail(e, EXIT_USAGE)
        except JarVerificationError as e:
            return _fail(e, EXIT_USAGE if no_digest else EXIT_ENVIRONMENT)
        except (OSError, http.client.HTTPException) as e:  # network and cache, 404 included
            return _fail(e, EXIT_ENVIRONMENT)
        return 0

    import os

    from .core.openrocket_instance import _resolve_default_jar
    from .jars import find_installed

    def _installed_note():
        inst = find_installed()
        if inst is not None:
            print(
                f"note: desktop OpenRocket {inst.version} found at {inst.jar} "
                "(not used automatically — pass jar_path= or set ORLAB_JAR)",
                file=sys.stderr,
            )

    try:
        path, source = _resolve_default_jar()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        _installed_note()
        return EXIT_ENVIRONMENT
    exists = os.path.exists(path)
    print(f"{path} (via {source}){'' if exists else ' — does not exist'}")
    _installed_note()
    return 0 if exists else EXIT_ENVIRONMENT


if __name__ == "__main__":
    sys.exit(main())
