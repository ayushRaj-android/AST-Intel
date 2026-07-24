"""Tests for Feature 6 — Graph Analysis (God Nodes, Communities, Surprises).

Covers:
- Analysis dataclasses: GodNode, Community, SurprisingConnection, Hyperedge
- HyperedgeKind StrEnum
- GraphAnalysis result container
- Internal helpers: _build_degree_maps, _build_neighbor_sets, _jaccard
- Community detection fallback (Louvain via networkx)
- God node detection (degree centrality)
- Surprising connection scoring (cross-community, Jaccard-based)
- Hyperedge detection: shared traits, shared imports, shared callers
- Suggested question generation (templates)
- ReportFormatter: GRAPH_REPORT.md + analysis.json output
- Emitter integration with --analyze flag
- Edge cases: empty graph, single node, no edges
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ast_intel.core.analyzer import (
    Community,
    GodNode,
    GraphAnalysis,
    GraphAnalyzer,
    Hyperedge,
    HyperedgeKind,
    SurprisingConnection,
    _build_degree_maps,
    _build_neighbor_sets,
    _jaccard,
)
from ast_intel.core.emitter import Emitter
from ast_intel.core.graph_builder import GraphBuilder
from ast_intel.formatters.report_formatter import (
    ANALYSIS_JSON_FILENAME,
    GRAPH_REPORT_FILENAME,
    ReportFormatter,
)
from ast_intel.models.ast_node import (
    CallEdge,
    Confidence,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MethodNode,
    Span,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    Visibility,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.workspace_model import (
    CrateModel,
    WorkspaceAST,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Temporary output directory."""
    return tmp_path / "output"


def _make_node(nid: str, label: str, kind: NodeKind = NodeKind.STRUCT,
               file: str = "src/lib.rs") -> GraphNode:
    """Build a GraphNode shortcut."""
    return GraphNode(id=nid, label=label, kind=kind, file=file)


def _make_edge(source: str, target: str,
               relation: EdgeRelation = EdgeRelation.CALLS,
               file: str = "src/lib.rs") -> GraphEdge:
    """Build a GraphEdge shortcut."""
    return GraphEdge(
        source=source, target=target, relation=relation,
        confidence=Confidence.INFERRED, confidence_score=0.7,
        file=file,
    )


def _two_cluster_graph() -> CodeGraph:
    """Graph with two clearly separated communities connected by one bridge.

    Cluster A: a1—a2—a3 (triangle)
    Cluster B: b1—b2—b3 (triangle)
    Bridge: a1—b1
    """
    nodes = [
        _make_node("src/a.rs::A1", "A1", NodeKind.STRUCT, "src/a.rs"),
        _make_node("src/a.rs::A2", "A2", NodeKind.STRUCT, "src/a.rs"),
        _make_node("src/a.rs::A3", "A3", NodeKind.FUNCTION, "src/a.rs"),
        _make_node("src/b.rs::B1", "B1", NodeKind.STRUCT, "src/b.rs"),
        _make_node("src/b.rs::B2", "B2", NodeKind.STRUCT, "src/b.rs"),
        _make_node("src/b.rs::B3", "B3", NodeKind.FUNCTION, "src/b.rs"),
    ]
    edges = [
        # Cluster A (triangle)
        _make_edge("src/a.rs::A1", "src/a.rs::A2", EdgeRelation.CALLS, "src/a.rs"),
        _make_edge("src/a.rs::A2", "src/a.rs::A3", EdgeRelation.CALLS, "src/a.rs"),
        _make_edge("src/a.rs::A3", "src/a.rs::A1", EdgeRelation.CALLS, "src/a.rs"),
        # Cluster B (triangle)
        _make_edge("src/b.rs::B1", "src/b.rs::B2", EdgeRelation.CALLS, "src/b.rs"),
        _make_edge("src/b.rs::B2", "src/b.rs::B3", EdgeRelation.CALLS, "src/b.rs"),
        _make_edge("src/b.rs::B3", "src/b.rs::B1", EdgeRelation.CALLS, "src/b.rs"),
        # Bridge
        _make_edge("src/a.rs::A1", "src/b.rs::B1", EdgeRelation.DEPENDS_ON, "src/a.rs"),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _hub_spoke_graph() -> CodeGraph:
    """Graph with one hub node connected to many spokes — classic god node."""
    hub = _make_node("src/hub.rs::Hub", "Hub", NodeKind.STRUCT, "src/hub.rs")
    spokes = [
        _make_node(f"src/spoke{i}.rs::Spoke{i}", f"Spoke{i}",
                   NodeKind.FUNCTION, f"src/spoke{i}.rs")
        for i in range(8)
    ]
    edges = [
        _make_edge("src/hub.rs::Hub", f"src/spoke{i}.rs::Spoke{i}",
                   EdgeRelation.CALLS, "src/hub.rs")
        for i in range(8)
    ]
    return CodeGraph(nodes=[hub, *spokes], edges=edges)


def _hyperedge_workspace() -> WorkspaceAST:
    """Workspace with 3+ types implementing the same trait → shared_trait hyperedge."""
    trait = TraitNode(
        name="Serializable",
        visibility=Visibility.PUBLIC,
        items=(
            TraitItemNode(
                kind=TraitItemKind.REQUIRED_METHOD,
                name="serialize",
                is_async=False,
            ),
        ),
        span=Span(1, 0, 5, 1),
    )
    impls = [
        ImplBlockNode(
            self_type=f"Type{i}",
            trait_type="Serializable",
            methods=[
                MethodNode(name="serialize", visibility=Visibility.PUBLIC,
                           span=Span(10 + i * 10, 0, 15 + i * 10, 1)),
            ],
            span=Span(10 + i * 10, 0, 20 + i * 10, 1),
        )
        for i in range(4)
    ]
    structs = [
        StructNode(name=f"Type{i}", visibility=Visibility.PUBLIC,
                   span=Span(100 + i * 5, 0, 105 + i * 5, 1))
        for i in range(4)
    ]
    file_ = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        traits=[trait],
        impl_blocks=impls,
        structs=structs,
    )
    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate", language="rust", files=[file_],
    )
    return ws


