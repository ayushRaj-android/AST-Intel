"""Tests for Feature 4 — Rationale Comment Extraction.

Covers:
- RationaleNode dataclass construction and immutability
- Shared extractor: prefix recognition, parent scope resolution, merge logic
- Per-language extraction via each extractor's extract() method
- JSON formatter serialization of rationale_comments
- Markdown formatter rendering of rationale section
- WorkspaceMeta total_rationale_comments statistic
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ast_intel.extractors._rationale import extract_rationale_comments
from ast_intel.models.ast_node import RationaleNode, Span

if TYPE_CHECKING:
    from ast_intel.models.ast_node import FileAST

# ---------------------------------------------------------------------------
# region:    --- Fixture Paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
RUST_RATIONALE = FIXTURES / "rust" / "rationale_comments.rs"
PYTHON_RATIONALE = FIXTURES / "python" / "rationale_comments.py"
TS_RATIONALE = FIXTURES / "typescript" / "rationale_comments.ts"
GO_RATIONALE = FIXTURES / "go" / "rationale_comments.go"
CS_RATIONALE = FIXTURES / "csharp" / "rationale_comments.cs"

# endregion: --- Fixture Paths


# ---------------------------------------------------------------------------
# region:    --- RationaleNode Dataclass Tests
# ---------------------------------------------------------------------------


class TestRationaleNode:
    """Core RationaleNode dataclass behavior."""

    def test_construction(self) -> None:
        span = Span(start_line=10, start_col=1, end_line=10, end_col=40)
        node = RationaleNode(kind="NOTE", text="important detail", span=span, parent="main")
        assert node.kind == "NOTE"
        assert node.text == "important detail"
        assert node.span == span
        assert node.parent == "main"

    def test_frozen(self) -> None:
        span = Span(start_line=1, start_col=1, end_line=1, end_col=10)
        node = RationaleNode(kind="TODO", text="fix this", span=span, parent="<file>")
        with pytest.raises(FrozenInstanceError):
            node.kind = "HACK"  # type: ignore[misc]

    def test_equality(self) -> None:
        span = Span(start_line=5, start_col=1, end_line=5, end_col=30)
        a = RationaleNode(kind="HACK", text="workaround", span=span, parent="foo")
        b = RationaleNode(kind="HACK", text="workaround", span=span, parent="foo")
        assert a == b

    def test_asdict(self) -> None:
        span = Span(start_line=1, start_col=1, end_line=1, end_col=20)
        node = RationaleNode(kind="WHY", text="reason", span=span, parent="bar")
        d = asdict(node)
        assert d["kind"] == "WHY"
        assert d["text"] == "reason"
        assert d["parent"] == "bar"
        assert d["span"]["start_line"] == 1

    def test_in_all_exports(self) -> None:
        from ast_intel.models import ast_node
        assert "RationaleNode" in ast_node.__all__


# endregion: --- RationaleNode Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- Shared Extractor Unit Tests
# ---------------------------------------------------------------------------


def _parse_and_extract(source: str, language: str) -> list[RationaleNode]:
    """Parse source string with tree-sitter and run rationale extraction."""
    import tree_sitter_c_sharp as tscsharp
    import tree_sitter_go as tsgo
    import tree_sitter_python as tspython
    import tree_sitter_rust as tsrust
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    lang_map = {
        "rust": Language(tsrust.language()),
        "python": Language(tspython.language()),
        "typescript": Language(tsts.language_typescript()),
        "go": Language(tsgo.language()),
        "csharp": Language(tscsharp.language()),
    }
    parser = Parser(lang_map[language])
    src = source.encode("utf-8")
    tree = parser.parse(src)
    return extract_rationale_comments(tree.root_node, src)


class TestSharedExtractor:
    """Tests for the shared _rationale.py extractor module."""

    @pytest.mark.parametrize(
        "prefix",
        ["NOTE", "HACK", "WHY", "TODO", "FIXME", "IMPORTANT", "SAFETY", "RATIONALE", "PERF"],
    )
    def test_all_prefixes_rust(self, prefix: str) -> None:
        """Every recognized prefix is extracted from Rust line comments."""
        source = f"// {prefix}: test body\nfn main() {{}}"
        results = _parse_and_extract(source, "rust")
        assert len(results) >= 1
        matched = [r for r in results if r.kind == prefix]
        assert matched, f"Prefix {prefix} not matched"
        assert matched[0].text == "test body"

    @pytest.mark.parametrize(
        "prefix",
        ["NOTE", "HACK", "WHY", "TODO", "FIXME", "IMPORTANT", "SAFETY", "RATIONALE", "PERF"],
    )
    def test_all_prefixes_python(self, prefix: str) -> None:
        """Every recognized prefix is extracted from Python hash comments."""
        source = f"# {prefix}: test body\ndef main(): pass"
        results = _parse_and_extract(source, "python")
        assert len(results) >= 1
        matched = [r for r in results if r.kind == prefix]
        assert matched, f"Prefix {prefix} not matched"
        assert matched[0].text == "test body"

    def test_case_insensitive(self) -> None:
        """Prefixes are matched case-insensitively."""
        source = "# note: lowercase works\ndef f(): pass"
        results = _parse_and_extract(source, "python")
        assert len(results) == 1
        assert results[0].kind == "NOTE"

    def test_block_comment_rust(self) -> None:
        """Block comments are recognized in Rust."""
        source = "/* IMPORTANT: block comment body */\nfn main() {}"
        results = _parse_and_extract(source, "rust")
        assert len(results) >= 1
        assert results[0].kind == "IMPORTANT"

    def test_no_match_for_regular_comments(self) -> None:
        """Regular comments without prefixes are not extracted."""
        source = "// this is a regular comment\nfn main() {}"
        results = _parse_and_extract(source, "rust")
        assert len(results) == 0

    def test_parent_scope_function(self) -> None:
        """Comment inside a function resolves parent to function name."""
        source = "fn process() {\n    // NOTE: inside function\n    let x = 1;\n}"
        results = _parse_and_extract(source, "rust")
        assert len(results) == 1
        assert results[0].parent == "process"

    def test_parent_scope_file_level(self) -> None:
        """File-level comment has parent '<file>'."""
        source = "// NOTE: file level comment\nconst X: i32 = 1;"
        results = _parse_and_extract(source, "rust")
        assert len(results) == 1
        assert results[0].parent == "<file>"

    def test_merge_consecutive(self) -> None:
        """Adjacent same-prefix comments are merged."""
        source = (
            "// WHY: first line\n"
            "// WHY: second line\n"
            "fn main() {}"
        )
        results = _parse_and_extract(source, "rust")
        why_nodes = [r for r in results if r.kind == "WHY"]
        assert len(why_nodes) == 1
        assert "first line" in why_nodes[0].text
        assert "second line" in why_nodes[0].text

    def test_no_merge_different_prefix(self) -> None:
        """Adjacent comments with different prefixes stay separate."""
        source = (
            "// NOTE: a note\n"
            "// TODO: a todo\n"
            "fn main() {}"
        )
        results = _parse_and_extract(source, "rust")
        assert len(results) == 2

    def test_span_populated(self) -> None:
        """Extracted nodes have non-None spans with valid line numbers."""
        source = "// TODO: check this\nfn main() {}"
        results = _parse_and_extract(source, "rust")
        assert len(results) == 1
        assert results[0].span.start_line >= 1

    def test_empty_source(self) -> None:
        """Empty source produces no rationale nodes."""
        results = _parse_and_extract("", "rust")
        assert results == []


# endregion: --- Shared Extractor Unit Tests


# ---------------------------------------------------------------------------
# region:    --- Rust Extractor Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def rust_ext() -> object:
    from ast_intel.extractors.rust import RustExtractor
    return RustExtractor()


class TestRustRationale:
    """Rust extractor populates rationale_comments from fixture."""

    def test_extracts_rationale(self, rust_ext: object) -> None:
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        ast = rust_ext.extract(RUST_RATIONALE, RUST_RATIONALE.read_bytes())
        assert len(ast.rationale_comments) > 0

    def test_all_prefix_kinds_found(self, rust_ext: object) -> None:
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        ast = rust_ext.extract(RUST_RATIONALE, RUST_RATIONALE.read_bytes())
        kinds = {r.kind for r in ast.rationale_comments}
        expected = {
            "NOTE", "TODO", "HACK", "WHY", "SAFETY",
            "FIXME", "IMPORTANT", "PERF", "RATIONALE",
        }
        assert expected == kinds

    def test_block_comment_extracted(self, rust_ext: object) -> None:
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        ast = rust_ext.extract(RUST_RATIONALE, RUST_RATIONALE.read_bytes())
        important = [r for r in ast.rationale_comments if r.kind == "IMPORTANT"]
        assert len(important) >= 1

    def test_consecutive_merged(self, rust_ext: object) -> None:
        """The two consecutive WHY: lines are merged into one node."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        ast = rust_ext.extract(RUST_RATIONALE, RUST_RATIONALE.read_bytes())
        why = [r for r in ast.rationale_comments if r.kind == "WHY"]
        assert len(why) == 1
        assert "clone" in why[0].text.lower() or "borrow" in why[0].text.lower()

    def test_parent_scope_resolved(self, rust_ext: object) -> None:
        """Comments inside functions resolve to function name."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        ast = rust_ext.extract(RUST_RATIONALE, RUST_RATIONALE.read_bytes())
        hack = [r for r in ast.rationale_comments if r.kind == "HACK"]
        assert len(hack) >= 1
        assert hack[0].parent != "<file>"


# endregion: --- Rust Extractor Integration


# ---------------------------------------------------------------------------
# region:    --- Python Extractor Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def python_ext() -> object:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor()


class TestPythonRationale:
    """Python extractor populates rationale_comments from fixture."""

    def test_extracts_rationale(self, python_ext: object) -> None:
        from ast_intel.extractors.python import PythonExtractor
        assert isinstance(python_ext, PythonExtractor)
        ast = python_ext.extract(PYTHON_RATIONALE, PYTHON_RATIONALE.read_bytes())
        assert len(ast.rationale_comments) > 0

    def test_all_prefix_kinds_found(self, python_ext: object) -> None:
        from ast_intel.extractors.python import PythonExtractor
        assert isinstance(python_ext, PythonExtractor)
        ast = python_ext.extract(PYTHON_RATIONALE, PYTHON_RATIONALE.read_bytes())
        kinds = {r.kind for r in ast.rationale_comments}
        expected = {
            "NOTE", "TODO", "HACK", "WHY", "FIXME",
            "IMPORTANT", "PERF", "SAFETY", "RATIONALE",
        }
        assert expected == kinds

    def test_hash_comment_prefix(self, python_ext: object) -> None:
        """Python uses # comments — verify the pattern matches."""
        from ast_intel.extractors.python import PythonExtractor
        assert isinstance(python_ext, PythonExtractor)
        ast = python_ext.extract(PYTHON_RATIONALE, PYTHON_RATIONALE.read_bytes())
        notes = [r for r in ast.rationale_comments if r.kind == "NOTE"]
        assert len(notes) >= 1


