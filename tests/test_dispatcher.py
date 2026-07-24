"""Tests for ``ast_intel.core.dispatcher`` — parallel file extraction."""

from __future__ import annotations

from pathlib import Path

from ast_intel.core.dispatcher import (
    Dispatcher,
    _build_extractor_registry,
    _detect_is_test,
)
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import (
    CrateModel,
    WorkspaceAST,
    WorkspaceMeta,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


def _make_workspace(
    tmp_path: Path,
    files: dict[str, str],
) -> WorkspaceAST:
    """Build a ``WorkspaceAST`` with one crate and given files on disk.

    Args:
        tmp_path: Temporary directory used as mock repo root.
        files: Mapping of relative paths → file content.

    Returns:
        A workspace with stub ``FileAST`` entries.
    """
    crate = CrateModel(
        name="test-crate",
        language="rust",
        manifest_path="Cargo.toml",
    )
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "test-crate"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    for rel_path, content in files.items():
        abs_path = tmp_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)
        stub = FileAST(
            file=rel_path,
            module_path=f"test-crate::{Path(rel_path).stem}",
        )
        crate.files.append(stub)

    meta = WorkspaceMeta(workspace_root=str(tmp_path))
    return WorkspaceAST(meta=meta, crates={"test-crate": crate})


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Registry Tests
# ---------------------------------------------------------------------------


class TestExtractorRegistry:
    """Tests for ``_build_extractor_registry``."""

    def test_rust_registered(self) -> None:
        registry = _build_extractor_registry()
        assert ".rs" in registry
        # Registry now holds classes, not instances
        assert registry[".rs"].language_id == "rust"

    def test_unsupported_extensions_absent(self) -> None:
        registry = _build_extractor_registry()
        assert ".xyz" not in registry


# endregion: --- Registry Tests


# ---------------------------------------------------------------------------
# region:    --- Is-Test Detection Tests
# ---------------------------------------------------------------------------


class TestDetectIsTest:
    """Tests for ``_detect_is_test``."""

    def test_extractor_flag_wins(self) -> None:
        assert _detect_is_test("src/lib.rs", extractor_flag=True) is True

    def test_tests_directory(self) -> None:
        assert _detect_is_test("tests/integration.rs", extractor_flag=False) is True

    def test_test_directory(self) -> None:
        assert _detect_is_test("test/unit.rs", extractor_flag=False) is True

    def test_test_prefix_filename(self) -> None:
        assert _detect_is_test("src/test_utils.py", extractor_flag=False) is True

    def test_test_suffix_filename(self) -> None:
        assert _detect_is_test("src/handler_test.rs", extractor_flag=False) is True

    def test_normal_file(self) -> None:
        assert _detect_is_test("src/handler.rs", extractor_flag=False) is False


# endregion: --- Is-Test Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Dispatcher Tests
# ---------------------------------------------------------------------------


class TestDispatcher:
    """Tests for ``Dispatcher.dispatch``."""

    def test_extracts_rust_files(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {
            "src/main.rs": 'fn main() { println!("hello"); }',
        })
        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        crate = result.crates["test-crate"]
        assert len(crate.files) == 1
        f = crate.files[0]
        assert f.functions or f.errors  # Extracted something

    def test_multiple_files(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {
            "src/lib.rs": "pub fn hello() -> u32 { 42 }",
            "src/util.rs": "pub fn add(a: u32, b: u32) -> u32 { a + b }",
        })
        dispatcher = Dispatcher(workers=2, quiet=True)
        result = dispatcher.dispatch(ws)
        crate = result.crates["test-crate"]
        all_fns = [fn.name for f in crate.files for fn in f.functions]
        assert "hello" in all_fns
        assert "add" in all_fns

    def test_workers_1_and_4_produce_same_output(self, tmp_path: Path) -> None:
        files = {
            "src/a.rs": "pub fn a() {}",
            "src/b.rs": "pub fn b() {}",
            "src/c.rs": "pub fn c() {}",
        }
        ws1 = _make_workspace(tmp_path, files)
        ws4 = _make_workspace(tmp_path, files)

        r1 = Dispatcher(workers=1, quiet=True).dispatch(ws1)
        r4 = Dispatcher(workers=4, quiet=True).dispatch(ws4)

        funcs1 = sorted(fn.name for c in r1.crates.values() for f in c.files for fn in f.functions)
        funcs4 = sorted(fn.name for c in r4.crates.values() for f in c.files for fn in f.functions)
        assert funcs1 == funcs4

    def test_corrupt_file_does_not_abort(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {
            "src/good.rs": "fn good() {}",
            "src/bad.rs": "this is not valid rust {{{{[[[[",
        })
        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        crate = result.crates["test-crate"]
        # Both files should be present
        assert len(crate.files) == 2
        good = next(f for f in crate.files if "good" in f.file)
        assert len(good.functions) == 1
        # bad.rs may have errors or partial output but run didn't abort
        assert True  # If we got here, dispatch didn't crash

    def test_missing_file_records_error(self, tmp_path: Path) -> None:
        crate = CrateModel(
            name="test-crate",
            language="rust",
            manifest_path="Cargo.toml",
        )
        crate.files.append(FileAST(file="nonexistent.rs", module_path="test-crate::nonexistent"))
        meta = WorkspaceMeta(workspace_root=str(tmp_path))
        ws = WorkspaceAST(meta=meta, crates={"test-crate": crate})

        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        f = result.crates["test-crate"].files[0]
        assert len(f.errors) > 0
        assert "Cannot read" in f.errors[0]

    def test_skip_methods_flag(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {
            "src/main.rs": (
                "use std::io::Write;\n"
                "fn main() {\n"
                "    let mut buf = Vec::new();\n"
                '    buf.write_all(b"hello").unwrap();\n'
                "}\n"
            ),
        })
        dispatcher = Dispatcher(workers=1, skip_methods=True, quiet=True)
        result = dispatcher.dispatch(ws)
        f = result.crates["test-crate"].files[0]
        assert f.imported_package_methods == {}

    def test_is_test_flag_set(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {
            "tests/integration.rs": "fn test_something() {}",
        })
        # Override module_path for the stub
        ws.crates["test-crate"].files[0].module_path = "test-crate::integration"
        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        f = result.crates["test-crate"].files[0]
        assert f.is_test is True

    def test_empty_workspace(self, tmp_path: Path) -> None:
        meta = WorkspaceMeta(workspace_root=str(tmp_path))
        ws = WorkspaceAST(meta=meta)
        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        assert len(result.crates) == 0

    def test_no_matching_extractor_skips_file(self, tmp_path: Path) -> None:
        crate = CrateModel(name="test", language="unknown", manifest_path="")
        txt = tmp_path / "readme.txt"
        txt.write_text("Hello")
        crate.files.append(FileAST(file="readme.txt"))
        meta = WorkspaceMeta(workspace_root=str(tmp_path))
        ws = WorkspaceAST(meta=meta, crates={"test": crate})
        dispatcher = Dispatcher(workers=1, quiet=True)
        result = dispatcher.dispatch(ws)
        f = result.crates["test"].files[0]
        assert f.file == "readme.txt"
        # No extraction happened — file remains as stub
        assert len(f.functions) == 0


# endregion: --- Dispatcher Tests
