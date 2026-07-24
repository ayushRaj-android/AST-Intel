"""Tests for Feature 13 — MCP Server.

Covers:
- Tool listing via :func:`create_server`
- Each tool handler dispatched through :func:`_dispatch`
- Error handling for unknown tools and missing symbols
- CLI ``serve`` subcommand surface test
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ast_intel.mcp_server import (
    _SUMMARY_URI,
    _build_summary,
    _dep_dict,
    _dispatch,
    _error,
    _explanation_dict,
    _file_info_dict,
    _impact_dict,
    _node_dict,
    _text,
    _usage_dict,
    create_server,
)
from ast_intel.models.ast_node import Confidence, Span
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


def _make_node(
    nid: str,
    label: str,
    kind: NodeKind = NodeKind.FUNCTION,
    file: str = "src/lib.rs",
    span: Span | None = None,
    **props: str,
) -> GraphNode:
    return GraphNode(
        id=nid,
        label=label,
        kind=kind,
        file=file,
        span=span or Span(1, 1, 10, 1),
        properties=dict(props),
    )


def _make_edge(
    src: str,
    tgt: str,
    relation: EdgeRelation = EdgeRelation.CALLS,
    score: float = 1.0,
    file: str = "src/lib.rs",
) -> GraphEdge:
    return GraphEdge(
        source=src,
        target=tgt,
        relation=relation,
        confidence=Confidence.EXTRACTED,
        confidence_score=score,
        file=file,
    )


def _sample_graph() -> CodeGraph:
    """Build a small test graph with enough structure for all tools."""
    file_a = _make_node("file_a", "lib.rs", NodeKind.FILE, "src/lib.rs")
    file_b = _make_node("file_b", "main.rs", NodeKind.FILE, "src/main.rs")
    func_a = _make_node("func_a", "parse", NodeKind.FUNCTION, "src/lib.rs")
    func_b = _make_node("func_b", "validate", NodeKind.FUNCTION, "src/lib.rs")
    func_c = _make_node("func_c", "run", NodeKind.FUNCTION, "src/main.rs")
    struct_x = _make_node("struct_x", "Parser", NodeKind.STRUCT, "src/lib.rs")
    trait_y = _make_node("trait_y", "Validator", NodeKind.TRAIT, "src/lib.rs")

    nodes = [file_a, file_b, func_a, func_b, func_c, struct_x, trait_y]
    edges = [
        _make_edge("file_a", "func_a", EdgeRelation.CONTAINS),
        _make_edge("file_a", "func_b", EdgeRelation.CONTAINS),
        _make_edge("file_a", "struct_x", EdgeRelation.CONTAINS),
        _make_edge("file_a", "trait_y", EdgeRelation.CONTAINS),
        _make_edge("file_b", "func_c", EdgeRelation.CONTAINS),
        _make_edge("func_c", "func_a", EdgeRelation.CALLS),
        _make_edge("func_c", "func_b", EdgeRelation.CALLS),
        _make_edge("func_a", "func_b", EdgeRelation.CALLS),
        _make_edge("struct_x", "trait_y", EdgeRelation.IMPLEMENTS),
        _make_edge("func_c", "struct_x", EdgeRelation.IMPORTS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


@pytest.fixture
def graph() -> CodeGraph:
    return _sample_graph()


@pytest.fixture
def engine(graph: CodeGraph) -> Any:
    from ast_intel.core._query_engine import QueryEngine

    return QueryEngine(graph)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Serialization Tests
# ---------------------------------------------------------------------------


class TestSerialization:
    """Test the serialization helper functions."""

    def test_node_dict_basic(self) -> None:
        n = _make_node("n1", "foo", NodeKind.FUNCTION, "a.rs")
        d = _node_dict(n)
        assert d["id"] == "n1"
        assert d["label"] == "foo"
        assert d["kind"] == "function"
        assert d["file"] == "a.rs"
        assert "span" in d

    def test_node_dict_no_span(self) -> None:
        n = GraphNode(id="x", label="x", kind=NodeKind.STRUCT, file="b.rs")
        d = _node_dict(n)
        assert "span" not in d

    def test_node_dict_with_properties(self) -> None:
        n = _make_node("n1", "foo", visibility="pub", is_async="true")
        d = _node_dict(n)
        assert d["properties"] == {"visibility": "pub", "is_async": "true"}

    def test_text_wrapper(self) -> None:
        result = _text({"hello": "world"})
        assert len(result) == 1
        assert result[0].type == "text"
        parsed = json.loads(result[0].text)
        assert parsed == {"hello": "world"}

    def test_error_wrapper(self) -> None:
        result = _error("something broke")
        parsed = json.loads(result[0].text)
        assert parsed == {"error": "something broke"}

    def test_dep_dict(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        dep = engine.get_dependencies("run")
        d = _dep_dict(dep)
        assert "node" in d
        assert d["node"]["label"] == "run"

    def test_explanation_dict(self, engine: Any) -> None:
        exp = engine.explain("parse")
        d = _explanation_dict(exp)
        assert d["node"]["label"] == "parse"
        assert "summary" in d
        assert "role" in d

    def test_impact_dict(self, engine: Any) -> None:
        imp = engine.impact("parse")
        d = _impact_dict(imp)
        assert d["root"]["label"] == "parse"
        assert "affected_count" in d

    def test_usage_dict(self, engine: Any) -> None:
        usages = engine.find_usages("parse")
        assert len(usages) > 0
        d = _usage_dict(usages[0])
        assert "node" in d
        assert "relation" in d
        assert "file" in d

    def test_file_info_dict(self, engine: Any) -> None:
        files = engine.list_files()
        assert len(files) > 0
        d = _file_info_dict(files[0])
        assert "file" in d
        assert "symbol_count" in d
        assert "key_symbols" in d


# endregion: --- Serialization Tests


# ---------------------------------------------------------------------------
# region:    --- Tool Dispatch Tests
# ---------------------------------------------------------------------------


class TestDispatch:
    """Test _dispatch routing for every tool."""

    def test_search_symbols(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "search_symbols", {"pattern": "parse"})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert any(n["label"] == "parse" for n in data)

    def test_health_check(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "health_check", {})
        data = json.loads(result[0].text)
        assert data["status"] == "ok"
        assert data["node_count"] == len(graph.nodes)
        assert data["edge_count"] == len(graph.edges)

    def test_search_symbols_with_kind(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(
            engine,
            graph,
            "search_symbols",
            {"pattern": "parse", "kind": "function"},
        )
        data = json.loads(result[0].text)
        assert all(n["kind"] == "function" for n in data)

    def test_search_symbols_regex(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(
            engine,
            graph,
            "search_symbols",
            {"pattern": "^par", "regex": True},
        )
        data = json.loads(result[0].text)
        labels = [n["label"] for n in data]
        assert "parse" in labels or "Parser" in labels

    def test_find_path_exists(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "find_path", {"source": "run", "target": "validate"})
        data = json.loads(result[0].text)
        assert data["path"] is not None
        assert len(data["path"]) >= 2

    def test_find_path_no_path(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "find_path", {"source": "validate", "target": "run"})
        data = json.loads(result[0].text)
        assert data["path"] is None

    def test_explain_symbol(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "explain_symbol", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert data["node"]["label"] == "parse"
        assert "role" in data
        assert "summary" in data

    def test_get_impact(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_impact", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert data["root"]["label"] == "parse"
        assert "affected" in data

    def test_get_impact_with_depth(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_impact", {"symbol": "parse", "depth": 1})
        data = json.loads(result[0].text)
        assert data["depth_limit"] == 1

    def test_get_dependencies(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_dependencies", {"symbol": "run"})
        data = json.loads(result[0].text)
        assert "node" in data
        assert data["node"]["label"] == "run"

    def test_get_dependents(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_dependents", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert "node" in data

    def test_get_context(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_context", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert "explanation" in data
        assert "dependencies" in data
        assert "dependents" in data
        assert "siblings" in data

    def test_find_usages(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "find_usages", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_files(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "list_files", {})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) == 2  # file_a and file_b

    def test_find_dead_code(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "find_dead_code", {})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        for entry in data:
            assert "node" in entry
            assert "in_degree" in entry

    def test_list_files_with_pattern(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "list_files", {"pattern": "*lib*"})
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert "lib" in data[0]["file"]

    def test_list_files_with_limit(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "list_files", {"limit": 1})
        data = json.loads(result[0].text)
        assert len(data) == 1

    def test_list_files_with_offset(self, engine: Any, graph: CodeGraph) -> None:
        all_result = _dispatch(engine, graph, "list_files", {})
        all_data = json.loads(all_result[0].text)
        result = _dispatch(engine, graph, "list_files", {"offset": 1})
        data = json.loads(result[0].text)
        assert len(data) == len(all_data) - 1

    def test_list_files_with_offset_and_limit(self, engine: Any, graph: CodeGraph) -> None:
        all_data = json.loads(
            _dispatch(engine, graph, "list_files", {})[0].text,
        )
        data = json.loads(
            _dispatch(
                engine, graph, "list_files", {"offset": 1, "limit": 1},
            )[0].text,
        )
        assert len(data) == 1
        assert data[0]["file"] == all_data[1]["file"]

    def test_list_files_no_limit_returns_all(self, engine: Any, graph: CodeGraph) -> None:
        data = json.loads(
            _dispatch(engine, graph, "list_files", {})[0].text,
        )
        assert len(data) == 2  # both files returned

    def test_get_routes(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        route = GraphNode(
            id="r1",
            label="GET /api/ping",
            kind=NodeKind.ROUTE,
            file="src/api.py",
            span=Span(3, 1, 5, 1),
            properties={
                "path": "/api/ping",
                "method": "GET",
                "framework": "flask",
                "handler": "ping",
            },
            service="gateway",
        )
        handler = _make_node("src/api.py::ping", "ping",
                              NodeKind.FUNCTION, "src/api.py")
        g = CodeGraph(
            nodes=[route, handler],
            edges=[_make_edge("r1", "src/api.py::ping",
                              EdgeRelation.HANDLES)],
        )
        eng = QueryEngine(g)
        data = json.loads(_dispatch(eng, g, "get_routes", {})[0].text)
        assert len(data) == 1
        assert data[0]["method"] == "GET"
        assert data[0]["path"] == "/api/ping"
        assert data[0]["handler"] == "ping"
        assert data[0]["service"] == "gateway"
        assert data[0]["handler_node"]["label"] == "ping"

    def test_get_routes_service_filter(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        def _route(rid: str, label: str, svc: str) -> GraphNode:
            method, path = label.split(" ", 1)
            return GraphNode(
                id=rid, label=label, kind=NodeKind.ROUTE, file="x.py",
                properties={"path": path, "method": method,
                            "framework": "fastapi", "handler": "h"},
                service=svc,
            )

        g = CodeGraph(
            nodes=[
                _route("a", "GET /a", "svc1"),
                _route("b", "GET /b", "svc2"),
            ],
            edges=[],
        )
        eng = QueryEngine(g)
        data = json.loads(
            _dispatch(eng, g, "get_routes", {"service": "svc1"})[0].text,
        )
        assert len(data) == 1
        assert data[0]["path"] == "/a"

    def test_get_hyperedges_by_value(self) -> None:
        from ast_intel.core._query_engine import QueryEngine
        from ast_intel.models.graph_model import HyperEdge, HyperRelation

        he = HyperEdge(
            id="he::route_group::api",
            relation=HyperRelation.ROUTE_GROUP,
            members=["a", "b"],
            label="/api/*",
            metadata={},
        )
        g = CodeGraph(
            nodes=[_make_node("a", "A"), _make_node("b", "B")],
            edges=[], hyperedges=[he],
        )
        eng = QueryEngine(g)
        data = json.loads(
            _dispatch(eng, g, "get_hyperedges",
                      {"relation": "route_group"})[0].text,
        )
        assert len(data) == 1
        assert data[0]["relation"] == "route_group"

    def test_get_hyperedges_by_name_uppercase(self) -> None:
        """Accept the enum NAME (uppercase) too — regression for the
        '\\'ROUTE_GROUP\\' is not a valid HyperRelation' error."""
        from ast_intel.core._query_engine import QueryEngine
        from ast_intel.models.graph_model import HyperEdge, HyperRelation

        he = HyperEdge(
            id="he::route_group::api",
            relation=HyperRelation.ROUTE_GROUP,
            members=["a", "b"],
            label="/api/*",
            metadata={},
        )
        g = CodeGraph(
            nodes=[_make_node("a", "A"), _make_node("b", "B")],
            edges=[], hyperedges=[he],
        )
        eng = QueryEngine(g)
        data = json.loads(
            _dispatch(eng, g, "get_hyperedges",
                      {"relation": "ROUTE_GROUP"})[0].text,
        )
        assert len(data) == 1
        assert data[0]["relation"] == "route_group"

    def test_get_hyperedges_invalid_relation(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        g = CodeGraph(
            nodes=[_make_node("a", "A")], edges=[], hyperedges=[],
        )
        eng = QueryEngine(g)
        data = json.loads(
            _dispatch(eng, g, "get_hyperedges",
                      {"relation": "NOPE"})[0].text,
        )
        assert "error" in data
        assert "route_group" in data["error"]

    def test_get_implementors(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "get_implementors", {"trait": "Validator"})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["node"]["label"] == "Parser"

    def test_find_similar_no_edges(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "find_similar", {"symbol": "parse"})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        # No SIMILAR_TO edges in test graph, so empty
        assert len(data) == 0

    def test_get_community_no_analysis(self, engine: Any, graph: CodeGraph) -> None:
        with pytest.raises(KeyError, match="No community found"):
            _dispatch(engine, graph, "get_community", {"symbol": "parse"})

    def test_unknown_tool(self, engine: Any, graph: CodeGraph) -> None:
        result = _dispatch(engine, graph, "nonexistent_tool", {})
        data = json.loads(result[0].text)
        assert "error" in data
        assert "Unknown tool" in data["error"]

    def test_symbol_not_found(self, engine: Any, graph: CodeGraph) -> None:
        with pytest.raises(KeyError, match="Symbol not found"):
            _dispatch(engine, graph, "explain_symbol", {"symbol": "DOES_NOT_EXIST"})


# endregion: --- Tool Dispatch Tests


# ---------------------------------------------------------------------------
# region:    --- Server Creation Tests
# ---------------------------------------------------------------------------


class TestCreateServer:
    """Test the create_server factory."""

    def test_server_name(self, engine: Any, graph: CodeGraph) -> None:
        server = create_server(engine, graph)
        assert server.name == "ast-intel"

    def test_tool_count(self) -> None:
        from ast_intel.mcp_server import _TOOLS

        # 19 graph + IaC tools + 1 cloud resources + 3 history + 7 Phase 4
        # + 2 minimalism protocol (find_reusable, audit_codebase)
        # + 2 tracking (track_minimalism, review_minimalism_ledger)
        # + 1 find_dead_code + 1 get_data_egress + 1 health_check.
        assert len(_TOOLS) == 37

    def test_get_routes_tool_registered(self) -> None:
        from ast_intel.mcp_server import _TOOLS

        tool = next((t for t in _TOOLS if t.name == "get_routes"), None)
        assert tool is not None
        assert "service" in tool.inputSchema["properties"]

    def test_all_tools_have_schemas(self) -> None:
        from ast_intel.mcp_server import _TOOLS

        for tool in _TOOLS:
            assert tool.name
            assert tool.description
            assert tool.inputSchema
            assert tool.inputSchema["type"] == "object"

    def test_tool_names_unique(self) -> None:
        from ast_intel.mcp_server import _TOOLS

        names = [t.name for t in _TOOLS]
        assert len(names) == len(set(names))


# endregion: --- Server Creation Tests


# ---------------------------------------------------------------------------
# region:    --- Similarity Auto-Compute Tests
# ---------------------------------------------------------------------------


class TestSimilarityAutoCompute:
    """Tests for Feature 18 — similarity auto-enable."""

    def test_find_similar_auto_computed(self) -> None:
        """Similarity edges are auto-computed when graph has none."""
        from ast_intel.core._query_engine import QueryEngine
        from ast_intel.core._similarity import compute_similarity_edges

        # Build two structs with shared HAS_FIELD targets so the
        # similarity algorithm produces ≥2 features per node.
        nodes = [
            _make_node("file_a", "lib.rs", NodeKind.FILE, "src/lib.rs"),
            _make_node("s1", "AlphaService", NodeKind.STRUCT, "src/lib.rs"),
            _make_node("s2", "BetaService", NodeKind.STRUCT, "src/lib.rs"),
        ]
        edges = [
            _make_edge("file_a", "s1", EdgeRelation.CONTAINS),
            _make_edge("file_a", "s2", EdgeRelation.CONTAINS),
            # Shared field types → field_type:String, field_type:Config
            _make_edge("s1", "src/lib.rs::String", EdgeRelation.HAS_FIELD),
            _make_edge("s1", "src/lib.rs::Config", EdgeRelation.HAS_FIELD),
            _make_edge("s2", "src/lib.rs::String", EdgeRelation.HAS_FIELD),
            _make_edge("s2", "src/lib.rs::Config", EdgeRelation.HAS_FIELD),
        ]
        g = CodeGraph(nodes=nodes, edges=edges)
        assert not any(
            e.relation == EdgeRelation.SIMILAR_TO for e in g.edges
        )

        # Simulate what run_stdio does: auto-compute similarity.
        sim_edges = compute_similarity_edges(g, threshold=0.3)
        g.edges.extend(sim_edges)

        engine = QueryEngine(g)
        result = _dispatch(engine, g, "find_similar", {"symbol": "AlphaService"})
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["node"]["label"] == "BetaService"

    def test_similarity_skipped_when_edges_exist(self) -> None:
        """No duplicate computation when graph already has SIMILAR_TO."""
        from ast_intel.core._query_engine import QueryEngine

        nodes = [
            _make_node("s1", "Foo", NodeKind.STRUCT, "a.rs"),
            _make_node("s2", "Bar", NodeKind.STRUCT, "a.rs"),
        ]
        edges = [
            GraphEdge(
                source="s1", target="s2",
                relation=EdgeRelation.SIMILAR_TO,
                confidence=Confidence.INFERRED,
                confidence_score=0.8,
            ),
        ]
        g = CodeGraph(nodes=nodes, edges=edges)

        # Graph already has SIMILAR_TO → the check should skip.
        has_similar = any(
            e.relation == EdgeRelation.SIMILAR_TO for e in g.edges
        )
        assert has_similar

        engine = QueryEngine(g)
        result = _dispatch(engine, g, "find_similar", {"symbol": "Foo"})
        data = json.loads(result[0].text)
        assert len(data) == 1
        assert data[0]["node"]["label"] == "Bar"

    def test_serve_help_shows_similarity(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--similarity" in result.output
        assert "--similarity-thr" in result.output


# endregion: --- Similarity Auto-Compute Tests


# ---------------------------------------------------------------------------
# region:    --- CLI Surface Tests
# ---------------------------------------------------------------------------


class TestCLIServe:
    """Test the serve CLI subcommand exists and has correct help."""

    def test_serve_in_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "MCP server" in result.output

    def test_serve_requires_repo_path(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["serve"])
        assert result.exit_code != 0


# endregion: --- CLI Surface Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Summary Resource Tests (Feature 20)
# ---------------------------------------------------------------------------


class TestBuildSummary:
    """Unit tests for the ``_build_summary`` helper."""

    def test_total_counts(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        assert summary["total_nodes"] == len(graph.nodes)
        assert summary["total_edges"] == len(graph.edges)

    def test_nodes_by_kind(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        nbk = summary["nodes_by_kind"]
        assert nbk["file"] == 2
        assert nbk["function"] == 3
        assert nbk["struct"] == 1
        assert nbk["trait"] == 1

    def test_edges_by_relation(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        ebr = summary["edges_by_relation"]
        assert ebr["contains"] == 5
        assert ebr["calls"] == 3
        assert ebr["implements"] == 1
        assert ebr["imports"] == 1

    def test_crates_list(self) -> None:
        """Crate nodes are collected into the 'crates' list."""
        nodes = [
            _make_node("crate::alpha", "alpha", NodeKind.CRATE),
            _make_node("crate::beta", "beta", NodeKind.CRATE),
            _make_node("f1", "lib.rs", NodeKind.FILE, "src/lib.rs"),
        ]
        g = CodeGraph(nodes=nodes, edges=[])
        summary = _build_summary(g)
        assert summary["crates"] == ["alpha", "beta"]

    def test_file_count(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        assert summary["file_count"] == 2

    def test_top_connected(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        top = summary["top_connected"]
        assert isinstance(top, list)
        assert len(top) <= 10
        # Each entry has id, label, kind, degree
        for entry in top:
            assert "id" in entry
            assert "label" in entry
            assert "kind" in entry
            assert "degree" in entry
            assert entry["degree"] > 0

    def test_top_connected_sorted_by_degree(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        degrees = [e["degree"] for e in summary["top_connected"]]
        assert degrees == sorted(degrees, reverse=True)

    def test_empty_graph(self) -> None:
        g = CodeGraph(nodes=[], edges=[])
        summary = _build_summary(g)
        assert summary["total_nodes"] == 0
        assert summary["total_edges"] == 0
        assert summary["crates"] == []
        assert summary["file_count"] == 0
        assert summary["top_connected"] == []

    def test_summary_is_json_serializable(self, graph: CodeGraph) -> None:
        summary = _build_summary(graph)
        text = json.dumps(summary)
        assert isinstance(json.loads(text), dict)


class TestResourceHandlers:
    """Integration tests for MCP resource list/read handlers."""

    def test_list_resources(self, graph: CodeGraph) -> None:
        import asyncio

        from mcp.types import ListResourcesRequest

        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        server = create_server(engine, graph)

        async def _run() -> object:
            req = ListResourcesRequest(method="resources/list", params=None)
            handler = server.request_handlers[ListResourcesRequest]
            return await handler(req)

        result = asyncio.run(_run())
        resources = result.root.resources
        assert len(resources) == 1
        assert str(resources[0].uri) == _SUMMARY_URI
        assert resources[0].name == "Codebase Summary"
        assert resources[0].mimeType == "application/json"

    def test_read_summary_resource(self, graph: CodeGraph) -> None:
        import asyncio

        from mcp.types import (
            ReadResourceRequest,
            ReadResourceRequestParams,
        )
        from pydantic import AnyUrl

        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        server = create_server(engine, graph)

        async def _run() -> object:
            params = ReadResourceRequestParams(uri=AnyUrl(_SUMMARY_URI))
            req = ReadResourceRequest(
                method="resources/read", params=params,
            )
            handler = server.request_handlers[ReadResourceRequest]
            return await handler(req)

        result = asyncio.run(_run())
        contents = result.root.contents
        assert len(contents) == 1
        payload = json.loads(contents[0].text)
        assert payload["total_nodes"] == 7
        assert payload["total_edges"] == 10

    def test_read_unknown_resource_raises(
        self, graph: CodeGraph,
    ) -> None:
        import asyncio

        from mcp.types import (
            ReadResourceRequest,
            ReadResourceRequestParams,
        )
        from pydantic import AnyUrl

        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        server = create_server(engine, graph)

        async def _run() -> object:
            params = ReadResourceRequestParams(
                uri=AnyUrl("ast-intel://unknown"),
            )
            req = ReadResourceRequest(
                method="resources/read", params=params,
            )
            handler = server.request_handlers[ReadResourceRequest]
            return await handler(req)

        with pytest.raises(ValueError, match="Unknown resource"):
            asyncio.run(_run())


# endregion: --- Graph Summary Resource Tests (Feature 20)
