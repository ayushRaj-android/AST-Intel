"""Hyperedge detection — find group relationships in the code graph.

Runs as a post-processing pass after the graph is fully built.
Detects patterns like trait implementor groups, route groups,
linear call flows, and persists Leiden communities.

Public API
----------
- ``detect_hyperedges(graph, ...)`` — main entry point
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    HyperEdge,
    HyperRelation,
    NodeKind,
)

__all__: list[str] = ["detect_hyperedges"]

logger = logging.getLogger(__name__)

_MIN_GROUP_SIZE: int = 3
_MAX_LABEL_LEN: int = 120


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def detect_hyperedges(
    graph: CodeGraph,
    *,
    community_map: dict[str, int] | None = None,
    min_group_size: int = _MIN_GROUP_SIZE,
) -> list[HyperEdge]:
    """Detect all hyperedge group patterns in the graph.

    Args:
        graph: Fully built CodeGraph.
        community_map: Optional mapping node_id → community_id.
        min_group_size: Minimum members for a group (default 3).

    Returns:
        Sorted list of detected HyperEdge objects.
    """
    results: list[HyperEdge] = []

    results.extend(_detect_implements_groups(graph, min_group_size))
    results.extend(_detect_route_groups(graph, min_group_size))
    results.extend(_detect_flows(graph, min_group_size))
    results.extend(_detect_field_groups(graph, min_group_size))

    if community_map:
        results.extend(_persist_communities(community_map, min_group_size))

    results.sort(key=lambda h: (h.relation.value, h.id))
    return results


# endregion: --- Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Implements Groups
# ---------------------------------------------------------------------------


def _detect_implements_groups(
    graph: CodeGraph,
    min_size: int,
) -> list[HyperEdge]:
    """Group all types implementing the same trait/interface."""
    # Build map: trait_id → [implementor_ids]
    impl_targets: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.relation in (EdgeRelation.IMPLEMENTS, EdgeRelation.INHERITS):
            impl_targets[edge.target].append(edge.source)

    # Build node label lookup
    labels = {n.id: n.label for n in graph.nodes}

    results: list[HyperEdge] = []
    for trait_id, implementors in impl_targets.items():
        if len(implementors) < min_size:
            continue
        trait_label = labels.get(trait_id, trait_id)
        members = sorted(implementors)
        results.append(
            HyperEdge(
                id=f"he::implements_group::{_sanitize(trait_label)}",
                relation=HyperRelation.IMPLEMENTS_GROUP,
                members=[trait_id, *members],
                label=f"Implementors of {trait_label}",
                metadata={"trait_id": trait_id},
            ),
        )

    return results


# endregion: --- Implements Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Route Groups
# ---------------------------------------------------------------------------


def _detect_route_groups(
    graph: CodeGraph,
    min_size: int,
) -> list[HyperEdge]:
    """Group routes by shared URL prefix."""
    # Collect route nodes with their paths.
    routes: list[tuple[str, str]] = []  # (node_id, path)
    for node in graph.nodes:
        if node.kind != NodeKind.ROUTE:
            continue
        path = node.properties.get("path", "") or node.label
        if path:
            routes.append((node.id, path))

    if len(routes) < min_size:
        return []

    # Group by 2-segment prefix (e.g., "/api/v1").
    prefix_groups: dict[str, list[str]] = defaultdict(list)
    for node_id, path in routes:
        prefix = _extract_prefix(path)
        if prefix:
            prefix_groups[prefix].append(node_id)

    results: list[HyperEdge] = []
    for prefix, members in prefix_groups.items():
        if len(members) < min_size:
            continue
        sorted_members = sorted(members)
        results.append(
            HyperEdge(
                id=f"he::route_group::{_sanitize(prefix)}",
                relation=HyperRelation.ROUTE_GROUP,
                members=sorted_members,
                label=f"Routes under {prefix}",
                metadata={"prefix": prefix, "count": str(len(members))},
            ),
        )

    return results


def _extract_prefix(path: str) -> str:
    """Extract first 2 meaningful path segments as prefix."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:  # noqa: PLR2004
        return "/" + "/".join(parts[:2])
    if parts:
        return "/" + parts[0]
    return ""


# endregion: --- Route Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Flow Detection
# ---------------------------------------------------------------------------


