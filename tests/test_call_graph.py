"""Tests for Feature 2 — Intra-File Call-Graph Extraction.

Covers:
- CallEdge dataclass construction, immutability, and serialization
- Shared call-graph extractor: plain calls, member calls, resolution
- Per-language extraction via each extractor's extract() method
- Cross-file call resolution in the indexer (Pass 6)
- JSON formatter serialization of call_edges and total_call_edges
- Markdown formatter rendering of call-graph section
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ast_intel.models.ast_node import CallEdge, Span

if TYPE_CHECKING:
    from ast_intel.models.ast_node import FileAST

# ---------------------------------------------------------------------------
# region:    --- Fixture Paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
RUST_CALL_GRAPH = FIXTURES / "rust" / "call_graph.rs"
PYTHON_CALL_GRAPH = FIXTURES / "python" / "call_graph.py"
TS_CALL_GRAPH = FIXTURES / "typescript" / "call_graph.ts"
GO_CALL_GRAPH = FIXTURES / "go" / "call_graph.go"
CS_CALL_GRAPH = FIXTURES / "csharp" / "call_graph.cs"

# endregion: --- Fixture Paths


# ---------------------------------------------------------------------------
# region:    --- CallEdge Dataclass Tests
# ---------------------------------------------------------------------------


class TestCallEdge:
    """Core CallEdge dataclass behavior."""

    def test_construction(self) -> None:
        span = Span(start_line=10, start_col=5, end_line=10, end_col=20)
        edge = CallEdge(
            caller="main",
            callee="helper",
            call_site=span,
            resolved_target="helper",
            is_method_call=False,
        )
        assert edge.caller == "main"
        assert edge.callee == "helper"
        assert edge.call_site == span
        assert edge.resolved_target == "helper"
        assert edge.is_method_call is False

    def test_defaults(self) -> None:
        span = Span(start_line=1, start_col=1, end_line=1, end_col=10)
        edge = CallEdge(caller="f", callee="g", call_site=span)
        assert edge.resolved_target == ""
        assert edge.is_method_call is False

    def test_frozen(self) -> None:
        span = Span(start_line=1, start_col=1, end_line=1, end_col=10)
        edge = CallEdge(caller="f", callee="g", call_site=span)
        with pytest.raises(FrozenInstanceError):
            edge.caller = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        span = Span(start_line=5, start_col=1, end_line=5, end_col=15)
        e1 = CallEdge(caller="a", callee="b", call_site=span)
        e2 = CallEdge(caller="a", callee="b", call_site=span)
        assert e1 == e2

    def test_asdict(self) -> None:
        span = Span(start_line=10, start_col=1, end_line=10, end_col=15)
        edge = CallEdge(
            caller="process",
            callee="validate",
            call_site=span,
            resolved_target="validate",
            is_method_call=False,
        )
        d = asdict(edge)
        assert d["caller"] == "process"
        assert d["callee"] == "validate"
        assert d["resolved_target"] == "validate"
        assert d["is_method_call"] is False
        assert d["call_site"]["start_line"] == 10

    def test_in_all_exports(self) -> None:
        from ast_intel.models import ast_node

        assert "CallEdge" in ast_node.__all__


# endregion: --- CallEdge Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- Helper to Extract a Fixture
# ---------------------------------------------------------------------------


def _extract_rust(fixture: Path) -> FileAST:
    from ast_intel.extractors.rust import RustExtractor

    return RustExtractor().extract(fixture, fixture.read_bytes())


def _extract_python(fixture: Path) -> FileAST:
    from ast_intel.extractors.python import PythonExtractor

    return PythonExtractor().extract(fixture, fixture.read_bytes())


def _extract_ts(fixture: Path) -> FileAST:
    from ast_intel.extractors.typescript import TypeScriptExtractor

    return TypeScriptExtractor().extract(fixture, fixture.read_bytes())


def _extract_go(fixture: Path) -> FileAST:
    from ast_intel.extractors.go import GoExtractor

    return GoExtractor().extract(fixture, fixture.read_bytes())


def _extract_csharp(fixture: Path) -> FileAST:
    from ast_intel.extractors.csharp import CSharpExtractor

    return CSharpExtractor().extract(fixture, fixture.read_bytes())


# endregion: --- Helper to Extract a Fixture


# ---------------------------------------------------------------------------
# region:    --- Shared Extractor Unit Tests
# ---------------------------------------------------------------------------


class TestSharedExtractor:
    """Tests for _call_graph.py extract_call_edges and collect_defined_names."""

    def test_collect_defined_names_from_functions(self) -> None:
        from ast_intel.extractors._call_graph import collect_defined_names

        ast = _extract_python(PYTHON_CALL_GRAPH)
        names = collect_defined_names(ast)
        assert "helper" in names
        assert "validate" in names
        assert "process" in names
        assert "transform" in names
        assert "orchestrate" in names
        assert "use_calculator" in names

    def test_collect_defined_names_includes_methods(self) -> None:
        from ast_intel.extractors._call_graph import collect_defined_names

        ast = _extract_python(PYTHON_CALL_GRAPH)
        names = collect_defined_names(ast)
        # self_methods include class methods
        assert "add" in names or "__init__" in names

    def test_extract_returns_list(self) -> None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        from ast_intel.extractors._call_graph import (
            extract_call_edges,
        )

        parser = Parser(Language(tspython.language()))
        source = b"def foo():\n    bar()\n"
        tree = parser.parse(source)
        edges = extract_call_edges(tree.root_node, source, frozenset())
        assert isinstance(edges, list)

    def test_plain_call_resolved(self) -> None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        from ast_intel.extractors._call_graph import extract_call_edges

        parser = Parser(Language(tspython.language()))
        source = b"def helper():\n    pass\n\ndef main():\n    helper()\n"
        tree = parser.parse(source)
        edges = extract_call_edges(
            tree.root_node, source, frozenset({"helper"}),
        )
        assert len(edges) >= 1
        resolved = [e for e in edges if e.resolved_target == "helper"]
        assert len(resolved) == 1
        assert resolved[0].callee == "helper"
        assert resolved[0].is_method_call is False

    def test_member_call_unresolved(self) -> None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        from ast_intel.extractors._call_graph import extract_call_edges

        parser = Parser(Language(tspython.language()))
        source = b"def main():\n    obj.method()\n"
        tree = parser.parse(source)
        edges = extract_call_edges(tree.root_node, source, frozenset())
        method_edges = [e for e in edges if e.is_method_call]
        assert len(method_edges) >= 1
        assert method_edges[0].resolved_target == ""

    def test_edges_sorted_by_line(self) -> None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        from ast_intel.extractors._call_graph import extract_call_edges

        parser = Parser(Language(tspython.language()))
        source = (
            b"def a():\n    pass\n"
            b"def b():\n    pass\n"
            b"def main():\n    b()\n    a()\n"
        )
        tree = parser.parse(source)
        edges = extract_call_edges(
            tree.root_node, source, frozenset({"a", "b"}),
        )
        lines = [e.call_site.start_line for e in edges]
        assert lines == sorted(lines)


# endregion: --- Shared Extractor Unit Tests


# ---------------------------------------------------------------------------
# region:    --- Rust Call-Graph Tests
# ---------------------------------------------------------------------------


class TestRustCallGraph:
    """Rust extractor populates call_edges from fixture."""

    def test_call_edges_populated(self) -> None:
        ast = _extract_rust(RUST_CALL_GRAPH)
        assert len(ast.call_edges) > 0

    def test_intra_file_resolved(self) -> None:
        ast = _extract_rust(RUST_CALL_GRAPH)
        resolved = [e for e in ast.call_edges if e.resolved_target]
        assert len(resolved) >= 1, "At least one call should be resolved"
        callee_names = {e.callee for e in resolved}
        # process calls validate and helper
        assert "validate" in callee_names or "helper" in callee_names

    def test_method_calls_exist(self) -> None:
        ast = _extract_rust(RUST_CALL_GRAPH)
        method_edges = [e for e in ast.call_edges if e.is_method_call]
        assert len(method_edges) >= 1, "Should have method call edges"

    def test_callers_have_names(self) -> None:
        ast = _extract_rust(RUST_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.caller, "Every edge must have a caller"
            assert edge.callee, "Every edge must have a callee"

    def test_call_site_spans_populated(self) -> None:
        ast = _extract_rust(RUST_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.call_site.start_line > 0
            assert edge.call_site.start_col >= 0


# endregion: --- Rust Call-Graph Tests


# ---------------------------------------------------------------------------
# region:    --- Python Call-Graph Tests
# ---------------------------------------------------------------------------


class TestPythonCallGraph:
    """Python extractor populates call_edges from fixture."""

    def test_call_edges_populated(self) -> None:
        ast = _extract_python(PYTHON_CALL_GRAPH)
        assert len(ast.call_edges) > 0

    def test_intra_file_calls_resolved(self) -> None:
        ast = _extract_python(PYTHON_CALL_GRAPH)
        resolved = [e for e in ast.call_edges if e.resolved_target]
        callee_names = {e.callee for e in resolved}
        # orchestrate calls process and transform
        assert "process" in callee_names or "validate" in callee_names

    def test_method_calls_captured(self) -> None:
        ast = _extract_python(PYTHON_CALL_GRAPH)
        method_edges = [e for e in ast.call_edges if e.is_method_call]
        assert len(method_edges) >= 1

    def test_all_edges_have_spans(self) -> None:
        ast = _extract_python(PYTHON_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.call_site.start_line > 0

    def test_orchestrate_calls(self) -> None:
        """Verify orchestrate calls process and transform."""
        ast = _extract_python(PYTHON_CALL_GRAPH)
        orch_edges = [e for e in ast.call_edges if "orchestrate" in e.caller]
        callee_names = {e.callee for e in orch_edges}
        assert "process" in callee_names
        assert "transform" in callee_names


# endregion: --- Python Call-Graph Tests


# ---------------------------------------------------------------------------
# region:    --- TypeScript Call-Graph Tests
# ---------------------------------------------------------------------------


class TestTypeScriptCallGraph:
    """TypeScript extractor populates call_edges from fixture."""

    def test_call_edges_populated(self) -> None:
        ast = _extract_ts(TS_CALL_GRAPH)
        assert len(ast.call_edges) > 0

    def test_intra_file_calls_resolved(self) -> None:
        ast = _extract_ts(TS_CALL_GRAPH)
        resolved = [e for e in ast.call_edges if e.resolved_target]
        assert len(resolved) >= 1

    def test_method_calls_captured(self) -> None:
        ast = _extract_ts(TS_CALL_GRAPH)
        method_edges = [e for e in ast.call_edges if e.is_method_call]
        assert len(method_edges) >= 1

    def test_all_edges_have_spans(self) -> None:
        ast = _extract_ts(TS_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.call_site.start_line > 0


# endregion: --- TypeScript Call-Graph Tests


# ---------------------------------------------------------------------------
# region:    --- Go Call-Graph Tests
# ---------------------------------------------------------------------------


class TestGoCallGraph:
    """Go extractor populates call_edges from fixture."""

    def test_call_edges_populated(self) -> None:
        ast = _extract_go(GO_CALL_GRAPH)
        assert len(ast.call_edges) > 0

    def test_intra_file_calls_resolved(self) -> None:
        ast = _extract_go(GO_CALL_GRAPH)
        resolved = [e for e in ast.call_edges if e.resolved_target]
        assert len(resolved) >= 1

    def test_method_calls_captured(self) -> None:
        ast = _extract_go(GO_CALL_GRAPH)
        method_edges = [e for e in ast.call_edges if e.is_method_call]
        assert len(method_edges) >= 1

    def test_all_edges_have_spans(self) -> None:
        ast = _extract_go(GO_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.call_site.start_line > 0


# endregion: --- Go Call-Graph Tests


# ---------------------------------------------------------------------------
# region:    --- C# Call-Graph Tests
# ---------------------------------------------------------------------------


class TestCSharpCallGraph:
    """C# extractor populates call_edges from fixture."""

    def test_call_edges_populated(self) -> None:
        ast = _extract_csharp(CS_CALL_GRAPH)
        assert len(ast.call_edges) > 0

    def test_intra_file_calls_resolved(self) -> None:
        ast = _extract_csharp(CS_CALL_GRAPH)
        resolved = [e for e in ast.call_edges if e.resolved_target]
        assert len(resolved) >= 1

    def test_method_calls_captured(self) -> None:
        ast = _extract_csharp(CS_CALL_GRAPH)
        method_edges = [e for e in ast.call_edges if e.is_method_call]
        assert len(method_edges) >= 1

    def test_all_edges_have_spans(self) -> None:
        ast = _extract_csharp(CS_CALL_GRAPH)
        for edge in ast.call_edges:
            assert edge.call_site.start_line > 0


