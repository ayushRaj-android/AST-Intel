"""Tests for hyperedge detection (Feature 25)."""

from __future__ import annotations

import json
from pathlib import Path

from ast_intel.core._hyperedge import (
    _detect_field_groups,
    _detect_flows,
    _detect_implements_groups,
    _detect_route_groups,
    _extract_prefix,
    _persist_communities,
    _sanitize,
    detect_hyperedges,
)
from ast_intel.formatters.graph_json_formatter import GraphJsonFormatter
from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    HyperEdge,
    HyperRelation,
    NodeKind,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, kind: NodeKind = NodeKind.FUNCTION, label: str = "") -> GraphNode:
    return GraphNode(id=node_id, label=label or node_id.rsplit("::", maxsplit=1)[-1], kind=kind)


def _edge(
    source: str,
    target: str,
    relation: EdgeRelation = EdgeRelation.CALLS,
) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relation=relation,
        confidence=Confidence.EXTRACTED,
        confidence_score=1.0,
    )


# endregion: --- Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Implements Groups
# ---------------------------------------------------------------------------


class TestImplementsGroups:
    """Tests for trait/interface implementor group detection."""

    def test_basic_group(self) -> None:
        trait = _node("mod::Serializable", NodeKind.TRAIT, "Serializable")
        impls = [
            _node(f"mod::Impl{i}", NodeKind.STRUCT, f"Impl{i}")
            for i in range(4)
        ]
        edges = [
            _edge(impl.id, trait.id, EdgeRelation.IMPLEMENTS)
            for impl in impls
        ]
        graph = CodeGraph(nodes=[trait, *impls], edges=edges)
        result = _detect_implements_groups(graph, min_size=3)
        assert len(result) == 1
        he = result[0]
        assert he.relation == HyperRelation.IMPLEMENTS_GROUP
        assert trait.id in he.members
        assert all(impl.id in he.members for impl in impls)
        assert "Serializable" in he.label

    def test_below_min_size(self) -> None:
        trait = _node("mod::Trait", NodeKind.TRAIT, "Trait")
        impls = [_node(f"mod::A{i}", NodeKind.STRUCT) for i in range(2)]
        edges = [
            _edge(impl.id, trait.id, EdgeRelation.IMPLEMENTS)
            for impl in impls
        ]
        graph = CodeGraph(nodes=[trait, *impls], edges=edges)
        result = _detect_implements_groups(graph, min_size=3)
        assert result == []

    def test_inherits_also_counted(self) -> None:
        base = _node("mod::Base", NodeKind.STRUCT, "Base")
        children = [_node(f"mod::Child{i}", NodeKind.STRUCT) for i in range(3)]
        edges = [
            _edge(c.id, base.id, EdgeRelation.INHERITS)
            for c in children
        ]
        graph = CodeGraph(nodes=[base, *children], edges=edges)
        result = _detect_implements_groups(graph, min_size=3)
        assert len(result) == 1
        assert result[0].relation == HyperRelation.IMPLEMENTS_GROUP


# endregion: --- Implements Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Route Groups
# ---------------------------------------------------------------------------


class TestRouteGroups:
    """Tests for route grouping by URL prefix."""

    def test_basic_route_group(self) -> None:
        nodes = [
            GraphNode(
                id=f"routes::get_{i}",
                label=f"GET /api/v1/item{i}",
                kind=NodeKind.ROUTE,
                properties={"path": f"/api/v1/item{i}"},
            )
            for i in range(4)
        ]
        graph = CodeGraph(nodes=nodes, edges=[])
        result = _detect_route_groups(graph, min_size=3)
        assert len(result) == 1
        he = result[0]
        assert he.relation == HyperRelation.ROUTE_GROUP
        assert "/api/v1" in he.label
        assert len(he.members) == 4

    def test_different_prefixes(self) -> None:
        api_nodes = [
            GraphNode(
                id=f"r::api{i}",
                label=f"/api/v1/x{i}",
                kind=NodeKind.ROUTE,
                properties={"path": f"/api/v1/x{i}"},
            )
            for i in range(3)
        ]
        auth_nodes = [
            GraphNode(
                id=f"r::auth{i}",
                label=f"/auth/v2/y{i}",
                kind=NodeKind.ROUTE,
                properties={"path": f"/auth/v2/y{i}"},
            )
            for i in range(3)
        ]
        graph = CodeGraph(nodes=[*api_nodes, *auth_nodes], edges=[])
        result = _detect_route_groups(graph, min_size=3)
        assert len(result) == 2

    def test_below_min_size(self) -> None:
        nodes = [
            GraphNode(
                id=f"r::{i}",
                label=f"/a/b/{i}",
                kind=NodeKind.ROUTE,
                properties={"path": f"/a/b/{i}"},
            )
            for i in range(2)
        ]
        graph = CodeGraph(nodes=nodes, edges=[])
        result = _detect_route_groups(graph, min_size=3)
        assert result == []


