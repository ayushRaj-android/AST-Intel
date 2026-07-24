"""Tests for ast_intel.core._merge — graph merge engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ast_intel.core._merge import (
    _infer_service_name,
    _prefix_id,
    merge_graphs,
)
from ast_intel.formatters.graph_json_formatter import GraphJsonFormatter
from ast_intel.models.ast_node import Confidence
from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.workspace_model import WorkspaceMeta

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_graph(
    *,
    workspace_root: str = "/repo",
    nodes: list[GraphNode] | None = None,
    edges: list[GraphEdge] | None = None,
) -> CodeGraph:
    """Build a minimal CodeGraph for testing."""
    meta = WorkspaceMeta(
        schema_version="1",
        tool_version="0.1.0",
        workspace_root=workspace_root,
        total_crates=1,
        total_files=2,
        total_structs=1,
        total_functions=1,
    )
    if nodes is None:
        nodes = [
            GraphNode(
                id="src/lib.rs",
                label="lib.rs",
                kind=NodeKind.FILE,
                file="src/lib.rs",
            ),
            GraphNode(
                id="src/lib.rs::MyStruct",
                label="MyStruct",
                kind=NodeKind.STRUCT,
                file="src/lib.rs",
            ),
            GraphNode(
                id="src/lib.rs::my_func",
                label="my_func",
                kind=NodeKind.FUNCTION,
                file="src/lib.rs",
            ),
        ]
    if edges is None:
        edges = [
            GraphEdge(
                source="src/lib.rs",
                target="src/lib.rs::MyStruct",
                relation=EdgeRelation.CONTAINS,
                confidence=Confidence.EXTRACTED,
                confidence_score=1.0,
                file="src/lib.rs",
            ),
            GraphEdge(
                source="src/lib.rs::my_func",
                target="src/lib.rs::MyStruct",
                relation=EdgeRelation.CALLS,
                confidence=Confidence.EXTRACTED,
                confidence_score=1.0,
                file="src/lib.rs",
            ),
        ]
    return CodeGraph(nodes=nodes, edges=edges, meta=meta)


@pytest.fixture
def graph_a() -> CodeGraph:
    """First test graph — simulates 'user-service'."""
    return _make_graph(workspace_root="/repos/user-service")


@pytest.fixture
def graph_b() -> CodeGraph:
    """Second test graph — simulates 'order-service'."""
    return _make_graph(
        workspace_root="/repos/order-service",
        nodes=[
            GraphNode(
                id="src/main.rs",
                label="main.rs",
                kind=NodeKind.FILE,
                file="src/main.rs",
            ),
            GraphNode(
                id="src/main.rs::OrderHandler",
                label="OrderHandler",
                kind=NodeKind.STRUCT,
                file="src/main.rs",
            ),
        ],
        edges=[
            GraphEdge(
                source="src/main.rs",
                target="src/main.rs::OrderHandler",
                relation=EdgeRelation.CONTAINS,
                confidence=Confidence.EXTRACTED,
                confidence_score=1.0,
                file="src/main.rs",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tests: Helper Functions
# ---------------------------------------------------------------------------


class TestInferServiceName:
    """Tests for _infer_service_name."""

    def test_unix_path(self) -> None:
        g = _make_graph(workspace_root="/home/user/my-project")
        assert _infer_service_name(g) == "my-project"

    def test_trailing_slash(self) -> None:
        g = _make_graph(workspace_root="/home/user/my-project/")
        assert _infer_service_name(g) == "my-project"

    def test_no_meta(self) -> None:
        g = CodeGraph()
        assert _infer_service_name(g) == "unknown"

    def test_empty_workspace_root(self) -> None:
        g = _make_graph(workspace_root="")
        assert _infer_service_name(g) == "unknown"


class TestPrefixId:
    """Tests for _prefix_id."""

    def test_basic(self) -> None:
        assert _prefix_id("svc", "src/lib.rs") == "svc::src/lib.rs"

    def test_nested(self) -> None:
        assert (
            _prefix_id("api", "src/lib.rs::Foo")
            == "api::src/lib.rs::Foo"
        )


class TestMergeGraphs:
    """Tests for the main merge_graphs function."""

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            merge_graphs([])

    def test_duplicate_names_raises(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        with pytest.raises(ValueError, match="Duplicate service name"):
            merge_graphs([("svc", graph_a), ("svc", graph_b)])

    def test_node_count(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        # graph_a: 3 nodes + 1 SERVICE = 4
        # graph_b: 2 nodes + 1 SERVICE = 3
        assert len(merged.nodes) == 7

    def test_edge_count(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        # graph_a: 2 edges + 1 BELONGS_TO (1 FILE node) = 3
        # graph_b: 1 edge  + 1 BELONGS_TO (1 FILE node) = 2
        assert len(merged.edges) == 5

    def test_service_nodes_created(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("alpha", graph_a), ("beta", graph_b)])
        service_nodes = [
            n for n in merged.nodes if n.kind == NodeKind.SERVICE
        ]
        assert len(service_nodes) == 2
        names = {n.label for n in service_nodes}
        assert names == {"alpha", "beta"}

    def test_all_ids_prefixed(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        for node in merged.nodes:
            if node.kind == NodeKind.SERVICE:
                # SERVICE nodes use bare service name as ID
                assert "::" not in node.id or node.id.startswith("a::") or node.id.startswith("b::")
            else:
                assert node.id.startswith("a::") or node.id.startswith("b::")

    def test_edges_reference_prefixed_ids(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        for edge in merged.edges:
            assert (
                edge.source.startswith("a::")
                or edge.source.startswith("b::")
                or edge.source in ("a", "b")
            ), f"Unexpected source: {edge.source}"
            assert (
                edge.target.startswith("a::")
                or edge.target.startswith("b::")
                or edge.target in ("a", "b")
            ), f"Unexpected target: {edge.target}"

    def test_belongs_to_edges(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        bt_edges = [
            e for e in merged.edges
            if e.relation == EdgeRelation.BELONGS_TO
        ]
        # 1 FILE in graph_a + 1 FILE in graph_b
        assert len(bt_edges) == 2
        targets = {e.target for e in bt_edges}
        assert targets == {"a", "b"}

    def test_origin_id_preserved(
        self, graph_a: CodeGraph,
    ) -> None:
        merged = merge_graphs([("svc", graph_a)])
        non_service = [
            n for n in merged.nodes if n.kind != NodeKind.SERVICE
        ]
        for node in non_service:
            assert node.origin_id != ""
            assert node.origin_id in node.id  # original is a suffix

    def test_service_field_set(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("alpha", graph_a), ("beta", graph_b)])
        for node in merged.nodes:
            assert node.service in ("alpha", "beta")

    def test_meta_merged(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        assert merged.meta is not None
        # Both graphs have total_files=2
        assert merged.meta.total_files == 4
        assert merged.meta.total_structs == 2
        assert merged.meta.total_functions == 2
        assert "user-service" in merged.meta.workspace_root
        assert "order-service" in merged.meta.workspace_root

    def test_single_graph_merge(self, graph_a: CodeGraph) -> None:
        """Merging a single graph should still work."""
        merged = merge_graphs([("only", graph_a)])
        assert len(merged.nodes) == 4  # 3 original + 1 SERVICE
        service_nodes = [
            n for n in merged.nodes if n.kind == NodeKind.SERVICE
        ]
        assert len(service_nodes) == 1


# ---------------------------------------------------------------------------
# Tests: Round-trip serialization
# ---------------------------------------------------------------------------


class TestMergeRoundTrip:
    """Verify merged graphs survive JSON serialization round-trip."""

    def test_round_trip(
        self,
        graph_a: CodeGraph,
        graph_b: CodeGraph,
        tmp_path: Path,
    ) -> None:
        merged = merge_graphs([("a", graph_a), ("b", graph_b)])

        out_path = tmp_path / "merged.json"
        formatter = GraphJsonFormatter()
        formatter.write(merged, out_path)

        loaded = formatter.read(out_path)

        assert len(loaded.nodes) == len(merged.nodes)
        assert len(loaded.edges) == len(merged.edges)

        # Verify SERVICE nodes survive
        service_nodes = [
            n for n in loaded.nodes if n.kind == NodeKind.SERVICE
        ]
        assert len(service_nodes) == 2

        # Verify service field survives
        svc_fields = {n.service for n in loaded.nodes}
        assert "a" in svc_fields
        assert "b" in svc_fields

        # Verify origin_id survives
        non_svc = [
            n for n in loaded.nodes if n.kind != NodeKind.SERVICE
        ]
        assert all(n.origin_id != "" for n in non_svc)

    def test_json_has_service_field(
        self,
        graph_a: CodeGraph,
        tmp_path: Path,
    ) -> None:
        merged = merge_graphs([("mysvc", graph_a)])
        out_path = tmp_path / "out.json"
        GraphJsonFormatter().write(merged, out_path)

        raw: dict[str, Any] = json.loads(out_path.read_text())
        svc_nodes = [
            n for n in raw["nodes"] if n.get("kind") == "service"
        ]
        assert len(svc_nodes) == 1
        assert svc_nodes[0]["label"] == "mysvc"

        # Non-service nodes should have service and origin_id
        regular = [
            n for n in raw["nodes"] if n.get("kind") != "service"
        ]
        assert all("service" in n for n in regular)
        assert all("origin_id" in n for n in regular)


# ---------------------------------------------------------------------------
# Tests: QueryEngine compatibility
# ---------------------------------------------------------------------------


class TestMergedGraphQueryEngine:
    """Verify QueryEngine works on merged graphs."""

    def test_search_on_merged(
        self, graph_a: CodeGraph, graph_b: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        merged = merge_graphs([("a", graph_a), ("b", graph_b)])
        engine = QueryEngine(merged)

        # Should find MyStruct from graph_a
        results = engine.search("MyStruct")
        assert len(results) > 0
        assert any("MyStruct" in n.label for n in results)

        # Should find OrderHandler from graph_b
        results = engine.search("OrderHandler")
        assert len(results) > 0
        assert any("OrderHandler" in n.label for n in results)

    def test_shortest_path_within_service(
        self, graph_a: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        merged = merge_graphs([("svc", graph_a)])
        engine = QueryEngine(merged)

        result = engine.shortest_path("my_func", "MyStruct")
        assert result is not None


# ---------------------------------------------------------------------------
# region:    --- TestMergeCrossServiceResolution
# ---------------------------------------------------------------------------


class TestMergeCrossServiceResolution:
    """Verify CALLS_SERVICE edges appear after merge with resolve=True."""

    @staticmethod
    def _graph_with_route() -> CodeGraph:
        """Service that exposes a ROUTE."""
        return _make_graph(
            workspace_root="/server",
            nodes=[
                GraphNode(
                    id="route:get:/api/v1/items",
                    label="GET /api/v1/items",
                    kind=NodeKind.ROUTE,
                    file="routes.rs",
                    properties={"path": "/api/v1/items", "method": "GET"},
                ),
                GraphNode(
                    id="file:routes.rs",
                    label="routes.rs",
                    kind=NodeKind.FILE,
                    file="routes.rs",
                ),
            ],
            edges=[
                GraphEdge(
                    source="file:routes.rs",
                    target="route:get:/api/v1/items",
                    relation=EdgeRelation.EXPOSES,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=1.0,
                ),
            ],
        )

    @staticmethod
    def _graph_with_http_call() -> CodeGraph:
        """Service that makes an HTTP_CALL."""
        return _make_graph(
            workspace_root="/client",
            nodes=[
                GraphNode(
                    id="http_call:get:/api/v1/items",
                    label="GET http://server:8080/api/v1/items",
                    kind=NodeKind.HTTP_CALL,
                    file="client.rs",
                    properties={
                        "url": "http://server:8080/api/v1/items",
                        "method": "GET",
                    },
                ),
                GraphNode(
                    id="file:client.rs",
                    label="client.rs",
                    kind=NodeKind.FILE,
                    file="client.rs",
                ),
            ],
            edges=[],
        )

    def test_merge_creates_calls_service_edges(self) -> None:
        server = self._graph_with_route()
        client = self._graph_with_http_call()
        merged = merge_graphs([("server", server), ("client", client)])

        cs_edges = [
            e for e in merged.edges
            if e.relation == EdgeRelation.CALLS_SERVICE
        ]
        assert len(cs_edges) == 1
        assert cs_edges[0].source == "client::http_call:get:/api/v1/items"
        assert cs_edges[0].target == "server::route:get:/api/v1/items"
        assert cs_edges[0].properties["client_service"] == "client"
        assert cs_edges[0].properties["server_service"] == "server"

    def test_merge_no_resolve_skips_resolution(self) -> None:
        server = self._graph_with_route()
        client = self._graph_with_http_call()
        merged = merge_graphs(
            [("server", server), ("client", client)],
            resolve=False,
        )

        cs_edges = [
            e for e in merged.edges
            if e.relation == EdgeRelation.CALLS_SERVICE
        ]
        assert len(cs_edges) == 0


# endregion: --- TestMergeCrossServiceResolution