# endregion: --- Python Extractor Integration


# ---------------------------------------------------------------------------
# region:    --- TypeScript Extractor Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def ts_ext() -> object:
    from ast_intel.extractors.typescript import TypeScriptExtractor
    return TypeScriptExtractor()


class TestTypeScriptRationale:
    """TypeScript extractor populates rationale_comments from fixture."""

    def test_extracts_rationale(self, ts_ext: object) -> None:
        from ast_intel.extractors.typescript import TypeScriptExtractor
        assert isinstance(ts_ext, TypeScriptExtractor)
        ast = ts_ext.extract(TS_RATIONALE, TS_RATIONALE.read_bytes())
        assert len(ast.rationale_comments) > 0

    def test_all_prefix_kinds_found(self, ts_ext: object) -> None:
        from ast_intel.extractors.typescript import TypeScriptExtractor
        assert isinstance(ts_ext, TypeScriptExtractor)
        ast = ts_ext.extract(TS_RATIONALE, TS_RATIONALE.read_bytes())
        kinds = {r.kind for r in ast.rationale_comments}
        expected = {
            "NOTE", "TODO", "HACK", "WHY", "FIXME",
            "IMPORTANT", "PERF", "SAFETY", "RATIONALE",
        }
        assert expected == kinds


# endregion: --- TypeScript Extractor Integration


