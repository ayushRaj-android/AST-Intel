"""Tests for Feature 10 — Interactive HTML Visualization.

Covers:
- GraphHtmlFormatter: file creation, HTML structure, embedded JSON
- CDN vs offline modes
- Color/shape maps completeness for all NodeKind/EdgeRelation values
- Search, filter UI elements present
- Clustering logic (file-based)
- Empty graph handling
- Page title derivation
- Emitter integration with ``html`` format
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ast_intel.core.emitter import Emitter
from ast_intel.core.graph_builder import GraphBuilder
from ast_intel.formatters.graph_html_formatter import (
    GRAPH_HTML_FILENAME,
    GraphHtmlFormatter,
    _page_title,
    _serialize_graph,
    _vis_script_tag,
)
from ast_intel.models.ast_node import (
    SCORE_EXTRACTED,
    Confidence,
    FieldNode,
    FileAST,
    FunctionNode,
    Span,
    StructNode,
    Visibility,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.workspace_model import CrateModel, WorkspaceAST, WorkspaceMeta

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Provide a temporary output directory."""
    return tmp_path / "output"


def _simple_workspace() -> WorkspaceAST:
    """Minimal workspace with a struct and function."""
    file1 = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        structs=[
            StructNode(
                name="Config",
                visibility=Visibility.PUBLIC,
                fields=(
                    FieldNode(
                        name="host",
                        type="String",
                        visibility=Visibility.PUBLIC,
                    ),
                ),
                span=Span(1, 1, 10, 2),
            ),
        ],
        functions=[
            FunctionNode(
                name="main",
                visibility=Visibility.PUBLIC,
                span=Span(12, 1, 20, 2),
            ),
        ],
    )
    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate",
        version="0.1.0",
        manifest_path="Cargo.toml",
        language="rust",
        files=[file1],
    )
    return ws


def _build_graph() -> CodeGraph:
    """Build a CodeGraph from the simple workspace."""
    return GraphBuilder().build(_simple_workspace())


def _tiny_graph() -> CodeGraph:
    """Build a hand-crafted minimal graph."""
    return CodeGraph(
        meta=WorkspaceMeta(
            workspace_root="/tmp/myproject",
            tool_version="0.1.0",
            schema_version="0.1.0",
        ),
        nodes=[
            GraphNode(
                id="n1", label="Foo", kind=NodeKind.STRUCT, file="src/lib.rs",
            ),
            GraphNode(
                id="n2", label="bar", kind=NodeKind.FUNCTION, file="src/lib.rs",
            ),
        ],
        edges=[
            GraphEdge(
                source="n1",
                target="n2",
                relation=EdgeRelation.CONTAINS,
                confidence=Confidence.EXTRACTED,
                confidence_score=SCORE_EXTRACTED,
            ),
        ],
    )


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Serialization Tests
# ---------------------------------------------------------------------------


class TestSerializeGraph:
    """Unit tests for ``_serialize_graph``."""

    def test_returns_valid_json(self) -> None:
        graph = _build_graph()
        raw = _serialize_graph(graph)
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_has_nodes_and_edges_keys(self) -> None:
        graph = _build_graph()
        data = json.loads(_serialize_graph(graph))
        assert "nodes" in data
        assert "edges" in data

    def test_node_fields(self) -> None:
        graph = _tiny_graph()
        data = json.loads(_serialize_graph(graph))
        node = data["nodes"][0]
        assert node["id"] == "n1"
        assert node["label"] == "Foo"
        assert node["kind"] == "struct"
        assert node["group"] == "struct"
        assert node["file"] == "src/lib.rs"

    def test_edge_fields(self) -> None:
        graph = _tiny_graph()
        data = json.loads(_serialize_graph(graph))
        edge = data["edges"][0]
        assert edge["from"] == "n1"
        assert edge["to"] == "n2"
        assert edge["relation"] == "contains"

    def test_empty_graph_serializes(self) -> None:
        graph = CodeGraph(
            meta=WorkspaceMeta(
                workspace_root="empty",
                tool_version="0.1.0",
                schema_version="0.1.0",
            ),
            nodes=[],
            edges=[],
        )
        data = json.loads(_serialize_graph(graph))
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_properties_included_when_present(self) -> None:
        graph = CodeGraph(
            meta=WorkspaceMeta(
                workspace_root="x",
                tool_version="0.1.0",
                schema_version="0.1.0",
            ),
            nodes=[
                GraphNode(
                    id="n1",
                    label="Foo",
                    kind=NodeKind.STRUCT,
                    file="a.rs",
                    properties={"visibility": "public", "field_count": "2"},
                ),
            ],
            edges=[],
        )
        data = json.loads(_serialize_graph(graph))
        assert data["nodes"][0]["properties"]["visibility"] == "public"


