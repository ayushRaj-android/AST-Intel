"""Graph analyzer — compute analytical insights from a CodeGraph.

Produces god-node rankings, community clusters, surprising cross-community
connections, hyperedges (group relationships), and template-generated
suggested questions.

The analyzer is **pure-algorithmic** (no LLM dependency).  Community
detection uses NetworkX Louvain by default and can be upgraded to
Leiden via the optional ``graspologic`` dependency.

Usage::

    from ast_intel.core.analyzer import GraphAnalyzer
    analysis = GraphAnalyzer().analyze(code_graph)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ast_intel.models.graph_model import CodeGraph, EdgeRelation, GraphEdge

__all__: list[str] = [
    "Community",
    "GodNode",
    "GraphAnalysis",
    "GraphAnalyzer",
    "Hyperedge",
    "HyperedgeKind",
    "SurprisingConnection",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Enumerations
# ---------------------------------------------------------------------------


class HyperedgeKind(StrEnum):
    """Kind of group relationship captured by a hyperedge."""

    SHARED_TRAIT = "shared_trait"
    """All types implementing the same trait."""

    SHARED_CALLER = "shared_caller"
    """All functions called by the same set of callers."""

    SHARED_IMPORT = "shared_import"
    """All files importing the same module."""

    SHARED_SIMILARITY = "shared_similarity"
    """Connected component of structurally similar symbols."""


# endregion: --- Enumerations


# ---------------------------------------------------------------------------
# region:    --- Analysis Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GodNode:
    """A high-centrality node in the code graph.

    God nodes are the handful of symbols that nearly everything connects
    through — the hub types (``Config``, ``Response``, ``Error``, …)
    whose change would ripple across many files and modules.

    Attributes:
        node_id: Unique graph node ID.
        label: Human-readable symbol name.
        kind: Node category (struct, trait, function, …).
        degree: Total edges (in + out).
        in_degree: Incoming edge count.
        out_degree: Outgoing edge count.
        community: Leiden/Louvain community label.
        connected_communities: How many distinct communities this node bridges.
    """

    node_id: str
    label: str
    kind: str
    degree: int
    in_degree: int
    out_degree: int
    community: int
    connected_communities: int


@dataclass(frozen=True, slots=True)
class SurprisingConnection:
    """An edge connecting nodes from different community clusters.

    Ranked by *surprise score* — a combination of low Jaccard
    similarity between neighbourhoods and cross-community distance.

    Attributes:
        source_id: Source node ID.
        target_id: Target node ID.
        relation: Edge relation label.
        source_community: Source node's community ID.
        target_community: Target node's community ID.
        surprise_score: Higher = more surprising (0.0–1.0 range, boosted).
        why: Template-generated human-readable explanation.
    """

    source_id: str
    target_id: str
    relation: str
    source_community: int
    target_community: int
    surprise_score: float
    why: str


@dataclass(frozen=True, slots=True)
class Community:
    """A cluster of tightly-connected nodes found by Louvain/Leiden.

    Attributes:
        id: Integer community label.
        label: Auto-generated from the highest-degree node(s).
        node_count: Number of nodes in this community.
        internal_edge_count: Edges whose source *and* target are inside.
        key_nodes: Top-5 nodes by internal degree.
    """

    id: int
    label: str
    node_count: int
    internal_edge_count: int
    key_nodes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hyperedge:
    """A group relationship connecting 3 or more nodes.

    Examples: "All implementors of ``Serializable``", "All consumers of
    ``logging``".

    Attributes:
        label: Human-readable description.
        kind: Category (shared_trait, shared_caller, shared_import).
        members: Node IDs participating in the group.
    """

    label: str
    kind: HyperedgeKind
    members: tuple[str, ...]


@dataclass(slots=True)
class GraphAnalysis:
    """Full graph analysis results.

    Produced by :meth:`GraphAnalyzer.analyze`.

    Attributes:
        god_nodes: Ranked list, highest degree first.
        communities: Clusters found by community detection.
        surprising_connections: Cross-community surprises, highest score first.
        suggested_questions: Template-generated analysis questions.
        hyperedges: Group relationships involving ≥ 3 nodes.
        modularity: Community detection quality metric (0.0–1.0).
    """

    god_nodes: list[GodNode] = field(default_factory=list)
    communities: list[Community] = field(default_factory=list)
    surprising_connections: list[SurprisingConnection] = field(
        default_factory=list,
    )
    suggested_questions: list[str] = field(default_factory=list)
    hyperedges: list[Hyperedge] = field(default_factory=list)
    modularity: float = 0.0


# endregion: --- Analysis Dataclasses


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _build_degree_maps(
    edges: list[GraphEdge],
) -> tuple[dict[str, int], dict[str, int]]:
    """Count in-degree and out-degree for every node referenced by *edges*."""
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for e in edges:
        out_deg[e.source] = out_deg.get(e.source, 0) + 1
        in_deg[e.target] = in_deg.get(e.target, 0) + 1
    return in_deg, out_deg


def _build_neighbor_sets(
    edges: list[GraphEdge],
) -> dict[str, set[str]]:
    """Build undirected neighbour sets for Jaccard computation."""
    nbrs: dict[str, set[str]] = {}
    for e in edges:
        nbrs.setdefault(e.source, set()).add(e.target)
        nbrs.setdefault(e.target, set()).add(e.source)
    return nbrs


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets.  Returns 0.0 for empty sets."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _bfs_components(
    adj: dict[str, set[str]],
) -> list[list[str]]:
    """Find connected components in an undirected adjacency dict via BFS.

    Returns sorted components (sorted by first member, members sorted).
    """
    from collections import deque

    visited: set[str] = set()
    components: list[list[str]] = []
    for start in sorted(adj):
        if start in visited:
            continue
        component: list[str] = []
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            queue.extend(n for n in adj.get(node, set()) if n not in visited)
        components.append(sorted(component))
    return components


def _detect_communities_networkx(
    edges: list[GraphEdge],
    node_ids: list[str],
    *,
    resolution: float = 1.0,
    seed: int = 42,
) -> tuple[dict[str, int], float]:
    """Community detection via NetworkX Louvain (always available).

    Returns:
        Tuple of (node_id → community_int, modularity_float).
    """
    import networkx as nx  # type: ignore[import-untyped]

    g = nx.Graph()
    g.add_nodes_from(node_ids)
    for e in edges:
        if g.has_edge(e.source, e.target):
            g[e.source][e.target]["weight"] += 1
        else:
            g.add_edge(e.source, e.target, weight=1)

    communities_list: list[set[str]] = nx.community.louvain_communities(
        g, resolution=resolution, seed=seed,
    )

    mapping: dict[str, int] = {}
    for cid, members in enumerate(communities_list):
        for node_id in members:
            mapping[node_id] = cid

    modularity = (
        0.0
        if g.number_of_edges() == 0
        else nx.community.modularity(g, communities_list)
    )
    return mapping, modularity


def _detect_communities(
    edges: list[GraphEdge],
    node_ids: list[str],
    *,
    resolution: float = 1.0,
    seed: int = 42,
) -> tuple[dict[str, int], float]:
    """Try Leiden (graspologic) first, fall back to Louvain (networkx).

    Returns:
        Tuple of (node_id → community_int, modularity_float).
    """
    try:
        import networkx as nx
        from graspologic.partition import leiden  # type: ignore[import-not-found]

        logger.info("Using Leiden community detection (graspologic)")
        g = nx.Graph()
        g.add_nodes_from(node_ids)
        for e in edges:
            if g.has_edge(e.source, e.target):
                g[e.source][e.target]["weight"] += 1
            else:
                g.add_edge(e.source, e.target, weight=1)

        partition = leiden(g, resolution=resolution, random_seed=seed)
        mapping: dict[str, int] = {}
        for node_id, cid in partition.items():
            mapping[str(node_id)] = int(cid)

        # Reconstruct community sets for modularity calculation
        community_sets: dict[int, set[str]] = {}
        for nid, cid in mapping.items():
            community_sets.setdefault(cid, set()).add(nid)
        modularity = (
            0.0
            if g.number_of_edges() == 0
            else nx.community.modularity(
                g, list(community_sets.values()),
            )
        )
    except ImportError:
        logger.info(
            "graspologic not installed — falling back to Louvain (networkx). "
            "Install graspologic for Leiden: pip install ast-intel[analysis]",
        )
        return _detect_communities_networkx(
            edges, node_ids, resolution=resolution, seed=seed,
        )
    else:
        return mapping, modularity


# endregion: --- Internal Helpers


# ---------------------------------------------------------------------------
# region:    --- GraphAnalyzer
# ---------------------------------------------------------------------------


# Default limits — configurable via constructor.
_DEFAULT_GOD_NODE_LIMIT: int = 10
_DEFAULT_SURPRISE_LIMIT: int = 20
_DEFAULT_QUESTION_LIMIT: int = 5

# Surprise score boost factors.
_CROSS_COMMUNITY_BOOST: float = 1.5
_CROSS_FILE_BOOST: float = 1.2

# Minimum group size for a hyperedge.
_MIN_HYPEREDGE_SIZE: int = 3

# Threshold for "orphan" community question.
_ORPHAN_COMMUNITY_THRESHOLD: int = 5

# Threshold for "high fan-out" question.
_HIGH_FANOUT_THRESHOLD: int = 5


class GraphAnalyzer:
    """Compute analytical insights from a ``CodeGraph``.

    All algorithms are pure graph-algorithmic — no LLM required.

    Args:
        god_node_limit: Maximum god nodes to report.
        surprise_limit: Maximum surprising connections to report.
        question_limit: Maximum suggested questions to emit.
        resolution: Community detection resolution parameter.
        seed: Random seed for reproducible community detection.
    """

    def __init__(
        self,
        *,
        god_node_limit: int = _DEFAULT_GOD_NODE_LIMIT,
        surprise_limit: int = _DEFAULT_SURPRISE_LIMIT,
        question_limit: int = _DEFAULT_QUESTION_LIMIT,
        resolution: float = 1.0,
        seed: int = 42,
    ) -> None:
        self._god_node_limit = god_node_limit
        self._surprise_limit = surprise_limit
        self._question_limit = question_limit
        self._resolution = resolution
        self._seed = seed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, graph: CodeGraph) -> GraphAnalysis:
        """Run the full analysis pipeline on a ``CodeGraph``.

        Pipeline:
            1. Degree centrality → god nodes
            2. Community detection (Leiden/Louvain)
            3. Cross-community surprising connections
            4. Hyperedge detection
            5. Suggested question generation

        Args:
            graph: A ``CodeGraph`` produced by ``GraphBuilder``.

        Returns:
            A ``GraphAnalysis`` with all results populated.
        """
        if not graph.nodes:
            logger.warning("Empty graph — nothing to analyze")
            return GraphAnalysis()

        # ---- Pre-computation ----
        node_ids = [n.id for n in graph.nodes]
        in_deg, out_deg = _build_degree_maps(graph.edges)
        nbrs = _build_neighbor_sets(graph.edges)

        # ---- 1. Community detection ----
        community_map, modularity = _detect_communities(
            graph.edges,
            node_ids,
            resolution=self._resolution,
            seed=self._seed,
        )
        # Assign community 0 to any orphan nodes not in an edge
        for nid in node_ids:
            community_map.setdefault(nid, 0)

        # Filter community_map to only graph node IDs so totals match
        node_id_set = set(node_ids)
        community_map = {
            k: v for k, v in community_map.items() if k in node_id_set
        }

        communities = self._build_communities(
            graph, community_map, in_deg, out_deg,
        )

        # ---- 2. God nodes ----
        god_nodes = self._find_god_nodes(
            graph, in_deg, out_deg, community_map, nbrs,
        )

        # ---- 3. Surprising connections ----
        surprising = self._find_surprising_connections(
            graph, community_map, nbrs, communities,
        )

        # ---- 4. Hyperedges ----
        hyperedges = self._find_hyperedges(graph)

        # ---- 5. Suggested questions ----
        questions = self._generate_questions(
            god_nodes, communities, surprising,
        )

        logger.info(
            "Analysis complete: %d god nodes, %d communities, "
            "%d surprising connections, %d hyperedges, %d questions",
            len(god_nodes),
            len(communities),
            len(surprising),
            len(hyperedges),
            len(questions),
        )

        return GraphAnalysis(
            god_nodes=god_nodes,
            communities=communities,
            surprising_connections=surprising,
            suggested_questions=questions,
            hyperedges=hyperedges,
            modularity=modularity,
        )

    # ------------------------------------------------------------------
    # Phase 6B — God Node Detection
    # ------------------------------------------------------------------

    def _find_god_nodes(
        self,
        graph: CodeGraph,
        in_deg: dict[str, int],
        out_deg: dict[str, int],
        community_map: dict[str, int],
        nbrs: dict[str, set[str]],
    ) -> list[GodNode]:
        """Find the highest-degree nodes — the hubs of the codebase."""
        candidates: list[tuple[int, str]] = []
        for n in graph.nodes:
            total = in_deg.get(n.id, 0) + out_deg.get(n.id, 0)
            candidates.append((total, n.id))

        # Sort by total degree descending, break ties by ID for stability
        candidates.sort(key=lambda x: (-x[0], x[1]))

        node_map = {n.id: n for n in graph.nodes}
        result: list[GodNode] = []
        for degree, nid in candidates[: self._god_node_limit]:
            if degree == 0:
                break
            node = node_map[nid]
            # Count how many *distinct* communities this node's neighbours
            # belong to.
            neighbor_communities: set[int] = set()
            for neighbor_id in nbrs.get(nid, set()):
                neighbor_communities.add(community_map.get(neighbor_id, 0))
            result.append(
                GodNode(
                    node_id=nid,
                    label=node.label,
                    kind=node.kind.value,
                    degree=degree,
                    in_degree=in_deg.get(nid, 0),
                    out_degree=out_deg.get(nid, 0),
                    community=community_map.get(nid, 0),
                    connected_communities=len(neighbor_communities),
                ),
            )
        return result

    # ------------------------------------------------------------------
    # Phase 6C — Community Building
    # ------------------------------------------------------------------

    def _build_communities(
        self,
        graph: CodeGraph,
        community_map: dict[str, int],
        in_deg: dict[str, int],
        out_deg: dict[str, int],
    ) -> list[Community]:
        """Build ``Community`` objects from the community assignment map."""
        # Group nodes by community
        groups: dict[int, list[str]] = {}
        for nid, cid in community_map.items():
            groups.setdefault(cid, []).append(nid)

        # Count internal edges per community
        internal_counts: dict[int, int] = {}
        for e in graph.edges:
            sc = community_map.get(e.source, -1)
            tc = community_map.get(e.target, -1)
            if sc == tc and sc >= 0:
                internal_counts[sc] = internal_counts.get(sc, 0) + 1

        result: list[Community] = []
        for cid in sorted(groups):
            members = groups[cid]
            # Sort by internal degree to find key nodes
            member_degrees: list[tuple[int, str]] = []
            for nid in members:
                d = in_deg.get(nid, 0) + out_deg.get(nid, 0)
                member_degrees.append((d, nid))
            member_degrees.sort(key=lambda x: (-x[0], x[1]))

            top_5 = tuple(nid for _, nid in member_degrees[:5])
            # Label from highest-degree node
            label = (
                top_5[0].rsplit("::", maxsplit=1)[-1]
                if top_5
                else f"community-{cid}"
            )
            result.append(
                Community(
                    id=cid,
                    label=label,
                    node_count=len(members),
                    internal_edge_count=internal_counts.get(cid, 0),
                    key_nodes=top_5,
                ),
            )
        return result

    # ------------------------------------------------------------------
    # Phase 6D — Surprising Connections
    # ------------------------------------------------------------------

    def _find_surprising_connections(
        self,
        graph: CodeGraph,
        community_map: dict[str, int],
        nbrs: dict[str, set[str]],
        communities: list[Community],
    ) -> list[SurprisingConnection]:
        """Find the most surprising cross-community edges."""
        community_label_map = {c.id: c.label for c in communities}

        # Count inter-community edge counts for "why" text
        inter_counts: dict[tuple[int, int], int] = {}
        for e in graph.edges:
            sc = community_map.get(e.source, 0)
            tc = community_map.get(e.target, 0)
            if sc != tc:
                key = (min(sc, tc), max(sc, tc))
                inter_counts[key] = inter_counts.get(key, 0) + 1

        candidates: list[SurprisingConnection] = []
        seen_pairs: set[tuple[str, str]] = set()

        # Pre-build node file lookup — O(N) once instead of O(N*E)
        node_file_map = {n.id: n.file for n in graph.nodes}

        for e in graph.edges:
            sc = community_map.get(e.source, 0)
            tc = community_map.get(e.target, 0)
            if sc == tc:
                continue  # Same community — not surprising

            pair_key = (min(e.source, e.target), max(e.source, e.target))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # Jaccard-based surprise
            src_nbrs = nbrs.get(e.source, set())
            tgt_nbrs = nbrs.get(e.target, set())
            jacc = _jaccard(src_nbrs, tgt_nbrs)
            score = 1.0 - jacc

            # Boost for cross-community
            score *= _CROSS_COMMUNITY_BOOST

            # Boost for cross-file
            src_file = e.file
            tgt_node_file = node_file_map.get(e.target, "")
            if src_file and tgt_node_file and src_file != tgt_node_file:
                score *= _CROSS_FILE_BOOST

            # Clamp to [0, 1]
            score = min(score, 1.0)

            # Build "why" text
            inter_key = (min(sc, tc), max(sc, tc))
            shared_edges = inter_counts.get(inter_key, 0)
            src_label = e.source.rsplit("::", maxsplit=1)[-1]
            tgt_label = e.target.rsplit("::", maxsplit=1)[-1]
            src_comm_label = community_label_map.get(sc, f"community-{sc}")
            tgt_comm_label = community_label_map.get(tc, f"community-{tc}")

            why = (
                f"{src_label} (in {src_comm_label}) connects to "
                f"{tgt_label} (in {tgt_comm_label}) via {e.relation} "
                f"— these communities share only {shared_edges} other "
                f"connection(s)"
            )

            candidates.append(
                SurprisingConnection(
                    source_id=e.source,
                    target_id=e.target,
                    relation=e.relation.value
                    if hasattr(e.relation, "value")
                    else str(e.relation),
                    source_community=sc,
                    target_community=tc,
                    surprise_score=round(score, 4),
                    why=why,
                ),
            )

        # Sort by surprise score descending, then by source+target for stability
        candidates.sort(
            key=lambda c: (-c.surprise_score, c.source_id, c.target_id),
        )
        return candidates[: self._surprise_limit]

    # ------------------------------------------------------------------
    # Phase 6E — Suggested Questions
    # ------------------------------------------------------------------

    def _generate_questions(
        self,
        god_nodes: list[GodNode],
        communities: list[Community],
        surprising: list[SurprisingConnection],
    ) -> list[str]:
        """Generate template-based analysis questions."""
        questions: list[str] = []

        # Q1-Q2: God nodes
        questions.extend(
            f"What role does `{gn.label}` play in the architecture? "
            f"It connects to {gn.degree} other symbols across "
            f"{gn.connected_communities} modules."
            for gn in god_nodes[:2]
        )

        # Q3: Most surprising connection
        if surprising:
            top = surprising[0]
            src_label = top.source_id.rsplit("::", maxsplit=1)[-1]
            tgt_label = top.target_id.rsplit("::", maxsplit=1)[-1]
            questions.append(
                f"Why does `{src_label}` depend on `{tgt_label}`? "
                f"They appear in different architectural modules.",
            )

        # Q4: Orphan community (only internal edges, no external)
        for c in communities:
            if (
                c.internal_edge_count > 0
                and c.node_count <= _ORPHAN_COMMUNITY_THRESHOLD
            ):
                questions.append(
                    f"Community {c.id} ({c.label}) has only "
                    f"{c.node_count} node(s) — is it a self-contained "
                    f"module or accidentally isolated?",
                )
                break

        # Q5: High fan-out god node
        for gn in god_nodes:
            if (
                gn.out_degree > gn.in_degree
                and gn.out_degree >= _HIGH_FANOUT_THRESHOLD
            ):
                questions.append(
                    f"Function `{gn.label}` calls {gn.out_degree} other "
                    f"functions — is it doing too much?",
                )
                break

        return questions[: self._question_limit]

    # ------------------------------------------------------------------
    # Phase 6F — Hyperedge Detection
    # ------------------------------------------------------------------

    def _find_hyperedges(self, graph: CodeGraph) -> list[Hyperedge]:
        """Detect group relationships (hyperedges) from edge patterns."""
        from ast_intel.models.graph_model import EdgeRelation

        hyperedges: list[Hyperedge] = []
        self._collect_shared_traits(graph.edges, hyperedges, EdgeRelation)
        self._collect_shared_imports(graph.edges, hyperedges, EdgeRelation)
        self._collect_shared_callers(graph.edges, hyperedges, EdgeRelation)
        self._collect_similarity_clusters(graph.edges, hyperedges, EdgeRelation)
        return hyperedges

    @staticmethod
    def _collect_shared_traits(
        edges: list[GraphEdge],
        out: list[Hyperedge],
        er: type[EdgeRelation],
    ) -> None:
        """Collect shared-trait-implementation hyperedges."""
        trait_implementors: dict[str, list[str]] = {}
        for e in edges:
            if e.relation in (er.IMPLEMENTS, er.INHERITS):
                trait_implementors.setdefault(e.target, []).append(e.source)

        for trait_id, impls in sorted(trait_implementors.items()):
            if len(impls) >= _MIN_HYPEREDGE_SIZE:
                trait_label = trait_id.rsplit("::", maxsplit=1)[-1]
                out.append(
                    Hyperedge(
                        label=f"Implementors of {trait_label}",
                        kind=HyperedgeKind.SHARED_TRAIT,
                        members=tuple(sorted(impls)),
                    ),
                )

    @staticmethod
    def _collect_shared_imports(
        edges: list[GraphEdge],
        out: list[Hyperedge],
        er: type[EdgeRelation],
    ) -> None:
        """Collect shared-import hyperedges."""
        import_consumers: dict[str, list[str]] = {}
        for e in edges:
            if e.relation == er.IMPORTS:
                import_consumers.setdefault(e.target, []).append(e.source)

        for mod_id, consumers in sorted(import_consumers.items()):
            if len(consumers) >= _MIN_HYPEREDGE_SIZE:
                mod_label = mod_id.rsplit("::", maxsplit=1)[-1]
                out.append(
                    Hyperedge(
                        label=f"Consumers of {mod_label}",
                        kind=HyperedgeKind.SHARED_IMPORT,
                        members=tuple(sorted(consumers)),
                    ),
                )

    @staticmethod
    def _collect_shared_callers(
        edges: list[GraphEdge],
        out: list[Hyperedge],
        er: type[EdgeRelation],
    ) -> None:
        """Collect shared-caller hyperedges."""
        call_targets: dict[str, list[str]] = {}
        for e in edges:
            if e.relation == er.CALLS:
                call_targets.setdefault(e.target, []).append(e.source)

        for fn_id, callers in sorted(call_targets.items()):
            if len(callers) >= _MIN_HYPEREDGE_SIZE:
                fn_label = fn_id.rsplit("::", maxsplit=1)[-1]
                out.append(
                    Hyperedge(
                        label=f"Callers of {fn_label}",
                        kind=HyperedgeKind.SHARED_CALLER,
                        members=tuple(sorted(callers)),
                    ),
                )

    @staticmethod
    def _collect_similarity_clusters(
        edges: list[GraphEdge],
        out: list[Hyperedge],
        er: type[EdgeRelation],
    ) -> None:
        """Collect connected components of SIMILAR_TO edges."""
        adj: dict[str, set[str]] = {}
        for e in edges:
            if e.relation == er.SIMILAR_TO:
                adj.setdefault(e.source, set()).add(e.target)
                adj.setdefault(e.target, set()).add(e.source)

        if not adj:
            return

        for component in _bfs_components(adj):
            if len(component) >= _MIN_HYPEREDGE_SIZE:
                pivot_label = component[0].rsplit("::", maxsplit=1)[-1]
                out.append(
                    Hyperedge(
                        label=f"Similar to {pivot_label}",
                        kind=HyperedgeKind.SHARED_SIMILARITY,
                        members=tuple(component),
                    ),
                )


# endregion: --- GraphAnalyzer
