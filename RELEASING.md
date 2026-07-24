# Releasing ast-intel

How to cut a new version and build a distributable wheel.

## TL;DR

```bash
# Bump the patch version (0.1.1 -> 0.1.2) and build the wheel
make bump V=patch

# …or a specific version
make bump V=0.2.0
```

The wheel lands in `dist/ast_intel-<version>-py3-none-any.whl`.

## Why a script?

The version is stored **statically** in three places that must stay in
sync — a test (`tests/test_packaging.py::test_version_constants_match`)
fails if they drift:

| File | What |
|------|------|
| `pyproject.toml` | `version = "X.Y.Z"` — drives the wheel filename |
| `ast_intel/__init__.py` | `TOOL_VERSION` — stamped into every `graph.json`; also invalidates `.ast-intel-cache.json` on change |
| `tests/test_package.py` | pinned `assert TOOL_VERSION == "X.Y.Z"` |

`scripts/bump_version.py` updates all three together, then builds.

## Usage

```bash
# Bump a semver component (relative to the current version)
python scripts/bump_version.py patch      # 0.1.1 -> 0.1.2
python scripts/bump_version.py minor      # 0.1.1 -> 0.2.0
python scripts/bump_version.py major      # 0.1.1 -> 1.0.0

# …or set an explicit version
python scripts/bump_version.py 0.2.0

# Preview without writing or building
python scripts/bump_version.py minor --dry-run

# Update the version files but don't build
python scripts/bump_version.py patch --no-build
```

Equivalent `make` shortcuts:

```bash
make bump V=patch
make bump V=0.2.0
```

## Recommended flow

```bash
make check                 # lint + tests must pass first
make bump V=patch          # sync version in 3 files + build wheel
ls dist/                   # ast_intel-0.1.2-py3-none-any.whl
```

Then share/install the wheel — see `docs/SETUP_INSTRUCTIONS_README.md`
("Build the Wheel" / "Share the Wheel Directly").

## Requirements

- The `build` package: `pip install build`
- Run from the repository root.