# endregion: --- C# Call-Graph Tests


# ---------------------------------------------------------------------------
# region:    --- Indexer Cross-File Resolution Tests
# ---------------------------------------------------------------------------


class TestIndexerCrossFileResolution:
    """Indexer Pass 6 resolves unresolved call edges across files."""

    def test_unresolved_call_gets_resolved(self) -> None:
        """Build a workspace with two files; a call in file A to function
        defined in file B should be resolved by the indexer."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import (
            CallEdge,
            FileAST,
            FunctionNode,
            MethodNode,
            Span,
        )
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        # File A: calls "helper" but doesn't define it
        file_a = FileAST(file="src/main.py", module_path="crate::main")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="helper",
                call_site=Span(start_line=5, start_col=4, end_line=5, end_col=12),
                resolved_target="",
                is_method_call=False,
            ),
        ]
        _span_main = Span(
            start_line=1, start_col=0, end_line=10, end_col=0,
        )
        file_a.functions = [FunctionNode(name="main", span=_span_main)]
        file_a.self_methods = [
            MethodNode(name="main", span=_span_main, context="free"),
        ]

        # File B: defines "helper"
        file_b = FileAST(file="src/utils.py", module_path="crate::utils")
        _span_helper = Span(
            start_line=1, start_col=0, end_line=3, end_col=0,
        )
        file_b.functions = [
            FunctionNode(name="helper", span=_span_helper),
        ]
        file_b.self_methods = [
            MethodNode(
                name="helper", span=_span_helper, context="free",
            ),
        ]

        crate = CrateModel(name="test_crate", files=[file_a, file_b])
        ws = WorkspaceAST(crates={"test_crate": crate})

        indexer = Indexer()
        indexer.build_cross_references(ws)

        # Now file_a's unresolved "helper" should be resolved
        resolved_edges = [
            e for e in file_a.call_edges if e.resolved_target
        ]
        assert len(resolved_edges) == 1
        assert "helper" in resolved_edges[0].resolved_target

    def test_ambiguous_call_stays_unresolved(self) -> None:
        """If multiple files define the same function name, the call stays
        unresolved (ambiguous)."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import (
            CallEdge,
            FileAST,
            MethodNode,
            Span,
        )
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = FileAST(file="src/main.py", module_path="crate::main")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="helper",
                call_site=Span(start_line=5, start_col=4, end_line=5, end_col=12),
                resolved_target="",
                is_method_call=False,
            ),
        ]

        # Two files define "helper" — ambiguous
        _span = Span(start_line=1, start_col=0, end_line=3, end_col=0)
        file_b = FileAST(file="src/utils.py", module_path="crate::utils")
        file_b.self_methods = [
            MethodNode(name="helper", span=_span, context="free"),
        ]

        file_c = FileAST(file="src/other.py", module_path="crate::other")
        file_c.self_methods = [
            MethodNode(name="helper", span=_span, context="free"),
        ]

        crate = CrateModel(name="test_crate", files=[file_a, file_b, file_c])
        ws = WorkspaceAST(crates={"test_crate": crate})

        indexer = Indexer()
        indexer.build_cross_references(ws)

        # Call should stay unresolved
        for edge in file_a.call_edges:
            assert edge.resolved_target == ""

    def test_method_calls_not_resolved_cross_file(self) -> None:
        """Method calls (is_method_call=True) are not resolved by Pass 6."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import (
            CallEdge,
            FileAST,
            MethodNode,
            Span,
        )
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = FileAST(file="src/main.py", module_path="crate::main")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="do_thing",
                call_site=Span(start_line=5, start_col=4, end_line=5, end_col=20),
                resolved_target="",
                is_method_call=True,  # method call — skipped
            ),
        ]

        _span = Span(start_line=1, start_col=0, end_line=3, end_col=0)
        file_b = FileAST(file="src/utils.py", module_path="crate::utils")
        file_b.self_methods = [
            MethodNode(
                name="do_thing", span=_span, context="free",
            ),
        ]

        crate = CrateModel(name="test_crate", files=[file_a, file_b])
        ws = WorkspaceAST(crates={"test_crate": crate})

        indexer = Indexer()
        indexer.build_cross_references(ws)

        # Method call should stay unresolved
        assert file_a.call_edges[0].resolved_target == ""

    def test_already_resolved_not_overwritten(self) -> None:
        """Edges already resolved by the extractor are not touched."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import CallEdge, FileAST, Span
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = FileAST(file="src/main.py", module_path="crate::main")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="local_fn",
                call_site=Span(start_line=5, start_col=4, end_line=5, end_col=15),
                resolved_target="local_fn",  # Already resolved
                is_method_call=False,
            ),
        ]

        crate = CrateModel(name="test_crate", files=[file_a])
        ws = WorkspaceAST(crates={"test_crate": crate})

        indexer = Indexer()
        indexer.build_cross_references(ws)

        assert file_a.call_edges[0].resolved_target == "local_fn"


