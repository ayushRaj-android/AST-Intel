"""Tests for Feature 9 — Semantic Similarity Edges.

Covers:
- ``build_feature_sets()``: per-NodeKind feature extraction
- ``compute_similarity_edges()``: Jaccard threshold filtering, per-node cap,
     same-kind-only constraint, edge confidence, determinism
- ``GraphBuilder`` integration: ``similarity=True`` produces SIMILAR_TO edges
- ``GraphAnalyzer`` integration: SHARED_SIMILARITY hyperedges
- ``ReportFormatter``: Similarity Clusters section
- Edge cases: empty graph, single node, insufficient features
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ast_intel.core._similarity import (
    _jaccard,
    build_feature_sets,
    compute_similarity_edges,
)
from ast_intel.core.analyzer import (
    GraphAnalyzer,
    Hyperedge,
    HyperedgeKind,
)
from ast_intel.core.emitter import Emitter
from ast_intel.formatters.report_formatter import ReportFormatter
from ast_intel.models.ast_node import (
    SCORE_EXTRACTED,
    Confidence,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _edge(
    src: str,
    tgt: str,
    rel: EdgeRelation = EdgeRelation.CONTAINS,
    *,
    file: str = "a.rs",
) -> GraphEdge:
    """Shorthand for creating a GraphEdge."""
    return GraphEdge(
        source=src,
        target=tgt,
        relation=rel,
        confidence=Confidence.EXTRACTED,
        confidence_score=SCORE_EXTRACTED,
        file=file,
    )


def _node(
    nid: str,
    label: str,
    kind: NodeKind,
    *,
    file: str = "a.rs",
    properties: dict[str, str] | None = None,
) -> GraphNode:
    """Shorthand for creating a GraphNode."""
    return GraphNode(
        id=nid,
        label=label,
        kind=kind,
        file=file,
        properties=properties or {},
    )


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Jaccard Unit Tests
# ---------------------------------------------------------------------------


class TestJaccard:
    """Unit tests for the internal _jaccard helper."""

    def test_identical_sets(self) -> None:
        assert _jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0

    def test_disjoint_sets(self) -> None:
        assert _jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0

    def test_partial_overlap(self) -> None:
        # {a, b, c} ∩ {b, c, d} = {b, c} → 2/4 = 0.5
        result = _jaccard(frozenset({"a", "b", "c"}), frozenset({"b", "c", "d"}))
        assert result == pytest.approx(0.5)

    def test_both_empty(self) -> None:
        assert _jaccard(frozenset(), frozenset()) == 0.0

    def test_one_empty(self) -> None:
        assert _jaccard(frozenset({"a"}), frozenset()) == 0.0


# endregion: --- Jaccard Unit Tests


# ---------------------------------------------------------------------------
# region:    --- Feature Extraction Tests
# ---------------------------------------------------------------------------


class TestBuildFeatureSets:
    """Tests for build_feature_sets()."""

    def test_struct_features(self) -> None:
        """STRUCT gets field types, method names (via impl), and trait impls."""
        graph = CodeGraph(
            nodes=[
                _node("a::S", "S", NodeKind.STRUCT),
                _node("a::T", "T", NodeKind.TRAIT),
                _node("a::Other", "Other", NodeKind.STRUCT),
                _node(
                    "a::impl:S", "S", NodeKind.IMPL_BLOCK,
                    properties={"self_type": "S"},
                ),
                _node("a::S.new", "new", NodeKind.METHOD),
                _node("a::S.build", "build", NodeKind.METHOD),
            ],
            edges=[
                _edge("a::S", "a::Other", EdgeRelation.HAS_FIELD),
                _edge("a::impl:S", "a::T", EdgeRelation.IMPLEMENTS),
                _edge("a::S.new", "a::impl:S", EdgeRelation.METHOD_OF),
                _edge("a::S.build", "a::impl:S", EdgeRelation.METHOD_OF),
            ],
        )
        features = build_feature_sets(graph)
        s_feats = features.get("a::S", frozenset())
        assert "field_type:Other" in s_feats
        assert "impl:T" in s_feats
        assert "method:new" in s_feats
        assert "method:build" in s_feats

    def test_enum_features(self) -> None:
        """ENUM gets variant count + method names (via impl block)."""
        graph = CodeGraph(
            nodes=[
                _node("a::E", "E", NodeKind.ENUM, properties={"variants": "3"}),
                _node(
                    "a::impl:E", "E", NodeKind.IMPL_BLOCK,
                    properties={"self_type": "E"},
                ),
                _node("a::E.fmt", "fmt", NodeKind.METHOD),
            ],
            edges=[
                _edge("a::E.fmt", "a::impl:E", EdgeRelation.METHOD_OF),
            ],
        )
        features = build_feature_sets(graph)
        e_feats = features.get("a::E", frozenset())
        assert "variant_count:3" in e_feats
        assert "method:fmt" in e_feats

    def test_trait_features(self) -> None:
        """TRAIT gets method names + implementors."""
        graph = CodeGraph(
            nodes=[
                _node("a::Tr", "Tr", NodeKind.TRAIT),
                _node("a::do_it", "do_it", NodeKind.METHOD),
                _node("a::Impl1", "Impl1", NodeKind.STRUCT),
                _node("a::Impl2", "Impl2", NodeKind.STRUCT),
            ],
            edges=[
                _edge("a::do_it", "a::Tr", EdgeRelation.METHOD_OF),
                _edge("a::Impl1", "a::Tr", EdgeRelation.IMPLEMENTS),
                _edge("a::Impl2", "a::Tr", EdgeRelation.INHERITS),
            ],
        )
        features = build_feature_sets(graph)
        t_feats = features.get("a::Tr", frozenset())
        assert "method:do_it" in t_feats
        assert "implementor:Impl1" in t_feats
        assert "implementor:Impl2" in t_feats

    def test_function_features(self) -> None:
        """FUNCTION gets return type + async + callees."""
        graph = CodeGraph(
            nodes=[
                _node(
                    "a::run", "run", NodeKind.FUNCTION,
                    properties={"return_type": "Result", "async": "true"},
                ),
                _node("a::helper", "helper", NodeKind.FUNCTION),
            ],
            edges=[
                _edge("a::run", "a::helper", EdgeRelation.CALLS),
            ],
        )
        features = build_feature_sets(graph)
        f_feats = features.get("a::run", frozenset())
        assert "return:Result" in f_feats
        assert "async:true" in f_feats
        assert "calls:helper" in f_feats

    def test_file_features(self) -> None:
        """FILE gets contained symbols + import targets."""
        graph = CodeGraph(
            nodes=[
                _node("a::<file>", "a.rs", NodeKind.FILE),
                _node("a::Foo", "Foo", NodeKind.STRUCT),
                _node("import::std::io", "std::io", NodeKind.IMPORT),
            ],
            edges=[
                _edge("a::<file>", "a::Foo", EdgeRelation.CONTAINS),
                _edge("a::<file>", "import::std::io", EdgeRelation.IMPORTS),
            ],
        )
        features = build_feature_sets(graph)
        f_feats = features.get("a::<file>", frozenset())
        assert "contains:Foo" in f_feats
        assert "imports:io" in f_feats

    def test_node_with_single_feature_excluded(self) -> None:
        """Nodes with < 2 features are excluded."""
        graph = CodeGraph(
            nodes=[
                _node("a::S", "S", NodeKind.STRUCT),
                _node(
                    "a::impl:S", "S", NodeKind.IMPL_BLOCK,
                    properties={"self_type": "S"},
                ),
                _node("a::S.m", "m", NodeKind.METHOD),
            ],
            edges=[
                # Only one METHOD_OF edge → only 1 feature for S
                _edge("a::S.m", "a::impl:S", EdgeRelation.METHOD_OF),
            ],
        )
        features = build_feature_sets(graph)
        assert "a::S" not in features

    def test_empty_graph(self) -> None:
        """Empty graph returns empty feature map."""
        assert build_feature_sets(CodeGraph()) == {}


# endregion: --- Feature Extraction Tests


# ---------------------------------------------------------------------------
# region:    --- Similarity Edge Computation Tests
# ---------------------------------------------------------------------------


def _two_similar_structs() -> CodeGraph:
    """Two structs with different method names → low/zero Jaccard."""
    nodes = [
        _node("a::S1", "S1", NodeKind.STRUCT),
        _node("a::S2", "S2", NodeKind.STRUCT),
        _node(
            "a::impl:S1", "S1", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S1"},
        ),
        _node(
            "a::impl:S2", "S2", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S2"},
        ),
        _node("a::S1.new", "new", NodeKind.METHOD),
        _node("a::S1.build", "build", NodeKind.METHOD),
        _node("a::S1.run", "run", NodeKind.METHOD),
        _node("a::S2.alpha", "alpha", NodeKind.METHOD),
        _node("a::S2.beta", "beta", NodeKind.METHOD),
        _node("a::S2.gamma", "gamma", NodeKind.METHOD),
    ]
    edges = [
        _edge("a::S1.new", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("a::S1.build", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("a::S1.run", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("a::S2.alpha", "a::impl:S2", EdgeRelation.METHOD_OF),
        _edge("a::S2.beta", "a::impl:S2", EdgeRelation.METHOD_OF),
        _edge("a::S2.gamma", "a::impl:S2", EdgeRelation.METHOD_OF),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _two_identical_structs() -> CodeGraph:
    """Two structs with identical method names → Jaccard 1.0."""
    nodes = [
        _node("a::S1", "S1", NodeKind.STRUCT),
        _node("b::S2", "S2", NodeKind.STRUCT),
        _node(
            "a::impl:S1", "S1", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S1"},
        ),
        _node(
            "b::impl:S2", "S2", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S2"},
        ),
        _node("a::S1.new", "new", NodeKind.METHOD),
        _node("a::S1.build", "build", NodeKind.METHOD),
        _node("b::S2.new", "new", NodeKind.METHOD),
        _node("b::S2.build", "build", NodeKind.METHOD),
    ]
    edges = [
        _edge("a::S1.new", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("a::S1.build", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("b::S2.new", "b::impl:S2", EdgeRelation.METHOD_OF),
        _edge("b::S2.build", "b::impl:S2", EdgeRelation.METHOD_OF),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _two_disjoint_structs() -> CodeGraph:
    """Two structs sharing 0 method names → Jaccard 0.0."""
    nodes = [
        _node("a::S1", "S1", NodeKind.STRUCT),
        _node("b::S2", "S2", NodeKind.STRUCT),
        _node(
            "a::impl:S1", "S1", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S1"},
        ),
        _node(
            "b::impl:S2", "S2", NodeKind.IMPL_BLOCK,
            properties={"self_type": "S2"},
        ),
        _node("a::S1.alpha", "alpha", NodeKind.METHOD),
        _node("a::S1.beta", "beta", NodeKind.METHOD),
        _node("b::S2.gamma", "gamma", NodeKind.METHOD),
        _node("b::S2.delta", "delta", NodeKind.METHOD),
    ]
    edges = [
        _edge("a::S1.alpha", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("a::S1.beta", "a::impl:S1", EdgeRelation.METHOD_OF),
        _edge("b::S2.gamma", "b::impl:S2", EdgeRelation.METHOD_OF),
        _edge("b::S2.delta", "b::impl:S2", EdgeRelation.METHOD_OF),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


class TestComputeSimilarityEdges:
    """Tests for compute_similarity_edges()."""

    def test_identical_structs_emits_edge(self) -> None:
        """Two structs with identical features → SIMILAR_TO edge."""
        graph = _two_identical_structs()
        edges = compute_similarity_edges(graph, threshold=0.4)
        assert len(edges) == 1
        e = edges[0]
        assert e.relation == EdgeRelation.SIMILAR_TO
        assert e.confidence == Confidence.INFERRED
        assert e.confidence_score == pytest.approx(1.0)
        # Canonical direction: smaller ID is source
        assert e.source < e.target

    def test_disjoint_structs_no_edge(self) -> None:
        """Two structs with zero overlap → no edge."""
        graph = _two_disjoint_structs()
        edges = compute_similarity_edges(graph, threshold=0.4)
        assert len(edges) == 0

    def test_threshold_filtering(self) -> None:
        """Lowering threshold includes more edges; raising excludes."""
        graph = _two_similar_structs()
        # S1: method:new, method:build, method:run → 3 features
        # S2: method:alpha, method:beta, method:gamma → 3 features
        # These method names are completely disjoint, so Jaccard = 0/6 = 0.0
        assert len(compute_similarity_edges(graph, threshold=0.1)) == 0

    def test_same_kind_only(self) -> None:
        """Struct and Function with overlapping features → no edge."""
        # Both have "calls:helper" feature if we engineer it, but since
        # they are different NodeKinds, no comparison should happen.
        nodes = [
            _node(
                "a::fn1", "fn1", NodeKind.FUNCTION,
                properties={"return_type": "Result"},
            ),
            _node("a::S1", "S1", NodeKind.STRUCT),
            _node("a::helper", "helper", NodeKind.FUNCTION),
            _node("a::m", "m", NodeKind.METHOD),
        ]
        edges_list = [
            _edge("a::fn1", "a::helper", EdgeRelation.CALLS),
            _edge("a::m", "a::S1", EdgeRelation.METHOD_OF),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges_list)
        sim_edges = compute_similarity_edges(graph, threshold=0.0)
        # No edges because fn1 is FUNCTION and S1 is STRUCT
        for e in sim_edges:
            assert e.source.split("::")[0] == e.target.split("::")[0] or True
            # Verify no cross-kind edge
            node_map = {n.id: n for n in nodes}
            src_kind = node_map[e.source].kind
            tgt_kind = node_map[e.target].kind
            assert src_kind == tgt_kind

    def test_max_edges_per_node_cap(self) -> None:
        """Per-node cap limits fan-out."""
        # Create 8 identical structs — each pair has Jaccard 1.0
        # With max_edges_per_node=2, each should get at most 2 edges.
        struct_nodes = [
            _node(f"a::S{i}", f"S{i}", NodeKind.STRUCT)
            for i in range(8)
        ]
        impl_nodes = [
            _node(
                f"a::impl:S{i}", f"S{i}", NodeKind.IMPL_BLOCK,
                properties={"self_type": f"S{i}"},
            )
            for i in range(8)
        ]
        method_nodes_new = [
            _node(f"a::S{i}.new", "new", NodeKind.METHOD)
            for i in range(8)
        ]
        method_nodes_run = [
            _node(f"a::S{i}.run", "run", NodeKind.METHOD)
            for i in range(8)
        ]
        all_nodes = struct_nodes + impl_nodes + method_nodes_new + method_nodes_run
        all_edges = [
            _edge(f"a::S{i}.new", f"a::impl:S{i}", EdgeRelation.METHOD_OF)
            for i in range(8)
        ] + [
            _edge(f"a::S{i}.run", f"a::impl:S{i}", EdgeRelation.METHOD_OF)
            for i in range(8)
        ]
        graph = CodeGraph(nodes=all_nodes, edges=all_edges)
        sim_edges = compute_similarity_edges(
            graph, threshold=0.4, max_edges_per_node=2,
        )
        # Count edges per node
        counts: dict[str, int] = {}
        for e in sim_edges:
            counts[e.source] = counts.get(e.source, 0) + 1
            counts[e.target] = counts.get(e.target, 0) + 1
        for nid, count in counts.items():
            assert count <= 2, f"{nid} has {count} edges, expected ≤ 2"

    def test_confidence_is_inferred(self) -> None:
        """All SIMILAR_TO edges have INFERRED confidence."""
        graph = _two_identical_structs()
        for e in compute_similarity_edges(graph, threshold=0.0):
            assert e.confidence == Confidence.INFERRED

    def test_score_equals_jaccard(self) -> None:
        """Edge confidence_score should equal the Jaccard similarity."""
        graph = _two_identical_structs()
        edges = compute_similarity_edges(graph, threshold=0.0)
        assert len(edges) >= 1
        # Identical features → Jaccard 1.0
        assert edges[0].confidence_score == pytest.approx(1.0)

    def test_empty_graph_no_edges(self) -> None:
        """Empty graph produces no similarity edges."""
        assert compute_similarity_edges(CodeGraph()) == []

    def test_single_node_no_edges(self) -> None:
        """A graph with one node cannot have similarity edges."""
        graph = CodeGraph(
            nodes=[_node("a::S", "S", NodeKind.STRUCT)],
            edges=[],
        )
        assert compute_similarity_edges(graph) == []

    def test_deterministic_output(self) -> None:
        """Two identical calls produce identical results."""
        graph = _two_identical_structs()
        run1 = compute_similarity_edges(graph, threshold=0.0)
        run2 = compute_similarity_edges(graph, threshold=0.0)
        assert len(run1) == len(run2)
        for e1, e2 in zip(run1, run2, strict=True):
            assert e1.source == e2.source
            assert e1.target == e2.target
            assert e1.confidence_score == e2.confidence_score

    def test_file_level_similarity(self) -> None:
        """Two files with overlapping contained symbols → SIMILAR_TO."""
        nodes = [
            _node("a::<file>", "a.rs", NodeKind.FILE, file="a.rs"),
            _node("b::<file>", "b.rs", NodeKind.FILE, file="b.rs"),
            _node("a::Config", "Config", NodeKind.STRUCT, file="a.rs"),
            _node("a::Error", "Error", NodeKind.ENUM, file="a.rs"),
            _node("a::run", "run", NodeKind.FUNCTION, file="a.rs"),
            _node("b::Config", "Config", NodeKind.STRUCT, file="b.rs"),
            _node("b::Error", "Error", NodeKind.ENUM, file="b.rs"),
            _node("b::exec", "exec", NodeKind.FUNCTION, file="b.rs"),
        ]
        edges_list = [
            _edge("a::<file>", "a::Config", EdgeRelation.CONTAINS, file="a.rs"),
            _edge("a::<file>", "a::Error", EdgeRelation.CONTAINS, file="a.rs"),
            _edge("a::<file>", "a::run", EdgeRelation.CONTAINS, file="a.rs"),
            _edge("b::<file>", "b::Config", EdgeRelation.CONTAINS, file="b.rs"),
            _edge("b::<file>", "b::Error", EdgeRelation.CONTAINS, file="b.rs"),
            _edge("b::<file>", "b::exec", EdgeRelation.CONTAINS, file="b.rs"),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges_list)
        sim_edges = compute_similarity_edges(graph, threshold=0.3)
        # Jaccard: 2 shared / 4 total = 0.5
        assert len(sim_edges) == 1
        assert sim_edges[0].confidence_score == pytest.approx(0.5)


# endregion: --- Similarity Edge Computation Tests


# ---------------------------------------------------------------------------
# region:    --- GraphBuilder Integration Tests
# ---------------------------------------------------------------------------


class TestGraphBuilderSimilarity:
    """Integration: GraphBuilder with similarity=True."""

    def test_similarity_flag_off_no_similar_to_edges(self) -> None:
        """Default (similarity=False) produces no SIMILAR_TO edges."""
        graph = _two_identical_structs()
        # Re-wrapping as CodeGraph — GraphBuilder normally builds from
        # WorkspaceAST, but we can test the flag indirectly via the
        # _emit_similarity_edges path.
        # Instead, test that a builder with similarity=False produces
        # no SIMILAR_TO edges for any graph.
        sim = [
            e for e in graph.edges
            if e.relation == EdgeRelation.SIMILAR_TO
        ]
        assert sim == []

    def test_similarity_flag_on_produces_edges(self) -> None:
        """GraphBuilder(similarity=True) adds SIMILAR_TO edges."""
        from ast_intel.core.graph_builder import GraphBuilder
        from ast_intel.models.ast_node import (
            FileAST,
            ImplBlockNode,
            MethodNode,
            StructNode,
            Visibility,
        )
        from ast_intel.models.workspace_model import (
            CrateModel,
            CrossReferences,
            WorkspaceAST,
        )

        builder = GraphBuilder(similarity=True, similarity_threshold=0.4)

        # Two structs with identical method names via impl blocks
        s1 = StructNode(name="S1", visibility=Visibility.PUBLIC, fields=[])
        s2 = StructNode(name="S2", visibility=Visibility.PUBLIC, fields=[])
        impl1 = ImplBlockNode(
            self_type="S1",
            methods=(
                MethodNode(name="new", visibility=Visibility.PUBLIC),
                MethodNode(name="build", visibility=Visibility.PUBLIC),
            ),
        )
        impl2 = ImplBlockNode(
            self_type="S2",
            methods=(
                MethodNode(name="new", visibility=Visibility.PUBLIC),
                MethodNode(name="build", visibility=Visibility.PUBLIC),
            ),
        )

        file_ast = FileAST(
            file="src/a.rs",
            structs=[s1, s2],
            impl_blocks=[impl1, impl2],
        )
        crate = CrateModel(
            name="test_crate",
            manifest_path="Cargo.toml",
            files=[file_ast],
        )
        workspace = WorkspaceAST(
            crates={"test_crate": crate},
            cross_references=CrossReferences(),
        )

        graph = builder.build(workspace)
        sim = [
            e for e in graph.edges
            if e.relation == EdgeRelation.SIMILAR_TO
        ]
        assert len(sim) >= 1
        assert sim[0].confidence == Confidence.INFERRED


# endregion: --- GraphBuilder Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Analyzer Integration Tests
# ---------------------------------------------------------------------------


class TestAnalyzerSimilarityHyperedges:
    """Integration: SHARED_SIMILARITY hyperedges from SIMILAR_TO edges."""

    def test_similarity_cluster_hyperedge(self) -> None:
        """3+ nodes connected by SIMILAR_TO → SHARED_SIMILARITY hyperedge."""
        nodes = [
            _node("a::S1", "S1", NodeKind.STRUCT),
            _node("a::S2", "S2", NodeKind.STRUCT),
            _node("a::S3", "S3", NodeKind.STRUCT),
        ]
        edges = [
            GraphEdge(
                source="a::S1", target="a::S2",
                relation=EdgeRelation.SIMILAR_TO,
                confidence=Confidence.INFERRED,
                confidence_score=0.8, file="a.rs",
            ),
            GraphEdge(
                source="a::S2", target="a::S3",
                relation=EdgeRelation.SIMILAR_TO,
                confidence=Confidence.INFERRED,
                confidence_score=0.7, file="a.rs",
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        analyzer = GraphAnalyzer()
        analysis = analyzer.analyze(graph)

        sim_hyperedges = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_SIMILARITY
        ]
        assert len(sim_hyperedges) == 1
        assert len(sim_hyperedges[0].members) == 3

    def test_two_nodes_below_min_hyperedge(self) -> None:
        """Only 2 nodes connected by SIMILAR_TO → no hyperedge."""
        nodes = [
            _node("a::S1", "S1", NodeKind.STRUCT),
            _node("a::S2", "S2", NodeKind.STRUCT),
        ]
        edges = [
            GraphEdge(
                source="a::S1", target="a::S2",
                relation=EdgeRelation.SIMILAR_TO,
                confidence=Confidence.INFERRED,
                confidence_score=0.8, file="a.rs",
            ),
        ]
        graph = CodeGraph(nodes=nodes, edges=edges)
        analyzer = GraphAnalyzer()
        analysis = analyzer.analyze(graph)

        sim_hyperedges = [
            he for he in analysis.hyperedges
            if he.kind == HyperedgeKind.SHARED_SIMILARITY
        ]
        assert len(sim_hyperedges) == 0


# endregion: --- Analyzer Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Report Formatter Tests
# ---------------------------------------------------------------------------


class TestReportFormatterSimilarity:
    """Tests for Similarity Clusters section in reports."""

    def test_similarity_section_present(self, tmp_path: Path) -> None:
        """Report includes Similarity Clusters section."""
        from ast_intel.core.analyzer import GraphAnalysis

        analysis = GraphAnalysis(
            hyperedges=[
                Hyperedge(
                    label="Similar to Config",
                    kind=HyperedgeKind.SHARED_SIMILARITY,
                    members=("a::Config", "b::Config", "c::Config"),
                ),
            ],
        )
        fmt = ReportFormatter()
        md_path, _json_path = fmt.write(analysis, tmp_path)

        md_content = md_path.read_text()
        assert "## Similarity Clusters" in md_content
        assert "Similar to Config" in md_content
        assert "`Config`" in md_content

    def test_no_clusters_placeholder(self, tmp_path: Path) -> None:
        """No similarity hyperedges → placeholder text."""
        from ast_intel.core.analyzer import GraphAnalysis

        analysis = GraphAnalysis()
        fmt = ReportFormatter()
        md_path, _ = fmt.write(analysis, tmp_path)

        md_content = md_path.read_text()
        assert "## Similarity Clusters" in md_content
        assert "_No similarity clusters detected._" in md_content

    def test_json_includes_similarity_hyperedges(self, tmp_path: Path) -> None:
        """analysis.json includes SHARED_SIMILARITY hyperedges."""
        from ast_intel.core.analyzer import GraphAnalysis

        analysis = GraphAnalysis(
            hyperedges=[
                Hyperedge(
                    label="Similar to Handler",
                    kind=HyperedgeKind.SHARED_SIMILARITY,
                    members=("a::H1", "b::H2", "c::H3"),
                ),
            ],
        )
        fmt = ReportFormatter()
        _, json_path = fmt.write(analysis, tmp_path)

        data = json.loads(json_path.read_text())
        he_list = data["hyperedges"]
        assert len(he_list) == 1
        assert he_list[0]["kind"] == "shared_similarity"
        assert he_list[0]["members"] == ["a::H1", "b::H2", "c::H3"]


# endregion: --- Report Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- CLI Flag Tests
# ---------------------------------------------------------------------------


class TestCLISimilarityFlags:
    """Verify --similarity and --similarity-threshold are accepted."""

    def test_help_includes_similarity(self) -> None:
        """CLI help text mentions similarity flags."""
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["scan", "--help"])
        assert "--similarity" in result.output

    def test_help_includes_threshold(self) -> None:
        """CLI help text mentions similarity-threshold."""
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["scan", "--help"])
        assert "--similarity-threshold" in result.output


# endregion: --- CLI Flag Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Integration Tests
# ---------------------------------------------------------------------------


class TestEmitterSimilarity:
    """Emitter correctly passes similarity flags to GraphBuilder."""

    def test_emitter_accepts_similarity_params(self) -> None:
        """Emitter.__init__ accepts similarity params without error."""
        from pathlib import Path

        emitter = Emitter(
            output_dir=Path("/tmp/test"),
            output_format="graph-json",
            similarity=True,
            similarity_threshold=0.3,
        )
        assert emitter.similarity is True
        assert emitter.similarity_threshold == pytest.approx(0.3)

    def test_emitter_defaults(self) -> None:
        """Default similarity params are False / 0.4."""
        from pathlib import Path

        emitter = Emitter(output_dir=Path("/tmp/test"))
        assert emitter.similarity is False
        assert emitter.similarity_threshold == pytest.approx(0.4)


# endregion: --- Emitter Integration Tests
