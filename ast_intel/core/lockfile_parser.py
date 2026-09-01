"""Lockfile parsing — *resolved* (pinned) dependency versions.

Manifests declare version *constraints* (``requests>=2.0``); lockfiles record the
*resolved* versions actually installed (``requests==2.30.0``), including transitive
dependencies. This module extracts pinned versions from an ecosystem's lockfile so
the semantic index can carry an accurate package inventory, not just declared ranges.

Supported ecosystems (manifest → lockfile, in decreasing order of resolution):
    * Python — ``poetry.lock`` → ``Pipfile.lock`` → pinned ``requirements.txt``
    * npm    — ``package-lock.json`` / ``npm-shrinkwrap.json`` → ``yarn.lock`` → ``pnpm-lock.yaml``
    * Rust   — ``Cargo.lock``
    * Ruby   — ``Gemfile.lock``
    * PHP    — ``composer.lock``

Stdlib only — no third-party dependency resolvers or YAML library. Every parser
degrades to an empty list on a missing or malformed lockfile so extraction never
breaks. Use :func:`augment_with_lockfile` to merge a manifest's declared deps with
its resolved lockfile (pinned versions win; lockfile-only packages become transitive).
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from ast_intel.models.workspace_model import CrateDependency

__all__ = [
    "parse_python_lockfile",
    "parse_npm_lockfile",
    "parse_cargo_lockfile",
    "parse_ruby_lockfile",
    "parse_php_lockfile",
    "parse_lockfile",
    "merge_resolved_deps",
    "augment_with_lockfile",
    "is_pinned_version",
]

logger = logging.getLogger(__name__)

# Python lockfiles in preference order (most → least fully resolved).
_PYTHON_LOCKFILES: tuple[str, ...] = ("poetry.lock", "Pipfile.lock", "requirements.txt")

# A concrete single version has no range/comparator characters.
_RANGE_CHARS = set("<>=!~,* ")
_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)"
)
_REQ_NAME_SPEC_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$"
)


def is_pinned_version(version: str) -> bool:
    """True when ``version`` is a single concrete version (no range operators)."""
    v = (version or "").strip()
    return bool(v) and not any(c in v for c in _RANGE_CHARS)


def parse_python_lockfile(crate_dir: Path) -> list[CrateDependency]:
    """Return resolved deps from the best available Python lockfile in ``crate_dir``.

    Returns an empty list when no lockfile is present or it cannot be parsed.
    """
    for name in _PYTHON_LOCKFILES:
        path = crate_dir / name
        if not path.is_file():
            continue
        try:
            if name == "poetry.lock":
                return _parse_poetry_lock(path)
            if name == "Pipfile.lock":
                return _parse_pipfile_lock(path)
            return _parse_requirements_txt(path)
        except Exception as exc:  # never break extraction on a malformed lockfile
            logger.warning("Failed to parse lockfile %s: %s", path, exc)
            return []
    return []


def _parse_poetry_lock(path: Path) -> list[CrateDependency]:
    """``poetry.lock`` — TOML with a ``[[package]]`` array (all resolved, transitive)."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[CrateDependency] = []
    for pkg in data.get("package", []) or []:
        name = str(pkg.get("name", "")).strip()
        version = str(pkg.get("version", "")).strip()
        if not name or not version:
            continue
        category = str(pkg.get("category", "main")).lower()
        out.append(
            CrateDependency(
                name=name, version=version,
                is_dev=(category == "dev"), is_transitive=True,
            )
        )
    return out


