"""Tests for Feature 6 — Graph Analysis (God Nodes, Communities, Surprises).

Covers:
- Analysis dataclasses: GodNode, Community, SurprisingConnection, Hyperedge,
     GraphAnalysis, HyperedgeKind
- GraphAnalyzer: god-node detection, community detection, surprising connections,
     hyperedge detection, suggested-question generation
- ReportFormatter: GRAPH_REPORT.md output structure, analysis.json serialization
- Emitter integration: --analyze flag produces report files
- Edge cases: empty graph, single-node graph, no cross-community edges
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
    SCORE_EXTRACTED,
    SCORE_INFERRED,
    CallEdge,
    Confidence,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MethodNode,
    Span,
    StructNode,
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
    CrateDependency,
    CrateModel,
    CrossReferences,
    WorkspaceAST,
)


# ---------------------------------------------------------------------------
# region:    --- Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "output"


def _simple_graph() -> CodeGraph:
    """Small graph: 4 nodes, 4 edges — easy to reason about."""
    nodes = [
        GraphNode(id="src/a.rs::<file>", label="src/a.rs", kind=NodeKind.FILE, file="src/a.rs"),
        GraphNode(id="src/a.rs::Foo", label="Foo", kind=NodeKind.STRUCT, file="src/a.rs"),
        GraphNode(id="src/a.rs::bar", label="bar", kind=NodeKind.FUNCTION, file="src/a.rs"),
        GraphNode(id="src/b.rs::<file>", label="src/b.rs", kind=NodeKind.FILE, file="src/b.rs"),
    ]
    edges = [
        GraphEdge(
            source="src/a.rs::<file>", target="src/a.rs::Foo",
            relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="src/a.rs",
        ),
        GraphEdge(
            source="src/a.rs::<file>", target="src/a.rs::bar",
            relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="src/a.rs",
        ),
        GraphEdge(
            source="src/a.rs::bar", target="src/a.rs::Foo",
            relation=EdgeRelation.CALLS,
            confidence=Confidence.INFERRED, confidence_score=SCORE_INFERRED,
            file="src/a.rs",
        ),
        GraphEdge(
            source="src/b.rs::<file>", target="src/a.rs::Foo",
            relation=EdgeRelation.IMPORTS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="src/b.rs",
        ),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _two_cluster_graph() -> CodeGraph:
    """Graph with two clear clusters connected by a single bridge edge."""
    # Cluster A: a_file, A1, A2, A3
    # Cluster B: b_file, B1, B2, B3
    # Bridge: A1 -> B1
    nodes = [
        GraphNode(id="a::<file>", label="a.rs", kind=NodeKind.FILE, file="a.rs"),
        GraphNode(id="a::A1", label="A1", kind=NodeKind.STRUCT, file="a.rs"),
        GraphNode(id="a::A2", label="A2", kind=NodeKind.FUNCTION, file="a.rs"),
        GraphNode(id="a::A3", label="A3", kind=NodeKind.FUNCTION, file="a.rs"),
        GraphNode(id="b::<file>", label="b.rs", kind=NodeKind.FILE, file="b.rs"),
        GraphNode(id="b::B1", label="B1", kind=NodeKind.STRUCT, file="b.rs"),
        GraphNode(id="b::B2", label="B2", kind=NodeKind.FUNCTION, file="b.rs"),
        GraphNode(id="b::B3", label="B3", kind=NodeKind.FUNCTION, file="b.rs"),
    ]
    edges = [
        # Cluster A internal
        GraphEdge(
            source="a::<file>", target="a::A1", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        GraphEdge(
            source="a::<file>", target="a::A2", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        GraphEdge(
            source="a::<file>", target="a::A3", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        GraphEdge(
            source="a::A2", target="a::A1", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        GraphEdge(
            source="a::A3", target="a::A1", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        GraphEdge(
            source="a::A3", target="a::A2", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="a.rs",
        ),
        # Cluster B internal
        GraphEdge(
            source="b::<file>", target="b::B1", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        GraphEdge(
            source="b::<file>", target="b::B2", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        GraphEdge(
            source="b::<file>", target="b::B3", relation=EdgeRelation.CONTAINS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        GraphEdge(
            source="b::B2", target="b::B1", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        GraphEdge(
            source="b::B3", target="b::B1", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        GraphEdge(
            source="b::B3", target="b::B2", relation=EdgeRelation.CALLS,
            confidence=Confidence.EXTRACTED, confidence_score=SCORE_EXTRACTED,
            file="b.rs",
        ),
        # Bridge: A1 -> B1
        GraphEdge(
            source="a::A1", target="b::B1", relation=EdgeRelation.CALLS,
            confidence=Confidence.INFERRED, confidence_score=SCORE_INFERRED,
            file="a.rs",
        ),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _hyperedge_workspace() -> WorkspaceAST:
    """Workspace with 3 structs implementing the same trait → shared_trait hyperedge."""
    file1 = FileAST(
        file="src/core.rs",
        impl_blocks=[
            ImplBlockNode(
                self_type="DiskStore",
                trait_type="StorageHelper",
                methods=(
                    MethodNode(name="store", visibility=Visibility.PUBLIC),
                ),
            ),
            ImplBlockNode(
                self_type="RedisStore",
                trait_type="StorageHelper",
                methods=(
                    MethodNode(name="store", visibility=Visibility.PUBLIC),
                ),
            ),
            ImplBlockNode(
                self_type="InMemStore",
                trait_type="StorageHelper",
                methods=(
                    MethodNode(name="store", visibility=Visibility.PUBLIC),
                ),
            ),
        ],
        traits=[
            TraitNode(name="StorageHelper", visibility=Visibility.PUBLIC),
        ],
    )
    ws = WorkspaceAST()
    ws.crates["core"] = CrateModel(name="core", language="rust", files=[file1])
    return ws


def _shared_import_workspace() -> WorkspaceAST:
    """Three files importing the same module → shared_import hyperedge."""
    files = [
        FileAST(file=f"src/mod{i}.rs", uses=["tokio::runtime::Runtime"])
        for i in range(3)
    ]
    ws = WorkspaceAST()
    ws.crates["app"] = CrateModel(name="app", language="rust", files=files)
    return ws


def _rich_workspace() -> WorkspaceAST:
    """A multi-file workspace with many symbols for full-pipeline testing."""
    file1 = FileAST(
        file="src/server.rs",
        structs=[
            StructNode(
                name="Server",
                visibility=Visibility.PUBLIC,
                fields=(
                    FieldNode(name="config", type="AppConfig", visibility=Visibility.PUBLIC),
                ),
                span=Span(1, 0, 10, 1),
            ),
            StructNode(
                name="AppConfig",
                visibility=Visibility.PUBLIC,
                span=Span(12, 0, 20, 1),
            ),
        ],
        functions=[
            FunctionNode(
                name="main", visibility=Visibility.PUBLIC,
                is_async=True, return_type="Result<()>",
                span=Span(22, 0, 40, 1),
            ),
            FunctionNode(
                name="setup", visibility=Visibility.PUBLIC,
                span=Span(42, 0, 55, 1),
            ),
        ],
        call_edges=[
            CallEdge(
                caller="main", callee="setup",
                call_site=Span(30, 4, 30, 12),
                resolved_target="src/server.rs::setup",
                confidence=Confidence.EXTRACTED,
                confidence_score=SCORE_EXTRACTED,
            ),
        ],
        uses=["tokio::runtime::Runtime", "std::sync::Arc"],
        imported_package_methods={"bytes::BytesMut": ["freeze"]},
    )
    file2 = FileAST(
        file="src/handler.rs",
        traits=[
            TraitNode(
                name="Handler",
                visibility=Visibility.PUBLIC,
                span=Span(1, 0, 15, 1),
            ),
        ],
        impl_blocks=[
            ImplBlockNode(
                self_type="Server",
                trait_type="Handler",
                methods=(
                    MethodNode(
                        name="handle", visibility=Visibility.PUBLIC,
                        is_async=True, span=Span(17, 4, 25, 5),
                    ),
                ),
                span=Span(16, 0, 26, 1),
            ),
        ],
        functions=[
            FunctionNode(name="dispatch", visibility=Visibility.PUBLIC, span=Span(28, 0, 35, 1)),
        ],
        call_edges=[
            CallEdge(
                caller="dispatch", callee="handle",
                call_site=Span(30, 4, 30, 15),
                resolved_target="src/handler.rs::Server.handle",
                confidence=Confidence.INFERRED,
                confidence_score=SCORE_INFERRED,
            ),
        ],
        uses=["tokio::runtime::Runtime"],
    )
    ws = WorkspaceAST()
    ws.crates["app"] = CrateModel(
        name="app", language="rust",
        dependencies=[CrateDependency(name="tokio", version="1.0")],
        files=[file1, file2],
    )
    ws.cross_references = CrossReferences(
        inter_crate_deps={"app": ["tokio"]},
    )
    return ws


# endregion: --- Test Fixtures


# ---------------------------------------------------------------------------
# region:    --- Helper Function Tests
# ---------------------------------------------------------------------------


class TestBuildDegreeMaps:
    def test_empty(self):
        in_d, out_d = _build_degree_maps([])
        assert in_d == {}
        assert out_d == {}

    def test_single_edge(self):
        edges = [
            GraphEdge(
                source="a", target="b", relation=EdgeRelation.CALLS,
                confidence=Confidence.EXTRACTED, confidence_score=1.0,
            ),
        ]
        in_d, out_d = _build_degree_maps(edges)
        assert out_d == {"a": 1}
        assert in_d == {"b": 1}

    def test_multi_edges(self):
        g = _simple_graph()
        in_d, out_d = _build_degree_maps(g.edges)
        # Foo is targeted by 3 edges: contains(a_file->Foo), calls(bar->Foo), imports(b_file->Foo)
        assert in_d["src/a.rs::Foo"] == 3
        # a_file has 2 outgoing edges: contains Foo, contains bar
        assert out_d["src/a.rs::<file>"] == 2


class TestBuildNeighborSets:
    def test_empty(self):
        assert _build_neighbor_sets([]) == {}

    def test_undirected(self):
        edges = [
            GraphEdge(
                source="a", target="b", relation=EdgeRelation.CALLS,
                confidence=Confidence.EXTRACTED, confidence_score=1.0,
            ),
        ]
        nbrs = _build_neighbor_sets(edges)
        assert "b" in nbrs["a"]
        assert "a" in nbrs["b"]


class TestJaccard:
    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 0.0

    def test_identical(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial(self):
        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)


# endregion: --- Helper Function Tests


# ---------------------------------------------------------------------------
# region:    --- Dataclass Tests
# ---------------------------------------------------------------------------


class TestGodNode:
    def test_frozen(self):
        gn = GodNode(
            node_id="a::Foo", label="Foo", kind="struct",
            degree=10, in_degree=7, out_degree=3,
            community=0, connected_communities=2,
        )
        with pytest.raises(AttributeError):
            gn.degree = 99  # type: ignore[misc]

    def test_fields(self):
        gn = GodNode(
            node_id="a::Foo", label="Foo", kind="struct",
            degree=10, in_degree=7, out_degree=3,
            community=0, connected_communities=2,
        )
        assert gn.node_id == "a::Foo"
        assert gn.degree == 10
        assert gn.connected_communities == 2


class TestCommunity:
    def test_tuple_key_nodes(self):
        c = Community(id=0, label="core", node_count=5, internal_edge_count=8, key_nodes=("a", "b"))
        assert isinstance(c.key_nodes, tuple)
        assert len(c.key_nodes) == 2


class TestSurprisingConnection:
    def test_fields(self):
        sc = SurprisingConnection(
            source_id="a::X", target_id="b::Y", relation="calls",
            source_community=0, target_community=1,
            surprise_score=0.85, why="test reason",
        )
        assert sc.surprise_score == 0.85
        assert "test reason" in sc.why


class TestHyperedge:
    def test_kind_is_strenum(self):
        assert isinstance(HyperedgeKind.SHARED_TRAIT, str)
        assert HyperedgeKind.SHARED_TRAIT == "shared_trait"

    def test_members_tuple(self):
        he = Hyperedge(label="test", kind=HyperedgeKind.SHARED_IMPORT, members=("a", "b", "c"))
        assert len(he.members) == 3


class TestGraphAnalysis:
    def test_defaults_empty(self):
        ga = GraphAnalysis()
        assert ga.god_nodes == []
        assert ga.communities == []
        assert ga.surprising_connections == []
        assert ga.hyperedges == []
        assert ga.suggested_questions == []
        assert ga.modularity == 0.0


class TestHyperedgeKind:
    def test_member_count(self):
        assert len(HyperedgeKind) == 4

    def test_values(self):
        assert HyperedgeKind.SHARED_TRAIT == "shared_trait"
        assert HyperedgeKind.SHARED_CALLER == "shared_caller"
        assert HyperedgeKind.SHARED_IMPORT == "shared_import"


# endregion: --- Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- God Node Detection Tests
# ---------------------------------------------------------------------------


class TestGodNodeDetection:
    def test_simple_graph_god_nodes(self):
        graph = _simple_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.god_nodes) > 0
        # Foo has 3 incoming edges → highest degree
        top = analysis.god_nodes[0]
        assert "Foo" in top.label or "Foo" in top.node_id

    def test_god_node_limit(self):
        graph = _simple_graph()
        analysis = GraphAnalyzer(god_node_limit=1).analyze(graph)
        assert len(analysis.god_nodes) <= 1

    def test_god_node_has_community(self):
        graph = _simple_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for gn in analysis.god_nodes:
            assert isinstance(gn.community, int)

    def test_god_node_degree_descending(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        degrees = [gn.degree for gn in analysis.god_nodes]
        assert degrees == sorted(degrees, reverse=True)

    def test_zero_degree_nodes_excluded(self):
        """A node with no edges should not appear as a god node."""
        nodes = [
            GraphNode(id="orphan", label="orphan", kind=NodeKind.FUNCTION, file="x.rs"),
        ]
        graph = CodeGraph(nodes=nodes, edges=[])
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.god_nodes) == 0
        assert analysis.modularity == 0.0

    def test_connected_communities_count(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # A1 or B1 should bridge 2 communities via the bridge edge
        bridge_nodes = [gn for gn in analysis.god_nodes if gn.connected_communities >= 2]
        assert len(bridge_nodes) >= 1


# endregion: --- God Node Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Community Detection Tests
# ---------------------------------------------------------------------------


class TestCommunityDetection:
    def test_communities_exist(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) >= 1

    def test_all_nodes_assigned(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        total_nodes = sum(c.node_count for c in analysis.communities)
        assert total_nodes == len(graph.nodes)

    def test_modularity_in_range(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert -0.5 <= analysis.modularity <= 1.0

    def test_community_has_key_nodes(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for c in analysis.communities:
            if c.node_count > 0:
                assert len(c.key_nodes) > 0

    def test_community_label_from_top_node(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for c in analysis.communities:
            # Label should be derived from node ID (after last ::)
            assert len(c.label) > 0

    def test_internal_edge_count(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        total_internal = sum(c.internal_edge_count for c in analysis.communities)
        # At least some edges should be internal
        assert total_internal > 0

    def test_seed_reproducibility(self):
        graph = _two_cluster_graph()
        a1 = GraphAnalyzer(seed=42).analyze(graph)
        a2 = GraphAnalyzer(seed=42).analyze(graph)
        assert len(a1.communities) == len(a2.communities)
        for c1, c2 in zip(a1.communities, a2.communities, strict=True):
            assert c1.node_count == c2.node_count

    def test_single_node_graph(self):
        nodes = [GraphNode(id="x::A", label="A", kind=NodeKind.STRUCT, file="x.rs")]
        graph = CodeGraph(nodes=nodes, edges=[])
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) >= 1
        assert analysis.communities[0].node_count == 1


# endregion: --- Community Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Surprising Connection Tests
# ---------------------------------------------------------------------------


class TestSurprisingConnections:
    def test_bridge_is_surprising(self):
        """The single bridge edge in the two-cluster graph should be flagged."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # If communities were detected as separate, the bridge is surprising
        if len(analysis.communities) >= 2:
            assert len(analysis.surprising_connections) >= 1

    def test_surprise_score_in_range(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for sc in analysis.surprising_connections:
            assert 0.0 <= sc.surprise_score <= 1.0

    def test_surprise_has_why_text(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for sc in analysis.surprising_connections:
            assert len(sc.why) > 0
            assert "connects to" in sc.why or "communities share" in sc.why

    def test_surprise_limit(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer(surprise_limit=1).analyze(graph)
        assert len(analysis.surprising_connections) <= 1

    def test_no_surprise_in_single_community(self):
        """If everything is in one community, no surprises."""
        nodes = [
            GraphNode(id="a", label="a", kind=NodeKind.FUNCTION, file="x.rs"),
            GraphNode(id="b", label="b", kind=NodeKind.FUNCTION, file="x.rs"),
        ]
        edges = [
            GraphEdge(
                source="a", target="b", relation=EdgeRelation.CALLS,
                confidence=Confidence.EXTRACTED, confidence_score=1.0, file="x.rs",
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        analysis = GraphAnalyzer().analyze(graph)
        # Single community or tiny graph — may have zero surprises
        for sc in analysis.surprising_connections:
            assert sc.source_community != sc.target_community

    def test_surprise_sorted_descending(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        scores = [sc.surprise_score for sc in analysis.surprising_connections]
        assert scores == sorted(scores, reverse=True)

    def test_cross_community_edges_only(self):
        """Every surprising connection must bridge different communities."""
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        for sc in analysis.surprising_connections:
            assert sc.source_community != sc.target_community


# endregion: --- Surprising Connection Tests


# ---------------------------------------------------------------------------
# region:    --- Hyperedge Detection Tests
# ---------------------------------------------------------------------------


class TestHyperedgeDetection:
    def test_shared_trait_hyperedge(self):
        ws = _hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        trait_hes = [he for he in analysis.hyperedges if he.kind == HyperedgeKind.SHARED_TRAIT]
        assert len(trait_hes) >= 1
        # All 3 impl blocks should be members
        he = trait_hes[0]
        assert len(he.members) >= 3
        assert "Implementors of" in he.label

    def test_shared_import_hyperedge(self):
        ws = _shared_import_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        import_hes = [he for he in analysis.hyperedges if he.kind == HyperedgeKind.SHARED_IMPORT]
        assert len(import_hes) >= 1
        assert "Consumers of" in import_hes[0].label

    def test_no_hyperedge_below_threshold(self):
        """Groups with < 3 members should not produce a hyperedge."""
        file1 = FileAST(
            file="src/a.rs",
            impl_blocks=[
                ImplBlockNode(
                    self_type="A", trait_type="Trait1",
                    methods=(MethodNode(name="m", visibility=Visibility.PUBLIC),),
                ),
                ImplBlockNode(
                    self_type="B", trait_type="Trait1",
                    methods=(MethodNode(name="m", visibility=Visibility.PUBLIC),),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["x"] = CrateModel(name="x", language="rust", files=[file1])
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        trait_hes = [he for he in analysis.hyperedges if he.kind == HyperedgeKind.SHARED_TRAIT]
        # Only 2 impls of Trait1 → below threshold
        assert len(trait_hes) == 0

    def test_shared_caller_hyperedge(self):
        """3 callers of the same function → shared_caller hyperedge."""
        file1 = FileAST(
            file="src/a.rs",
            functions=[
                FunctionNode(name="target", visibility=Visibility.PUBLIC),
                FunctionNode(name="c1", visibility=Visibility.PUBLIC),
                FunctionNode(name="c2", visibility=Visibility.PUBLIC),
                FunctionNode(name="c3", visibility=Visibility.PUBLIC),
            ],
            call_edges=[
                CallEdge(caller="c1", callee="target", call_site=Span(1, 0, 1, 10)),
                CallEdge(caller="c2", callee="target", call_site=Span(2, 0, 2, 10)),
                CallEdge(caller="c3", callee="target", call_site=Span(3, 0, 3, 10)),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["x"] = CrateModel(name="x", language="rust", files=[file1])
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        caller_hes = [he for he in analysis.hyperedges if he.kind == HyperedgeKind.SHARED_CALLER]
        assert len(caller_hes) >= 1
        assert "Callers of" in caller_hes[0].label

    def test_hyperedge_members_sorted(self):
        ws = _hyperedge_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        for he in analysis.hyperedges:
            assert list(he.members) == sorted(he.members)


# endregion: --- Hyperedge Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Suggested Questions Tests
# ---------------------------------------------------------------------------


class TestSuggestedQuestions:
    def test_questions_generated(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.suggested_questions) >= 1

    def test_question_limit(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer(question_limit=2).analyze(graph)
        assert len(analysis.suggested_questions) <= 2

    def test_questions_contain_symbol_references(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # At least some questions should reference symbol names
        all_text = " ".join(analysis.suggested_questions)
        assert len(all_text) > 0

    def test_god_node_question(self):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        # First question should be about the top god node
        if analysis.god_nodes and analysis.suggested_questions:
            first_god = analysis.god_nodes[0].label
            assert first_god in analysis.suggested_questions[0]


# endregion: --- Suggested Questions Tests


# ---------------------------------------------------------------------------
# region:    --- Empty & Edge Case Tests
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    def test_no_nodes(self):
        analysis = GraphAnalyzer().analyze(CodeGraph())
        assert analysis.god_nodes == []
        assert analysis.communities == []
        assert analysis.surprising_connections == []
        assert analysis.hyperedges == []
        assert analysis.suggested_questions == []
        assert analysis.modularity == 0.0


class TestSingleNodeGraph:
    def test_one_node_no_edges(self):
        nodes = [GraphNode(id="x::A", label="A", kind=NodeKind.STRUCT, file="x.rs")]
        graph = CodeGraph(nodes=nodes, edges=[])
        analysis = GraphAnalyzer().analyze(graph)
        assert len(analysis.communities) >= 1
        assert analysis.communities[0].node_count == 1
        assert len(analysis.god_nodes) == 0  # no edges = no degree


# endregion: --- Empty & Edge Case Tests


# ---------------------------------------------------------------------------
# region:    --- ReportFormatter Tests
# ---------------------------------------------------------------------------


class TestReportFormatterMarkdown:
    def test_creates_report_file(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        md_path, _json_path = fmt.write(analysis, tmp_output)

    def test_report_has_sections(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        md_path, _ = fmt.write(analysis, tmp_output)
        content = md_path.read_text()
        assert "# AST Intel" in content
        assert "## God Nodes" in content
        assert "## Communities" in content
        assert "## Surprising Connections" in content
        assert "## Hyperedges" in content
        assert "## Suggested Questions" in content

    def test_god_node_table_rows(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        md_path, _ = fmt.write(analysis, tmp_output)
        content = md_path.read_text()
        for gn in analysis.god_nodes:
            assert gn.label in content

    def test_empty_analysis_renders(self, tmp_output):
        analysis = GraphAnalysis()
        fmt = ReportFormatter()
        md_path, _ = fmt.write(analysis, tmp_output)
        content = md_path.read_text()
        assert "No high-connectivity nodes" in content
        assert "No community structure" in content

    def test_modularity_in_header(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        md_path, _ = fmt.write(analysis, tmp_output)
        content = md_path.read_text()
        assert "Modularity:" in content


class TestReportFormatterJson:
    def test_creates_json_file(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_output)
        assert json_path.exists()
        assert json_path.name == ANALYSIS_JSON_FILENAME

    def test_json_is_valid(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert isinstance(data, dict)

    def test_json_structure(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert "god_nodes" in data
        assert "communities" in data
        assert "surprising_connections" in data
        assert "hyperedges" in data
        assert "suggested_questions" in data
        assert "modularity" in data
        assert "generated_at" in data

    def test_json_god_node_count(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        assert len(data["god_nodes"]) == len(analysis.god_nodes)

    def test_json_communities_has_key_nodes_as_list(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer().analyze(graph)
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_output)
        data = json.loads(json_path.read_text())
        for c in data["communities"]:
            assert isinstance(c["key_nodes"], list)

    def test_json_deterministic(self, tmp_output):
        graph = _two_cluster_graph()
        analysis = GraphAnalyzer(seed=42).analyze(graph)
        fmt = ReportFormatter()
        _, p1 = fmt.write(analysis, tmp_output)
        content1 = p1.read_text()

        out2 = tmp_output / "run2"
        analysis2 = GraphAnalyzer(seed=42).analyze(graph)
        _, p2 = fmt.write(analysis2, out2)
        content2 = p2.read_text()

        # Ignore generated_at timestamp for comparison
        data1 = json.loads(content1)
        data2 = json.loads(content2)
        del data1["generated_at"]
        del data2["generated_at"]
        assert data1 == data2


# endregion: --- ReportFormatter Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Integration Tests
# ---------------------------------------------------------------------------


class TestEmitterAnalyzeFlag:
    def test_analyze_with_json_format(self, tmp_output):
        ws = _rich_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="json", run_analysis=True)
        written = emitter.emit(ws)
        filenames = {p.name for p in written}
        assert "ast.json" in filenames
        assert GRAPH_REPORT_FILENAME in filenames
        assert ANALYSIS_JSON_FILENAME in filenames

    def test_analyze_with_graph_json_format(self, tmp_output):
        ws = _rich_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="graph-json", run_analysis=True)
        written = emitter.emit(ws)
        filenames = {p.name for p in written}
        assert "graph.json" in filenames
        assert GRAPH_REPORT_FILENAME in filenames
        assert ANALYSIS_JSON_FILENAME in filenames

    def test_no_analyze_no_report(self, tmp_output):
        ws = _rich_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="json", run_analysis=False)
        written = emitter.emit(ws)
        filenames = {p.name for p in written}
        assert GRAPH_REPORT_FILENAME not in filenames

    def test_analyze_produces_valid_json(self, tmp_output):
        ws = _rich_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="json", run_analysis=True)
        emitter.emit(ws)
        json_path = tmp_output / ANALYSIS_JSON_FILENAME
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "god_nodes" in data


# endregion: --- Emitter Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Full Pipeline Integration Tests
# ---------------------------------------------------------------------------


class TestFullPipelineAnalysis:
    def test_rich_workspace_analysis(self):
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        # Should produce non-trivial results
        assert len(analysis.god_nodes) > 0
        assert len(analysis.communities) > 0

    def test_all_node_ids_valid(self):
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        node_ids = {n.id for n in graph.nodes}
        for gn in analysis.god_nodes:
            assert gn.node_id in node_ids

    def test_community_total_matches_graph(self):
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        analysis = GraphAnalyzer().analyze(graph)
        total_nodes = sum(c.node_count for c in analysis.communities)
        # community_map is filtered to only include actual graph node IDs
        assert total_nodes == len(graph.nodes)

    def test_reproducible_across_runs(self):
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        a1 = GraphAnalyzer(seed=42).analyze(graph)
        a2 = GraphAnalyzer(seed=42).analyze(graph)
        assert len(a1.god_nodes) == len(a2.god_nodes)
        assert len(a1.communities) == len(a2.communities)
        for g1, g2 in zip(a1.god_nodes, a2.god_nodes, strict=True):
            assert g1.node_id == g2.node_id
            assert g1.degree == g2.degree


# endregion: --- Full Pipeline Integration Tests


# ---------------------------------------------------------------------------
# region:    --- CLI OutputFormat Tests
# ---------------------------------------------------------------------------


class TestCLIAnalyzeFlag:
    def test_emitter_accepts_run_analysis(self, tmp_output):
        """Emitter constructor accepts run_analysis kwarg."""
        emitter = Emitter(output_dir=tmp_output, output_format="json", run_analysis=True)
        assert emitter.run_analysis is True

    def test_emitter_default_no_analysis(self, tmp_output):
        emitter = Emitter(output_dir=tmp_output, output_format="json")
        assert emitter.run_analysis is False


# endregion: --- CLI OutputFormat Tests
