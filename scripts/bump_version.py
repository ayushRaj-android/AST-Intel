#!/usr/bin/env python3
"""Bump the ast-intel version and (optionally) build the wheel.

ast-intel stores its version *statically* in three places that MUST stay
in sync — a test (``tests/test_packaging.py::test_version_constants_match``)
enforces the first two:

  1. pyproject.toml          ->  version = "X.Y.Z"
  2. ast_intel/__init__.py   ->  TOOL_VERSION: str = "X.Y.Z"
  3. tests/test_package.py   ->  assert TOOL_VERSION == "X.Y.Z"

This script updates all three together, then runs ``python -m build``.

Usage
-----
    # Explicit target version
    python scripts/bump_version.py 0.2.0

    # …or bump a semver component relative to the current version
    python scripts/bump_version.py patch      # 0.1.1 -> 0.1.2
    python scripts/bump_version.py minor      # 0.1.1 -> 0.2.0
    python scripts/bump_version.py major      # 0.1.1 -> 1.0.0

Options
-------
    --dry-run     Print the planned changes; write nothing, build nothing.
    --no-build    Update the version files only; skip ``python -m build``.
    --no-clean    Keep existing dist/ and build/ instead of removing them.

Examples
--------
    python scripts/bump_version.py patch
    python scripts/bump_version.py 1.0.0 --no-build
    python scripts/bump_version.py minor --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each target: (file, regex capturing (prefix)(version)(suffix), label).
# The regex must match exactly one version occurrence we want to rewrite.
_TARGETS: tuple[tuple[Path, re.Pattern[str], str], ...] = (
    (
        ROOT / "pyproject.toml",
        re.compile(r'(?m)^(version\s*=\s*")(\d+\.\d+\.\d+)(")'),
        "pyproject.toml [project] version",
    ),
    (
        ROOT / "ast_intel" / "__init__.py",
        re.compile(r'(TOOL_VERSION\s*:\s*str\s*=\s*")(\d+\.\d+\.\d+)(")'),
        "ast_intel/__init__.py TOOL_VERSION",
    ),
    (
        ROOT / "tests" / "test_package.py",
        re.compile(r'(assert\s+TOOL_VERSION\s*==\s*")(\d+\.\d+\.\d+)(")'),
        "tests/test_package.py pinned assertion",
    ),
)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_BUMPS = ("major", "minor", "patch")


def current_version() -> str:
    """Read the authoritative version from pyproject.toml."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return str(data["project"]["version"])


def resolve_target(arg: str, current: str) -> str:
    """Turn a CLI argument into a concrete ``X.Y.Z`` target version."""
    if _SEMVER.match(arg):
        return arg
    if arg in _BUMPS:
        major, minor, patch = (int(p) for p in current.split("."))
        if arg == "major":
            return f"{major + 1}.0.0"
        if arg == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"
    sys.exit(
        f"error: {arg!r} is not an X.Y.Z version or one of {_BUMPS}",
    )


def apply_updates(new: str, *, dry_run: bool) -> None:
    """Rewrite the version in every target file (or preview the change)."""
    for path, pattern, label in _TARGETS:
        if not path.is_file():
            sys.exit(f"error: missing file for {label}: {path}")
        text = path.read_text("utf-8")
        match = pattern.search(text)
        if match is None:
            sys.exit(f"error: version pattern not found in {label} ({path})")
        old = match.group(2)
        if dry_run:
            print(f"  [dry-run] {label}: {old} -> {new}")
            continue
        path.write_text(pattern.sub(rf"\g<1>{new}\g<3>", text, count=1), "utf-8")
        print(f"  updated  {label}: {old} -> {new}")


def build_wheel(*, clean: bool) -> None:
    """Run ``python -m build`` (optionally clearing stale artifacts first)."""
    if clean:
        for d in ("dist", "build"):
            shutil.rmtree(ROOT / d, ignore_errors=True)
        for egg in ROOT.glob("*.egg-info"):
            shutil.rmtree(egg, ignore_errors=True)
        print("  cleaned dist/, build/, *.egg-info")
    print("  running: python -m build")
    subprocess.run([sys.executable, "-m", "build"], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bump the ast-intel version and build the wheel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "version",
        help="Target version 'X.Y.Z', or one of: major, minor, patch.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show planned changes without writing files or building.",
    )
    parser.add_argument(
        "--no-build", action="store_true",
        help="Update version files only; skip 'python -m build'.",
    )
    parser.add_argument(
        "--no-clean", action="store_true",
        help="Do not remove dist/ build/ before building.",
    )
    args = parser.parse_args()

    current = current_version()
    target = resolve_target(args.version, current)
    if target == current and not args.dry_run:
        sys.exit(f"error: target version {target} equals the current version")

    print(f"ast-intel: {current} -> {target}")
    apply_updates(target, dry_run=args.dry_run)

    if args.dry_run:
        print("dry-run: nothing written, nothing built.")
        return
    if args.no_build:
        print("version files updated; skipping build (--no-build).")
        return

    build_wheel(clean=not args.no_clean)
    print(f"\nDone. Built dist/ast_intel-{target}-py3-none-any.whl")


if __name__ == "__main__":
    main()
