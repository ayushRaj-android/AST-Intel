"""Incremental cache — skip unchanged files across runs.

Stores SHA-256 hashes and serialized ``FileAST`` dicts for every file.
On subsequent runs, only files whose content hash has changed are re-parsed.
Cross-reference indexes are always rebuilt from scratch because they depend
on the full file set.

Cache location: ``<output_dir>/.ast-intel-cache.json``

Cache schema::

    {
        "version": 1,
        "entries": {
            "src/main.rs": {
                "file_hash": "abc123...",
                "file_ast": { ... }
            }
        }
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ast_intel import TOOL_VERSION
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import WorkspaceAST

__all__: list[str] = ["CacheDiff", "IncrementalCache", "compute_file_hash"]

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1
_HASH_ALGORITHM = "sha256"
_READ_CHUNK = 65536


# ---------------------------------------------------------------------------
# region:    --- Data Structures
# ---------------------------------------------------------------------------


class CacheDiff:
    """Result of comparing current workspace files against the cache.

    Attributes:
        unchanged: Relative file paths whose hash matches the cache.
        changed: Relative file paths whose hash differs from the cache.
        added: Relative file paths not present in the cache.
        deleted: Relative file paths in the cache but absent from workspace.
    """

    __slots__ = ("added", "changed", "deleted", "unchanged")

    def __init__(
        self,
        *,
        unchanged: frozenset[str] = frozenset(),
        changed: frozenset[str] = frozenset(),
        added: frozenset[str] = frozenset(),
        deleted: frozenset[str] = frozenset(),
    ) -> None:
        self.unchanged = unchanged
        self.changed = changed
        self.added = added
        self.deleted = deleted


# endregion: --- Data Structures


# ---------------------------------------------------------------------------
# region:    --- JSON Encoder
# ---------------------------------------------------------------------------


class _CacheEncoder(json.JSONEncoder):
    """Encoder that handles dataclasses, Enums, Paths, sets, and tuples."""

    def default(self, o: Any) -> Any:  # noqa: ANN401, PLR0911
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, SimpleNamespace):
            return _namespace_to_dict(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, set | frozenset):
            return sorted(o)
        if isinstance(o, tuple):
            return list(o)
        return super().default(o)


# endregion: --- JSON Encoder


# ---------------------------------------------------------------------------
# region:    --- IncrementalCache
# ---------------------------------------------------------------------------


class IncrementalCache:
    """Manages an on-disk file-level cache for incremental extraction.

    Args:
        cache_path: Absolute path to the cache JSON file.
    """

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path

    # -- Public API ---------------------------------------------------------

    def load(self) -> dict[str, dict[str, Any]]:  # noqa: PLR0911
        """Load cache entries from disk.

        Returns:
            A dict mapping relative file path → ``{"file_hash": ...,
            "file_ast": ...}``.  Returns an empty dict on missing /
            corrupt / version-mismatched cache.
        """
        if not self._path.is_file():
            logger.debug("No cache file at %s", self._path)
            return {}

        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cache unreadable (%s), starting fresh", exc)
            return {}

        if not isinstance(data, dict):
            return {}
        if data.get("version") != _CACHE_VERSION:
            logger.info(
                "Cache version mismatch (got %s, want %d), discarding",
                data.get("version"),
                _CACHE_VERSION,
            )
            return {}

        if data.get("tool_version") != TOOL_VERSION:
            logger.info(
                "Cache tool version mismatch (got %s, want %s), discarding",
                data.get("tool_version"),
                TOOL_VERSION,
            )
            return {}

        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {}
        return entries

    def save(
        self,
        workspace: WorkspaceAST,
        file_hashes: dict[str, str],
    ) -> None:
        """Persist current workspace state to the cache file.

        Uses atomic write (temp file + rename) so a crash never
        corrupts the existing cache.

        Args:
            workspace: The fully populated workspace AST.
            file_hashes: Mapping of relative file path → SHA-256 hex digest.
        """
        entries: dict[str, dict[str, Any]] = {}
        for crate in workspace.crates.values():
            for file_ast in crate.files:
                rel = file_ast.file
                h = file_hashes.get(rel, "")
                entries[rel] = {
                    "file_hash": h,
                    "file_ast": asdict(file_ast),
                }

        payload: dict[str, Any] = {
            "version": _CACHE_VERSION,
            "tool_version": TOOL_VERSION,
            "entries": entries,
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file in same dir, then rename
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".ast-cache-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, cls=_CacheEncoder, sort_keys=True)
            Path(tmp).replace(self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        logger.info("Cache saved (%d entries)", len(entries))

    def diff(
        self,
        workspace: WorkspaceAST,
        cached: dict[str, dict[str, Any]],
        repo_root: Path,
    ) -> tuple[CacheDiff, dict[str, str]]:
        """Compare workspace files against cached entries.

        Also computes current file hashes (needed later for ``save``).

        Args:
            workspace: The discovered workspace (files are stubs).
            cached: Previously loaded cache entries.
            repo_root: Absolute path to the repository root.

        Returns:
            A ``(CacheDiff, file_hashes)`` tuple.  ``file_hashes`` maps
            relative path → SHA-256 hex string for every current file.
        """
        current_files: set[str] = set()
        file_hashes: dict[str, str] = {}

        for crate in workspace.crates.values():
            for stub in crate.files:
                current_files.add(stub.file)
                abs_path = repo_root / stub.file
                file_hashes[stub.file] = compute_file_hash(abs_path)

        cached_files = set(cached)

        added = frozenset(current_files - cached_files)
        deleted = frozenset(cached_files - current_files)

        unchanged: set[str] = set()
        changed: set[str] = set()

        for rel in current_files & cached_files:
            entry = cached.get(rel, {})
            old_hash = entry.get("file_hash", "")
            if old_hash and old_hash == file_hashes.get(rel):
                unchanged.add(rel)
            else:
                changed.add(rel)

        result = CacheDiff(
            unchanged=frozenset(unchanged),
            changed=frozenset(changed),
            added=added,
            deleted=deleted,
        )

        logger.info(
            "Cache diff: %d unchanged, %d changed, %d added, %d deleted",
            len(result.unchanged),
            len(result.changed),
            len(result.added),
            len(result.deleted),
        )

        return result, file_hashes

    def restore_cached_asts(
        self,
        workspace: WorkspaceAST,
        cached: dict[str, dict[str, Any]],
        unchanged: frozenset[str],
    ) -> None:
        """Replace stub ``FileAST`` objects with cached versions.

        For every file in *unchanged*, deserializes the cached ``file_ast``
        dict back into a ``FileAST`` and writes it into the workspace.

        Args:
            workspace: The workspace whose stubs should be replaced.
            cached: The loaded cache entries.
            unchanged: Set of relative paths to restore from cache.
        """
        restored = 0
        for crate in workspace.crates.values():
            for idx, stub in enumerate(crate.files):
                if stub.file not in unchanged:
                    continue
                entry = cached.get(stub.file)
                if entry is None:
                    continue
                ast_data = entry.get("file_ast")
                if not isinstance(ast_data, dict):
                    continue
                crate.files[idx] = _dict_to_file_ast(ast_data)
                restored += 1

        logger.info("Restored %d file(s) from cache", restored)


# endregion: --- IncrementalCache


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def compute_file_hash(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file.

    Returns an empty string if the file is unreadable.
    """
    h = hashlib.new(_HASH_ALGORITHM)
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(_READ_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError as exc:
        logger.debug("Cannot hash %s: %s", path, exc)
        return ""
    return h.hexdigest()


class _EnumStr(str):
    """String subclass that also supports ``.value`` for enum compatibility.

    When AST nodes are restored from the JSON cache, enum fields
    (e.g. ``visibility``, ``confidence``) are plain strings. Wrapping
    them in ``_EnumStr`` lets consumer code use both ``s.visibility``
    (as a string) and ``s.visibility.value`` (enum-style) transparently.
    """

    __slots__ = ()

    @property
    def value(self) -> str:
        """Return the string itself, mimicking Enum.value."""
        return str(self)


def _safe_list(value: Any) -> list[Any]:  # noqa: ANN401
    """Validate that *value* is a list; return empty list otherwise.

    Nested dicts are converted to SimpleNamespace for attribute access.
    """
    if not isinstance(value, list):
        return []
    return [_dict_to_namespace(v) if isinstance(v, dict) else v for v in value]


def _safe_dict(value: Any) -> dict[str, Any]:  # noqa: ANN401
    """Validate that *value* is a dict; return empty dict otherwise.

    Nested dicts in values are converted to SimpleNamespace.
    """
    if not isinstance(value, dict):
        return {}
    return {
        k: _dict_to_namespace(v) if isinstance(v, dict) else v
        for k, v in value.items()
    }


def _dict_to_namespace(d: dict[str, Any]) -> Any:  # noqa: ANN401
    """Recursively convert a dict to SimpleNamespace for attribute access.

    String values are wrapped in ``_EnumStr`` so that enum-style
    ``.value`` access works transparently on cached data.
    """
    converted: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            converted[k] = _dict_to_namespace(v)
        elif isinstance(v, list):
            converted[k] = [
                _dict_to_namespace(i) if isinstance(i, dict) else
                (_EnumStr(i) if isinstance(i, str) else i)
                for i in v
            ]
        elif isinstance(v, str):
            converted[k] = _EnumStr(v)
        else:
            converted[k] = v
    return SimpleNamespace(**converted)


def _dict_to_file_ast(data: dict[str, Any]) -> FileAST:
    """Reconstruct a ``FileAST`` from a plain dict (cache JSON).

    Only maps the top-level scalar/list fields — nested dataclasses are
    left as dicts since ``dataclasses.asdict`` produced them and the
    JSON formatter / cross-ref indexer consume them equivalently.

    All collection fields are validated to be the expected container type.
    """
    return FileAST(
        file=str(data.get("file", "")),
        module_path=str(data.get("module_path", "")),
        is_test=bool(data.get("is_test", False)),
        uses=_safe_list(data.get("uses", [])),
        modules=_safe_list(data.get("modules", [])),
        structs=_safe_list(data.get("structs", [])),
        enums=_safe_list(data.get("enums", [])),
        traits=_safe_list(data.get("traits", [])),
        functions=_safe_list(data.get("functions", [])),
        impl_blocks=_safe_list(data.get("impl_blocks", [])),
        type_aliases=_safe_list(data.get("type_aliases", [])),
        constants=_safe_list(data.get("constants", [])),
        macros=_safe_list(data.get("macros", [])),
        self_methods=_safe_list(data.get("self_methods", [])),
        imported_package_methods=_safe_dict(
            data.get("imported_package_methods", {}),
        ),
        call_edges=_safe_list(data.get("call_edges", [])),
        rationale_comments=_safe_list(data.get("rationale_comments", [])),
        errors=_safe_list(data.get("errors", [])),
    )


def _namespace_to_dict(ns: SimpleNamespace) -> dict[str, Any]:
    """Recursively convert a SimpleNamespace back to a plain dict."""
    result: dict[str, Any] = {}
    for k, v in ns.__dict__.items():
        if isinstance(v, SimpleNamespace):
            result[k] = _namespace_to_dict(v)
        elif isinstance(v, list):
            result[k] = [
                _namespace_to_dict(i) if isinstance(i, SimpleNamespace) else i
                for i in v
            ]
        else:
            result[k] = v
    return result


# endregion: --- Helpers
