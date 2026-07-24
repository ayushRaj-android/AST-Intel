"""Semantic similarity — Jaccard-based structural feature comparison.

Computes ``SIMILAR_TO`` edges between graph nodes of the **same kind**
by comparing structural feature sets.  Two symbols are considered
*similar* when their Jaccard index exceeds a configurable threshold.

Feature sets per :class:`~ast_intel.models.graph_model.NodeKind`:

- **STRUCT**: field-target types + method names + trait implementations
- **ENUM**: variant count tag + method names
- **TRAIT**: method names + implementing types
- **FUNCTION / METHOD**: parameter types + return type + callees
- **FILE**: contained symbol names + import targets

Only nodes with ≥ 2 features are compared (insufficient signal below that).
"""

from __future__ import annotations

import logging
from itertools import combinations

from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

__all__: list[str] = [
    "build_feature_sets",
    "compute_similarity_edges",
]

logger = logging.getLogger(__name__)

# Minimum features a node must have to participate in comparison.
_MIN_FEATURES: int = 2


# ---------------------------------------------------------------------------
# region:    --- Feature Extraction
# ---------------------------------------------------------------------------


def build_feature_sets(
    graph: CodeGraph,
) -> dict[str, frozenset[str]]:
    """Extract structural feature sets for every eligible node.

    Features are prefixed strings that encode the kind of feature so
    that e.g. a method called ``new`` and a callee called ``new`` do
    not collide.

    Args:
        graph: A ``CodeGraph`` produced by ``GraphBuilder``.

    Returns:
        Mapping of *node_id* → *frozenset of feature tags*.
        Nodes with fewer than ``_MIN_FEATURES`` features are omitted.
    """
    # Pre-index edges by source and target for O(1) lookups.
    edges_by_source: dict[str, list[GraphEdge]] = {}
    edges_by_target: dict[str, list[GraphEdge]] = {}
    for e in graph.edges:
        edges_by_source.setdefault(e.source, []).append(e)
        edges_by_target.setdefault(e.target, []).append(e)

    node_map: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    result: dict[str, frozenset[str]] = {}

    for node in graph.nodes:
        features: set[str] = set()
        _extract_features(
            node, edges_by_source, edges_by_target, node_map, features,
        )
        if len(features) >= _MIN_FEATURES:
            result[node.id] = frozenset(features)

    return result


def _extract_features(
    node: GraphNode,
    by_src: dict[str, list[GraphEdge]],
    by_tgt: dict[str, list[GraphEdge]],
    node_map: dict[str, GraphNode],
    out: set[str],
) -> None:
    """Populate *out* with feature tags for a single node."""
    kind = node.kind

    if kind == NodeKind.STRUCT:
        _struct_features(node, by_src, by_tgt, node_map, out)

    elif kind == NodeKind.ENUM:
        _enum_features(node, by_src, by_tgt, node_map, out)

    elif kind == NodeKind.TRAIT:
        _trait_features(node, by_tgt, out)

    elif kind in (NodeKind.FUNCTION, NodeKind.METHOD):
        _callable_features(node, by_src, out)

    elif kind == NodeKind.FILE:
        _file_features(node, by_src, out)


def _find_impl_blocks_for(
    type_name: str,
    node_map: dict[str, GraphNode],
) -> list[str]:
    """Find IMPL_BLOCK node IDs whose ``self_type`` matches *type_name*."""
    return [
        n.id for n in node_map.values()
        if n.kind == NodeKind.IMPL_BLOCK
        and n.properties.get("self_type") == type_name
    ]


def _struct_features(
    node: GraphNode,
    by_src: dict[str, list[GraphEdge]],
    by_tgt: dict[str, list[GraphEdge]],
    node_map: dict[str, GraphNode],
    out: set[str],
) -> None:
    """STRUCT: field targets + methods (via impl blocks) + trait impls."""
    for e in by_src.get(node.id, []):
        if e.relation == EdgeRelation.HAS_FIELD:
            tgt_label = e.target.rsplit("::", maxsplit=1)[-1]
            out.add(f"field_type:{tgt_label}")

    # Methods and trait impls live on impl_block nodes whose
    # ``self_type`` property matches this struct's label.
    impl_ids = _find_impl_blocks_for(node.label, node_map)
    for impl_id in impl_ids:
        # Methods point METHOD_OF → impl_block
        for e in by_tgt.get(impl_id, []):
            if e.relation == EdgeRelation.METHOD_OF:
                method_label = e.source.rsplit("::", maxsplit=1)[-1]
                # Method IDs are like "file::Type.method" — extract method name
                if "." in method_label:
                    method_label = method_label.split(".", maxsplit=1)[1]
                out.add(f"method:{method_label}")
        # Impl block → trait (IMPLEMENTS / INHERITS)
        for e in by_src.get(impl_id, []):
            if e.relation in (EdgeRelation.IMPLEMENTS, EdgeRelation.INHERITS):
                tgt_label = e.target.rsplit("::", maxsplit=1)[-1]
                out.add(f"impl:{tgt_label}")