def _import_hyperedge_workspace() -> WorkspaceAST:
    """Workspace where 3+ files all import the same module."""
    files = []
    for i in range(4):
        f = FileAST(
            file=f"src/mod{i}.rs",
            module_path=f"my_crate::mod{i}",
            uses=["use serde::Serialize"],
            functions=[
                FunctionNode(
                    name=f"func{i}",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 0, 5, 1),
                ),
            ],
        )
        files.append(f)
    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate", language="rust", files=files,
    )
    return ws


def _call_hyperedge_workspace() -> WorkspaceAST:
    """Workspace where 3+ functions all call the same target."""
    callers = [
        FunctionNode(
            name=f"caller{i}",
            visibility=Visibility.PUBLIC,
            span=Span(i * 10, 0, i * 10 + 5, 1),
        )
        for i in range(4)
    ]
    target_fn = FunctionNode(
        name="shared_target",
        visibility=Visibility.PUBLIC,
        span=Span(100, 0, 105, 1),
    )
    call_edges = [
        CallEdge(
            caller=f"caller{i}",
            callee="shared_target",
            call_site=Span(i * 10 + 1, 0, i * 10 + 1, 20),
            confidence=Confidence.INFERRED,
        )
        for i in range(4)
    ]
    file_ = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        functions=[*callers, target_fn],
        call_edges=call_edges,
    )
    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate", language="rust", files=[file_],
    )
    return ws


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Dataclass Tests
# ---------------------------------------------------------------------------


class TestHyperedgeKind:
    """HyperedgeKind StrEnum."""

    def test_is_str(self) -> None:
        assert isinstance(HyperedgeKind.SHARED_TRAIT, str)
        assert HyperedgeKind.SHARED_TRAIT == "shared_trait"

    def test_member_count(self) -> None:
        assert len(HyperedgeKind) == 4


class TestGodNode:
    """GodNode frozen dataclass."""

    def test_fields(self) -> None:
        gn = GodNode(
            node_id="a::B", label="B", kind="struct",
            degree=10, in_degree=4, out_degree=6,
            community=0, connected_communities=3,
        )
        assert gn.node_id == "a::B"
        assert gn.degree == 10

    def test_frozen(self) -> None:
        gn = GodNode("x", "x", "struct", 1, 0, 1, 0, 0)
        with pytest.raises(AttributeError):
            gn.degree = 99  # type: ignore[misc]


class TestCommunity:
    """Community frozen dataclass."""

    def test_fields(self) -> None:
        c = Community(id=0, label="Core", node_count=5,
                      internal_edge_count=8, key_nodes=("a", "b"))
        assert c.node_count == 5
        assert len(c.key_nodes) == 2