# endregion: --- Indexer Cross-File Resolution Tests


# ---------------------------------------------------------------------------
# region:    --- JSON Formatter Tests
# ---------------------------------------------------------------------------


class TestJsonFormatterCallEdges:
    """JSON formatter includes call_edges in output."""

    def test_call_edges_in_json(self, tmp_path: Path) -> None:
        import json

        from ast_intel.formatters.json_formatter import JsonFormatter
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = _extract_python(PYTHON_CALL_GRAPH)
        crate = CrateModel(name="test", files=[file_a])
        ws = WorkspaceAST(crates={"test": crate})

        out = tmp_path / "ast.json"
        JsonFormatter().write(ws, out)

        data = json.loads(out.read_text())
        # call_edges should be in the file data
        files = data["crates"]["test"]["files"]
        assert len(files) == 1
        assert "call_edges" in files[0]
        assert len(files[0]["call_edges"]) > 0

    def test_total_call_edges_in_meta(self, tmp_path: Path) -> None:
        import json

        from ast_intel.formatters.json_formatter import JsonFormatter
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = _extract_python(PYTHON_CALL_GRAPH)
        crate = CrateModel(name="test", files=[file_a])
        ws = WorkspaceAST(crates={"test": crate})

        out = tmp_path / "ast.json"
        JsonFormatter().write(ws, out)

        data = json.loads(out.read_text())
        assert "total_call_edges" in data["meta"]
        assert data["meta"]["total_call_edges"] > 0


