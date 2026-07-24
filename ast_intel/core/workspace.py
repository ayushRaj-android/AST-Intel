"""Workspace discovery — repository walking, file filtering, and crate grouping.

Walks the repository tree from the root, applies include/exclude filters,
respects ``.gitignore`` and ``.ast-intel-ignore`` rules, detects language
manifests, and groups source files under the nearest parent manifest as
logical crates.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pathspec

import ast_intel
from ast_intel.core.manifest_parser import (
    CSPROJ_EXTENSION,
    MANIFEST_FILENAMES,
    ManifestParser,
)
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import CrateModel, WorkspaceAST, WorkspaceMeta

__all__: list[str] = ["WorkspaceDiscovery"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

# Source-file extensions that we recognise (across all supported languages).
# If ``--lang`` is given, we filter down further in ``_extension_for_languages``.
_ALL_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    # Rust
    ".rs",
    # Go
    ".go",
    # Python
    ".py", ".pyi",
    # TypeScript / JavaScript
    ".ts", ".tsx", ".js", ".jsx",
    # C#
    ".cs",
    # C / C++
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx",
    # Java
    ".java",
    # Ruby
    ".rb",
    # Kotlin
    ".kt", ".kts",
    # Scala
    ".scala", ".sc",
    # Swift
    ".swift",
    # PHP
    ".php",
    # Contract files (Protobuf, GraphQL)
    ".proto", ".graphql", ".gql",
})

# Known OpenAPI / Swagger filenames. These are picked up even though
# ``.yaml`` / ``.json`` are not in ``_ALL_SOURCE_EXTENSIONS``.
_OPENAPI_FILE_PATTERNS: frozenset[str] = frozenset({
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
})

# Language → extensions mapping (used when ``--lang`` is specified)
_LANGUAGE_EXTENSIONS: dict[str, frozenset[str]] = {
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "python": frozenset({".py", ".pyi"}),
    "typescript": frozenset({".ts", ".tsx", ".js", ".jsx"}),
    "csharp": frozenset({".cs"}),
    "cpp": frozenset({".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx"}),
    "java": frozenset({".java"}),
    "ruby": frozenset({".rb"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "scala": frozenset({".scala", ".sc"}),
    "swift": frozenset({".swift"}),
    "php": frozenset({".php"}),
    "contract": frozenset({
        ".yaml", ".yml", ".json", ".proto", ".graphql", ".gql",
    }),
}

# Maximum file size we will attempt to parse (10 MB).
_MAX_FILE_SIZE: int = 10 * 1024 * 1024

# Number of bytes to read for binary-file detection.
_BINARY_CHECK_BYTES: int = 8192

# Default directories that are always excluded.
_DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "venv",
    "target",       # Rust build output
    "dist",
    "build",
    ".next",
    "coverage",
})

# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _load_gitignore(repo_root: Path) -> pathspec.PathSpec[Any] | None:
    """Load ``.gitignore`` from repo root and return a compiled ``PathSpec``.

    Returns ``None`` if no ``.gitignore`` exists.
    """
    gi_path = repo_root / ".gitignore"
    if not gi_path.is_file():
        return None
    try:
        lines = gi_path.read_text(encoding="utf-8").splitlines()
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except OSError:
        logger.warning("Could not read %s", gi_path)
        return None


_AST_INTEL_IGNORE_FILENAME: str = ".ast-intel-ignore"
_AST_INTEL_SCAN_FILENAME: str = ".ast-intel-scan"


def _load_ast_intel_scan(repo_root: Path) -> list[str]:
    """Load ``.ast-intel-scan`` from repo root and return include paths.

    Each non-empty, non-comment line is treated as a relative path to
    include in the scan — same as passing ``--include`` on the CLI.
    Returns an empty list if the file does not exist.
    """
    scan_path = repo_root / _AST_INTEL_SCAN_FILENAME
    if not scan_path.is_file():
        return []
    try:
        lines = scan_path.read_text(encoding="utf-8").splitlines()
        paths = [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]
        if paths:
            logger.info(
                "Loaded %d include path(s) from %s",
                len(paths),
                _AST_INTEL_SCAN_FILENAME,
            )
        return paths
    except OSError:
        logger.warning("Could not read %s", scan_path)
        return []


def _load_ast_intel_ignore(
    repo_root: Path,
) -> pathspec.PathSpec[Any] | None:
    """Load ``.ast-intel-ignore`` from repo root and return a compiled ``PathSpec``.

    The file follows gitignore glob syntax and lets projects commit
    exclusion rules (e.g. generated code, vendored dirs) that are
    applied *in addition to* ``.gitignore`` and ``_DEFAULT_EXCLUDES``.

    Returns ``None`` if the file does not exist.
    """
    ignore_path = repo_root / _AST_INTEL_IGNORE_FILENAME
    if not ignore_path.is_file():
        return None
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except OSError:
        logger.warning("Could not read %s", ignore_path)
        return None


def _is_binary(file_path: Path) -> bool:
    """Return ``True`` if the file appears to be binary.

    Reads the first ``_BINARY_CHECK_BYTES`` bytes and checks for null bytes.
    """
    try:
        chunk = file_path.read_bytes()[:_BINARY_CHECK_BYTES]
    except OSError:
        return True  # Can't read → treat as binary
    else:
        return b"\x00" in chunk


def _is_utf8(file_path: Path) -> bool:
    """Return ``True`` if the file can be decoded as UTF-8.

    Only samples the first ``_BINARY_CHECK_BYTES`` bytes rather than
    reading the entire file, since the dispatcher will read the full
    content later and handle encoding errors there.
    """
    try:
        sample = file_path.read_bytes()[:_BINARY_CHECK_BYTES]
        sample.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return True


def _extensions_for_languages(
    languages: list[str] | None,
) -> frozenset[str]:
    """Return the set of extensions to process.

    If *languages* is ``None`` or empty, return all known extensions.
    Otherwise return the union of extensions for the requested languages.
    """
    if not languages:
        return _ALL_SOURCE_EXTENSIONS
    exts: set[str] = set()
    for lang in languages:
        exts.update(_LANGUAGE_EXTENSIONS.get(lang, frozenset()))
    return frozenset(exts) if exts else _ALL_SOURCE_EXTENSIONS


def _compile_exclude_patterns(
    exclude_paths: list[str],
) -> pathspec.PathSpec[Any] | None:
    """Compile user ``--exclude`` globs into a ``PathSpec``.

    Users naturally write ``*/tests/*`` meaning "any tests directory at any
    depth". Gitignore-style patterns treat ``*`` as non-recursive (matches
    only within one path component). We normalise leading/trailing lone ``*``
    to ``**`` so the patterns behave as users expect.

    Returns ``None`` if *exclude_paths* is empty.
    """
    if not exclude_paths:
        return None

    normalised: list[str] = []
    for pat in exclude_paths:
        # Replace lone * (not **) at boundaries with ** for recursive match
        parts = pat.split("/")
        parts = ["**" if p == "*" else p for p in parts]
        normalised.append("/".join(parts))

    return pathspec.PathSpec.from_lines("gitignore", normalised)


def _build_include_set(
    repo_root: Path,
    include_paths: list[str],
) -> frozenset[Path] | None:
    """Resolve ``--include`` paths to absolute paths.

    Returns ``None`` if *include_paths* is empty (meaning include everything).
    """
    if not include_paths:
        return None
    result: set[Path] = set()
    for p in include_paths:
        resolved = (repo_root / p).resolve()
        result.add(resolved)
    return frozenset(result)


def _path_under_includes(
    file_path: Path,
    include_set: frozenset[Path] | None,
) -> bool:
    """Return ``True`` if *file_path* is under one of the include roots.

    If *include_set* is ``None`` everything is included.
    """
    if include_set is None:
        return True
    return any(
        file_path == inc or inc in file_path.parents
        for inc in include_set
    )


def _find_nearest_manifest(
    file_path: Path,
    manifest_dirs: dict[Path, str],
    repo_root: Path,
) -> Path | None:
    """Walk up from *file_path* to find the nearest directory containing a manifest.

    Returns ``None`` if no manifest directory is found before reaching *repo_root*.
    """
    current = file_path.parent
    while current != repo_root.parent and current.is_relative_to(repo_root):
        if current in manifest_dirs:
            return current
        current = current.parent
    return None


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- WorkspaceDiscovery
# ---------------------------------------------------------------------------


class WorkspaceDiscovery:
    """Discover source files and manifests in a repository.

    Args:
        repo_root: Absolute path to the repository root.
        include_paths: Paths to include (relative to repo root). Empty = all.
        exclude_paths: Glob patterns to exclude.
        languages: Restrict to specific languages. ``None`` = auto-detect.
    """

    def __init__(
        self,
        repo_root: Path,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()

        # Merge CLI --include paths with .ast-intel-scan file entries.
        # CLI args take precedence: if both are empty, scan everything.
        scan_file_paths = _load_ast_intel_scan(self.repo_root)
        cli_paths = include_paths or []
        merged = list(dict.fromkeys(cli_paths + scan_file_paths))  # dedupe, preserve order
        self.include_paths = merged

        self.exclude_paths = exclude_paths or []
        self.languages = languages

    def discover(self) -> WorkspaceAST:
        """Walk the repository and return a populated ``WorkspaceAST``.

        Returns:
            A ``WorkspaceAST`` with discovered crate structure and stub
            ``FileAST`` entries (only ``file`` and ``module_path`` set).
        """
        logger.info("Discovering files in %s", self.repo_root)

        workspace = WorkspaceAST(
            meta=WorkspaceMeta(
                schema_version=ast_intel.SCHEMA_VERSION,
                tool_version=ast_intel.TOOL_VERSION,
                workspace_root=str(self.repo_root),
            ),
        )

        valid_exts = _extensions_for_languages(self.languages)
        gitignore = _load_gitignore(self.repo_root)
        project_ignore = _load_ast_intel_ignore(self.repo_root)
        user_excludes = _compile_exclude_patterns(self.exclude_paths)
        include_set = _build_include_set(self.repo_root, self.include_paths)

        # --- Phase A: find all manifest files ---
        manifest_dirs, crates = self._discover_manifests(
            valid_exts, gitignore, project_ignore, user_excludes, include_set,
        )

        # --- Phase B: walk all source files and assign to crates ---
        ungrouped = CrateModel(
            name="ungrouped",
            language="unknown",
            manifest_path="",
        )
        skipped_binary = 0
        skipped_large = 0
        skipped_encoding = 0

        for source_file in self._walk_source_files(
            valid_exts, gitignore, project_ignore, user_excludes, include_set,
        ):
            # Size check
            try:
                size = source_file.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_SIZE:
                logger.debug("Skipping (>10 MB): %s", source_file)
                skipped_large += 1
                continue

            # Binary check
            if _is_binary(source_file):
                logger.debug("Skipping (binary): %s", source_file)
                skipped_binary += 1
                continue

            # UTF-8 check
            if not _is_utf8(source_file):
                logger.debug("Skipping (non-UTF-8): %s", source_file)
                skipped_encoding += 1
                continue

            # Create a stub FileAST
            rel_path = source_file.relative_to(self.repo_root)
            stub = FileAST(file=str(rel_path))

            # Find nearest manifest
            manifest_dir = _find_nearest_manifest(
                source_file, manifest_dirs, self.repo_root,
            )
            if manifest_dir is not None:
                crate_name = manifest_dirs[manifest_dir]
                crate = crates[crate_name]
                # Build module path from crate name + relative path
                stub.module_path = _build_module_path(
                    crate.name, source_file, manifest_dir,
                )
                crate.files.append(stub)
            else:
                stub.module_path = "ungrouped"
                ungrouped.files.append(stub)

        # Add ungrouped if it has files
        if ungrouped.files:
            crates["ungrouped"] = ungrouped

        workspace.crates = crates

        total_files = sum(len(c.files) for c in workspace.crates.values())
        logger.info(
            "Discovery complete: %d crate(s), %d file(s) "
            "(skipped: %d binary, %d large, %d non-UTF-8)",
            len(workspace.crates),
            total_files,
            skipped_binary,
            skipped_large,
            skipped_encoding,
        )

        return workspace

    # --- Internal discovery methods ---

    def _discover_manifests(
        self,
        _valid_exts: frozenset[str],
        gitignore: pathspec.PathSpec[Any] | None,
        project_ignore: pathspec.PathSpec[Any] | None,
        user_excludes: pathspec.PathSpec[Any] | None,
        include_set: frozenset[Path] | None,
    ) -> tuple[dict[Path, str], dict[str, CrateModel]]:
        """Find all manifest files and parse them into ``CrateModel`` instances.

        Returns:
            A tuple of (manifest_dir → crate_name mapping, crate_name → CrateModel).
        """
        manifest_dirs: dict[Path, str] = {}
        crates: dict[str, CrateModel] = {}
        parser = ManifestParser()

        for dirpath_str, dirnames, filenames in os.walk(self.repo_root):
            # Prune excluded directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in _DEFAULT_EXCLUDES
            ]

            for fname in sorted(filenames):
                is_manifest = (
                    fname in MANIFEST_FILENAMES
                    or fname.endswith(CSPROJ_EXTENSION)
                )
                if not is_manifest:
                    continue

                path = Path(dirpath_str) / fname
                rel = path.relative_to(self.repo_root)
                rel_str = str(rel)

                if self._should_skip(
                    rel_str, gitignore, project_ignore, user_excludes,
                ):
                    continue
                if not _path_under_includes(path, include_set):
                    continue

                crate = parser.parse(path)

                original_name = crate.name
                suffix = 2
                while crate.name in crates:
                    crate.name = f"{original_name}-{suffix}"
                    suffix += 1

                crate.manifest_path = str(rel)

                if self.languages and crate.language not in self.languages:
                    continue

                crates[crate.name] = crate
                manifest_dirs[path.parent.resolve()] = crate.name
                logger.debug(
                    "Found manifest: %s → crate '%s'", rel, crate.name,
                )

        return manifest_dirs, crates

    def _walk_source_files(
        self,
        valid_exts: frozenset[str],
        gitignore: pathspec.PathSpec[Any] | None,
        project_ignore: pathspec.PathSpec[Any] | None,
        user_excludes: pathspec.PathSpec[Any] | None,
        include_set: frozenset[Path] | None,
    ) -> list[Path]:
        """Collect all source files matching filters.

        Uses ``os.walk`` with top-down pruning to avoid descending into
        excluded directories (e.g. ``node_modules``, ``target``, ``.git``).

        Returns a sorted list for deterministic order.
        """
        result: list[Path] = []

        for dirpath_str, dirnames, filenames in os.walk(self.repo_root):
            # Prune excluded directories in-place (top-down walk)
            dirnames[:] = [
                d for d in dirnames
                if d not in _DEFAULT_EXCLUDES
            ]

            for fname in filenames:
                path = Path(dirpath_str) / fname
                # Accept source files by extension, plus known OpenAPI
                # filenames (whose .yaml/.json suffix is not in valid_exts).
                if (
                    path.suffix not in valid_exts
                    and fname.lower() not in _OPENAPI_FILE_PATTERNS
                ):
                    continue

                rel = path.relative_to(self.repo_root)
                rel_str = str(rel)

                if self._should_skip(
                    rel_str, gitignore, project_ignore, user_excludes,
                ):
                    continue
                if not _path_under_includes(path, include_set):
                    continue

                result.append(path)

        return sorted(result)

    @staticmethod
    def _should_skip(
        rel_path: str,
        gitignore: pathspec.PathSpec[Any] | None,
        project_ignore: pathspec.PathSpec[Any] | None,
        user_excludes: pathspec.PathSpec[Any] | None,
    ) -> bool:
        """Return ``True`` if *rel_path* should be excluded."""
        # Default directory exclusions
        parts = rel_path.split("/")
        if any(part in _DEFAULT_EXCLUDES for part in parts):
            return True

        # .gitignore
        if gitignore is not None and gitignore.match_file(rel_path):
            return True

        # .ast-intel-ignore
        if project_ignore is not None and project_ignore.match_file(rel_path):
            return True

        # User --exclude patterns
        return user_excludes is not None and user_excludes.match_file(rel_path)


# endregion: --- WorkspaceDiscovery


# ---------------------------------------------------------------------------
# region:    --- Module Path Builder
# ---------------------------------------------------------------------------


def _build_module_path(
    crate_name: str,
    file_path: Path,
    manifest_dir: Path,
) -> str:
    """Build a qualified module path for a file relative to its crate.

    Example: crate ``approval-engine``, file ``src/handlers/process.rs``
    → ``approval-engine::handlers::process``
    """
    try:
        rel = file_path.relative_to(manifest_dir)
    except ValueError:
        return crate_name

    parts = list(rel.with_suffix("").parts)

    # Strip leading ``src`` / ``src/lib`` directories
    if parts and parts[0] == "src":
        parts = parts[1:]

    # Strip well-known terminal module names
    if parts and parts[-1] in ("lib", "main", "mod", "__init__", "index"):
        parts = parts[:-1]

    if parts:
        return f"{crate_name}::{'::'.join(parts)}"
    return crate_name


# endregion: --- Module Path Builder
