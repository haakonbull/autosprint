# Autosprint task runner.
#
# The Python package lives at the repo root; the documentation area (Quarto
# site + LikeC4 model) is the one nested module. Run `just` with no arguments
# for the full list. Module recipes use path syntax: `just docs build`.

mod docs

_default:
    @just --list --list-submodules

# Install deps from the lockfile and wire up the git hooks.
install:
    uv sync
    prek install
    prek install -t commit-msg

# Re-resolve and upgrade dependencies (bumps uv.lock).
update:
    uv lock --upgrade
    uv sync

# Read-only quality gate: lint, format, types, import layers, markdown, C4.
check: docs::likec4::check
    uv run ruff check src tests
    uv run ruff format --check src tests
    uv run ty check src
    uv run lint-imports
    uv run rumdl check .

# Apply autofixes: format, auto-fixable lint, markdown. Mutates files.
fix:
    uv run ruff format src tests
    uv run ruff check --fix src tests
    uv run rumdl check --fix .

# Run all git hooks across the whole tree.
hooks:
    prek run --all-files

# Run the test suite (forwards args to pytest, e.g. `just test -k plan`).
test *args:
    uv run pytest {{ args }}

# Build the sdist + wheel into dist/.
build:
    uv build