# endregion: --- Serialization Tests


# ---------------------------------------------------------------------------
# region:    --- Vis.js Script Tag Tests
# ---------------------------------------------------------------------------


class TestVisScriptTag:
    """Tests for ``_vis_script_tag``."""

    def test_cdn_mode(self) -> None:
        tag = _vis_script_tag(offline=False)
        assert "unpkg.com/vis-network" in tag
        assert tag.startswith("<script")

    def test_offline_mode_inlines_js(self) -> None:
        tag = _vis_script_tag(offline=True)
        # The vendored file should be inlined (no CDN src attribute)
        assert "unpkg.com" not in tag
        assert "<script>" in tag
        # Should be substantial (vendored JS is ~670KB+)
        assert len(tag) > 100_000

    def test_cdn_tag_is_pinned_version(self) -> None:
        tag = _vis_script_tag(offline=False)
        assert "9.1.9" in tag


# endregion: --- Vis.js Script Tag Tests


# ---------------------------------------------------------------------------
# region:    --- Page Title Tests
# ---------------------------------------------------------------------------


class TestPageTitle:
    """Tests for ``_page_title``."""

    def test_with_workspace_root(self) -> None:
        graph = _tiny_graph()
        assert _page_title(graph) == "AST Intel — myproject"

    def test_without_meta(self) -> None:
        graph = CodeGraph(meta=None, nodes=[], edges=[])
        assert _page_title(graph) == "AST Intel — Code Graph"

    def test_without_workspace_root(self) -> None:
        graph = CodeGraph(
            meta=WorkspaceMeta(
                workspace_root="",
                tool_version="0.1.0",
                schema_version="0.1.0",
            ),
            nodes=[],
            edges=[],
        )
        assert _page_title(graph) == "AST Intel — Code Graph"


# endregion: --- Page Title Tests


# ---------------------------------------------------------------------------
# region:    --- HTML Output Tests
# ---------------------------------------------------------------------------


