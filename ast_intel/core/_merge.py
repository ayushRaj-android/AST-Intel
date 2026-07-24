"""Graph merge engine — combine multiple CodeGraphs into one.

Implements Feature 15 from the Microservices roadmap: namespaced IDs,
SERVICE nodes, and BELONGS_TO edges for multi-repo stitching.

Usage::

    merged = merge_graphs([
        ("user-service", user_graph),
        ("order-service", order_graph),
    ])
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ast_intel import SCHEMA_VERSION, TOOL_VERSION
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

if TYPE_CHECKING:
    from ast_intel.models.ast_node import Confidence
    from ast_intel.models.workspace_model import WorkspaceMeta

logger = logging.getLogger(__name__)

_SEPARATOR: str = "::"


def _infer_service_name(graph: CodeGraph) -> str:
    """Best-effort service name from graph metadata.

    Falls back to ``"unknown"`` if no useful metadata exists.
    """
    if graph.meta and graph.meta.workspace_root:
        # Use the last path component of the workspace root
        root = graph.meta.workspace_root.rstrip("/").rstrip("\\")
        name = root.rsplit("/", maxsplit=1)[-1]
        name = name.rsplit("\\", maxsplit=1)[-1]
        if name:
            return name
    return "unknown"


def _prefix_id(service: str, original_id: str) -> str:
    """Namespace a node ID with the service prefix."""
    return f"{service}{_SEPARATOR}{original_id}"


def _remap_node(node: GraphNode, service: str) -> GraphNode:
    """Create a copy of *node* with a prefixed ID and service metadata."""
    return GraphNode(
        id=_prefix_id(service, node.id),
        label=node.label,
        kind=node.kind,
        file=node.file,
        span=node.span,
        properties=dict(node.properties),
        service=service,
        origin_id=node.id,
    )


def _remap_edge(edge: GraphEdge, service: str) -> GraphEdge:
    """Create a copy of *edge* with prefixed source and target IDs."""
    return GraphEdge(
        source=_prefix_id(service, edge.source),
        target=_prefix_id(service, edge.target),
        relation=edge.relation,
        confidence=edge.confidence,
        confidence_score=edge.confidence_score,
        file=edge.file,
        span=edge.span,
    )


def _make_service_node(service: str) -> GraphNode:
    """Create the top-level SERVICE node for a graph."""
    return GraphNode(
        id=service,
        label=service,
        kind=NodeKind.SERVICE,
        service=service,
    )


def _merge_meta(
    graphs: list[tuple[str, CodeGraph]],
) -> WorkspaceMeta:
    """Combine metadata from all input graphs."""
    from ast_intel.models.workspace_model import WorkspaceMeta

    total_crates = 0
    total_files = 0
    total_structs = 0
    total_enums = 0
    total_traits = 0
    total_functions = 0
    total_impl_blocks = 0
    total_self_methods = 0
    total_pkg_method_call_sites = 0
    total_call_edges = 0
    total_rationale_comments = 0
    total_errors = 0
    roots: list[str] = []

    for _name, g in graphs:
        if g.meta is None:
            continue
        m = g.meta
        total_crates += m.total_crates
        total_files += m.total_files
        total_structs += m.total_structs
        total_enums += m.total_enums
        total_traits += m.total_traits
        total_functions += m.total_functions
        total_impl_blocks += m.total_impl_blocks
        total_self_methods += m.total_self_methods
        total_pkg_method_call_sites += m.total_pkg_method_call_sites
        total_call_edges += m.total_call_edges
        total_rationale_comments += m.total_rationale_comments
        total_errors += m.total_errors
        if m.workspace_root:
            roots.append(m.workspace_root)

    return WorkspaceMeta(
        schema_version=SCHEMA_VERSION,
        tool_version=TOOL_VERSION,
        generated_at=datetime.now(tz=UTC).isoformat(),
        workspace_root=", ".join(roots) if roots else "",
        total_crates=total_crates,
        total_files=total_files,
        total_structs=total_structs,
        total_enums=total_enums,
        total_traits=total_traits,
        total_functions=total_functions,
        total_impl_blocks=total_impl_blocks,
        total_self_methods=total_self_methods,
        total_pkg_method_call_sites=total_pkg_method_call_sites,
        total_call_edges=total_call_edges,
        total_rationale_comments=total_rationale_comments,
        total_errors=total_errors,
    )


def merge_graphs(
    graphs: list[tuple[str, CodeGraph]],
    *,
    resolve: bool = True,
) -> CodeGraph:
    """Merge multiple :class:`CodeGraph` instances into one.

    Each graph's node IDs are prefixed with ``"{service}::"`` to avoid
    collisions.  A ``SERVICE`` node is created for each input graph, and
    ``BELONGS_TO`` edges connect every ``FILE`` node to its service.

    When *resolve* is ``True`` (the default), cross-service
    ``CALLS_SERVICE`` edges are resolved by matching HTTP_CALL nodes
    against ROUTE nodes across services.

    Args:
        graphs: Pairs of ``(service_name, graph)``.
        resolve: Run cross-service edge resolution (default ``True``).

    Returns:
        A single merged :class:`CodeGraph`.

    Raises:
        ValueError: If *graphs* is empty or contains duplicate service names.
    """
    if not graphs:
        msg = "merge_graphs() requires at least one graph"
        raise ValueError(msg)

    seen_names: set[str] = set()
    for name, _ in graphs:
        if name in seen_names:
            msg = f"Duplicate service name: {name!r}"
            raise ValueError(msg)
        seen_names.add(name)

    all_nodes: list[GraphNode] = []
    all_edges: list[GraphEdge] = []

    for service, graph in graphs:
        logger.info(
            "Merging %r: %d nodes, %d edges",
            service,
            len(graph.nodes),
            len(graph.edges),
        )

        # Create SERVICE node
        service_node = _make_service_node(service)
        all_nodes.append(service_node)

        # Remap and collect nodes
        for node in graph.nodes:
            remapped = _remap_node(node, service)
            all_nodes.append(remapped)

            # FILE nodes get a BELONGS_TO edge to their service
            if node.kind == NodeKind.FILE:
                all_edges.append(
                    GraphEdge(
                        source=remapped.id,
                        target=service,
                        relation=EdgeRelation.BELONGS_TO,
                        confidence=_extracted_confidence(),
                        confidence_score=1.0,
                        file=node.file,
                    ),
                )

        # Remap and collect edges
        all_edges.extend(_remap_edge(e, service) for e in graph.edges)

    meta = _merge_meta(graphs)

    # Cross-service edge resolution
    merged = CodeGraph(nodes=all_nodes, edges=all_edges, meta=meta)
    if resolve:
        from ast_intel.core._iac_merge_resolver import resolve_cross_service_iac
        from ast_intel.core._url_matcher import resolve_cross_service_edges

        cs_edges = resolve_cross_service_edges(merged)
        if cs_edges:
            all_edges.extend(cs_edges)
            merged = CodeGraph(nodes=all_nodes, edges=all_edges, meta=meta)
            logger.info(
                "Added %d CALLS_SERVICE edges",
                len(cs_edges),
            )

        # IaC-aware cross-service resolution + dedup
        iac_edges = resolve_cross_service_iac(merged)
        if iac_edges:
            all_edges.extend(iac_edges)
            merged = CodeGraph(nodes=all_nodes, edges=all_edges, meta=meta)

    logger.info(
        "Merged result: %d nodes, %d edges from %d services",
        len(all_nodes),
        len(all_edges),
        len(graphs),
    )

    return merged


def _extracted_confidence() -> Confidence:
    """Return ``Confidence.EXTRACTED`` without a top-level import."""
    from ast_intel.models.ast_node import Confidence

    return Confidence.EXTRACTED