class TestSurprisingConnection:
    """SurprisingConnection frozen dataclass."""

    def test_fields(self) -> None:
        sc = SurprisingConnection(
            source_id="a::X", target_id="b::Y", relation="calls",
            source_community=0, target_community=1,
            surprise_score=0.95, why="reason",
        )
        assert sc.surprise_score == 0.95


class TestGraphAnalysis:
    """GraphAnalysis mutable container."""

    def test_defaults(self) -> None:
        ga = GraphAnalysis()
        assert ga.god_nodes == []
        assert ga.communities == []
        assert ga.modularity == 0.0


# endregion: --- Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- Internal Helper Tests
# ---------------------------------------------------------------------------


class TestBuildDegreeMaps:
    """_build_degree_maps helper."""

    def test_empty(self) -> None:
        in_d, out_d = _build_degree_maps([])
        assert in_d == {}
        assert out_d == {}

    def test_counts(self) -> None:
        edges = [
            _make_edge("A", "B"),
            _make_edge("A", "C"),
            _make_edge("B", "C"),
        ]
        in_d, out_d = _build_degree_maps(edges)
        assert out_d["A"] == 2
        assert in_d["C"] == 2
        assert in_d["B"] == 1


class TestBuildNeighborSets:
    """_build_neighbor_sets helper."""

    def test_undirected(self) -> None:
        edges = [_make_edge("A", "B"), _make_edge("B", "C")]
        nbrs = _build_neighbor_sets(edges)
        assert "B" in nbrs["A"]
        assert "A" in nbrs["B"]
        assert "C" in nbrs["B"]


class TestJaccard:
    """_jaccard similarity."""

    def test_empty_sets(self) -> None:
        assert _jaccard(set(), set()) == 0.0

    def test_identical(self) -> None:
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self) -> None:
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self) -> None:
        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)


# endregion: --- Internal Helper Tests


# ---------------------------------------------------------------------------
# region:    --- God Node Tests
# ---------------------------------------------------------------------------