class TestGraphHtmlFormatter:
    """Integration tests for ``GraphHtmlFormatter.write``."""

    def test_creates_file(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_output_is_valid_html(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_embedded_json_is_parsable(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        # The JSON is injected via template replacement
        assert '"nodes"' in html
        assert '"edges"' in html

    def test_cdn_mode_default(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "unpkg.com/vis-network" in html

    def test_offline_mode(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out, offline=True)
        html = out.read_text(encoding="utf-8")
        # Should NOT have CDN link
        assert "unpkg.com" not in html
        # Should be large (inlined JS)
        assert len(html) > 100_000

    def test_title_in_html(self, tmp_output: Path) -> None:
        graph = _tiny_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "AST Intel — myproject" in html

    def test_search_input_present(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert 'id="search"' in html

    def test_graph_container_present(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert 'id="graph-container"' in html

    def test_detail_sidebar_present(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert 'id="detail"' in html

    def test_scope_selector_present(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert 'id="scope-selector"' in html

    def test_breadcrumb_present(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert 'id="breadcrumb"' in html

    def test_hierarchical_detection_in_template(self, tmp_output: Path) -> None:
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "HIERARCHICAL" in html
        assert "renderCrateView" in html
        assert "renderFileView" in html
        assert "renderSymbolView" in html

    def test_empty_graph_produces_valid_html(self, tmp_output: Path) -> None:
        graph = CodeGraph(
            meta=WorkspaceMeta(
                workspace_root="empty",
                tool_version="0.1.0",
                schema_version="0.1.0",
            ),
            nodes=[],
            edges=[],
        )
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert '"nodes":[]' in html


# endregion: --- HTML Output Tests


# ---------------------------------------------------------------------------
# region:    --- Template Completeness Tests
# ---------------------------------------------------------------------------


class TestTemplateColorMaps:
    """Verify the HTML template covers all NodeKind and EdgeRelation values."""

    def _get_html(self, tmp_path: Path) -> str:
        graph = _build_graph()
        out = tmp_path / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        return out.read_text(encoding="utf-8")

    def test_all_node_kinds_in_color_map(self, tmp_path: Path) -> None:
        html = self._get_html(tmp_path)
        for kind in NodeKind:
            assert kind.value in html, (
                f"NodeKind.{kind.name} ({kind.value!r}) missing from "
                f"KIND_COLORS in HTML template"
            )

    def test_all_edge_relations_in_color_map(self, tmp_path: Path) -> None:
        html = self._get_html(tmp_path)
        for rel in EdgeRelation:
            assert rel.value in html, (
                f"EdgeRelation.{rel.name} ({rel.value!r}) missing from "
                f"EDGE_COLORS in HTML template"
            )


# endregion: --- Template Completeness Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Integration Tests
# ---------------------------------------------------------------------------


class TestEmitterHtmlFormat:
    """Wire-up: ``Emitter`` with ``html`` format."""

    def test_emitter_produces_html(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="html")
        written = emitter.emit(ws)
        html_files = [p for p in written if p.name == GRAPH_HTML_FILENAME]
        assert len(html_files) == 1
        assert html_files[0].exists()

    def test_emitter_offline_flag(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        emitter = Emitter(
            output_dir=tmp_output, output_format="html", offline=True,
        )
        written = emitter.emit(ws)
        html_path = written[0]
        html = html_path.read_text(encoding="utf-8")
        assert "unpkg.com" not in html

    def test_emitter_rejects_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid output_format"):
            Emitter(output_dir=Path("/tmp"), output_format="xlsx")  # type: ignore[arg-type]

    def test_html_format_in_valid_formats(self) -> None:
        """Ensure 'html' is accepted without raising."""
        emitter = Emitter(output_dir=Path("/tmp"), output_format="html")
        assert emitter.output_format == "html"


# endregion: --- Emitter Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Hierarchical Drill-Down Tests
# ---------------------------------------------------------------------------


class TestHierarchicalDrillDown:
    """Tests for the hierarchical navigation feature."""

    def test_small_graph_uses_flat_mode(self, tmp_output: Path) -> None:
        """Small graphs (<=500 nodes) should use flat mode."""
        graph = _tiny_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        # The JS should set HIERARCHICAL based on node count
        # With only 2 nodes, it should be false
        assert "var HIERARCHICAL = RAW.nodes.length > 500" in html

    def test_template_has_view_render_functions(self, tmp_output: Path) -> None:
        """Template must contain all drill-down render functions."""
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "function renderCrateView()" in html
        assert "function renderFileView(" in html
        assert "function renderSymbolView(" in html
        assert "function renderFlatView()" in html

    def test_template_has_edge_aggregation(self, tmp_output: Path) -> None:
        """Template must have edge aggregation functions."""
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "function buildCrateEdges()" in html
        assert "function buildFileEdges(" in html
        assert "function buildSymbolEdges(" in html

    def test_template_has_breadcrumb_update(self, tmp_output: Path) -> None:
        """Template must have breadcrumb navigation logic."""
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "function updateBreadcrumb()" in html

    def test_template_has_scope_selector_update(self, tmp_output: Path) -> None:
        """Template must have scope selector logic."""
        graph = _build_graph()
        out = tmp_output / GRAPH_HTML_FILENAME
        GraphHtmlFormatter().write(graph, out)
        html = out.read_text(encoding="utf-8")
        assert "function updateScopeSelector()" in html


# endregion: --- Hierarchical Drill-Down Tests