def _parse_pipfile_lock(path: Path) -> list[CrateDependency]:
    """``Pipfile.lock`` — JSON with ``default``/``develop`` maps of ``{version: "==x"}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[CrateDependency] = []
    for section, is_dev in (("default", False), ("develop", True)):
        for name, meta in (data.get(section) or {}).items():
            version = ""
            if isinstance(meta, dict):
                version = str(meta.get("version", "")).lstrip("=").strip()
            if not name or not version:
                continue
            out.append(
                CrateDependency(
                    name=str(name).strip(), version=version,
                    is_dev=is_dev, is_transitive=True,
                )
            )
    return out


def _parse_requirements_txt(path: Path) -> list[CrateDependency]:
    """``requirements.txt`` — top-level deps; ``==`` entries are pins, ranges kept as-is."""
    out: list[CrateDependency] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue  # skip blanks, comments, -r/-e/--options
        line = line.split(" #", 1)[0].strip()  # strip inline comment
        pin = _REQ_PIN_RE.match(line)
        if pin:
            out.append(
                CrateDependency(
                    name=pin.group(1), version=pin.group(2).strip(),
                    is_transitive=False,
                )
            )
            continue
        # Non-pinned (range/unpinned/url): keep name + specifier, not a resolved pin.
        m = _REQ_NAME_SPEC_RE.match(line)
        if m and m.group(1):
            out.append(
                CrateDependency(
                    name=m.group(1), version=m.group(2).strip(),
                    is_transitive=False,
                )
            )
    return out


# ---------------------------------------------------------------------------
# npm / JavaScript — package-lock.json, yarn.lock, pnpm-lock.yaml
# ---------------------------------------------------------------------------

# npm lockfiles in preference order (most → least fully resolved / reliable).
_NPM_LOCKFILES: tuple[str, ...] = (
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
)


def parse_npm_lockfile(crate_dir: Path) -> list[CrateDependency]:
    """Return resolved deps from the best available npm lockfile in ``crate_dir``."""
    for name in _NPM_LOCKFILES:
        path = crate_dir / name
        if not path.is_file():
            continue
        try:
            if name in ("package-lock.json", "npm-shrinkwrap.json"):
                return _parse_package_lock(path)
            if name == "yarn.lock":
                return _parse_yarn_lock(path)
            return _parse_pnpm_lock(path)
        except Exception as exc:  # never break extraction on a malformed lockfile
            logger.warning("Failed to parse lockfile %s: %s", path, exc)
            return []
    return []


def _parse_package_lock(path: Path) -> list[CrateDependency]:
    """``package-lock.json`` — lockfile v2/v3 ``packages`` map, v1 ``dependencies`` tree."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[CrateDependency] = []
    seen: set[str] = set()

    packages = data.get("packages")
    if isinstance(packages, dict):  # lockfile v2 / v3
        marker = "node_modules/"
        for key, meta in packages.items():
            if not key or not isinstance(meta, dict):
                continue  # "" is the root project; skip non-dict values
            idx = key.rfind(marker)
            if idx < 0:
                continue  # workspace / link entry without a node_modules path
            name = key[idx + len(marker):]
            version = str(meta.get("version", "")).strip()
            if not name or not version or name in seen:
                continue
            seen.add(name)
            out.append(CrateDependency(
                name=name, version=version,
                is_dev=bool(meta.get("dev", False)), is_transitive=True,
            ))
        if out:
            return out

    deps_map = data.get("dependencies")  # lockfile v1 fallback
    if isinstance(deps_map, dict):
        _collect_npm_v1(deps_map, out, seen)
    return out


def _collect_npm_v1(
    deps_map: dict, out: list[CrateDependency], seen: set[str],
) -> None:
    """Recursively collect resolved deps from a v1 ``dependencies`` tree."""
    for name, meta in deps_map.items():
        if not isinstance(meta, dict):
            continue
        version = str(meta.get("version", "")).strip()
        if name and version and name not in seen:
            seen.add(name)
            out.append(CrateDependency(
                name=name, version=version,
                is_dev=bool(meta.get("dev", False)), is_transitive=True,
            ))
        nested = meta.get("dependencies")
        if isinstance(nested, dict):
            _collect_npm_v1(nested, out, seen)


