"""Tests for Feature 11 — Graph Query CLI.

Covers:
- :class:`QueryEngine` (search, shortest_path, explain)
- :class:`NodeExplanation` dataclass
- :func:`GraphJsonFormatter.read` (round-trip deserialization)
- :func:`load_or_build_graph` (graph loader cache logic)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.models.ast_node import (
    Confidence,
    Span,
)
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
    """Convenience factory for test graph nodes."""
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
    confidence: Confidence = Confidence.EXTRACTED,
    score: float = 1.0,
) -> GraphEdge:
    """Convenience factory for test graph edges."""
    return GraphEdge(
        source=src,
        target=tgt,
        relation=relation,
        confidence=confidence,
        confidence_score=score,
    )


def _sample_graph() -> CodeGraph:
    """Build a small graph with known topology.

    Topology::

        FileA (file) --contains--> FuncA (function)
        FileA (file) --contains--> FuncB (function)
        FuncA --calls--> FuncB
        FuncB --calls--> FuncC (function)
        StructX (struct) --contains--> (implicit)
        FuncC --calls--> FuncD (function)
        StructX --implements--> TraitY (trait)

    Plus an isolated node ``Lonely``.
    """
    nodes = [
        _make_node("f::FileA", "FileA", NodeKind.FILE, "src/a.rs"),
        _make_node("f::FileA::FuncA", "FuncA", NodeKind.FUNCTION, "src/a.rs"),
        _make_node("f::FileA::FuncB", "FuncB", NodeKind.FUNCTION, "src/a.rs"),
        _make_node("f::FileB::FuncC", "FuncC", NodeKind.FUNCTION, "src/b.rs"),
        _make_node("f::FileB::FuncD", "FuncD", NodeKind.FUNCTION, "src/b.rs"),
        _make_node("f::StructX", "StructX", NodeKind.STRUCT, "src/a.rs"),
        _make_node("f::TraitY", "TraitY", NodeKind.TRAIT, "src/b.rs"),
        _make_node("f::Lonely", "Lonely", NodeKind.FUNCTION, "src/c.rs"),
    ]
    edges = [
        _make_edge("f::FileA", "f::FileA::FuncA", EdgeRelation.CONTAINS),
        _make_edge("f::FileA", "f::FileA::FuncB", EdgeRelation.CONTAINS),
        _make_edge("f::FileA::FuncA", "f::FileA::FuncB", EdgeRelation.CALLS),
        _make_edge("f::FileA::FuncB", "f::FileB::FuncC", EdgeRelation.CALLS),
        _make_edge("f::FileB::FuncC", "f::FileB::FuncD", EdgeRelation.CALLS),
        _make_edge("f::StructX", "f::TraitY", EdgeRelation.IMPLEMENTS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


@pytest.fixture
def graph() -> CodeGraph:
    return _sample_graph()


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.search
# ---------------------------------------------------------------------------


class TestQueryEngineSearch:
    """Tests for QueryEngine.search()."""

    def test_exact_label_match(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("FuncA")
        assert len(hits) == 1
        assert hits[0].label == "FuncA"

    def test_substring_match(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("Func")
        labels = {h.label for h in hits}
        assert {"FuncA", "FuncB", "FuncC", "FuncD"} == labels

    def test_regex_match(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search(r"Func[AB]", regex=True)
        labels = {h.label for h in hits}
        assert labels == {"FuncA", "FuncB"}

    def test_kind_filter(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("", kind=NodeKind.STRUCT)
        assert len(hits) == 1
        assert hits[0].kind == NodeKind.STRUCT

    def test_no_match_returns_empty(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("nonexistent_xyz_999")
        assert hits == []

    def test_case_insensitive(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("funca")
        assert len(hits) == 1
        assert hits[0].label == "FuncA"

    def test_results_sorted_by_degree(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        hits = engine.search("Func")
        # FuncB has the most connections (contains edge + 2 calls)
        # so it should appear first or near the top.
        assert len(hits) >= 2
        # Just verify the list is non-empty and ordered (no crash).
        degrees = [
            engine._nx.degree(h.id) for h in hits
        ]
        assert degrees == sorted(degrees, reverse=True)

    def test_max_50_results(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        # Build a graph with 60 functions.
        nodes = [
            _make_node(f"fn{i}", f"item{i}", NodeKind.FUNCTION)
            for i in range(60)
        ]
        g = CodeGraph(nodes=nodes, edges=[])
        engine = QueryEngine(g)
        hits = engine.search("item")
        assert len(hits) == 50


# endregion: --- QueryEngine.search


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.shortest_path
# ---------------------------------------------------------------------------


class TestQueryEngineShortestPath:
    """Tests for QueryEngine.shortest_path()."""

    def test_direct_path(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.shortest_path("FuncA", "FuncB")
        assert result is not None
        assert len(result) == 2
        assert result[0][0].label == "FuncA"
        assert result[0][1] == EdgeRelation.CALLS
        assert result[1][0].label == "FuncB"
        assert result[1][1] is None

    def test_multi_hop_path(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.shortest_path("FuncA", "FuncD")
        assert result is not None
        # FuncA → FuncB → FuncC → FuncD = 4 nodes
        assert len(result) == 4
        labels = [n.label for n, _ in result]
        assert labels == ["FuncA", "FuncB", "FuncC", "FuncD"]

    def test_no_path_returns_none(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        # Lonely is isolated — no path from FuncA.
        result = engine.shortest_path("FuncA", "Lonely")
        assert result is None

    def test_same_source_and_target(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.shortest_path("FuncA", "FuncA")
        assert result is not None
        assert len(result) == 1
        assert result[0][0].label == "FuncA"

    def test_lookup_by_node_id(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.shortest_path(
            "f::FileA::FuncA", "f::FileA::FuncB",
        )
        assert result is not None
        assert result[0][0].label == "FuncA"

    def test_unresolvable_symbol_returns_none(
        self, graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.shortest_path("NoSuchSymbol", "FuncA")
        assert result is None


# endregion: --- QueryEngine.shortest_path


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.explain
# ---------------------------------------------------------------------------


class TestQueryEngineExplain:
    """Tests for QueryEngine.explain()."""

    def test_explain_returns_explanation(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import NodeExplanation, QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncA")
        assert isinstance(expl, NodeExplanation)
        assert expl.node.label == "FuncA"
        assert expl.degree > 0

    def test_isolated_role(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("Lonely")
        assert expl.role == "isolated"
        assert expl.degree == 0

    def test_leaf_role(self, graph: CodeGraph) -> None:
        """FuncD only has incoming calls, no outgoing — leaf."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncD")
        assert expl.role == "leaf"

    def test_callees_populated(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncA")
        assert "FuncB" in expl.callees

    def test_callers_populated(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncB")
        assert "FuncA" in expl.callers

    def test_summary_contains_label_and_file(
        self, graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncA")
        assert "FuncA" in expl.summary
        assert "src/a.rs" in expl.summary

    def test_summary_contains_span(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncA")
        assert "L1" in expl.summary

    def test_explain_unknown_symbol_raises(
        self, graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        with pytest.raises(KeyError, match="NoSuchSymbol"):
            engine.explain("NoSuchSymbol")


# endregion: --- QueryEngine.explain


# ---------------------------------------------------------------------------
# region:    --- NodeExplanation dataclass
# ---------------------------------------------------------------------------


class TestNodeExplanation:
    """Tests for the NodeExplanation dataclass."""

    def test_frozen(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import NodeExplanation, QueryEngine

        engine = QueryEngine(graph)
        expl = engine.explain("FuncA")
        assert isinstance(expl, NodeExplanation)
        with pytest.raises(AttributeError):
            expl.role = "different"  # type: ignore[misc]

    def test_community_none_without_analysis(
        self, graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph, analysis=None)
        expl = engine.explain("FuncA")
        assert expl.community is None


# endregion: --- NodeExplanation dataclass


# ---------------------------------------------------------------------------
# region:    --- GraphJsonFormatter round-trip
# ---------------------------------------------------------------------------


class TestGraphJsonFormatterRoundTrip:
    """Tests for GraphJsonFormatter.read() deserialization."""

    def test_round_trip_node_count(
        self, graph: CodeGraph, tmp_path: Path,
    ) -> None:
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        path = tmp_path / "graph.json"
        GraphJsonFormatter().write(graph, path)
        loaded = GraphJsonFormatter.read(path)
        assert len(loaded.nodes) == len(graph.nodes)

    def test_round_trip_edge_count(
        self, graph: CodeGraph, tmp_path: Path,
    ) -> None:
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        path = tmp_path / "graph.json"
        GraphJsonFormatter().write(graph, path)
        loaded = GraphJsonFormatter.read(path)
        assert len(loaded.edges) == len(graph.edges)

    def test_round_trip_node_fields(self, tmp_path: Path) -> None:
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        node = _make_node(
            "a::B",
            "B",
            NodeKind.STRUCT,
            "src/b.rs",
            Span(10, 1, 50, 1),
            visibility="pub",
        )
        g = CodeGraph(nodes=[node], edges=[])
        path = tmp_path / "graph.json"
        GraphJsonFormatter().write(g, path)
        loaded = GraphJsonFormatter.read(path)

        ln = loaded.nodes[0]
        assert ln.id == "a::B"
        assert ln.label == "B"
        assert ln.kind == NodeKind.STRUCT
        assert ln.file == "src/b.rs"
        assert ln.span is not None
        assert ln.span.start_line == 10
        assert ln.span.end_line == 50
        assert ln.properties["visibility"] == "pub"

    def test_round_trip_edge_fields(self, tmp_path: Path) -> None:
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        edge = _make_edge(
            "a::X",
            "b::Y",
            EdgeRelation.CALLS,
            Confidence.INFERRED,
            0.85,
        )
        node_x = _make_node("a::X", "X")
        node_y = _make_node("b::Y", "Y")
        g = CodeGraph(nodes=[node_x, node_y], edges=[edge])
        path = tmp_path / "graph.json"
        GraphJsonFormatter().write(g, path)
        loaded = GraphJsonFormatter.read(path)

        le = loaded.edges[0]
        assert le.source == "a::X"
        assert le.target == "b::Y"
        assert le.relation == EdgeRelation.CALLS
        assert le.confidence == Confidence.INFERRED
        assert le.confidence_score == pytest.approx(0.85)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        path = tmp_path / "bad.json"
        path.write_text('["not", "an", "object"]', encoding="utf-8")
        with pytest.raises(TypeError, match="Expected JSON object"):
            GraphJsonFormatter.read(path)


# endregion: --- GraphJsonFormatter round-trip


# ---------------------------------------------------------------------------
# region:    --- Graph Loader
# ---------------------------------------------------------------------------


class TestGraphLoader:
    """Tests for load_or_build_graph."""

    def test_loads_cached_graph(
        self, graph: CodeGraph, tmp_path: Path,
    ) -> None:
        """If graph.json exists, the loader reads it without building."""
        from ast_intel.core._graph_loader import load_or_build_graph
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        # Pre-write a graph.json.
        graph_path = tmp_path / "graph.json"
        GraphJsonFormatter().write(graph, graph_path)

        # Create a dummy repo dir (loader needs a valid path).
        repo = tmp_path / "repo"
        repo.mkdir()

        loaded = load_or_build_graph(repo, output_dir=tmp_path)
        assert len(loaded.nodes) == len(graph.nodes)
        assert len(loaded.edges) == len(graph.edges)

    def test_no_cache_flag_ignores_existing(
        self, tmp_path: Path,
    ) -> None:
        """With no_cache=True, even if graph.json exists the loader
        runs the pipeline (which yields an empty graph for empty repo).
        """
        from ast_intel.core._graph_loader import load_or_build_graph
        from ast_intel.formatters.graph_json_formatter import (
            GraphJsonFormatter,
        )

        # Pre-write a graph with 8 nodes.
        graph_path = tmp_path / "graph.json"
        GraphJsonFormatter().write(_sample_graph(), graph_path)

        # Empty repo — pipeline will produce an empty graph.
        repo = tmp_path / "repo"
        repo.mkdir()

        loaded = load_or_build_graph(
            repo, output_dir=tmp_path, no_cache=True,
        )
        # The fresh build on an empty repo yields 0 nodes.
        assert len(loaded.nodes) == 0


# endregion: --- Graph Loader


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.impact (blast radius)
# ---------------------------------------------------------------------------


class TestQueryEngineImpact:
    """Tests for QueryEngine.impact() — blast-radius queries."""

    def test_impact_leaf_node(self, graph: CodeGraph) -> None:
        """FuncD is called by FuncC→FuncB→FuncA, FileA contains FuncA & FuncB."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD")
        labels = [n.label for n in result.affected]
        # FuncC calls FuncD directly (dist 1)
        assert "FuncC" in labels
        # FuncB calls FuncC (dist 2)
        assert "FuncB" in labels
        # FuncA calls FuncB (dist 3)
        assert "FuncA" in labels

    def test_impact_distances_correct(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD")
        dist_by_label = {
            self._label(result, nid): d
            for nid, d in result.distances.items()
        }
        assert dist_by_label["FuncC"] == 1
        assert dist_by_label["FuncB"] == 2
        assert dist_by_label["FuncA"] == 3

    def test_impact_sorted_by_distance(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD")
        distances = [result.distances[n.id] for n in result.affected]
        assert distances == sorted(distances)

    def test_impact_depth_limit(self, graph: CodeGraph) -> None:
        """Depth=1 should only return direct predecessors."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD", depth=1)
        labels = {n.label for n in result.affected}
        assert "FuncC" in labels
        assert "FuncB" not in labels
        assert result.depth_limit == 1

    def test_impact_depth_two(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD", depth=2)
        labels = {n.label for n in result.affected}
        assert "FuncC" in labels
        assert "FuncB" in labels
        assert "FuncA" not in labels

    def test_impact_isolated_node(self, graph: CodeGraph) -> None:
        """An isolated node has no dependents."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("Lonely")
        assert len(result.affected) == 0

    def test_impact_unknown_symbol(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        with pytest.raises(KeyError, match="NoSuchSymbol"):
            engine.impact("NoSuchSymbol")

    def test_impact_root_excluded(self, graph: CodeGraph) -> None:
        """The root node itself must NOT appear in affected."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncD")
        affected_ids = {n.id for n in result.affected}
        assert result.root.id not in affected_ids

    def test_impact_result_fields(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import ImpactResult, QueryEngine

        engine = QueryEngine(graph)
        result = engine.impact("FuncC", depth=5)
        assert isinstance(result, ImpactResult)
        assert result.root.label == "FuncC"
        assert result.depth_limit == 5

    # Helper
    @staticmethod
    def _label(result: object, nid: str) -> str:
        from ast_intel.core._query_engine import ImpactResult

        assert isinstance(result, ImpactResult)
        for n in result.affected:
            if n.id == nid:
                return n.label
        return nid


# endregion: --- QueryEngine.impact (blast radius)


# ---------------------------------------------------------------------------
# region:    --- CLI Subcommands
# ---------------------------------------------------------------------------


class TestCLISubcommands:
    """Tests for the CLI subcommand structure."""

    def test_help_lists_subcommands(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "query" in result.output
        assert "path" in result.output
        assert "explain" in result.output
        assert "impact" in result.output

    def test_scan_help_shows_options(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "--include" in result.output
        assert "--format" in result.output
        assert "--analyze" in result.output

    def test_query_help_shows_options(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["query", "--help"])
        assert result.exit_code == 0
        assert "pattern" in result.output.lower() or "PATTERN" in result.output
        assert "--kind" in result.output
        assert "--regex" in result.output

    def test_path_help_shows_options(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["path", "--help"])
        assert result.exit_code == 0
        assert "source" in result.output.lower() or "SOURCE" in result.output
        assert "target" in result.output.lower() or "TARGET" in result.output

    def test_explain_help_shows_options(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["explain", "--help"])
        assert result.exit_code == 0
        assert "symbol" in result.output.lower() or "SYMBOL" in result.output

    def test_version_flag(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "ast-intel" in result.output

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0

    def test_bare_path_without_subcommand_errors(
        self, tmp_path: Path,
    ) -> None:
        """After restructure, `ast-intel /path` without a subcommand
        should fail (breaking change)."""
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [str(tmp_path)])
        # Typer shows help or error for unknown subcommand.
        assert result.exit_code != 0


# endregion: --- CLI Subcommands


# ---------------------------------------------------------------------------
# region:    --- CLI Impact Subcommand
# ---------------------------------------------------------------------------


class TestCLIImpactSubcommand:
    """Tests for the impact CLI subcommand."""

    def test_impact_help_shows_options(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["impact", "--help"])
        assert result.exit_code == 0
        assert "symbol" in result.output.lower() or "SYMBOL" in result.output
        assert "--depth" in result.output

    def test_impact_on_empty_repo(self, tmp_path: Path) -> None:
        """Impact on an empty repo should exit gracefully."""
        from typer.testing import CliRunner

        from ast_intel.cli import app

        repo = tmp_path / "repo"
        repo.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["impact", "FuncA", str(repo), "--output", str(tmp_path)],
        )
        # Symbol not found → exit 1, or no dependents → exit 0.
        assert result.exit_code in {0, 1}


# endregion: --- CLI Impact Subcommand


# ---------------------------------------------------------------------------
# region:    --- Extended Fixture (richer edges for new queries)
# ---------------------------------------------------------------------------


def _extended_graph() -> CodeGraph:
    """A richer graph that adds IMPORTS, SIMILAR_TO, FileB, and a method.

    Extends ``_sample_graph`` topology with::

        FileB (file) --contains--> FuncC
        FileB (file) --contains--> FuncD
        FileA --imports--> import_os
        StructX --similar_to--> StructY (score=0.75)
        StructX --has_field--> FieldZ
        MethodM (method) --method_of--> StructX
    """
    nodes = [
        _make_node("f::FileA", "FileA", NodeKind.FILE, "src/a.rs"),
        _make_node("f::FileB", "FileB", NodeKind.FILE, "src/b.rs"),
        _make_node("f::FileA::FuncA", "FuncA", NodeKind.FUNCTION, "src/a.rs",
                    visibility="pub", return_type="Result<()>"),
        _make_node("f::FileA::FuncB", "FuncB", NodeKind.FUNCTION, "src/a.rs"),
        _make_node("f::FileB::FuncC", "FuncC", NodeKind.FUNCTION, "src/b.rs"),
        _make_node("f::FileB::FuncD", "FuncD", NodeKind.FUNCTION, "src/b.rs"),
        _make_node("f::StructX", "StructX", NodeKind.STRUCT, "src/a.rs"),
        _make_node("f::StructY", "StructY", NodeKind.STRUCT, "src/b.rs"),
        _make_node("f::TraitY", "TraitY", NodeKind.TRAIT, "src/b.rs"),
        _make_node("f::Lonely", "Lonely", NodeKind.FUNCTION, "src/c.rs"),
        _make_node("f::import_os", "import os", NodeKind.IMPORT, "src/a.rs"),
        _make_node("f::FieldZ", "field_z", NodeKind.CONSTANT, "src/a.rs"),
        _make_node("f::MethodM", "process", NodeKind.METHOD, "src/a.rs"),
    ]
    edges = [
        # CONTAINS
        _make_edge("f::FileA", "f::FileA::FuncA", EdgeRelation.CONTAINS),
        _make_edge("f::FileA", "f::FileA::FuncB", EdgeRelation.CONTAINS),
        _make_edge("f::FileB", "f::FileB::FuncC", EdgeRelation.CONTAINS),
        _make_edge("f::FileB", "f::FileB::FuncD", EdgeRelation.CONTAINS),
        # CALLS
        _make_edge("f::FileA::FuncA", "f::FileA::FuncB", EdgeRelation.CALLS),
        _make_edge("f::FileA::FuncB", "f::FileB::FuncC", EdgeRelation.CALLS),
        _make_edge("f::FileB::FuncC", "f::FileB::FuncD", EdgeRelation.CALLS),
        # IMPLEMENTS
        _make_edge("f::StructX", "f::TraitY", EdgeRelation.IMPLEMENTS),
        # IMPORTS
        _make_edge("f::FileA", "f::import_os", EdgeRelation.IMPORTS),
        # SIMILAR_TO
        GraphEdge(
            source="f::StructX", target="f::StructY",
            relation=EdgeRelation.SIMILAR_TO,
            confidence=Confidence.INFERRED,
            confidence_score=0.75,
        ),
        # HAS_FIELD
        _make_edge("f::StructX", "f::FieldZ", EdgeRelation.HAS_FIELD),
        # METHOD_OF
        _make_edge("f::MethodM", "f::StructX", EdgeRelation.METHOD_OF),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


@pytest.fixture
def ext_graph() -> CodeGraph:
    return _extended_graph()


# endregion: --- Extended Fixture


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.get_dependencies
# ---------------------------------------------------------------------------


class TestGetDependencies:
    """Tests for QueryEngine.get_dependencies()."""

    def test_forward_calls(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependencies("FuncA")
        labels = {n.label for n in result.calls}
        assert "FuncB" in labels

    def test_forward_contains(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependencies("FileA")
        labels = {n.label for n in result.contains}
        assert "FuncA" in labels
        assert "FuncB" in labels

    def test_forward_imports(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependencies("FileA")
        labels = {n.label for n in result.imports}
        assert "import os" in labels

    def test_forward_implements(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependencies("StructX")
        labels = {n.label for n in result.implements}
        assert "TraitY" in labels

    def test_isolated_node_empty(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependencies("Lonely")
        assert len(result.calls) == 0
        assert len(result.imports) == 0

    def test_unknown_symbol_raises(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        with pytest.raises(KeyError, match="NoSuchSymbol"):
            engine.get_dependencies("NoSuchSymbol")


# endregion: --- QueryEngine.get_dependencies


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.get_dependents
# ---------------------------------------------------------------------------


class TestGetDependents:
    """Tests for QueryEngine.get_dependents()."""

    def test_reverse_calls(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependents("FuncB")
        labels = {n.label for n in result.calls}
        # FuncA calls FuncB
        assert "FuncA" in labels

    def test_reverse_contains(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependents("FuncA")
        labels = {n.label for n in result.contains}
        assert "FileA" in labels

    def test_reverse_implements(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependents("TraitY")
        labels = {n.label for n in result.implements}
        assert "StructX" in labels

    def test_leaf_node_no_reverse_calls(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.get_dependents("FuncD")
        # FuncD has no outgoing calls, but FuncC calls FuncD
        labels = {n.label for n in result.calls}
        assert "FuncC" in labels

    def test_method_of_reverse(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        # MethodM --method_of--> StructX, so StructX's dependents include MethodM
        result = engine.get_dependents("StructX")
        other_labels = {n.label for n in result.other}
        assert "process" in other_labels


# endregion: --- QueryEngine.get_dependents


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    """Tests for QueryEngine.get_context()."""

    def test_context_has_explanation(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        ctx = engine.get_context("FuncA")
        assert ctx.explanation.node.label == "FuncA"
        assert ctx.explanation.role in {"hub", "leaf", "bridge", "isolated"}

    def test_context_has_dependencies(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        ctx = engine.get_context("FileA")
        assert len(ctx.dependencies.contains) >= 2

    def test_context_has_siblings(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        ctx = engine.get_context("FuncA")
        # FuncA is in src/a.rs — siblings include FuncB, StructX, etc.
        sib_labels = {s.label for s in ctx.siblings}
        assert "FuncB" in sib_labels

    def test_context_similar(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        ctx = engine.get_context("StructX")
        sim_labels = {n.label for n in ctx.similar}
        assert "StructY" in sim_labels

    def test_context_unknown_raises(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        with pytest.raises(KeyError):
            engine.get_context("NonExistent")


# endregion: --- QueryEngine.get_context


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.find_usages
# ---------------------------------------------------------------------------


class TestFindUsages:
    """Tests for QueryEngine.find_usages()."""

    def test_usages_of_funcb(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        entries = engine.find_usages("FuncB")
        # FuncA calls FuncB, FileA contains FuncB
        labels = {e.node.label for e in entries}
        assert "FuncA" in labels
        assert "FileA" in labels

    def test_usages_relations(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        entries = engine.find_usages("FuncB")
        rels = {e.relation for e in entries}
        assert EdgeRelation.CALLS in rels
        assert EdgeRelation.CONTAINS in rels

    def test_usages_isolated_empty(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        entries = engine.find_usages("Lonely")
        assert entries == []

    def test_usages_unknown_raises(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        with pytest.raises(KeyError):
            engine.find_usages("NonExistent")

    def test_usages_have_file(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        entries = engine.find_usages("TraitY")
        assert all(e.file for e in entries)


# endregion: --- QueryEngine.find_usages


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    """Tests for QueryEngine.list_files()."""

    def test_all_files(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        infos = engine.list_files()
        file_paths = {f.node.file for f in infos}
        assert "src/a.rs" in file_paths
        assert "src/b.rs" in file_paths

    def test_glob_filter(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        infos = engine.list_files("*a.rs")
        assert len(infos) == 1
        assert infos[0].node.file == "src/a.rs"

    def test_symbol_counts(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        infos = engine.list_files("*a.rs")
        info = infos[0]
        # FileA contains FuncA, FuncB
        assert info.symbol_count >= 2
        assert info.functions >= 2

    def test_sorted_by_symbol_count(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        infos = engine.list_files()
        counts = [f.symbol_count for f in infos]
        assert counts == sorted(counts, reverse=True)

    def test_limit(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        all_files = engine.list_files()
        limited = engine.list_files(limit=1)
        assert len(limited) == 1
        assert limited[0].node.file == all_files[0].node.file

    def test_offset(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        all_files = engine.list_files()
        offset_files = engine.list_files(offset=1)
        assert len(offset_files) == len(all_files) - 1
        assert offset_files[0].node.file == all_files[1].node.file

    def test_offset_and_limit(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        all_files = engine.list_files()
        page = engine.list_files(offset=1, limit=1)
        assert len(page) == 1
        assert page[0].node.file == all_files[1].node.file

    def test_limit_none_returns_all(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        all_files = engine.list_files(limit=None)
        no_limit = engine.list_files()
        assert len(all_files) == len(no_limit)

    def test_offset_beyond_end(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        result = engine.list_files(offset=9999)
        assert result == []

    def test_pagination_preserves_sort(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        page = engine.list_files(offset=0, limit=2)
        counts = [f.symbol_count for f in page]
        assert counts == sorted(counts, reverse=True)


# endregion: --- QueryEngine.list_files


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.list_routes
# ---------------------------------------------------------------------------


def _routes_graph() -> CodeGraph:
    """Graph with ROUTE nodes across two services for routes tests.

    - ``users`` service: ``GET /api/users`` (handler resolved) and
      ``POST /api/users``.
    - ``orders`` service: ``GET /api/orders`` (no HANDLES edge).
    """
    nodes = [
        _make_node(
            "users::src/users.py", "users.py", NodeKind.FILE, "src/users.py",
        ),
        _make_node(
            "users::src/users.py::list_users", "list_users",
            NodeKind.FUNCTION, "src/users.py",
        ),
        GraphNode(
            id="users::route:GET:/api/users",
            label="GET /api/users",
            kind=NodeKind.ROUTE,
            file="src/users.py",
            span=Span(10, 1, 12, 1),
            properties={
                "path": "/api/users",
                "method": "GET",
                "framework": "fastapi",
                "handler": "list_users",
            },
            service="users",
        ),
        GraphNode(
            id="users::route:POST:/api/users",
            label="POST /api/users",
            kind=NodeKind.ROUTE,
            file="src/users.py",
            properties={
                "path": "/api/users",
                "method": "POST",
                "framework": "fastapi",
                "handler": "create_user",
            },
            service="users",
        ),
        GraphNode(
            id="orders::route:GET:/api/orders",
            label="GET /api/orders",
            kind=NodeKind.ROUTE,
            file="src/orders.py",
            properties={
                "path": "/api/orders",
                "method": "GET",
                "framework": "flask",
                "handler": "list_orders",
            },
            service="orders",
        ),
    ]
    edges = [
        _make_edge(
            "users::src/users.py", "users::route:GET:/api/users",
            EdgeRelation.EXPOSES,
        ),
        _make_edge(
            "users::route:GET:/api/users",
            "users::src/users.py::list_users", EdgeRelation.HANDLES,
        ),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


class TestListRoutes:
    """Tests for QueryEngine.list_routes()."""

    def test_all_routes(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes()
        assert len(routes) == 3
        pairs = {(r.method, r.path) for r in routes}
        assert ("GET", "/api/users") in pairs
        assert ("POST", "/api/users") in pairs
        assert ("GET", "/api/orders") in pairs

    def test_filter_by_service(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes(service="users")
        assert len(routes) == 2
        assert all(r.service == "users" for r in routes)

    def test_filter_by_service_case_insensitive(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes(service="USERS")
        assert len(routes) == 2

    def test_filter_unknown_service_empty(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        assert engine.list_routes(service="nope") == []

    def test_route_fields(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes(service="users")
        get_route = next(r for r in routes if r.method == "GET")
        assert get_route.path == "/api/users"
        assert get_route.framework == "fastapi"
        assert get_route.handler == "list_users"
        assert get_route.file == "src/users.py"

    def test_handler_node_resolved(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes(service="users")
        get_route = next(r for r in routes if r.method == "GET")
        assert get_route.handler_node is not None
        assert get_route.handler_node.label == "list_users"

    def test_handler_node_none_when_missing(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes(service="orders")
        assert routes[0].handler_node is None

    def test_sorted_order(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_routes_graph())
        routes = engine.list_routes()
        keys = [(r.service, r.path, r.method) for r in routes]
        assert keys == sorted(keys)

    def test_no_routes_empty(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)  # no ROUTE nodes
        assert engine.list_routes() == []


# endregion: --- QueryEngine.list_routes


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.get_implementors
# ---------------------------------------------------------------------------


class TestGetImplementors:
    """Tests for QueryEngine.get_implementors()."""

    def test_trait_implementors(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        impls = engine.get_implementors("TraitY")
        labels = {n.label for n, _ in impls}
        assert "StructX" in labels

    def test_no_implementors(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        # FuncA has no IMPLEMENTS edges targeting it
        impls = engine.get_implementors("FuncA")
        assert impls == []

    def test_implementors_include_file(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        impls = engine.get_implementors("TraitY")
        for _, f in impls:
            assert f  # non-empty file path

    def test_unknown_trait_raises(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        with pytest.raises(KeyError):
            engine.get_implementors("NonExistent")


# endregion: --- QueryEngine.get_implementors


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.find_similar
# ---------------------------------------------------------------------------


class TestFindSimilar:
    """Tests for QueryEngine.find_similar()."""

    def test_similar_to_struct(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        hits = engine.find_similar("StructX")
        labels = {n.label for n, _ in hits}
        assert "StructY" in labels

    def test_similar_score(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        hits = engine.find_similar("StructX")
        # Our test edge has score 0.75
        scores = {n.label: s for n, s in hits}
        assert scores["StructY"] == pytest.approx(0.75)

    def test_similar_bidirectional(self, ext_graph: CodeGraph) -> None:
        """SIMILAR_TO edges should be found from either side."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        hits = engine.find_similar("StructY")
        labels = {n.label for n, _ in hits}
        assert "StructX" in labels

    def test_similar_limit(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        hits = engine.find_similar("StructX", limit=1)
        assert len(hits) <= 1

    def test_no_similar(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        hits = engine.find_similar("Lonely")
        assert hits == []


# endregion: --- QueryEngine.find_similar


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.rank_similar
# ---------------------------------------------------------------------------


def _rankable_graph() -> CodeGraph:
    """Two functions with overlapping callees (no SIMILAR_TO edges)."""
    nodes = [
        _make_node("f::FuncA", "FuncA", NodeKind.FUNCTION),
        _make_node("f::FuncB", "FuncB", NodeKind.FUNCTION),
        _make_node("f::FuncC", "FuncC", NodeKind.FUNCTION),
        _make_node("f::lib::x", "x", NodeKind.FUNCTION),
        _make_node("f::lib::y", "y", NodeKind.FUNCTION),
        _make_node("f::lib::z", "z", NodeKind.FUNCTION),
    ]
    edges = [
        _make_edge("f::FuncA", "f::lib::x", EdgeRelation.CALLS),
        _make_edge("f::FuncA", "f::lib::y", EdgeRelation.CALLS),
        _make_edge("f::FuncB", "f::lib::x", EdgeRelation.CALLS),
        _make_edge("f::FuncB", "f::lib::y", EdgeRelation.CALLS),
        _make_edge("f::FuncC", "f::lib::z", EdgeRelation.CALLS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


class TestRankSimilar:
    """Tests for QueryEngine.rank_similar() (on-demand, no edges)."""

    def test_ranks_overlapping_function(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_rankable_graph())
        hits = engine.rank_similar("FuncA")
        assert hits[0][0].label == "FuncB"
        assert hits[0][1] == pytest.approx(1.0)

    def test_no_overlap_returns_empty(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_rankable_graph())
        assert engine.rank_similar("FuncC") == []

    def test_limit_respected(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_rankable_graph())
        assert len(engine.rank_similar("FuncA", limit=1)) <= 1

    def test_unknown_symbol_raises(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(_rankable_graph())
        with pytest.raises(KeyError):
            engine.rank_similar("Nope")


# endregion: --- QueryEngine.rank_similar



# ---------------------------------------------------------------------------
# region:    --- QueryEngine.get_community
# ---------------------------------------------------------------------------


class TestGetCommunity:
    """Tests for QueryEngine.get_community()."""

    def test_no_analysis_raises(self, ext_graph: CodeGraph) -> None:
        """Without analysis, get_community should raise."""
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)  # no analysis
        with pytest.raises(KeyError, match="No community"):
            engine.get_community("FuncA")

    def test_unknown_symbol_raises(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(ext_graph)
        with pytest.raises(KeyError):
            engine.get_community("NonExistent")


# endregion: --- QueryEngine.get_community


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.diff_graphs
# ---------------------------------------------------------------------------


class TestDiffGraphs:
    """Tests for QueryEngine.diff_graphs()."""

    def test_identical_graphs_empty_diff(self, ext_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        diff = QueryEngine.diff_graphs(ext_graph, ext_graph)
        assert len(diff.added_nodes) == 0
        assert len(diff.removed_nodes) == 0
        assert len(diff.added_edges) == 0
        assert len(diff.removed_edges) == 0

    def test_added_node_detected(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        old = CodeGraph(
            nodes=[_make_node("a", "A")],
            edges=[],
        )
        new = CodeGraph(
            nodes=[_make_node("a", "A"), _make_node("b", "B")],
            edges=[],
        )
        diff = QueryEngine.diff_graphs(old, new)
        assert len(diff.added_nodes) == 1
        assert diff.added_nodes[0].id == "b"

    def test_removed_node_detected(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        old = CodeGraph(
            nodes=[_make_node("a", "A"), _make_node("b", "B")],
            edges=[],
        )
        new = CodeGraph(
            nodes=[_make_node("a", "A")],
            edges=[],
        )
        diff = QueryEngine.diff_graphs(old, new)
        assert len(diff.removed_nodes) == 1
        assert diff.removed_nodes[0].id == "b"

    def test_added_edge_detected(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        nodes = [_make_node("a", "A"), _make_node("b", "B")]
        old = CodeGraph(nodes=nodes, edges=[])
        new = CodeGraph(
            nodes=nodes,
            edges=[_make_edge("a", "b", EdgeRelation.CALLS)],
        )
        diff = QueryEngine.diff_graphs(old, new)
        assert len(diff.added_edges) == 1
        assert diff.added_edges[0].source == "a"

    def test_removed_edge_detected(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        nodes = [_make_node("a", "A"), _make_node("b", "B")]
        old = CodeGraph(
            nodes=nodes,
            edges=[_make_edge("a", "b", EdgeRelation.CALLS)],
        )
        new = CodeGraph(nodes=nodes, edges=[])
        diff = QueryEngine.diff_graphs(old, new)
        assert len(diff.removed_edges) == 1

    def test_no_change_no_diff(self) -> None:
        from ast_intel.core._query_engine import QueryEngine

        nodes = [_make_node("a", "A"), _make_node("b", "B")]
        edges = [_make_edge("a", "b", EdgeRelation.CALLS)]
        old = CodeGraph(nodes=nodes, edges=edges)
        new = CodeGraph(nodes=list(nodes), edges=list(edges))
        diff = QueryEngine.diff_graphs(old, new)
        assert len(diff.added_nodes) == 0
        assert len(diff.removed_nodes) == 0


# endregion: --- QueryEngine.diff_graphs


# ---------------------------------------------------------------------------
# region:    --- Levenshtein Helper
# ---------------------------------------------------------------------------


class TestLevenshtein:
    """Unit tests for the _levenshtein edit distance helper."""

    def test_identical_strings(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("hello", "hello") == 0

    def test_single_insertion(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("cat", "cats") == 1

    def test_single_deletion(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("cats", "cat") == 1

    def test_single_substitution(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("cat", "car") == 1

    def test_classic_kitten_sitting(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("kitten", "sitting") == 3

    def test_empty_strings(self) -> None:
        from ast_intel.core._query_engine import _levenshtein

        assert _levenshtein("", "") == 0
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "abc") == 3


# endregion: --- Levenshtein Helper


# ---------------------------------------------------------------------------
# region:    --- Tokenize Helper
# ---------------------------------------------------------------------------


class TestTokenize:
    """Unit tests for the _tokenize helper."""

    def test_camel_case(self) -> None:
        from ast_intel.core._query_engine import _tokenize

        assert _tokenize("StorageHelper") == frozenset({"storage", "helper"})

    def test_snake_case(self) -> None:
        from ast_intel.core._query_engine import _tokenize

        assert _tokenize("my_func") == frozenset({"my", "func"})

    def test_kebab_case(self) -> None:
        from ast_intel.core._query_engine import _tokenize

        assert _tokenize("get-config") == frozenset({"get", "config"})

    def test_space_separated(self) -> None:
        from ast_intel.core._query_engine import _tokenize

        assert _tokenize("storage helper") == frozenset({"storage", "helper"})


# endregion: --- Tokenize Helper


# ---------------------------------------------------------------------------
# region:    --- Symbol Resolution Tiers
# ---------------------------------------------------------------------------


def _resolution_graph() -> CodeGraph:
    """Graph with intentionally diverse IDs for resolution testing.

    Nodes:
        - Alpha  with ID  ``"crate_a/src/config.rs::Alpha"``
        - Beta   with ID  ``"crate_b/src/handler.rs::Beta"``
        - Gamma  with ID  ``"crate_a/src/models.rs::GammaService"``
        - StorageHelper with full path ID
    """
    nodes = [
        _make_node(
            "crate_a/src/config.rs::Alpha",
            "Alpha",
            NodeKind.STRUCT,
            "crate_a/src/config.rs",
        ),
        _make_node(
            "crate_b/src/handler.rs::Beta",
            "Beta",
            NodeKind.STRUCT,
            "crate_b/src/handler.rs",
        ),
        _make_node(
            "crate_a/src/models.rs::GammaService",
            "GammaService",
            NodeKind.STRUCT,
            "crate_a/src/models.rs",
        ),
        _make_node(
            "lib-storage/src/storage_helper.rs::StorageHelper",
            "StorageHelper",
            NodeKind.TRAIT,
            "lib-storage/src/storage_helper.rs",
        ),
    ]
    edges = [
        _make_edge(
            "crate_a/src/config.rs::Alpha",
            "crate_b/src/handler.rs::Beta",
            EdgeRelation.CALLS,
        ),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


@pytest.fixture
def resolution_graph() -> CodeGraph:
    return _resolution_graph()


class TestResolution:
    """Tests for the 6-tier _resolve() strategy."""

    def test_tier1_exact_id(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("crate_a/src/config.rs::Alpha")
        assert ids == ["crate_a/src/config.rs::Alpha"]

    def test_tier2_exact_label(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("Alpha")
        assert ids == ["crate_a/src/config.rs::Alpha"]

    def test_tier3_suffix_match(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("StorageHelper")
        assert ids == ["lib-storage/src/storage_helper.rs::StorageHelper"]

    def test_tier4_token_match(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("gamma service")
        assert ids == ["crate_a/src/models.rs::GammaService"]

    def test_tier5_substring(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        # "amma" is a substring of "GammaService" but not an exact label,
        # suffix, or token match.
        ids = engine._resolve("amma")
        assert "crate_a/src/models.rs::GammaService" in ids

    def test_tier6_levenshtein(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        # "Alpah" is a typo for "Alpha" (edit distance 2).
        ids = engine._resolve("Alpah")
        assert "crate_a/src/config.rs::Alpha" in ids

    def test_tier_precedence_label_over_substring(
        self, resolution_graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        # "Beta" matches Tier 2 (exact label) even though "Beta" is also
        # a substring of other potential labels.
        ids = engine._resolve("Beta")
        assert ids == ["crate_b/src/handler.rs::Beta"]

    def test_resolve_empty(self, resolution_graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("CompletelyNonexistent999")
        assert ids == []

    def test_case_insensitive_suffix(
        self, resolution_graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(resolution_graph)
        ids = engine._resolve("storagehelper")
        assert ids == ["lib-storage/src/storage_helper.rs::StorageHelper"]


# endregion: --- Symbol Resolution Tiers


# ---------------------------------------------------------------------------
# region:    --- Disambiguation
# ---------------------------------------------------------------------------


def _ambiguous_graph() -> CodeGraph:
    """Graph with >5 identically-labelled nodes to trigger disambiguation."""
    nodes = [
        _make_node(f"crate_{i}/config.rs::Config", "Config", NodeKind.STRUCT,
                    f"crate_{i}/config.rs")
        for i in range(8)
    ]
    # Add a couple of edges so some nodes have higher degree.
    edges = [
        _make_edge("crate_0/config.rs::Config", "crate_1/config.rs::Config"),
        _make_edge("crate_0/config.rs::Config", "crate_2/config.rs::Config"),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


@pytest.fixture
def ambiguous_graph() -> CodeGraph:
    return _ambiguous_graph()


class TestDisambiguation:
    """Tests for AmbiguousSymbolError when matches exceed threshold."""

    def test_auto_pick_under_threshold(self) -> None:
        """≤5 matches → auto-picks highest degree, no exception."""
        from ast_intel.core._query_engine import QueryEngine

        nodes = [
            _make_node(f"crate_{i}/cfg.rs::Config", "Config", NodeKind.STRUCT,
                        f"crate_{i}/cfg.rs")
            for i in range(3)
        ]
        edges = [
            _make_edge("crate_0/cfg.rs::Config", "crate_1/cfg.rs::Config"),
        ]
        g = CodeGraph(nodes=nodes, edges=edges)
        engine = QueryEngine(g)
        _node_id, node = engine._resolve_one("Config")
        # Should succeed without raising.
        assert node.label == "Config"

    def test_disambiguation_over_threshold(
        self, ambiguous_graph: CodeGraph,
    ) -> None:
        """8 matches → raises AmbiguousSymbolError."""
        from ast_intel.core._query_engine import (
            AmbiguousSymbolError,
            QueryEngine,
        )

        engine = QueryEngine(ambiguous_graph)
        with pytest.raises(AmbiguousSymbolError) as exc_info:
            engine._resolve_one("Config")
        assert exc_info.value.total == 8
        assert len(exc_info.value.top_matches) == 8

    def test_disambiguation_error_attributes(
        self, ambiguous_graph: CodeGraph,
    ) -> None:
        from ast_intel.core._query_engine import (
            AmbiguousSymbolError,
            QueryEngine,
        )

        engine = QueryEngine(ambiguous_graph)
        with pytest.raises(AmbiguousSymbolError) as exc_info:
            engine._resolve_one("Config")
        err = exc_info.value
        assert err.symbol == "Config"
        assert "8 symbols" in str(err)
        # Top matches are sorted by degree (highest first).
        assert err.top_matches[0].id == "crate_0/config.rs::Config"


# endregion: --- Disambiguation


# ---------------------------------------------------------------------------
# region:    --- CLI New Subcommands
# ---------------------------------------------------------------------------


class TestCLINewSubcommands:
    """Tests for the new CLI subcommand help screens."""

    def test_deps_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["deps", "--help"])
        assert result.exit_code == 0
        assert "symbol" in result.output.lower() or "SYMBOL" in result.output

    def test_dependents_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["dependents", "--help"])
        assert result.exit_code == 0

    def test_context_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["context", "--help"])
        assert result.exit_code == 0

    def test_usages_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["usages", "--help"])
        assert result.exit_code == 0

    def test_files_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["files", "--help"])
        assert result.exit_code == 0

    def test_routes_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["routes", "--help"])
        assert result.exit_code == 0
        assert "--service" in result.output

    def test_implementors_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["implementors", "--help"])
        assert result.exit_code == 0

    def test_similar_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["similar", "--help"])
        assert result.exit_code == 0

    def test_community_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["community", "--help"])
        assert result.exit_code == 0

    def test_help_lists_all_subcommands(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("deps", "dependents", "context", "usages",
                     "files", "routes", "implementors", "similar",
                     "community"):
            assert cmd in result.output


# endregion: --- CLI New Subcommands


# ---------------------------------------------------------------------------
# region:    --- QueryEngine.find_dead_code
# ---------------------------------------------------------------------------


class TestQueryEngineDeadCode:
    """Tests for QueryEngine.find_dead_code()."""

    def test_finds_unreferenced_symbols(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        labels = {d.node.label for d in engine.find_dead_code()}
        # FuncA (only contained), StructX (only implements out), Lonely.
        assert labels == {"FuncA", "StructX", "Lonely"}

    def test_excludes_referenced_symbols(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        labels = {d.node.label for d in engine.find_dead_code()}
        assert "FuncB" not in labels and "TraitY" not in labels

    def test_excludes_file_nodes(self, graph: CodeGraph) -> None:
        from ast_intel.core._query_engine import QueryEngine

        engine = QueryEngine(graph)
        kinds = {d.node.kind for d in engine.find_dead_code()}
        assert NodeKind.FILE not in kinds


# endregion: --- QueryEngine.find_dead_code