# ---------------------------------------------------------------------------
# region:    --- Go Extractor Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def go_ext() -> object:
    from ast_intel.extractors.go import GoExtractor
    return GoExtractor()


class TestGoRationale:
    """Go extractor populates rationale_comments from fixture."""

    def test_extracts_rationale(self, go_ext: object) -> None:
        from ast_intel.extractors.go import GoExtractor
        assert isinstance(go_ext, GoExtractor)
        ast = go_ext.extract(GO_RATIONALE, GO_RATIONALE.read_bytes())
        assert len(ast.rationale_comments) > 0

    def test_all_prefix_kinds_found(self, go_ext: object) -> None:
        from ast_intel.extractors.go import GoExtractor
        assert isinstance(go_ext, GoExtractor)
        ast = go_ext.extract(GO_RATIONALE, GO_RATIONALE.read_bytes())
        kinds = {r.kind for r in ast.rationale_comments}
        expected = {
            "NOTE", "TODO", "HACK", "WHY", "FIXME",
            "IMPORTANT", "PERF", "SAFETY", "RATIONALE",
        }
        assert expected == kinds


# endregion: --- Go Extractor Integration


# ---------------------------------------------------------------------------
# region:    --- C# Extractor Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def csharp_ext() -> object:
    from ast_intel.extractors.csharp import CSharpExtractor
    return CSharpExtractor()