# endregion: --- JSON Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- Markdown Formatter Tests
# ---------------------------------------------------------------------------


class TestMarkdownFormatterCallGraph:
    """Markdown formatter includes call-graph section."""

    def test_call_graph_section_rendered(self, tmp_path: Path) -> None:
        from ast_intel.formatters.markdown_formatter import MarkdownFormatter
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = _extract_python(PYTHON_CALL_GRAPH)
        crate = CrateModel(name="test", files=[file_a])
        ws = WorkspaceAST(crates={"test": crate})

        out = tmp_path / "summary.md"
        MarkdownFormatter().write(ws, out)

        content = out.read_text()
        assert "#### Call Graph" in content
        assert "| Caller |" in content

    def test_call_edges_row_in_overview(self, tmp_path: Path) -> None:
        from ast_intel.formatters.markdown_formatter import MarkdownFormatter
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = _extract_python(PYTHON_CALL_GRAPH)
        crate = CrateModel(name="test", files=[file_a])
        ws = WorkspaceAST(crates={"test": crate})

        out = tmp_path / "summary.md"
        MarkdownFormatter().write(ws, out)

        content = out.read_text()
        assert "| Call Edges |" in content

    def test_no_call_graph_section_when_empty(self, tmp_path: Path) -> None:
        from ast_intel.formatters.markdown_formatter import MarkdownFormatter
        from ast_intel.models.ast_node import FileAST
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
        )

        file_a = FileAST(file="empty.py")
        crate = CrateModel(name="test", files=[file_a])
        ws = WorkspaceAST(crates={"test": crate})

        out = tmp_path / "summary.md"
        MarkdownFormatter().write(ws, out)

        content = out.read_text()
        assert "#### Call Graph" not in content


# endregion: --- Markdown Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- WorkspaceMeta Tests
# ---------------------------------------------------------------------------


class TestWorkspaceMetaCallEdges:
    """WorkspaceMeta includes total_call_edges field."""

    def test_total_call_edges_field(self) -> None:
        from ast_intel.models.workspace_model import WorkspaceMeta

        meta = WorkspaceMeta(total_call_edges=42)
        assert meta.total_call_edges == 42

    def test_total_call_edges_default(self) -> None:
        from ast_intel.models.workspace_model import WorkspaceMeta

        meta = WorkspaceMeta()
        assert meta.total_call_edges == 0


# endregion: --- WorkspaceMeta Tests
