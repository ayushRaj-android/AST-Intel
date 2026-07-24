"""Query engine — interactive graph queries.

Provides :class:`QueryEngine` for searching, path-finding,
explaining, impact-analysis, dependency/usage tracking, and
community exploration of symbols in a
:class:`~ast_intel.models.graph_model.CodeGraph`.
Used by CLI subcommands and later by the MCP server (Feature 13).
"""

from __future__ import annotations

import fnmatch
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ast_intel.models.ast_node import Span
from ast_intel.models.graph_model import EdgeRelation, NodeKind

if TYPE_CHECKING:
    from ast_intel.core.analyzer import GraphAnalysis
    from ast_intel.history.enrichment import EnrichmentPipeline
    from ast_intel.history.history_builder import HistoryBuilder
    from ast_intel.history.signals.signal_aggregator import SignalAggregator
    from ast_intel.history.models import (
        EnrichedHistoryRecord,
        HistoryRecord,
        OwnershipRecord,
    )
    from ast_intel.models.graph_model import CodeGraph, GraphEdge, GraphNode

__all__: list[str] = [
    "AmbiguousSymbolError",
    "CommunityResult",
    "DeadCodeEntry",
    "DependencyResult",
    "FileInfo",
    "GraphDiff",
    "ImpactResult",
    "NodeExplanation",
    "QueryEngine",
    "RouteInfo",
    "SymbolContext",
    "UsageEntry",
]

# Maximum search results returned by `search()`.
_MAX_SEARCH_RESULTS: int = 50

# When `_resolve()` returns more than this many matches,
# `_resolve_one()` raises :class:`AmbiguousSymbolError`.
_AMBIGUITY_THRESHOLD: int = 5

# Maximum edit distance for Levenshtein fuzzy matching.
_FUZZY_MAX_DISTANCE: int = 3


def _levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between *a* and *b*."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1] + [0] * len(b)
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr[j + 1] = min(
                curr[j] + 1,
                prev[j + 1] + 1,
                prev[j] + cost,
            )
        prev = curr
    return prev[-1]


def _tokenize(name: str) -> frozenset[str]:
    """Split a symbol name into lowercase tokens.

    Handles ``snake_case``, ``kebab-case``, space-separated, and
    ``CamelCase`` names::

        "StorageHelper"   → {"storage", "helper"}
        "my_func"         → {"my", "func"}
        "get-config-path" → {"get", "config", "path"}
    """
    import re as _re

    # First split on underscores, hyphens, spaces.
    parts = _re.split(r"[_\-\s]+", name)
    tokens: list[str] = []
    for part in parts:
        # Then split CamelCase: insert boundary before uppercase runs.
        camel_parts = _re.sub(
            r"([a-z])([A-Z])", r"\1 \2", part,
        ).split()
        tokens.extend(t.lower() for t in camel_parts if t)
    return frozenset(tokens)


class AmbiguousSymbolError(Exception):
    """Raised when symbol resolution finds too many matches.

    Carries the top candidate nodes so callers can present a
    disambiguation list.
    """

    def __init__(
        self,
        symbol: str,
        total: int,
        top_matches: list[GraphNode],
    ) -> None:
        self.symbol = symbol
        self.total = total
        self.top_matches = top_matches
        super().__init__(
            f"{symbol!r} matches {total} symbols. "
            "Narrow your query or use a full node ID.",
        )

# Guard networkx — only needed at runtime when QueryEngine is instantiated.
try:
    import networkx as nx  # type: ignore[import-untyped]
except ImportError as _nx_err:
    _NX_IMPORT_ERROR: ImportError | None = _nx_err
else:
    _NX_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# region:    --- Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeExplanation:
    """Structured explanation of a symbol's role in the graph.

    Attributes:
        node: The graph node being explained.
        degree: Total edges (in + out).
        in_degree: Incoming edge count.
        out_degree: Outgoing edge count.
        callers: Symbols that call this node.
        callees: Symbols this node calls.
        community: Community cluster ID (from :class:`GraphAnalysis`), if any.
        role: Structural classification: ``"hub"``, ``"bridge"``,
            ``"leaf"``, or ``"isolated"``.
        summary: Human-readable paragraph summarizing the node's role.
    """

    node: GraphNode
    degree: int
    in_degree: int
    out_degree: int
    callers: tuple[str, ...]
    callees: tuple[str, ...]
    community: int | None
    role: str
    summary: str


@dataclass(frozen=True, slots=True)
class ImpactResult:
    """Result of a blast-radius / impact query.

    Attributes:
        root: The starting node whose dependents are traced.
        affected: Nodes that transitively depend on *root*,
            sorted by distance (nearest first).
        distances: Mapping of node ID → hop distance from *root*.
        depth_limit: Maximum traversal depth (0 = unlimited).
    """

    root: GraphNode
    affected: tuple[GraphNode, ...]
    distances: dict[str, int]
    depth_limit: int


@dataclass(frozen=True, slots=True)
class DependencyResult:
    """Grouped 1-hop neighbors of a symbol, by edge relation.

    Attributes:
        node: The queried node.
        calls: Nodes connected by CALLS edges.
        imports: Nodes connected by IMPORTS edges.
        uses_methods: Nodes connected by USES_METHOD edges.
        inherits: Nodes connected by INHERITS edges.
        implements: Nodes connected by IMPLEMENTS edges.
        contains: Nodes connected by CONTAINS edges.
        other: Nodes connected by other relation types.
    """

    node: GraphNode
    calls: tuple[GraphNode, ...]
    imports: tuple[GraphNode, ...]
    uses_methods: tuple[GraphNode, ...]
    inherits: tuple[GraphNode, ...]
    implements: tuple[GraphNode, ...]
    contains: tuple[GraphNode, ...]
    other: tuple[GraphNode, ...]