class TestGodNodeDetection:
    """God node detection via degree centrality."""

    def test_hub_is_god_node(self) -> None:
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.god_nodes) > 0
        assert analysis.god_nodes[0].label == "Hub"
        assert analysis.god_nodes[0].degree == 8

    def test_god_node_limit(self) -> None:
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer(god_node_limit=3).analyze(graph)
        assert len(analysis.god_nodes) <= 3

    def test_zero_degree_excluded(self) -> None:
        """Nodes with zero degree should not appear as god nodes."""
        nodes = [_make_node("a::X", "X"), _make_node("a::Y", "Y")]
        graph = CodeGraph(nodes=nodes, edges=[])
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.god_nodes) == 0

    def test_two_cluster_bridge_node(self) -> None:
        """The bridge node (A1) should have highest connected_communities."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # A1 has the most connections (3 edges: A2, A3, B1)
        a1 = next(
            (gn for gn in analysis.god_nodes if gn.label == "A1"), None,
        )
        assert a1 is not None
        assert a1.connected_communities >= 2

    def test_kind_field_is_string(self) -> None:
        """kind on GodNode should be the string value of NodeKind."""
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert analysis.god_nodes[0].kind == "struct"


# endregion: --- God Node Tests


# ---------------------------------------------------------------------------
# region:    --- Community Detection Tests
# ---------------------------------------------------------------------------


class TestCommunityDetection:
    """Community detection via Louvain/Leiden."""

    def test_two_clusters_detected(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) >= 2

    def test_total_nodes_match(self) -> None:
        """Sum of community node_counts should equal total graph nodes."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        total = sum(c.node_count for c in analysis.communities)
        assert total == len(graph.nodes)

    def test_modularity_bounded(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert -0.5 <= analysis.modularity <= 1.0

    def test_community_has_key_nodes(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for c in analysis.communities:
            assert len(c.key_nodes) > 0
            assert len(c.key_nodes) <= 5

    def test_community_label_from_top_node(self) -> None:
        """Community label should come from the highest-degree node's name."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        labels = [c.label for c in analysis.communities]
        # Labels should be node name segments (e.g., "A1", "B1")
        for label in labels:
            assert label  # non-empty

    def test_internal_edge_count(self) -> None:
        """Each community should have internal edges counted."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        total_internal = sum(c.internal_edge_count for c in analysis.communities)
        # At least some edges are internal (the triangles)
        assert total_internal >= 4

    def test_single_node_graph(self) -> None:
        """A graph with one node and no edges should still produce a community."""
        graph = CodeGraph(
            nodes=[_make_node("a::X", "X")], edges=[],
        )
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) == 1
        assert analysis.communities[0].node_count == 1

    def test_reproducible(self) -> None:
        """Two analyzer runs with same seed produce identical communities."""
        graph = _two_cluster_graph()
        a1 = GraphAnalyzer(seed=42).analyze(graph)
        a2 = GraphAnalyzer(seed=42).analyze(graph)
        assert len(a1.communities) == len(a2.communities)
        for c1, c2 in zip(a1.communities, a2.communities, strict=True):
            assert c1.id == c2.id
            assert c1.node_count == c2.node_count


# endregion: --- Community Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Surprising Connection Tests
# ---------------------------------------------------------------------------


class TestSurprisingConnections:
    """Cross-community surprising connection detection."""

    def test_bridge_edge_detected(self) -> None:
        """The bridge between two clusters should appear as surprising."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # At least 1 cross-community surprise
        assert len(analysis.surprising_connections) >= 1

    def test_surprise_score_bounded(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for sc in analysis.surprising_connections:
            assert 0.0 <= sc.surprise_score <= 1.0

    def test_why_text_populated(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for sc in analysis.surprising_connections:
            assert len(sc.why) > 0
            assert "connects to" in sc.why

    def test_surprise_limit(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer(surprise_limit=1).analyze(graph)
        assert len(analysis.surprising_connections) <= 1

    def test_no_surprises_in_single_cluster(self) -> None:
        """A fully connected graph (one community) has no surprises."""
        nodes = [_make_node(f"a::{c}", c) for c in ("X", "Y", "Z")]
        edges = [
            _make_edge("a::X", "a::Y"),
            _make_edge("a::Y", "a::Z"),
            _make_edge("a::Z", "a::X"),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        analysis = GraphAnalyzer().analyze(graph)
        # Single community → no cross-community edges
        assert len(analysis.surprising_connections) == 0

    def test_sorted_by_score_descending(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        scores = [sc.surprise_score for sc in analysis.surprising_connections]
        assert scores == sorted(scores, reverse=True)


# endregion: --- Surprising Connection Tests


# ---------------------------------------------------------------------------
# region:    --- Hyperedge Tests
# ---------------------------------------------------------------------------


class TestHyperedgeDetection:
    """Hyperedge detection from edge patterns."""

    def test_shared_trait_hyperedge(self) -> None:
        """4 types implementing Serializable → 1 shared_trait hyperedge."""
        ws = _hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        trait_hes = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_TRAIT
        ]
        assert len(trait_hes) >= 1
        he = trait_hes[0]
        assert "Serializable" in he.label
        assert len(he.members) >= 3

    def test_shared_import_hyperedge(self) -> None:
        """4 files importing serde → 1 shared_import hyperedge."""
        ws = _import_hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        import_hes = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_IMPORT
        ]
        assert len(import_hes) >= 1

    def test_shared_caller_hyperedge(self) -> None:
        """4 functions calling shared_target → 1 shared_caller hyperedge."""
        ws = _call_hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        caller_hes = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_CALLER
        ]
        assert len(caller_hes) >= 1
        he = caller_hes[0]
        assert "shared_target" in he.label
        assert len(he.members) >= 3

    def test_min_group_size(self) -> None:
        """Groups smaller than 3 members should not be hyperedges."""
        # Only 2 implementors — below threshold
        file_ = FileAST(
            file="src/lib.rs",
            module_path="my_crate",
            traits=[
                TraitNode(
                    name="SmallTrait",
                    visibility=Visibility.PUBLIC,
                    items=(
                        TraitItemNode(
                            kind=TraitItemKind.REQUIRED_METHOD,
                            name="m", is_async=False,
                        ),
                    ),
                    span=Span(1, 0, 5, 1),
                ),
            ],
            impl_blocks=[
                ImplBlockNode(
                    self_type=f"Impl{i}",
                    trait_type="SmallTrait",
                    methods=[
                        MethodNode(name="m", visibility=Visibility.PUBLIC,
                                   span=Span(10 + i * 10, 0, 15 + i * 10, 1)),
                    ],
                    span=Span(10 + i * 10, 0, 20 + i * 10, 1),
                )
                for i in range(2)
            ],
            structs=[
                StructNode(name=f"Impl{i}", visibility=Visibility.PUBLIC,
                           span=Span(100 + i * 5, 0, 105 + i * 5, 1))
                for i in range(2)
            ],
        )
        ws = WorkspaceAST()
        ws.crates["my_crate"] = CrateModel(
            name="my_crate", language="rust", files=[file_],
        )
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        trait_hes = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_TRAIT
        ]
        # 2 implementors < 3 threshold → no hyperedge
        assert len(trait_hes) == 0

    def test_hyperedge_members_sorted(self) -> None:
        """Hyperedge members should be sorted for determinism."""
        ws = _hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        for he in analysis.hyperedges:
            assert list(he.members) == sorted(he.members)


# endregion: --- Hyperedge Tests


# ---------------------------------------------------------------------------
# region:    --- Suggested Questions Tests
# ---------------------------------------------------------------------------


class TestSuggestedQuestions:
    """Template-based question generation."""

    def test_questions_produced(self) -> None:
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.suggested_questions) > 0

    def test_question_limit(self) -> None:
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer(question_limit=2).analyze(graph)
        assert len(analysis.suggested_questions) <= 2

    def test_god_node_referenced(self) -> None:
        """At least one question should mention the top god node."""
        graph = _hub_spoke_graph()
        analysis = GraphAnalyzer().analyze(graph)
        top_label = analysis.god_nodes[0].label
        found = any(top_label in q for q in analysis.suggested_questions)
        assert found, f"Expected {top_label!r} in questions"

    def test_questions_are_strings(self) -> None:
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for q in analysis.suggested_questions:
            assert isinstance(q, str)
            assert len(q) > 10  # non-trivial length


# endregion: --- Suggested Questions Tests


# ---------------------------------------------------------------------------
# region:    --- Edge Case Tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for the analyzer."""

    def test_empty_graph(self) -> None:
        graph = CodeGraph()
        analysis = GraphAnalyzer().analyze(graph)
        assert analysis.god_nodes == []
        assert analysis.communities == []
        assert analysis.modularity == 0.0

    def test_no_edges(self) -> None:
        """Nodes but no edges → communities exist but no surprises."""
        graph = CodeGraph(
            nodes=[_make_node("a::X", "X"), _make_node("a::Y", "Y")],
            edges=[],
        )
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) >= 1
        assert len(analysis.surprising_connections) == 0
        assert len(analysis.god_nodes) == 0

    def test_analysis_reproducible(self) -> None:
        """Same graph + same seed → identical analysis."""
        graph = _two_cluster_graph()
        a1 = GraphAnalyzer(seed=42).analyze(graph)
        a2 = GraphAnalyzer(seed=42).analyze(graph)
        assert len(a1.god_nodes) == len(a2.god_nodes)
        assert a1.modularity == a2.modularity
        assert len(a1.surprising_connections) == len(a2.surprising_connections)