def _enum_features(
    node: GraphNode,
    by_src: dict[str, list[GraphEdge]],
    by_tgt: dict[str, list[GraphEdge]],
    node_map: dict[str, GraphNode],
    out: set[str],
) -> None:
    """ENUM: variant count + methods (via impl blocks)."""
    variants = node.properties.get("variants")
    if variants is not None:
        out.add(f"variant_count:{variants}")
    # Methods live on impl blocks, same as structs.
    impl_ids = _find_impl_blocks_for(node.label, node_map)
    for impl_id in impl_ids:
        for e in by_tgt.get(impl_id, []):
            if e.relation == EdgeRelation.METHOD_OF:
                method_label = e.source.rsplit("::", maxsplit=1)[-1]
                if "." in method_label:
                    method_label = method_label.split(".", maxsplit=1)[1]
                out.add(f"method:{method_label}")


def _trait_features(
    node: GraphNode,
    by_tgt: dict[str, list[GraphEdge]],
    out: set[str],
) -> None:
    """TRAIT: method names + implementing types."""
    for e in by_tgt.get(node.id, []):
        if e.relation == EdgeRelation.METHOD_OF:
            method_label = e.source.rsplit("::", maxsplit=1)[-1]
            out.add(f"method:{method_label}")
        if e.relation in (EdgeRelation.IMPLEMENTS, EdgeRelation.INHERITS):
            impl_label = e.source.rsplit("::", maxsplit=1)[-1]
            out.add(f"implementor:{impl_label}")


def _callable_features(
    node: GraphNode,
    by_src: dict[str, list[GraphEdge]],
    out: set[str],
) -> None:
    """FUNCTION / METHOD: return type + callees."""
    ret = node.properties.get("return_type")
    if ret:
        out.add(f"return:{ret}")
    is_async = node.properties.get("async")
    if is_async:
        out.add("async:true")
    for e in by_src.get(node.id, []):
        if e.relation == EdgeRelation.CALLS:
            callee_label = e.target.rsplit("::", maxsplit=1)[-1]
            out.add(f"calls:{callee_label}")


def _file_features(
    node: GraphNode,
    by_src: dict[str, list[GraphEdge]],
    out: set[str],
) -> None:
    """FILE: contained symbol names + import targets."""
    for e in by_src.get(node.id, []):
        if e.relation == EdgeRelation.CONTAINS:
            sym_label = e.target.rsplit("::", maxsplit=1)[-1]
            out.add(f"contains:{sym_label}")
        if e.relation == EdgeRelation.IMPORTS:
            imp_label = e.target.rsplit("::", maxsplit=1)[-1]
            out.add(f"imports:{imp_label}")


# endregion: --- Feature Extraction


# ---------------------------------------------------------------------------
# region:    --- Jaccard Similarity + Edge Emission
# ---------------------------------------------------------------------------


def compute_similarity_edges(
    graph: CodeGraph,
    *,
    threshold: float = 0.4,
    max_edges_per_node: int = 5,
) -> list[GraphEdge]:
    """Compute ``SIMILAR_TO`` edges between structurally similar nodes.

    Only nodes of the **same** :class:`NodeKind` are compared.
    Edges are emitted as a single directed edge from the
    lexicographically smaller node ID to the larger one.

    Args:
        graph: A ``CodeGraph`` with nodes and edges already populated.
        threshold: Minimum Jaccard similarity to emit an edge (0.0–1.0).
        max_edges_per_node: Maximum SIMILAR_TO edges per node.

    Returns:
        List of ``GraphEdge`` with ``relation=SIMILAR_TO``.
    """
    features = build_feature_sets(graph)
    if not features:
        return []

    node_map: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    candidates = _collect_candidates(features, node_map, threshold)
    result = _apply_per_node_cap(candidates, node_map, max_edges_per_node)

    logger.info(
        "Similarity: %d candidate pairs, %d edges emitted (threshold=%.2f)",
        len(candidates),
        len(result),
        threshold,
    )
    return result


def _collect_candidates(
    features: dict[str, frozenset[str]],
    node_map: dict[str, GraphNode],
    threshold: float,
) -> list[tuple[float, str, str]]:
    """Group nodes by kind and find all pairs above *threshold*."""
    kind_groups: dict[NodeKind, list[str]] = {}
    for nid in features:
        node = node_map.get(nid)
        if node is not None:
            kind_groups.setdefault(node.kind, []).append(nid)

    for ids in kind_groups.values():
        ids.sort()

    candidates: list[tuple[float, str, str]] = []
    for ids in kind_groups.values():
        if len(ids) < 2:  # noqa: PLR2004
            continue
        for a, b in combinations(ids, 2):
            score = _jaccard(features[a], features[b])
            if score >= threshold:
                candidates.append((score, a, b))

    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    return candidates


def _apply_per_node_cap(
    candidates: list[tuple[float, str, str]],
    node_map: dict[str, GraphNode],
    max_edges_per_node: int,
) -> list[GraphEdge]:
    """Convert candidates to edges, respecting the per-node cap."""
    edge_count: dict[str, int] = {}
    result: list[GraphEdge] = []

    for score, src, tgt in candidates:
        if (
            edge_count.get(src, 0) >= max_edges_per_node
            or edge_count.get(tgt, 0) >= max_edges_per_node
        ):
            continue
        src_node = node_map.get(src)
        result.append(
            GraphEdge(
                source=src,
                target=tgt,
                relation=EdgeRelation.SIMILAR_TO,
                confidence=Confidence.INFERRED,
                confidence_score=round(score, 4),
                file=src_node.file if src_node else "",
            ),
        )
        edge_count[src] = edge_count.get(src, 0) + 1
        edge_count[tgt] = edge_count.get(tgt, 0) + 1

    return result


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two frozen sets."""
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# endregion: --- Jaccard Similarity + Edge Emission