@dataclass(frozen=True, slots=True)
class UsageEntry:
    """A single reference (incoming edge) to a symbol.

    Attributes:
        node: The node that references the target.
        relation: The type of reference.
        file: File where the reference occurs.
        span: Source location of the reference, if available.
    """

    node: GraphNode
    relation: EdgeRelation
    file: str
    span: Span | None


@dataclass(frozen=True, slots=True)
class SymbolContext:
    """Unified single-shot context for a symbol.

    Combines explanation, dependencies, dependents, siblings,
    community peers, and similar nodes into one structure.
    """

    node: GraphNode
    explanation: NodeExplanation
    dependencies: DependencyResult
    dependents: DependencyResult
    siblings: tuple[GraphNode, ...]
    community_peers: tuple[GraphNode, ...]
    similar: tuple[GraphNode, ...]


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Summary information about a file node.

    Attributes:
        node: The FILE-kind graph node.
        symbol_count: Total symbols contained in this file.
        key_symbols: Top symbols by degree (up to 5).
        structs: Count of struct nodes.
        functions: Count of function nodes.
        methods: Count of method nodes.
        traits: Count of trait nodes.
    """

    node: GraphNode
    symbol_count: int
    key_symbols: tuple[str, ...]
    structs: int
    functions: int
    methods: int
    traits: int


@dataclass(frozen=True, slots=True)
class DeadCodeEntry:
    """A symbol with no incoming reference edges (dead-code candidate).

    Attributes:
        node: The unreferenced graph node.
        in_degree: Total incoming edges, including structural ones.
    """

    node: GraphNode
    in_degree: int


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """A single HTTP route / endpoint extracted from the graph.

    Attributes:
        node: The ``ROUTE``-kind graph node.
        method: HTTP method (``"GET"``, ``"POST"``, …) or ``"*"``.
        path: URL path pattern (e.g. ``"/api/users/{id}"``).
        framework: Web framework that registered the route
            (axum, actix, fastapi, flask, express, spring).
        handler: Handler function/method name.
        handler_node: Resolved handler node (via the ``HANDLES`` edge),
            or ``None`` if the handler is not present in the graph.
        file: Source file that defines the route.
        service: Owning service — set by ``ast-intel merge`` on a
            multi-repo graph, otherwise ``""``.
    """

    node: GraphNode
    method: str
    path: str
    framework: str
    handler: str
    handler_node: GraphNode | None
    file: str
    service: str


@dataclass(frozen=True, slots=True)
class CommunityResult:
    """All members of a community cluster.

    Attributes:
        community_id: The cluster identifier.
        members: All nodes in this community.
        hub_nodes: Highest-degree nodes in the community.
        member_count: Total member count.
    """

    community_id: int
    members: tuple[GraphNode, ...]
    hub_nodes: tuple[GraphNode, ...]
    member_count: int


@dataclass(frozen=True, slots=True)
class GraphDiff:
    """Structural difference between two code graphs.

    Attributes:
        added_nodes: Nodes present in *new* but not *old*.
        removed_nodes: Nodes present in *old* but not *new*.
        added_edges: Edges present in *new* but not *old*.
        removed_edges: Edges present in *old* but not *new*.
    """

    added_nodes: tuple[GraphNode, ...]
    removed_nodes: tuple[GraphNode, ...]
    added_edges: tuple[GraphEdge, ...]
    removed_edges: tuple[GraphEdge, ...]


# endregion: --- Data Structures


# ---------------------------------------------------------------------------
# region:    --- Query Engine
# ---------------------------------------------------------------------------


class QueryEngine:
    """Execute structured queries against a :class:`CodeGraph`.

    Wraps a directed :mod:`networkx` ``DiGraph`` and a label search
    index for fast look-ups.

    Args:
        graph: The code knowledge graph to query.
        analysis: Optional pre-computed analysis (for community info
            in :meth:`explain`).
    """

    def __init__(
        self,
        graph: CodeGraph,
        analysis: GraphAnalysis | None = None,
        *,
        history_builder: HistoryBuilder | None = None,
        enrichment: EnrichmentPipeline | None = None,
        signal_aggregator: SignalAggregator | None = None,
    ) -> None:
        if _NX_IMPORT_ERROR is not None:
            msg = (
                "networkx is required for graph queries. "
                "Install with: pip install ast-intel[analysis]"
            )
            raise ImportError(msg) from _NX_IMPORT_ERROR

        self._graph = graph
        self._analysis = analysis
        self._node_map: dict[str, GraphNode] = {
            n.id: n for n in graph.nodes
        }
        # Lazily computed structural feature sets for rank_similar.
        self._feature_sets: dict[str, frozenset[str]] | None = None
        self._nx: nx.DiGraph = self._build_digraph(graph.edges, graph.nodes)
        self._label_index: dict[str, list[str]] = self._build_label_index(
            graph.nodes,
        )
        self._suffix_index: dict[str, list[str]] = self._build_suffix_index(
            graph.nodes,
        )
        # Pre-compute community memberships from analysis.
        self._community_map: dict[str, int] = {}
        if analysis is not None:
            for comm in analysis.communities:
                for nid in comm.key_nodes:
                    self._community_map[nid] = comm.id
            # Also populate from god_nodes which carry community directly.
            for gn in analysis.god_nodes:
                self._community_map[gn.node_id] = gn.community

        # History / enrichment plumbing (Phase 3).  Both are optional —
        # the existing graph-query surface works without them.
        self._history_builder = history_builder
        self._enrichment = enrichment
        # Phase 4 — fused multi-source signal aggregator (optional).
        self._signal_aggregator = signal_aggregator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        pattern: str,
        *,
        kind: NodeKind | None = None,
        regex: bool = False,
    ) -> list[GraphNode]:
        """Find nodes matching *pattern* (substring or regex).

        Results are sorted by degree (most connected first) and capped
        at 50 entries.

        Args:
            pattern: Substring or regex to match against node labels.
            kind: Optional filter — only return nodes of this kind.
            regex: Treat *pattern* as a Python regex.

        Returns:
            Matching :class:`GraphNode` list, up to 50 entries.
        """
        compiled: re.Pattern[str] | None = None
        if regex:
            compiled = re.compile(pattern, re.IGNORECASE)
        lower_pattern = pattern.lower()

        hits: list[GraphNode] = []
        for node in self._graph.nodes:
            if kind is not None and node.kind != kind:
                continue
            if compiled is not None:
                if not compiled.search(node.label):
                    continue
            elif lower_pattern not in node.label.lower():
                continue
            hits.append(node)

        # Sort by degree descending (most connected first).
        hits.sort(key=lambda n: self._nx.degree(n.id), reverse=True)
        return hits[:_MAX_SEARCH_RESULTS]

    def shortest_path(
        self,
        source: str,
        target: str,
    ) -> list[tuple[GraphNode, EdgeRelation | None]] | None:
        """Find the shortest directed path between two symbols.

        *source* and *target* are resolved as node labels first, then
        as exact node IDs. If a label matches multiple nodes every
        combination is tried and the shortest overall path is returned.

        Returns:
            A list of ``(node, edge_relation_to_next)`` tuples. The
            last tuple has ``None`` as the relation. Returns ``None``
            if no path exists.
        """
        src_ids = self._resolve(source)
        tgt_ids = self._resolve(target)

        if not src_ids or not tgt_ids:
            return None

        best: list[str] | None = None
        for sid in src_ids:
            for tid in tgt_ids:
                if sid == tid:
                    # Trivial self-path.
                    if best is None or len(best) > 1:
                        best = [sid]
                    continue
                try:
                    candidate: list[str] = nx.shortest_path(
                        self._nx, sid, tid,
                    )
                except nx.NetworkXNoPath:
                    continue
                if best is None or len(candidate) < len(best):
                    best = candidate

        if best is None:
            return None

        return self._annotate_path(best)

    def explain(self, symbol: str) -> NodeExplanation:
        """Generate a structural explanation for *symbol*.

        Args:
            symbol: Node label or ID.

        Returns:
            A :class:`NodeExplanation` with degree stats, role
            classification, and a human-readable summary.

        Raises:
            KeyError: If *symbol* cannot be resolved to a graph node.
        """
        ids = self._resolve(symbol)
        if not ids:
            msg = f"Symbol not found: {symbol!r}"
            raise KeyError(msg)

        # Pick the highest-degree match.
        node_id = max(ids, key=self._nx.degree)
        node = self._node_map[node_id]

        in_deg: int = self._nx.in_degree(node_id)
        out_deg: int = self._nx.out_degree(node_id)
        degree = in_deg + out_deg

        callers = self._neighbors_by_relation(node_id, EdgeRelation.CALLS, incoming=True)
        callees = self._neighbors_by_relation(node_id, EdgeRelation.CALLS, incoming=False)

        community = self._community_map.get(node_id)
        role = self._classify_role(node_id, degree, in_deg, out_deg)
        summary = self._build_summary(
            node, degree, in_deg, out_deg,
            callers, callees, community, role,
        )

        return NodeExplanation(
            node=node,
            degree=degree,
            in_degree=in_deg,
            out_degree=out_deg,
            callers=tuple(callers),
            callees=tuple(callees),
            community=community,
            role=role,
            summary=summary,
        )

    def impact(
        self,
        symbol: str,
        *,
        depth: int = 0,
    ) -> ImpactResult:
        """Compute the blast radius (reverse transitive closure) for *symbol*.

        Walks **incoming** edges (predecessors in the DiGraph) via BFS
        to find every node that directly or transitively depends on the
        given symbol.

        Args:
            symbol: Node label or ID.
            depth: Maximum BFS depth.  ``0`` means unlimited.

        Returns:
            An :class:`ImpactResult` with the affected nodes and their
            distances from the root.

        Raises:
            KeyError: If *symbol* cannot be resolved to a graph node.
        """
        ids = self._resolve(symbol)
        if not ids:
            msg = f"Symbol not found: {symbol!r}"
            raise KeyError(msg)

        root_id = max(ids, key=self._nx.degree)
        root_node = self._node_map[root_id]

        # BFS on reversed edges (predecessors = "who depends on me").
        visited: dict[str, int] = {}  # node_id → distance
        frontier: list[tuple[str, int]] = [(root_id, 0)]

        while frontier:
            current, dist = frontier.pop(0)
            next_dist = dist + 1
            if depth > 0 and next_dist > depth:
                continue
            for pred in self._nx.predecessors(current):
                if pred not in visited and pred != root_id:
                    visited[pred] = next_dist
                    frontier.append((pred, next_dist))

        # Sort affected nodes by distance, then label.
        affected_nodes = sorted(
            (self._node_map[nid] for nid in visited),
            key=lambda n: (visited[n.id], n.label),
        )

        return ImpactResult(
            root=root_node,
            affected=tuple(affected_nodes),
            distances=visited,
            depth_limit=depth,
        )

    def get_dependencies(self, symbol: str) -> DependencyResult:
        """Return forward 1-hop neighbors grouped by edge relation.

        Args:
            symbol: Node label or ID.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        node_id, node = self._resolve_one(symbol)
        return self._collect_neighbors(node_id, node, outgoing=True)

    def get_dependents(self, symbol: str) -> DependencyResult:
        """Return reverse 1-hop neighbors grouped by edge relation.

        Args:
            symbol: Node label or ID.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        node_id, node = self._resolve_one(symbol)
        return self._collect_neighbors(node_id, node, outgoing=False)

    def get_context(self, symbol: str) -> SymbolContext:
        """Return unified single-shot context for *symbol*.

        Combines explanation, direct dependencies, direct dependents,
        same-file siblings, community peers, and similar nodes.

        Args:
            symbol: Node label or ID.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        node_id, node = self._resolve_one(symbol)
        explanation = self.explain(symbol)
        deps = self.get_dependencies(symbol)
        dependents = self.get_dependents(symbol)

        # Same-file siblings (other symbols in the same file).
        siblings = tuple(
            n for n in self._graph.nodes
            if n.file == node.file and n.id != node_id
            and n.kind != NodeKind.IMPORT
        )

        # Community peers.
        community_peers: tuple[GraphNode, ...] = ()
        comm = self._community_map.get(node_id)
        if comm is not None:
            peers = [
                self._node_map[nid]
                for nid, cid in self._community_map.items()
                if cid == comm and nid != node_id and nid in self._node_map
            ]
            peers.sort(key=lambda n: self._nx.degree(n.id), reverse=True)
            community_peers = tuple(peers[:10])

        # SIMILAR_TO neighbors.
        similar = self.find_similar(symbol)

        return SymbolContext(
            node=node,
            explanation=explanation,
            dependencies=deps,
            dependents=dependents,
            siblings=siblings,
            community_peers=community_peers,
            similar=tuple(n for n, _ in similar),
        )

    def find_usages(self, symbol: str) -> list[UsageEntry]:
        """Find all incoming references to *symbol*.

        Returns every edge whose target is the resolved node, with
        the referencing node, relation type, and call-site location.

        Args:
            symbol: Node label or ID.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        node_id, _ = self._resolve_one(symbol)
        entries: list[UsageEntry] = []
        for e in self._graph.edges:
            if e.target == node_id:
                src_node = self._node_map.get(e.source)
                if src_node is not None:
                    entries.append(UsageEntry(
                        node=src_node,
                        relation=e.relation,
                        file=e.file or src_node.file,
                        span=e.span,
                    ))
        return entries

    # Symbol kinds that can meaningfully be "dead code".
    _DEAD_CODE_KINDS: frozenset[NodeKind] = frozenset({
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.STRUCT,
        NodeKind.ENUM,
        NodeKind.TRAIT,
        NodeKind.TYPE_ALIAS,
        NodeKind.CONSTANT,
        NodeKind.MACRO,
    })

    # Structural edges that imply ownership, not actual usage.
    _STRUCTURAL_RELATIONS: frozenset[EdgeRelation] = frozenset({
        EdgeRelation.CONTAINS,
        EdgeRelation.METHOD_OF,
    })

    def find_dead_code(self) -> list[DeadCodeEntry]:
        """Find symbols with no incoming reference edges (dead code).

        A symbol is a dead-code candidate when nothing references it via a
        usage relation (calls, implements, imports, etc.). Structural
        ownership edges (CONTAINS, METHOD_OF) are ignored, since being
        contained in a file does not count as being used. Only true symbol
        kinds (functions, methods, types, constants, macros) are reported.

        Returns:
            :class:`DeadCodeEntry` list sorted by file then label.
        """
        referenced: set[str] = {
            e.target for e in self._graph.edges
            if e.relation not in self._STRUCTURAL_RELATIONS
        }
        dead = [
            DeadCodeEntry(node=n, in_degree=self._nx.in_degree(n.id))
            for n in self._graph.nodes
            if n.kind in self._DEAD_CODE_KINDS and n.id not in referenced
        ]
        dead.sort(key=lambda d: (d.node.file, d.node.label))
        return dead

    def list_files(
        self,
        pattern: str = "",
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[FileInfo]:
        """List file nodes with symbol counts and key symbols.

        Args:
            pattern: Optional glob pattern to filter file paths
                (e.g. ``"*redis*"``). Empty string matches all.
            offset: Number of files to skip (for pagination).
            limit: Maximum number of files to return.  *None*
                (the default) returns everything.

        Returns:
            :class:`FileInfo` list sorted by symbol count descending.
        """
        file_nodes = [
            n for n in self._graph.nodes if n.kind == NodeKind.FILE
        ]
        if pattern:
            lower_pat = pattern.lower()
            file_nodes = [
                n for n in file_nodes
                if fnmatch.fnmatch(n.file.lower(), lower_pat)
            ]

        result: list[FileInfo] = []
        for fn in file_nodes:
            children = self._children_of(fn.id)
            structs = sum(1 for c in children if c.kind == NodeKind.STRUCT)
            functions = sum(1 for c in children if c.kind == NodeKind.FUNCTION)
            methods = sum(1 for c in children if c.kind == NodeKind.METHOD)
            traits = sum(1 for c in children if c.kind == NodeKind.TRAIT)
            # Key symbols: top 5 non-import children by degree.
            key = sorted(
                (c for c in children if c.kind != NodeKind.IMPORT),
                key=lambda n: self._nx.degree(n.id),
                reverse=True,
            )[:5]
            result.append(FileInfo(
                node=fn,
                symbol_count=len(children),
                key_symbols=tuple(c.label for c in key),
                structs=structs,
                functions=functions,
                methods=methods,
                traits=traits,
            ))
        result.sort(key=lambda f: f.symbol_count, reverse=True)
        if offset:
            result = result[offset:]
        if limit is not None:
            result = result[:limit]
        return result

    def list_routes(
        self,
        service: str | None = None,
    ) -> list[RouteInfo]:
        """List HTTP routes / endpoints, optionally filtered by service.

        Routes are extracted from web-framework registrations (Axum,
        Actix, FastAPI, Flask, Express, Spring) and stored as ``ROUTE``
        graph nodes.

        Args:
            service: Restrict to routes owned by this service — a
                case-insensitive match against the SERVICE assigned
                during ``ast-intel merge``. ``None`` (the default)
                returns every route in the graph.

        Returns:
            :class:`RouteInfo` list sorted by ``(service, path, method)``.
        """
        # Map each ROUTE node id to its handler node id via HANDLES edges.
        handler_by_route: dict[str, str] = {
            e.source: e.target
            for e in self._graph.edges
            if e.relation == EdgeRelation.HANDLES
        }

        wanted = service.lower() if service else None
        routes: list[RouteInfo] = []
        for n in self._graph.nodes:
            if n.kind != NodeKind.ROUTE:
                continue
            if wanted is not None and n.service.lower() != wanted:
                continue
            props = n.properties
            handler_id = handler_by_route.get(n.id)
            handler_node = (
                self._node_map.get(handler_id) if handler_id else None
            )
            routes.append(RouteInfo(
                node=n,
                method=props.get("method", ""),
                path=props.get("path", n.label),
                framework=props.get("framework", ""),
                handler=props.get("handler", ""),
                handler_node=handler_node,
                file=n.file,
                service=n.service,
            ))
        routes.sort(key=lambda r: (r.service, r.path, r.method))
        return routes

    def get_implementors(
        self,
        trait_symbol: str,
    ) -> list[tuple[GraphNode, str]]:
        """Find all implementations of a trait / interface.

        Args:
            trait_symbol: Trait name or node ID.

        Returns:
            List of ``(implementor_node, file)`` tuples.

        Raises:
            KeyError: If *trait_symbol* cannot be resolved.
        """
        node_id, _ = self._resolve_one(trait_symbol)
        implementors: list[tuple[GraphNode, str]] = []
        for e in self._graph.edges:
            if e.target == node_id and e.relation == EdgeRelation.IMPLEMENTS:
                impl_node = self._node_map.get(e.source)
                if impl_node is not None:
                    implementors.append((impl_node, impl_node.file))
        return implementors

    def find_similar(
        self,
        symbol: str,
        limit: int = 5,
    ) -> list[tuple[GraphNode, float]]:
        """Find nodes connected by SIMILAR_TO edges.

        Args:
            symbol: Node label or ID.
            limit: Maximum results to return.

        Returns:
            List of ``(node, similarity_score)`` sorted by score desc.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        node_id, _ = self._resolve_one(symbol)
        hits: list[tuple[GraphNode, float]] = []
        for e in self._graph.edges:
            if e.relation != EdgeRelation.SIMILAR_TO:
                continue
            if e.source == node_id:
                other = self._node_map.get(e.target)
                if other is not None:
                    hits.append((other, e.confidence_score))
            elif e.target == node_id:
                other = self._node_map.get(e.source)
                if other is not None:
                    hits.append((other, e.confidence_score))
        hits.sort(key=lambda t: t[1], reverse=True)
        return hits[:limit]

    def rank_similar(
        self,
        symbol: str,
        limit: int = 5,
        *,
        same_kind: bool = True,
    ) -> list[tuple[GraphNode, float]]:
        """Rank structurally similar candidates on demand.

        Unlike :meth:`find_similar`, this does not require precomputed
        ``SIMILAR_TO`` edges. It computes Jaccard similarity over
        structural feature sets at query time, so it works on any graph.

        Args:
            symbol: Node label or ID.
            limit: Maximum candidates to return.
            same_kind: Restrict candidates to the target's NodeKind.

        Returns:
            List of ``(node, score)`` sorted by score desc, score > 0.

        Raises:
            KeyError: If *symbol* cannot be resolved.
        """
        # reuse: found via search_symbols — core/_similarity.py
        from ast_intel.core._similarity import _jaccard, build_feature_sets

        node_id, target = self._resolve_one(symbol)
        if self._feature_sets is None:
            self._feature_sets = build_feature_sets(self._graph)
        target_feats = self._feature_sets.get(node_id)
        if not target_feats:
            return []

        scored: list[tuple[GraphNode, float]] = []
        for nid, feats in self._feature_sets.items():
            if nid == node_id:
                continue
            other = self._node_map.get(nid)
            if other is None or (same_kind and other.kind != target.kind):
                continue
            score = _jaccard(target_feats, feats)
            if score > 0.0:
                scored.append((other, round(score, 4)))
        scored.sort(key=lambda t: (-t[1], t[0].id))
        return scored[:limit]

    def get_community(self, symbol: str) -> CommunityResult:
        """Return all nodes in the same community as *symbol*.

        Requires that analysis was passed to the constructor.

        Args:
            symbol: Node label or ID.

        Returns:
            A :class:`CommunityResult` with all community members.

        Raises:
            KeyError: If *symbol* cannot be resolved or has no community.
        """
        node_id, _ = self._resolve_one(symbol)
        comm = self._community_map.get(node_id)
        if comm is None:
            msg = (
                f"No community found for {symbol!r}. "
                "Run with --analyze to enable community detection."
            )
            raise KeyError(msg)

        members = [
            self._node_map[nid]
            for nid, cid in self._community_map.items()
            if cid == comm and nid in self._node_map
        ]
        members.sort(key=lambda n: self._nx.degree(n.id), reverse=True)
        hub_nodes = tuple(members[:5])

        return CommunityResult(
            community_id=comm,
            members=tuple(members),
            hub_nodes=hub_nodes,
            member_count=len(members),
        )

    @staticmethod
    def diff_graphs(old: CodeGraph, new: CodeGraph) -> GraphDiff:
        """Compute the structural difference between two graphs.

        Args:
            old: The baseline graph.
            new: The updated graph.

        Returns:
            A :class:`GraphDiff` with added/removed nodes and edges.
        """
        old_node_ids = {n.id for n in old.nodes}
        new_node_ids = {n.id for n in new.nodes}
        old_node_map = {n.id: n for n in old.nodes}
        new_node_map = {n.id: n for n in new.nodes}

        added_nodes = tuple(
            new_node_map[nid] for nid in sorted(new_node_ids - old_node_ids)
        )
        removed_nodes = tuple(
            old_node_map[nid] for nid in sorted(old_node_ids - new_node_ids)
        )

        def _edge_key(e: GraphEdge) -> tuple[str, str, str]:
            return (e.source, e.target, e.relation.value)

        old_edge_set = {_edge_key(e) for e in old.edges}
        new_edge_set = {_edge_key(e) for e in new.edges}
        old_edge_map = {_edge_key(e): e for e in old.edges}
        new_edge_map = {_edge_key(e): e for e in new.edges}

        added_edges = tuple(
            new_edge_map[k] for k in sorted(new_edge_set - old_edge_set)
        )
        removed_edges = tuple(
            old_edge_map[k] for k in sorted(old_edge_set - new_edge_set)
        )

        return GraphDiff(
            added_nodes=added_nodes,
            removed_nodes=removed_nodes,
            added_edges=added_edges,
            removed_edges=removed_edges,
        )

    # ------------------------------------------------------------------
    # History / decision-context (Phase 3)
    # ------------------------------------------------------------------

    def get_symbol_history(self, symbol: str) -> HistoryRecord:
        """Return the raw git history for *symbol*.

        Args:
            symbol: Any string accepted by :meth:`_resolve_one`.

        Raises:
            RuntimeError: If the engine was created without a
                :class:`~ast_intel.history.HistoryBuilder`.
            ValueError: If the resolved node lacks a file + span.
            KeyError / AmbiguousSymbolError: Per the resolver contract.
        """
        if self._history_builder is None:
            msg = "QueryEngine was constructed without a history_builder."
            raise RuntimeError(msg)
        from ast_intel.history.history_builder import symbol_ref_from_node

        _, node = self._resolve_one(symbol)
        ref = symbol_ref_from_node(node)
        if ref is None:
            msg = (
                f"Symbol {symbol!r} has no file/span and cannot be "
                "looked up in git history."
            )
            raise ValueError(msg)
        return self._history_builder.build(ref)

    def get_ownership(self, symbol: str) -> OwnershipRecord:
        """Return the per-author ownership ranking for *symbol*."""
        from ast_intel.history.ownership_scorer import OwnershipScorer

        record = self.get_symbol_history(symbol)
        return OwnershipScorer().score(
            symbol=record.symbol,
            head_sha=record.head_sha,
            commits=list(record.commits),
            blame=list(record.blame),
        )

    def get_decision_context(self, symbol: str) -> EnrichedHistoryRecord:
        """Return the enriched + signal-bearing decision context."""
        if self._enrichment is None:
            msg = "QueryEngine was constructed without an enrichment pipeline."
            raise RuntimeError(msg)
        record = self.get_symbol_history(symbol)
        return self._enrichment.enrich(record)

    # ------------------------------------------------------------------
    # Phase 4 — rich signals (RFCs + incidents + tribal knowledge)
    # ------------------------------------------------------------------

    def get_rich_context(
        self,
        symbol: str,
        *,
        include_hidden: bool = False,
    ) -> object:
        """Return a fully-fused :class:`AggregatedContext` for *symbol*.

        Includes git history + PR enrichment + RFC links + incident
        records + tribal notes, all confidence-scored with feedback
        adjustments and heuristic conflict detection applied.
        """
        if self._signal_aggregator is None:
            msg = (
                "QueryEngine was constructed without a signal_aggregator. "
                "Phase 4 features are unavailable."
            )
            raise RuntimeError(msg)
        from ast_intel.history.history_builder import symbol_ref_from_node

        node_id, node = self._resolve_one(symbol)
        ref = symbol_ref_from_node(node)
        if ref is None:
            msg = (
                f"Symbol {symbol!r} has no file/span and cannot be "
                "looked up in git history."
            )
            raise ValueError(msg)
        return self._signal_aggregator.aggregate(
            ref,
            symbol_id=node_id,
            symbol_label=node.label,
            include_hidden=include_hidden,
        )

    def list_rfcs(self, symbol: str) -> list:
        """Return RFC links for *symbol* (Phase 4)."""
        ctx = self.get_rich_context(symbol)
        return list(ctx.rfcs)  # type: ignore[attr-defined]

    def list_incidents(self, symbol: str) -> list:
        """Return incident links for *symbol* (Phase 4)."""
        ctx = self.get_rich_context(symbol)
        return list(ctx.incidents)  # type: ignore[attr-defined]

    def list_tribal_knowledge(self, symbol: str) -> list:
        """Return tribal notes for *symbol* (Phase 4)."""
        ctx = self.get_rich_context(symbol)
        return list(ctx.tribal)  # type: ignore[attr-defined]

    def add_tribal_knowledge(
        self,
        symbol: str,
        *,
        author: str,
        text: str,
        tags: tuple[str, ...] = (),
    ) -> object:
        """Persist a developer note about *symbol* (Phase 4)."""
        if self._signal_aggregator is None:
            msg = (
                "QueryEngine was constructed without a signal_aggregator."
            )
            raise RuntimeError(msg)
        node_id, node = self._resolve_one(symbol)
        store = self._signal_aggregator._tribal  # noqa: SLF001 — friend access
        return store.add(
            symbol_id=node_id,
            symbol_label=node.label,
            author=author,
            text=text,
            tags=tags,
        )

    def add_incident(
        self,
        symbol: str,
        *,
        title: str,
        severity: str = "sev3",
        status: str = "investigating",
        url: str = "",
        postmortem_url: str = "",
        fix_commit: str = "",
        fix_pr: str = "",
    ) -> object:
        """Persist an incident record linked to *symbol* (Phase 4)."""
        if self._signal_aggregator is None:
            msg = (
                "QueryEngine was constructed without a signal_aggregator."
            )
            raise RuntimeError(msg)
        node_id, node = self._resolve_one(symbol)
        store = self._signal_aggregator._incidents  # noqa: SLF001
        return store.add(
            title=title,
            symbol_id=node_id,
            symbol_label=node.label,
            severity=severity,
            status=status,
            url=url,
            postmortem_url=postmortem_url,
            fix_commit=fix_commit,
            fix_pr=fix_pr,
        )

    def submit_feedback(
        self,
        signal_id: str,
        verdict: str,
        *,
        author: str,
        comment: str = "",
        replacement_text: str = "",
    ) -> object:
        """Record accept/reject/edit/outdated feedback on a signal."""
        if self._signal_aggregator is None:
            msg = (
                "QueryEngine was constructed without a signal_aggregator."
            )
            raise RuntimeError(msg)
        store = self._signal_aggregator._feedback  # noqa: SLF001
        return store.submit(
            signal_id=signal_id,
            verdict=verdict,
            author=author,
            comment=comment,
            replacement_text=replacement_text,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fuzzy_resolve(self, lower_name: str) -> list[str]:
        """Tier 6 — Levenshtein fuzzy matching against node labels."""
        hits: list[tuple[int, str]] = []
        for n in self._graph.nodes:
            dist = _levenshtein(lower_name, n.label.lower())
            if dist <= _FUZZY_MAX_DISTANCE:
                hits.append((dist, n.id))
        hits.sort(key=lambda t: t[0])
        return [nid for _, nid in hits[:10]]

    def _resolve_one(self, symbol: str) -> tuple[str, GraphNode]:
        """Resolve *symbol* to a single ``(node_id, node)`` pair.

        When ≤ ``_AMBIGUITY_THRESHOLD`` matches exist the highest-degree
        node is returned.  When more matches exist an
        :class:`AmbiguousSymbolError` is raised with the top candidates.

        Raises:
            KeyError: If no match is found.
            AmbiguousSymbolError: If too many matches are found.
        """
        ids = self._resolve(symbol)
        if not ids:
            msg = f"Symbol not found: {symbol!r}"
            raise KeyError(msg)
        if len(ids) > _AMBIGUITY_THRESHOLD:
            ranked = sorted(ids, key=self._nx.degree, reverse=True)
            top = [self._node_map[nid] for nid in ranked[:10]]
            raise AmbiguousSymbolError(symbol, len(ids), top)
        node_id = max(ids, key=self._nx.degree)
        return node_id, self._node_map[node_id]

    def _collect_neighbors(
        self,
        node_id: str,
        node: GraphNode,
        *,
        outgoing: bool,
    ) -> DependencyResult:
        """Group 1-hop neighbors by edge relation.

        Args:
            node_id: The centre node's ID.
            node: The centre node.
            outgoing: ``True`` for forward (successors),
                ``False`` for reverse (predecessors).
        """
        buckets: dict[str, list[GraphNode]] = defaultdict(list)
        for e in self._graph.edges:
            if outgoing and e.source == node_id:
                nbr = self._node_map.get(e.target)
                if nbr is not None:
                    buckets[e.relation.value].append(nbr)
            elif not outgoing and e.target == node_id:
                nbr = self._node_map.get(e.source)
                if nbr is not None:
                    buckets[e.relation.value].append(nbr)

        return DependencyResult(
            node=node,
            calls=tuple(buckets.get("calls", [])),
            imports=tuple(buckets.get("imports", [])),
            uses_methods=tuple(buckets.get("uses_method", [])),
            inherits=tuple(buckets.get("inherits", [])),
            implements=tuple(buckets.get("implements", [])),
            contains=tuple(buckets.get("contains", [])),
            other=tuple(
                n
                for rel, nodes in buckets.items()
                if rel not in {
                    "calls", "imports", "uses_method",
                    "inherits", "implements", "contains",
                }
                for n in nodes
            ),
        )

    def _children_of(self, file_node_id: str) -> list[GraphNode]:
        """Return nodes contained by *file_node_id* (CONTAINS edges)."""
        children: list[GraphNode] = []
        for e in self._graph.edges:
            if e.source == file_node_id and e.relation == EdgeRelation.CONTAINS:
                child = self._node_map.get(e.target)
                if child is not None:
                    children.append(child)
        return children

    @staticmethod
    def _build_digraph(edges: list[GraphEdge], nodes: list[GraphNode]) -> nx.DiGraph:
        """Create a directed networkx graph from CodeGraph edges."""
        g: nx.DiGraph = nx.DiGraph()
        # Add all nodes so isolated ones are present in the graph.
        g.add_nodes_from(n.id for n in nodes)
        for e in edges:
            if g.has_edge(e.source, e.target):
                g[e.source][e.target].setdefault("relations", []).append(
                    e.relation.value,
                )
            else:
                g.add_edge(
                    e.source,
                    e.target,
                    relations=[e.relation.value],
                )
        return g

    @staticmethod
    def _build_label_index(
        nodes: list[GraphNode],
    ) -> dict[str, list[str]]:
        """Map ``label.lower()`` → list of node IDs."""
        idx: dict[str, list[str]] = defaultdict(list)
        for n in nodes:
            idx[n.label.lower()].append(n.id)
        return dict(idx)

    @staticmethod
    def _build_suffix_index(
        nodes: list[GraphNode],
    ) -> dict[str, list[str]]:
        """Map the ``::``-suffix of each node ID (lowered) → node IDs.

        E.g. ``"lib/storage.rs::StorageHelper"`` registers under
        ``"storagehelper"``.
        """
        idx: dict[str, list[str]] = defaultdict(list)
        for n in nodes:
            suffix = n.id.rsplit("::", maxsplit=1)[-1].lower()
            idx[suffix].append(n.id)
        return dict(idx)

    def _resolve(self, name: str) -> list[str]:
        """Resolve *name* to node IDs using a 6-tier strategy.

        Tiers (first non-empty wins):

        1. Exact node ID
        2. Exact label (case-insensitive)
        3. ID-suffix match (``"StorageHelper"`` matches
           ``"lib/.../storage_helper.rs::StorageHelper"``)
        4. Token match — tokenized input is a subset of a node's
           tokenized label (``"storage helper"`` → ``StorageHelper``)
        5. Substring — input appears inside a node label
        6. Levenshtein fuzzy (edit distance ≤ 3, top 10)
        """
        # Tier 1: Exact ID.
        if name in self._node_map:
            return [name]

        lower = name.lower()

        # Tier 2: Exact label (case-insensitive).
        ids = self._label_index.get(lower, [])
        if ids:
            return ids

        # Tier 3: Suffix match on node IDs.
        ids = self._suffix_index.get(lower, [])
        if ids:
            return ids

        # Tier 4: Token-set match.
        query_tokens = _tokenize(name)
        if query_tokens:
            token_hits = [
                n.id
                for n in self._graph.nodes
                if query_tokens <= _tokenize(n.label)
            ]
            if token_hits:
                return token_hits

        # Tier 5: Substring fallback.
        substr_hits = [
            n.id
            for n in self._graph.nodes
            if lower in n.label.lower()
        ]
        if substr_hits:
            return substr_hits

        # Tier 6: Levenshtein fuzzy.
        return self._fuzzy_resolve(lower)

    def _annotate_path(
        self,
        id_path: list[str],
    ) -> list[tuple[GraphNode, EdgeRelation | None]]:
        """Annotate an ID sequence with nodes and edge relations."""
        result: list[tuple[GraphNode, EdgeRelation | None]] = []
        for i, nid in enumerate(id_path):
            node = self._node_map[nid]
            if i + 1 < len(id_path):
                next_id = id_path[i + 1]
                relation = self._edge_relation(nid, next_id)
                result.append((node, relation))
            else:
                result.append((node, None))
        return result

    def _edge_relation(self, src: str, tgt: str) -> EdgeRelation | None:
        """Get the first edge relation between two adjacent nodes."""
        data = self._nx.get_edge_data(src, tgt)
        if data and data.get("relations"):
            return EdgeRelation(data["relations"][0])
        return None

    def _neighbors_by_relation(
        self,
        node_id: str,
        relation: EdgeRelation,
        *,
        incoming: bool,
    ) -> list[str]:
        """Collect neighbor labels filtered by edge relation."""
        labels: list[str] = []
        edges = self._graph.edges
        for e in edges:
            if incoming and e.target == node_id and e.relation == relation:
                src = self._node_map.get(e.source)
                if src:
                    labels.append(src.label)
            elif not incoming and e.source == node_id and e.relation == relation:
                tgt = self._node_map.get(e.target)
                if tgt:
                    labels.append(tgt.label)
        return labels

    def _classify_role(  # noqa: C901
        self,
        node_id: str,
        degree: int,
        in_degree: int,
        out_degree: int,
    ) -> str:
        """Classify a node's structural role."""
        if degree == 0:
            return "isolated"

        # Compute median degree across all graph nodes.
        all_degrees = [
            self._nx.degree(nid) for nid in self._nx.nodes
        ]
        median_deg = statistics.median(all_degrees) if all_degrees else 0

        # Bridge: connects 2+ communities.
        if self._analysis is not None:
            neighbor_communities: set[int] = set()
            for nbr in self._nx.predecessors(node_id):
                if nbr in self._community_map:
                    neighbor_communities.add(self._community_map[nbr])
            for nbr in self._nx.successors(node_id):
                if nbr in self._community_map:
                    neighbor_communities.add(self._community_map[nbr])
            own_comm = self._community_map.get(node_id)
            if own_comm is not None:
                neighbor_communities.discard(own_comm)
            if len(neighbor_communities) >= 2:  # noqa: PLR2004
                return "bridge"

        # Hub: degree > 2× median.
        if median_deg > 0 and degree > 2 * median_deg:
            return "hub"

        # Leaf: one-sided connectivity.
        if in_degree == 0 or out_degree == 0:
            return "leaf"

        return "hub" if degree > 2 * max(median_deg, 1) else "leaf"

    def _build_summary(  # noqa: PLR0913
        self,
        node: GraphNode,
        degree: int,
        in_degree: int,
        out_degree: int,
        callers: list[str],
        callees: list[str],
        community: int | None,
        role: str,
    ) -> str:
        """Build a human-readable paragraph explaining the node."""
        parts: list[str] = []

        # Identification line.
        span_text = ""
        if node.span:
            span_text = f" (L{node.span.start_line}-L{node.span.end_line})"
        parts.append(
            f"{node.label} is a {node.kind.value} in "
            f"{node.file}{span_text}.",
        )

        # Degree summary.
        parts.append(
            f"It has {degree} connections "
            f"({in_degree} incoming, {out_degree} outgoing).",
        )

        # Role + community.
        role_text = {
            "hub": "a hub — a central coordination point",
            "bridge": "a bridge connecting multiple communities",
            "leaf": "a leaf with limited connectivity",
            "isolated": "isolated — disconnected from the graph",
        }.get(role, role)

        comm_text = f" in community {community}" if community is not None else ""
        parts.append(f"It is {role_text}{comm_text}.")

        # Callers / callees.
        if callers:
            top = ", ".join(callers[:5])
            parts.append(f"Called by: {top}.")
        if callees:
            top = ", ".join(callees[:5])
            parts.append(f"Calls: {top}.")

        return " ".join(parts)


# endregion: --- Query Engine