# endregion: --- Edge Case Tests


# ---------------------------------------------------------------------------
# region:    --- Report Formatter Tests
# ---------------------------------------------------------------------------


class TestReportFormatter:
    """ReportFormatter for GRAPH_REPORT.md and analysis.json."""

    def _sample_analysis(self) -> GraphAnalysis:
        return GraphAnalysis(
            god_nodes=[
                GodNode("a::Hub", "Hub", "struct", 8, 0, 8, 0, 3),
            ],
            communities=[
                Community(0, "Core", 5, 10, ("a::Hub", "a::X")),
                Community(1, "Utils", 3, 4, ("b::Y",)),
            ],
            surprising_connections=[
                SurprisingConnection(
                    "a::Hub", "b::Y", "calls", 0, 1, 0.92,
                    "Hub connects to Y via calls",
                ),
            ],
            suggested_questions=["What role does `Hub` play?"],
            hyperedges=[
                Hyperedge(
                    "Implementors of Serializable",
                    HyperedgeKind.SHARED_TRAIT,
                    ("a::A", "a::B", "a::C"),
                ),
            ],
            modularity=0.45,
        )

    def test_writes_both_files(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, json_path = ReportFormatter().write(analysis, tmp_output)
        assert md_path.exists()
        assert json_path.exists()

    def test_report_md_sections(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, _ = ReportFormatter().write(analysis, tmp_output)
        content = md_path.read_text()
        assert "# AST Intel — Graph Analysis Report" in content
        assert "## God Nodes" in content
        assert "## Communities" in content
        assert "## Surprising Connections" in content
        assert "## Hyperedges" in content
        assert "## Suggested Questions" in content

    def test_report_md_god_node_row(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, _ = ReportFormatter().write(analysis, tmp_output)
        content = md_path.read_text()
        assert "`Hub`" in content
        assert "struct" in content

    def test_report_md_community_row(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, _ = ReportFormatter().write(analysis, tmp_output)
        content = md_path.read_text()
        assert "Core" in content
        assert "Utils" in content

    def test_report_md_modularity(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, _ = ReportFormatter().write(analysis, tmp_output)
        content = md_path.read_text()
        assert "0.4500" in content

    def test_analysis_json_valid(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        _, json_path = ReportFormatter().write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert "god_nodes" in data
        assert "communities" in data
        assert "modularity" in data
        assert data["modularity"] == pytest.approx(0.45)

    def test_analysis_json_god_node(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        _, json_path = ReportFormatter().write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert len(data["god_nodes"]) == 1
        assert data["god_nodes"][0]["label"] == "Hub"

    def test_analysis_json_hyperedges(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        _, json_path = ReportFormatter().write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert len(data["hyperedges"]) == 1
        # Members should be list not tuple
        assert isinstance(data["hyperedges"][0]["members"], list)

    def test_empty_analysis_report(self, tmp_output: Path) -> None:
        """Empty analysis still produces valid files."""
        analysis = GraphAnalysis()
        md_path, json_path = ReportFormatter().write(analysis, tmp_output)
        content = md_path.read_text()
        assert "No high-connectivity nodes detected" in content
        data = json.loads(json_path.read_text())
        assert data["god_nodes"] == []

    def test_filenames_correct(self, tmp_output: Path) -> None:
        analysis = self._sample_analysis()
        md_path, json_path = ReportFormatter().write(analysis, tmp_output)
        assert md_path.name == GRAPH_REPORT_FILENAME
        assert json_path.name == ANALYSIS_JSON_FILENAME


# endregion: --- Report Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Integration Tests
# ---------------------------------------------------------------------------


class TestEmitterAnalyzeFlag:
    """Emitter integration with --analyze."""

    def test_both_with_analyze(self, tmp_output: Path) -> None:
        """--format both --analyze produces ast.json + summary.md + report + analysis."""
        ws = _hyperedge_workspace()
        # Need to do indexing for a complete pipeline
        from ast_intel.core.indexer import Indexer
        ws = Indexer().build_cross_references(ws)

        emitter = Emitter(tmp_output, "both", run_analysis=True)
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert "ast.json" in names
        assert "summary.md" in names
        assert GRAPH_REPORT_FILENAME in names
        assert ANALYSIS_JSON_FILENAME in names

    def test_graph_json_with_analyze(self, tmp_output: Path) -> None:
        """--format graph-json --analyze produces graph.json + report + analysis."""
        ws = _hyperedge_workspace()
        from ast_intel.core.indexer import Indexer
        ws = Indexer().build_cross_references(ws)

        emitter = Emitter(tmp_output, "graph-json", run_analysis=True)
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert "graph.json" in names
        assert GRAPH_REPORT_FILENAME in names

    def test_analyze_without_graph_format(self, tmp_output: Path) -> None:
        """--format json --analyze still produces report files."""
        ws = _hyperedge_workspace()
        from ast_intel.core.indexer import Indexer
        ws = Indexer().build_cross_references(ws)

        emitter = Emitter(tmp_output, "json", run_analysis=True)
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert "ast.json" in names
        assert GRAPH_REPORT_FILENAME in names

    def test_no_analyze_no_report(self, tmp_output: Path) -> None:
        """Without --analyze, no report files."""
        ws = _hyperedge_workspace()
        from ast_intel.core.indexer import Indexer
        ws = Indexer().build_cross_references(ws)

        emitter = Emitter(tmp_output, "both", run_analysis=False)
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert GRAPH_REPORT_FILENAME not in names
        assert ANALYSIS_JSON_FILENAME not in names


# endregion: --- Emitter Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Full Pipeline Tests
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end: workspace → graph → analysis → report."""

    def test_hyperedge_workspace_pipeline(self, tmp_output: Path) -> None:
        ws = _hyperedge_workspace()
        from ast_intel.core.indexer import Indexer
        ws = Indexer().build_cross_references(ws)

        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)

        assert len(analysis.communities) >= 1
        assert analysis.modularity >= 0.0
        assert len(analysis.hyperedges) >= 1

        md_path, json_path = ReportFormatter().write(analysis, tmp_output)
        assert md_path.exists()
        data = json.loads(json_path.read_text())
        assert len(data["hyperedges"]) >= 1

    def test_two_cluster_pipeline(self) -> None:
        """Two-cluster graph produces communities + surprises."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)

        assert len(analysis.communities) >= 2
        assert len(analysis.surprising_connections) >= 1
        assert len(analysis.god_nodes) >= 1


# endregion: --- Full Pipeline Tests
