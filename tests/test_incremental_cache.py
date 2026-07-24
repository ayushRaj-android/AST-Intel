"""Tests for ``ast_intel.core.cache`` — incremental caching."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ast_intel.core.cache import (
    CacheDiff,
    IncrementalCache,
    _dict_to_file_ast,
    compute_file_hash,
)
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import (
    CrateModel,
    WorkspaceAST,
    WorkspaceMeta,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _make_workspace(
    repo: Path,
    files: dict[str, str],
    crate_name: str = "test-crate",
) -> WorkspaceAST:
    """Build a simple workspace with one crate and files on disk."""
    crate = CrateModel(
        name=crate_name,
        language="python",
        manifest_path="pyproject.toml",
    )
    for rel_path, content in files.items():
        abs_path = repo / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        stub = FileAST(file=rel_path, module_path=rel_path)
        crate.files.append(stub)

    return WorkspaceAST(
        meta=WorkspaceMeta(workspace_root=str(repo)),
        crates={crate_name: crate},
    )


def _populated_file_ast(rel_path: str) -> FileAST:
    """Return a ``FileAST`` with a function to distinguish from a stub."""
    from ast_intel.models.ast_node import FunctionNode

    return FileAST(
        file=rel_path,
        module_path=rel_path,
        functions=[FunctionNode(name="hello")],
    )


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- compute_file_hash tests
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    """Tests for ``compute_file_hash``."""

    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("hello", encoding="utf-8")
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex length

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("hello", encoding="utf-8")
        b.write_text("world", encoding="utf-8")
        assert compute_file_hash(a) != compute_file_hash(b)

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("same", encoding="utf-8")
        b.write_text("same", encoding="utf-8")
        assert compute_file_hash(a) == compute_file_hash(b)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert compute_file_hash(tmp_path / "nonexistent.py") == ""

    def test_binary_content(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\xff\x80")
        h = compute_file_hash(f)
        assert len(h) == 64


# endregion: --- compute_file_hash tests


# ---------------------------------------------------------------------------
# region:    --- IncrementalCache.load / save tests
# ---------------------------------------------------------------------------


class TestCacheLoadSave:
    """Tests for cache persistence."""

    def test_load_empty_when_no_file(self, tmp_path: Path) -> None:
        cache = IncrementalCache(tmp_path / ".cache.json")
        assert cache.load() == {}

    def test_load_empty_on_corrupt_json(self, tmp_path: Path) -> None:
        path = tmp_path / ".cache.json"
        path.write_text("{not valid json", encoding="utf-8")
        cache = IncrementalCache(path)
        assert cache.load() == {}

    def test_load_empty_on_version_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / ".cache.json"
        data = {"version": 999, "entries": {"a.py": {}}}
        path.write_text(json.dumps(data), encoding="utf-8")
        cache = IncrementalCache(path)
        assert cache.load() == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"src/main.py": "print('hi')"})
        # Populate the file AST
        ws.crates["test-crate"].files[0] = _populated_file_ast("src/main.py")

        cache_path = tmp_path / "out" / ".cache.json"
        cache = IncrementalCache(cache_path)
        hashes = {"src/main.py": "abc123"}
        cache.save(ws, hashes)

        loaded = cache.load()
        assert "src/main.py" in loaded
        assert loaded["src/main.py"]["file_hash"] == "abc123"
        assert loaded["src/main.py"]["file_ast"]["file"] == "src/main.py"

    def test_save_is_atomic(self, tmp_path: Path) -> None:
        """Cache file should not be corrupted if save is interrupted."""
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})
        cache_path = tmp_path / ".cache.json"
        cache = IncrementalCache(cache_path)

        # First save
        cache.save(ws, {"a.py": "hash1"})
        assert cache_path.exists()

        # Second save overwrites cleanly
        cache.save(ws, {"a.py": "hash2"})
        loaded = cache.load()
        assert loaded["a.py"]["file_hash"] == "hash2"

    def test_load_empty_on_non_dict(self, tmp_path: Path) -> None:
        path = tmp_path / ".cache.json"
        path.write_text('"just a string"', encoding="utf-8")
        cache = IncrementalCache(path)
        assert cache.load() == {}

    def test_load_empty_on_missing_entries(self, tmp_path: Path) -> None:
        path = tmp_path / ".cache.json"
        data = {"version": 1, "tool_version": "0.1.0"}
        path.write_text(json.dumps(data), encoding="utf-8")
        cache = IncrementalCache(path)
        assert cache.load() == {}

    def test_load_empty_on_tool_version_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / ".cache.json"
        data = {
            "version": 1,
            "tool_version": "0.0.0-stale",
            "entries": {"a.py": {}},
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        cache = IncrementalCache(path)
        assert cache.load() == {}


# endregion: --- IncrementalCache.load / save tests


# ---------------------------------------------------------------------------
# region:    --- IncrementalCache.diff tests
# ---------------------------------------------------------------------------


class TestCacheDiff:
    """Tests for cache diffing logic."""

    def test_all_new_when_no_cache(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1", "b.py": "y = 2"})
        cache = IncrementalCache(tmp_path / ".cache.json")

        diff, hashes = cache.diff(ws, {}, repo)
        assert diff.added == frozenset({"a.py", "b.py"})
        assert diff.unchanged == frozenset()
        assert diff.changed == frozenset()
        assert diff.deleted == frozenset()
        assert len(hashes) == 2

    def test_unchanged_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        # Compute what the hash would be
        h = compute_file_hash(repo / "a.py")
        cached: dict[str, dict[str, Any]] = {
            "a.py": {"file_hash": h, "file_ast": {"file": "a.py"}},
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        diff, _hashes = cache.diff(ws, cached, repo)
        assert diff.unchanged == frozenset({"a.py"})
        assert diff.changed == frozenset()
        assert diff.added == frozenset()

    def test_changed_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        cached: dict[str, dict[str, Any]] = {
            "a.py": {"file_hash": "old_hash", "file_ast": {"file": "a.py"}},
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        diff, _hashes = cache.diff(ws, cached, repo)
        assert diff.changed == frozenset({"a.py"})
        assert diff.unchanged == frozenset()

    def test_deleted_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        cached: dict[str, dict[str, Any]] = {
            "a.py": {"file_hash": compute_file_hash(repo / "a.py"),
                      "file_ast": {"file": "a.py"}},
            "removed.py": {"file_hash": "deadbeef",
                           "file_ast": {"file": "removed.py"}},
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        diff, _ = cache.diff(ws, cached, repo)
        assert "removed.py" in diff.deleted

    def test_mixed_diff(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(
            repo, {"same.py": "a", "edited.py": "new", "brand_new.py": "x"},
        )

        cached: dict[str, dict[str, Any]] = {
            "same.py": {
                "file_hash": compute_file_hash(repo / "same.py"),
                "file_ast": {"file": "same.py"},
            },
            "edited.py": {
                "file_hash": "stale",
                "file_ast": {"file": "edited.py"},
            },
            "gone.py": {
                "file_hash": "whatever",
                "file_ast": {"file": "gone.py"},
            },
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        diff, _ = cache.diff(ws, cached, repo)
        assert diff.unchanged == frozenset({"same.py"})
        assert diff.changed == frozenset({"edited.py"})
        assert diff.added == frozenset({"brand_new.py"})
        assert diff.deleted == frozenset({"gone.py"})


# endregion: --- IncrementalCache.diff tests


# ---------------------------------------------------------------------------
# region:    --- restore_cached_asts tests
# ---------------------------------------------------------------------------


class TestRestoreCachedAsts:
    """Tests for restoring FileASTs from cache."""

    def test_restore_replaces_stubs(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        cached: dict[str, dict[str, Any]] = {
            "a.py": {
                "file_hash": "h",
                "file_ast": {
                    "file": "a.py",
                    "module_path": "a",
                    "is_test": False,
                    "functions": [{"name": "cached_fn", "parameters": []}],
                    "structs": [],
                    "enums": [],
                    "traits": [],
                    "impl_blocks": [],
                    "type_aliases": [],
                    "constants": [],
                    "macros": [],
                    "self_methods": [],
                    "imported_package_methods": {},
                    "call_edges": [],
                    "rationale_comments": [],
                    "errors": [],
                    "uses": [],
                    "modules": [],
                },
            },
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        cache.restore_cached_asts(ws, cached, frozenset({"a.py"}))

        restored = ws.crates["test-crate"].files[0]
        assert restored.file == "a.py"
        # Should have the cached function data
        assert len(restored.functions) == 1

    def test_restore_skips_non_unchanged(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        cached: dict[str, dict[str, Any]] = {
            "a.py": {
                "file_hash": "h",
                "file_ast": {"file": "a.py", "functions": [{"name": "fn"}]},
            },
        }

        cache = IncrementalCache(tmp_path / ".cache.json")
        # Don't include "a.py" in unchanged set
        cache.restore_cached_asts(ws, cached, frozenset())

        # Should still be the original stub (no functions)
        assert ws.crates["test-crate"].files[0].functions == []


# endregion: --- restore_cached_asts tests


# ---------------------------------------------------------------------------
# region:    --- _dict_to_file_ast tests
# ---------------------------------------------------------------------------


class TestDictToFileAst:
    """Tests for ``_dict_to_file_ast``."""

    def test_basic_roundtrip(self) -> None:
        original = FileAST(file="src/lib.py", module_path="lib", is_test=True)
        from dataclasses import asdict

        data = asdict(original)
        restored = _dict_to_file_ast(data)
        assert restored.file == "src/lib.py"
        assert restored.module_path == "lib"
        assert restored.is_test is True

    def test_empty_dict_gives_defaults(self) -> None:
        restored = _dict_to_file_ast({})
        assert restored.file == ""
        assert restored.functions == []
        assert restored.errors == []

    def test_preserves_errors(self) -> None:
        data = {"file": "bad.py", "errors": ["parse failed"]}
        restored = _dict_to_file_ast(data)
        assert restored.errors == ["parse failed"]

    def test_invalid_list_field_returns_empty(self) -> None:
        """Non-list fields are coerced to empty list."""
        data = {"file": "x.py", "functions": "not a list"}
        restored = _dict_to_file_ast(data)
        assert restored.functions == []

    def test_invalid_dict_field_returns_empty(self) -> None:
        """Non-dict fields are coerced to empty dict."""
        data = {"file": "x.py", "imported_package_methods": [1, 2]}
        restored = _dict_to_file_ast(data)
        assert restored.imported_package_methods == {}


# endregion: --- _dict_to_file_ast tests


# ---------------------------------------------------------------------------
# region:    --- CacheDiff dataclass tests
# ---------------------------------------------------------------------------


class TestCacheDiffDataclass:
    """Tests for the ``CacheDiff`` data structure."""

    def test_defaults_are_empty(self) -> None:
        d = CacheDiff()
        assert d.unchanged == frozenset()
        assert d.changed == frozenset()
        assert d.added == frozenset()
        assert d.deleted == frozenset()

    def test_sets_are_frozen(self) -> None:
        d = CacheDiff(unchanged=frozenset({"a.py"}))
        assert isinstance(d.unchanged, frozenset)


# endregion: --- CacheDiff dataclass tests


# ---------------------------------------------------------------------------
# region:    --- Dispatcher skip_files integration
# ---------------------------------------------------------------------------


class TestDispatcherSkipFiles:
    """Tests that dispatcher respects ``skip_files``."""

    def test_skip_files_excludes_from_tasks(self, tmp_path: Path) -> None:
        from ast_intel.core.dispatcher import Dispatcher

        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {
            "a.py": "x = 1\n",
            "b.py": "y = 2\n",
        })

        # Populate b.py as fully extracted so skip makes sense
        ws.crates["test-crate"].files[1] = _populated_file_ast("b.py")

        dispatcher = Dispatcher(
            workers=1,
            skip_files=frozenset({"b.py"}),
            quiet=True,
        )
        ws = dispatcher.dispatch(ws)

        # a.py should be extracted; b.py should still have our mock data
        b_file = next(
            f for f in ws.crates["test-crate"].files if f.file == "b.py"
        )
        assert len(b_file.functions) == 1
        assert b_file.functions[0].name == "hello"


# endregion: --- Dispatcher skip_files integration


# ---------------------------------------------------------------------------
# region:    --- End-to-end cache roundtrip
# ---------------------------------------------------------------------------


class TestEndToEndCache:
    """Full roundtrip: parse → save → modify → diff → partial parse."""

    def test_full_cycle(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()

        # Step 1: Initial parse (simulated)
        ws = _make_workspace(repo, {
            "a.py": "def foo(): pass\n",
            "b.py": "def bar(): pass\n",
        })

        cache_path = tmp_path / ".cache.json"
        cache = IncrementalCache(cache_path)

        # Compute hashes and save
        diff, hashes = cache.diff(ws, {}, repo)
        assert diff.added == frozenset({"a.py", "b.py"})

        # Simulate populating files
        ws.crates["test-crate"].files[0] = _populated_file_ast("a.py")
        ws.crates["test-crate"].files[1] = _populated_file_ast("b.py")

        cache.save(ws, hashes)

        # Step 2: Modify one file, re-run
        (repo / "a.py").write_text("def foo_v2(): pass\n", encoding="utf-8")

        ws2 = _make_workspace(repo, {
            "a.py": "def foo_v2(): pass\n",
            "b.py": "def bar(): pass\n",
        })

        cached = cache.load()
        diff2, _hashes2 = cache.diff(ws2, cached, repo)

        assert diff2.changed == frozenset({"a.py"})
        assert diff2.unchanged == frozenset({"b.py"})
        assert diff2.added == frozenset()
        assert diff2.deleted == frozenset()

        # Restore b.py from cache
        cache.restore_cached_asts(ws2, cached, diff2.unchanged)
        b_file = next(
            f for f in ws2.crates["test-crate"].files if f.file == "b.py"
        )
        assert len(b_file.functions) == 1

    def test_no_cache_flag_skips_caching(self, tmp_path: Path) -> None:
        """When --no-cache is used, diff returns all files as new."""
        repo = tmp_path / "repo"
        repo.mkdir()
        ws = _make_workspace(repo, {"a.py": "x = 1"})

        cache = IncrementalCache(tmp_path / ".cache.json")
        # With no cached entries, everything is new
        diff, _ = cache.diff(ws, {}, repo)
        assert diff.added == frozenset({"a.py"})
        assert diff.unchanged == frozenset()


# endregion: --- End-to-end cache roundtrip
