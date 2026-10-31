# orlab task runner.
# All recipes run through uv so local and CI behavior match exactly.

# Show available recipes.
default:
    @just --list

# Install dependencies (dev group) into the uv environment.
setup:
    uv sync

# Format code in place.
fmt:
    uv run ruff format .

# Lint and apply safe autofixes.
lint:
    uv run ruff check --fix .

# Type-check the package with mypy.
typecheck:
    uv run mypy

# CI-equivalent gate: format check + lint + typecheck.
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy

# Run the unit test suite (no OpenRocket jar or JVM required).
test:
    uv run pytest -q

# Integration tests against real OpenRocket jars (downloaded to the cache
# dir on first run; override with ORLAB_JAR_CACHE / ORLAB_TEST_VERSION).
test-integration:
    uv run pytest -q -m integration tests/integration

# Build the docs site (CI-equivalent); `just docs serve` for live preview.
docs cmd="build":
    uv run --group docs mkdocs {{cmd}} {{ if cmd == "build" { "--strict" } else { "" } }}

# Audit every package pinned in uv.lock against OSV (uv's experimental `uv audit`).
audit:
    uv audit --frozen --preview-features audit-command