# endregion: --- Route Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Flow Detection
# ---------------------------------------------------------------------------


class TestFlowDetection:
    """Tests for linear call chain detection."""

    def test_basic_chain(self) -> None:
        """A→B→C→D should produce one flow hyperedge."""
        nodes = [_node(f"m::f{i}") for i in range(4)]
        edges = [
            _edge(nodes[i].id, nodes[i + 1].id, EdgeRelation.CALLS)
            for i in range(3)
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        result = _detect_flows(graph, min_size=3)
        assert len(result) == 1
        he = result[0]
        assert he.relation == HyperRelation.FLOW
        assert len(he.members) == 4
        # Preserves order
        assert he.members == [n.id for n in nodes]

    def test_branching_breaks_chain(self) -> None:
        """A→B, A→C should not form a chain (branching)."""
        a = _node("m::a")
        b = _node("m::b")
        c = _node("m::c")
        d = _node("m::d")
        edges = [
            _edge(a.id, b.id, EdgeRelation.CALLS),
            _edge(a.id, c.id, EdgeRelation.CALLS),
            _edge(c.id, d.id, EdgeRelation.CALLS),
        ]
        graph = CodeGraph(nodes=[a, b, c, d], edges=edges)
        result = _detect_flows(graph, min_size=3)
        assert result == []

    def test_short_chain_excluded(self) -> None:
        """A→B (length 2) is below default min_size=3."""
        nodes = [_node(f"m::x{i}") for i in range(2)]
        edges = [_edge(nodes[0].id, nodes[1].id, EdgeRelation.CALLS)]
        graph = CodeGraph(nodes=nodes, edges=edges)
        result = _detect_flows(graph, min_size=3)
        assert result == []


# endregion: --- Flow Detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Field Groups
# ---------------------------------------------------------------------------


class TestFieldGroups:
    """Tests for shared field type grouping."""

    def test_basic_field_group(self) -> None:
        type_node = _node("types::DateTime", NodeKind.STRUCT, "DateTime")
        structs = [_node(f"mod::Struct{i}", NodeKind.STRUCT) for i in range(3)]
        edges = [
            _edge(s.id, type_node.id, EdgeRelation.HAS_FIELD)
            for s in structs
        ]
        graph = CodeGraph(nodes=[type_node, *structs], edges=edges)
        result = _detect_field_groups(graph, min_size=3)
        assert len(result) == 1
        assert result[0].relation == HyperRelation.FIELD_GROUP
        assert "DateTime" in result[0].label

    def test_duplicate_struct_deduplicated(self) -> None:
        """Same struct with multiple fields of same type counted once."""
        type_node = _node("types::Str", NodeKind.STRUCT, "Str")
        structs = [_node(f"mod::S{i}", NodeKind.STRUCT) for i in range(3)]
        # S0 has two fields of type Str
        edges = [
            _edge(structs[0].id, type_node.id, EdgeRelation.HAS_FIELD),
            _edge(structs[0].id, type_node.id, EdgeRelation.HAS_FIELD),
            _edge(structs[1].id, type_node.id, EdgeRelation.HAS_FIELD),
            _edge(structs[2].id, type_node.id, EdgeRelation.HAS_FIELD),
        ]
        graph = CodeGraph(nodes=[type_node, *structs], edges=edges)
        result = _detect_field_groups(graph, min_size=3)
        assert len(result) == 1
        assert len(result[0].members) == 3


# endregion: --- Field Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Community Persistence
# ---------------------------------------------------------------------------


class TestCommunityPersistence:
    """Tests for Leiden community persistence."""

    def test_basic_communities(self) -> None:
        community_map = {
            "a": 0, "b": 0, "c": 0,
            "d": 1, "e": 1, "f": 1, "g": 1,
        }
        result = _persist_communities(community_map, min_size=3)
        assert len(result) == 2
        assert all(he.relation == HyperRelation.COMMUNITY for he in result)

    def test_small_community_excluded(self) -> None:
        community_map = {"a": 0, "b": 0, "c": 1}
        result = _persist_communities(community_map, min_size=3)
        assert result == []


# endregion: --- Community Persistence
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Top-Level detect_hyperedges
# ---------------------------------------------------------------------------


class TestDetectHyperedges:
    """Integration tests for the full detect_hyperedges function."""

    def test_empty_graph(self) -> None:
        graph = CodeGraph(nodes=[], edges=[])
        result = detect_hyperedges(graph)
        assert result == []

    def test_with_community_map(self) -> None:
        graph = CodeGraph(nodes=[], edges=[])
        community_map = {"a": 0, "b": 0, "c": 0, "d": 0}
        result = detect_hyperedges(graph, community_map=community_map)
        assert len(result) == 1
        assert result[0].relation == HyperRelation.COMMUNITY

    def test_custom_min_group_size(self) -> None:
        """Increasing min_group_size should exclude smaller groups."""
        trait = _node("m::T", NodeKind.TRAIT, "T")
        impls = [_node(f"m::I{i}", NodeKind.STRUCT) for i in range(3)]
        edges = [
            _edge(impl.id, trait.id, EdgeRelation.IMPLEMENTS)
            for impl in impls
        ]
        graph = CodeGraph(nodes=[trait, *impls], edges=edges)
        # min_group_size=3 → found
        assert len(detect_hyperedges(graph, min_group_size=3)) >= 1
        # min_group_size=4 → excluded
        assert len(detect_hyperedges(graph, min_group_size=4)) == 0

    def test_results_sorted(self) -> None:
        """Results should be sorted by (relation, id)."""
        graph = CodeGraph(nodes=[], edges=[])
        community_map = {
            f"n{i}": i // 3 for i in range(9)
        }
        result = detect_hyperedges(graph, community_map=community_map)
        ids = [he.id for he in result]
        assert ids == sorted(ids)


# endregion: --- Top-Level detect_hyperedges
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Serialization Roundtrip
# ---------------------------------------------------------------------------


class TestSerializationRoundtrip:
    """Test hyperedge JSON serialization/deserialization."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Hyperedges survive a write→read cycle."""
        nodes = [_node("m::a"), _node("m::b")]
        edges = [_edge("m::a", "m::b")]
        hyperedges = [
            HyperEdge(
                id="he::test::group1",
                relation=HyperRelation.IMPLEMENTS_GROUP,
                members=["m::a", "m::b"],
                label="Test group",
                metadata={"key": "value"},
            ),
        ]
        graph = CodeGraph(
            nodes=nodes, edges=edges, hyperedges=hyperedges,
        )
        out = tmp_path / "graph.json"
        GraphJsonFormatter().write(graph, out)
        loaded = GraphJsonFormatter.read(out)
        assert len(loaded.hyperedges) == 1
        he = loaded.hyperedges[0]
        assert he.id == "he::test::group1"
        assert he.relation == HyperRelation.IMPLEMENTS_GROUP
        assert he.members == ["m::a", "m::b"]
        assert he.label == "Test group"
        assert he.metadata == {"key": "value"}

    def test_empty_hyperedges_omitted(self, tmp_path: Path) -> None:
        """Empty hyperedges list should NOT appear in JSON output."""
        graph = CodeGraph(
            nodes=[_node("m::x")],
            edges=[],
            hyperedges=[],
        )
        out = tmp_path / "graph.json"
        GraphJsonFormatter().write(graph, out)
        with out.open() as f:
            data = json.load(f)
        assert "hyperedges" not in data

    def test_backward_compat_no_key(self, tmp_path: Path) -> None:
        """Old JSON without 'hyperedges' key should load with empty list."""
        data: dict[str, object] = {
            "meta": {},
            "nodes": [],
            "edges": [],
        }
        out = tmp_path / "graph.json"
        out.write_text(json.dumps(data))
        loaded = GraphJsonFormatter.read(out)
        assert loaded.hyperedges == []


# endregion: --- Serialization Roundtrip
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helper Functions
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for internal helper functions."""

    def test_sanitize_removes_special_chars(self) -> None:
        assert _sanitize("Hello World!") == "Hello_World_"

    def test_sanitize_preserves_safe_chars(self) -> None:
        assert _sanitize("foo/bar-baz_123") == "foo/bar-baz_123"

    def test_sanitize_truncates(self) -> None:
        long = "a" * 200
        assert len(_sanitize(long)) == 120

    def test_extract_prefix_two_segments(self) -> None:
        assert _extract_prefix("/api/v1/users") == "/api/v1"

    def test_extract_prefix_one_segment(self) -> None:
        assert _extract_prefix("/health") == "/health"

    def test_extract_prefix_empty(self) -> None:
        assert _extract_prefix("") == ""


# endregion: --- Helper Functions
# ---------------------------------------------------------------------------