def _yarn_spec_name(spec: str) -> str:
    """Extract the package name from a yarn spec like ``"@scope/pkg@^1.0.0"``."""
    s = spec.strip().strip('"')
    if s.startswith("@"):  # scoped: @scope/name@range
        at = s.find("@", 1)
        return s[:at] if at > 0 else s
    at = s.find("@")
    return s[:at] if at > 0 else s


_YARN_VERSION_RE = re.compile(r'^\s+version:?\s+"?([^"\s]+)"?')


def _parse_yarn_lock(path: Path) -> list[CrateDependency]:
    """``yarn.lock`` — classic (v1) and berry (v2+) block formats."""
    out: list[CrateDependency] = []
    seen: set[str] = set()
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and line.rstrip().endswith(":"):
            header = line.rstrip()[:-1].strip()
            if header == "__metadata":
                current = ""
                continue
            current = _yarn_spec_name(header.split(",")[0])
            continue
        m = _YARN_VERSION_RE.match(line)
        if m and current:
            if current not in seen:
                seen.add(current)
                out.append(CrateDependency(
                    name=current, version=m.group(1), is_transitive=True,
                ))
            current = ""
    return out


_PNPM_KEY_RE = re.compile(r"^\s{2,}'?/?((?:@[^/@]+/)?[^@/'\s]+)@([0-9][^:('\s]*)")


def _parse_pnpm_lock(path: Path) -> list[CrateDependency]:
    """``pnpm-lock.yaml`` — best-effort scan of ``packages:`` keys (no YAML dep)."""
    out: list[CrateDependency] = []
    seen: set[str] = set()
    in_packages = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^packages:\s*$", line):
            in_packages = True
            continue
        if in_packages and line and not line[0].isspace():
            break  # left the packages block (e.g. snapshots: at column 0)
        if not in_packages:
            continue
        m = _PNPM_KEY_RE.match(line)
        if m:
            name, version = m.group(1), m.group(2)
            if name not in seen:
                seen.add(name)
                out.append(CrateDependency(
                    name=name, version=version, is_transitive=True,
                ))
    return out


# ---------------------------------------------------------------------------
# Rust — Cargo.lock
# ---------------------------------------------------------------------------


def parse_cargo_lockfile(crate_dir: Path) -> list[CrateDependency]:
    """``Cargo.lock`` — TOML ``[[package]]`` array (fully resolved graph)."""
    path = crate_dir / "Cargo.lock"
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # never break extraction on a malformed lockfile
        logger.warning("Failed to parse lockfile %s: %s", path, exc)
        return []
    out: list[CrateDependency] = []
    seen: set[str] = set()
    for pkg in data.get("package", []) or []:
        name = str(pkg.get("name", "")).strip()
        version = str(pkg.get("version", "")).strip()
        if not name or not version or name in seen:
            continue
        seen.add(name)
        out.append(CrateDependency(name=name, version=version, is_transitive=True))
    return out


# ---------------------------------------------------------------------------
# Ruby — Gemfile.lock
# ---------------------------------------------------------------------------

# A resolved gem under ``GEM``/``specs:`` is indented exactly 4 spaces
# (``    rack (2.2.4)``); its own dependencies are indented 6 spaces.
_GEMFILE_LOCK_SPEC_RE = re.compile(r"^ {4}([A-Za-z0-9._-]+) \(([^)]+)\)\s*$")


def parse_ruby_lockfile(crate_dir: Path) -> list[CrateDependency]:
    """``Gemfile.lock`` — resolved gems from every ``specs:`` section."""
    path = crate_dir / "Gemfile.lock"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # never break extraction on a malformed lockfile
        logger.warning("Failed to parse lockfile %s: %s", path, exc)
        return []
    out: list[CrateDependency] = []
    seen: set[str] = set()
    in_specs = False
    for line in text.splitlines():
        if line.strip() == "specs:":
            in_specs = True
            continue
        if in_specs and line and not line[0].isspace():
            in_specs = False  # a new top-level section (PLATFORMS, DEPENDENCIES…)
        if not in_specs:
            continue
        m = _GEMFILE_LOCK_SPEC_RE.match(line)
        if m:
            name, version = m.group(1), m.group(2).strip()
            if is_pinned_version(version) and name not in seen:
                seen.add(name)
                out.append(CrateDependency(name=name, version=version, is_transitive=True))
    return out