class TestCSharpRationale:
    """C# extractor populates rationale_comments from fixture."""

    def test_extracts_rationale(self, csharp_ext: object) -> None:
        from ast_intel.extractors.csharp import CSharpExtractor
        assert isinstance(csharp_ext, CSharpExtractor)
        ast = csharp_ext.extract(CS_RATIONALE, CS_RATIONALE.read_bytes())
        assert len(ast.rationale_comments) > 0

    def test_all_prefix_kinds_found(self, csharp_ext: object) -> None:
        from ast_intel.extractors.csharp import CSharpExtractor
        assert isinstance(csharp_ext, CSharpExtractor)
        ast = csharp_ext.extract(CS_RATIONALE, CS_RATIONALE.read_bytes())
        kinds = {r.kind for r in ast.rationale_comments}
        expected = {
            "NOTE", "TODO", "HACK", "WHY", "FIXME",
            "IMPORTANT", "PERF", "SAFETY", "RATIONALE",
        }
        assert expected == kinds


# endregion: --- C# Extractor Integration


# ---------------------------------------------------------------------------
# region:    --- Formatter Tests
# ---------------------------------------------------------------------------


def _make_file_ast_with_rationale() -> FileAST:
    """Create a minimal FileAST with rationale_comments populated."""
    from ast_intel.models.ast_node import FileAST

    return FileAST(
        file="test.rs",
        rationale_comments=[
            RationaleNode(
                kind="NOTE",
                text="important detail",
                span=Span(start_line=5, start_col=1, end_line=5, end_col=30),
                parent="main",
            ),
            RationaleNode(
                kind="TODO",
                text="fix this later",
                span=Span(start_line=10, start_col=1, end_line=10, end_col=25),
                parent="<file>",
            ),
        ],
    )


class TestJsonFormatterRationale:
    """JSON formatter serializes rationale_comments correctly."""

    def test_rationale_in_asdict(self) -> None:
        """FileAST.rationale_comments appears in asdict output."""
        ast = _make_file_ast_with_rationale()
        d = asdict(ast)
        assert "rationale_comments" in d
        assert len(d["rationale_comments"]) == 2
        assert d["rationale_comments"][0]["kind"] == "NOTE"

    def test_total_rationale_comments_in_meta(self) -> None:
        """WorkspaceMeta.total_rationale_comments is computed by _build_meta."""
        from ast_intel.formatters.json_formatter import _build_meta
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_ast = _make_file_ast_with_rationale()
        crate = CrateModel(files=[file_ast])
        workspace = WorkspaceAST(crates={"test": crate})

        meta = _build_meta(workspace)
        assert meta.total_rationale_comments == 2


class TestMarkdownFormatterRationale:
    """Markdown formatter renders rationale section."""

    def test_rationale_section_rendered(self) -> None:
        """The Markdown output contains a Rationale Comments section."""
        from ast_intel.formatters.markdown_formatter import MarkdownFormatter
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_ast = _make_file_ast_with_rationale()
        crate = CrateModel(files=[file_ast])
        workspace = WorkspaceAST(crates={"test": crate})

        formatter = MarkdownFormatter()
        lines = formatter._render(workspace)
        text = "\n".join(lines)

        assert "#### Rationale Comments" in text
        assert "NOTE" in text
        assert "important detail" in text

    def test_rationale_count_in_overview(self) -> None:
        """The overview table includes 'Rationale Comments' row."""
        from ast_intel.formatters.markdown_formatter import MarkdownFormatter
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_ast = _make_file_ast_with_rationale()
        crate = CrateModel(files=[file_ast])
        workspace = WorkspaceAST(crates={"test": crate})

        formatter = MarkdownFormatter()
        lines = formatter._render(workspace)
        text = "\n".join(lines)

        assert "Rationale Comments" in text


# endregion: --- Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- FileAST Default Tests
# ---------------------------------------------------------------------------


class TestFileASTRationaleField:
    """FileAST.rationale_comments field has correct defaults."""

    def test_default_empty(self) -> None:
        from ast_intel.models.ast_node import FileAST
        ast = FileAST(file="x.rs")
        assert ast.rationale_comments == []

    def test_existing_extractors_still_work(self, rust_ext: object) -> None:
        """Extracting a fixture without rationale comments still succeeds."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = Path(__file__).parent / "fixtures" / "rust" / "struct_with_fields.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        # Should succeed — rationale_comments may be empty or not
        assert isinstance(ast.rationale_comments, list)


# endregion: --- FileAST Default Tests