def _detect_flows(  # noqa: C901
    graph: CodeGraph,
    min_size: int,
) -> list[HyperEdge]:
    """Detect linear call chains (A→B→C→D with no branching)."""
    # Build adjacency from CALLS edges only.
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = defaultdict(int)

    for edge in graph.edges:
        if edge.relation == EdgeRelation.CALLS:
            out_edges[edge.source].append(edge.target)
            in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    # Find chain starts: nodes with CALLS out-edges but either
    # in_degree==0 or in_degree>1 (they start a new chain).
    chain_starts: set[str] = set()
    for node_id in out_edges:
        if in_degree.get(node_id, 0) != 1:
            chain_starts.add(node_id)

    # Follow chains greedily.
    chains: list[list[str]] = []
    visited: set[str] = set()

    for start in chain_starts:
        if start in visited:
            continue
        chain = [start]
        current = start
        while True:
            targets = out_edges.get(current, [])
            # Linear: exactly 1 outgoing CALLS edge.
            if len(targets) != 1:
                break
            nxt = targets[0]
            # Target must have exactly 1 incoming CALLS edge.
            if in_degree.get(nxt, 0) != 1:
                break
            if nxt in visited:
                break
            chain.append(nxt)
            current = nxt
        if len(chain) >= min_size:
            chains.append(chain)
            visited.update(chain)

    # Build node label lookup.
    labels = {n.id: n.label for n in graph.nodes}

    results: list[HyperEdge] = []
    for chain in chains:
        first_label = labels.get(chain[0], chain[0])
        last_label = labels.get(chain[-1], chain[-1])
        results.append(
            HyperEdge(
                id=f"he::flow::{_sanitize(first_label)}..{_sanitize(last_label)}",
                relation=HyperRelation.FLOW,
                members=chain,  # preserves call order
                label=f"Flow: {first_label} → {last_label}",
                metadata={"length": str(len(chain))},
            ),
        )

    return results


# endregion: --- Flow Detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Field Groups
# ---------------------------------------------------------------------------


def _detect_field_groups(
    graph: CodeGraph,
    min_size: int,
) -> list[HyperEdge]:
    """Group structs that share field types (HAS_FIELD targets)."""
    # Map: field_type_node_id → [struct_ids that have a field of that type]
    # We look at HAS_FIELD edges where target is a type node.
    field_targets: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.relation == EdgeRelation.HAS_FIELD:
            field_targets[edge.target].append(edge.source)

    labels = {n.id: n.label for n in graph.nodes}

    results: list[HyperEdge] = []
    for type_id, structs in field_targets.items():
        unique_structs = sorted(set(structs))
        if len(unique_structs) < min_size:
            continue
        type_label = labels.get(type_id, type_id)
        results.append(
            HyperEdge(
                id=f"he::field_group::{_sanitize(type_label)}",
                relation=HyperRelation.FIELD_GROUP,
                members=unique_structs,
                label=f"Structs using {type_label}",
                metadata={"field_type_id": type_id},
            ),
        )

    return results


# endregion: --- Field Groups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Community Persistence
# ---------------------------------------------------------------------------


def _persist_communities(
    community_map: dict[str, int],
    min_size: int,
) -> list[HyperEdge]:
    """Persist Leiden communities as COMMUNITY hyperedges."""
    # Invert map: community_id → [node_ids]
    communities: dict[int, list[str]] = defaultdict(list)
    for node_id, comm_id in community_map.items():
        communities[comm_id].append(node_id)

    results: list[HyperEdge] = []
    for comm_id, members in sorted(communities.items()):
        if len(members) < min_size:
            continue
        sorted_members = sorted(members)
        results.append(
            HyperEdge(
                id=f"he::community::{comm_id}",
                relation=HyperRelation.COMMUNITY,
                members=sorted_members,
                label=f"Community {comm_id} ({len(members)} members)",
                metadata={
                    "community_id": str(comm_id),
                    "node_count": str(len(members)),
                },
            ),
        )

    return results


# endregion: --- Community Persistence
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_\-./]")


def _sanitize(text: str) -> str:
    """Sanitize text for use in hyperedge IDs."""
    result = _SANITIZE_RE.sub("_", text)
    return result[:_MAX_LABEL_LEN]


# endregion: --- Helpers