# ---------------------------------------------------------------------------
# PHP — composer.lock
# ---------------------------------------------------------------------------


def parse_php_lockfile(crate_dir: Path) -> list[CrateDependency]:
    """``composer.lock`` — JSON ``packages`` / ``packages-dev`` arrays."""
    path = crate_dir / "composer.lock"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # never break extraction on a malformed lockfile
        logger.warning("Failed to parse lockfile %s: %s", path, exc)
        return []
    out: list[CrateDependency] = []
    seen: set[str] = set()
    for section, is_dev in (("packages", False), ("packages-dev", True)):
        for pkg in data.get(section, []) or []:
            if not isinstance(pkg, dict):
                continue
            name = str(pkg.get("name", "")).strip()
            version = str(pkg.get("version", "")).strip()
            if not name or not version or name in seen:
                continue
            seen.add(name)
            out.append(CrateDependency(
                name=name, version=version, is_dev=is_dev, is_transitive=True,
            ))
    return out


# ---------------------------------------------------------------------------
# Merge + dispatch
# ---------------------------------------------------------------------------

# language_id (from each extractor) -> its lockfile parser.
_LOCKFILE_DISPATCH: dict[str, Callable[[Path], list[CrateDependency]]] = {
    "python": parse_python_lockfile,
    "typescript": parse_npm_lockfile,
    "javascript": parse_npm_lockfile,
    "rust": parse_cargo_lockfile,
    "ruby": parse_ruby_lockfile,
    "php": parse_php_lockfile,
}


def parse_lockfile(crate_dir: Path, language: str) -> list[CrateDependency]:
    """Return resolved deps from ``crate_dir``'s lockfile for ``language`` (or [])."""
    parser = _LOCKFILE_DISPATCH.get((language or "").lower())
    return parser(crate_dir) if parser is not None else []


def merge_resolved_deps(
    manifest_deps: list[CrateDependency],
    lock_deps: list[CrateDependency],
) -> list[CrateDependency]:
    """Merge declared manifest deps with resolved lockfile deps.

    Lockfile pins win on ``version``; deps also present in the manifest are marked
    direct (``is_transitive=False``), lockfile-only deps keep the lockfile parser's
    own transitivity signal.
    """
    direct_names = {d.name.lower() for d in manifest_deps}
    by_name: dict[str, CrateDependency] = {
        d.name.lower(): d for d in manifest_deps
    }
    for d in lock_deps:
        key = d.name.lower()
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = CrateDependency(
                name=d.name, version=d.version, is_dev=d.is_dev,
                is_transitive=False if key in direct_names else d.is_transitive,
            )
        else:
            by_name[key] = CrateDependency(
                name=existing.name or d.name,
                version=d.version or existing.version,
                path=existing.path,
                features=existing.features,
                is_workspace=existing.is_workspace,
                is_dev=existing.is_dev or d.is_dev,
                is_transitive=False,  # declared in the manifest → direct
            )
    return list(by_name.values())


def augment_with_lockfile(
    manifest_dir: Path,
    language: str,
    manifest_deps: list[CrateDependency],
) -> list[CrateDependency]:
    """Merge ``manifest_deps`` with resolved versions from a sibling lockfile.

    Convenience wrapper over :func:`parse_lockfile` + :func:`merge_resolved_deps` for
    language extractors. Returns ``manifest_deps`` unchanged when no lockfile exists.
    """
    return merge_resolved_deps(manifest_deps, parse_lockfile(manifest_dir, language))
