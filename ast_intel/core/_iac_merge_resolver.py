"""IaC-aware cross-service resolution for merged graphs.

After ``merge_graphs()`` namespaces node IDs and creates SERVICE nodes,
this module performs two additional passes:

1. **Deduplication** — detect the same infrastructure artifact (Docker
   image, K8s deployment, Helm chart) appearing in multiple repos and
   merge them into a single canonical node.

2. **Cross-service IaC edges** — discover relationships across service
   boundaries (K8S_SERVICE port → ROUTE in another service, Docker image
   built by one repo and deployed by another).

Called from ``merge_graphs()`` when ``resolve=True``.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

__all__: list[str] = ["resolve_cross_service_iac"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

_SEPARATOR: str = "::"

# Node kinds eligible for deduplication across services.
_DEDUP_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.DOCKER_IMAGE,
    NodeKind.K8S_DEPLOYMENT,
    NodeKind.K8S_SERVICE,
    NodeKind.K8S_CONFIGMAP,
    NodeKind.K8S_SECRET,
    NodeKind.HELM_CHART,
})

# Minimum confidence to emit an edge.
_MIN_CONFIDENCE: float = 0.6

# endregion: --- Constants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def resolve_cross_service_iac(graph: CodeGraph) -> list[GraphEdge]:
    """Run IaC deduplication and cross-service resolution on a merged graph.

    Args:
        graph: The merged CodeGraph (node IDs already namespaced).

    Returns:
        New edges to add to the merged graph.
    """
    # Early exit: if no IaC nodes exist, skip entirely.
    iac_nodes = [
        n for n in graph.nodes if n.kind in _DEDUP_KINDS
    ]
    if not iac_nodes:
        return []

    edges: list[GraphEdge] = []

    # Phase 1: Deduplicate shared artifacts.
    dedup_map = _deduplicate_iac_nodes(graph)
    if dedup_map:
        _apply_dedup(graph, dedup_map)
        logger.info(
            "IaC dedup: merged %d duplicate node(s)",
            len(dedup_map),
        )

    # Phase 2: Cross-service IaC edge resolution.
    edges.extend(_resolve_image_cross_service(graph))
    edges.extend(_resolve_k8s_service_to_route(graph))

    if edges:
        logger.info("IaC merge resolver: %d cross-service edge(s)", len(edges))

    return edges


# endregion: --- Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Deduplication
# ---------------------------------------------------------------------------


def _compute_identity_key(node: GraphNode) -> str | None:
    """Extract a canonical identity key for an IaC node.

    Returns None if the node is not dedup-eligible or lacks enough info.
    The key is independent of which repo produced the node.
    """
    # Strip the service:: prefix from the label/properties to get raw identity.
    label = node.label.lower().strip()

    if node.kind == NodeKind.DOCKER_IMAGE:
        # Identity: normalized image reference (no tag variation dedup).
        return f"docker-image::{label}"

    if node.kind in (NodeKind.K8S_DEPLOYMENT, NodeKind.K8S_SERVICE,
                     NodeKind.K8S_CONFIGMAP, NodeKind.K8S_SECRET):
        ns = node.properties.get("namespace", "default")
        k8s_kind = node.properties.get("k8s_kind", "unknown")
        return f"k8s::{k8s_kind}::{ns}::{label}"

    if node.kind == NodeKind.HELM_CHART:
        return f"helm-chart::{label}"

    return None


def _deduplicate_iac_nodes(
    graph: CodeGraph,
) -> dict[str, str]:
    """Find duplicate IaC nodes across services and build redirect map.

    Returns:
        A mapping of ``{duplicate_node_id: canonical_node_id}`` for nodes
        that should be merged.
    """
    # Group IaC nodes by identity key.
    identity_groups: dict[str, list[GraphNode]] = defaultdict(list)
    for node in graph.nodes:
        if node.kind not in _DEDUP_KINDS:
            continue
        # Must be from different services to be a cross-service dup.
        key = _compute_identity_key(node)
        if key:
            identity_groups[key].append(node)

    # Only process groups with nodes from multiple services.
    dedup_map: dict[str, str] = {}
    for nodes in identity_groups.values():
        if len(nodes) < 2:  # noqa: PLR2004
            continue
        # Check they come from different services.
        services = {n.service for n in nodes if n.service}
        if len(services) < 2:  # noqa: PLR2004
            continue

        # Select canonical: prefer node with more properties (richer def).
        canonical = _select_canonical(nodes, graph)
        for node in nodes:
            if node.id != canonical.id:
                dedup_map[node.id] = canonical.id

    return dedup_map


def _select_canonical(
    nodes: list[GraphNode],
    graph: CodeGraph,
) -> GraphNode:
    """Pick the canonical node from a group of duplicates.

    Heuristics (in order):
    1. Node that has an incoming BUILDS_IMAGE edge (it's the source of truth).
    2. Node with the most non-empty properties.
    3. Alphabetically first ID (deterministic tie-break).
    """
    # Check for nodes that have incoming BUILDS_IMAGE edges (the "builder").
    target_ids = {n.id for n in nodes}
    builder_targets: set[str] = set()
    for edge in graph.edges:
        if (
            edge.relation == EdgeRelation.BUILDS_IMAGE
            and edge.target in target_ids
        ):
            builder_targets.add(edge.target)

    if builder_targets:
        # Prefer the node that is a build target.
        for node in nodes:
            if node.id in builder_targets:
                return node

    # Fallback: most properties → alphabetically first.
    return sorted(
        nodes,
        key=lambda n: (-len(n.properties), n.id),
    )[0]


def _apply_dedup(
    graph: CodeGraph,
    dedup_map: dict[str, str],
) -> None:
    """Mutate graph: remove duplicate nodes and redirect edges.

    All edges pointing to/from a duplicate node are repointed to the
    canonical node. Duplicate nodes are removed from the graph.
    """
    # Remove duplicate nodes.
    graph.nodes[:] = [n for n in graph.nodes if n.id not in dedup_map]

    # Redirect edges (GraphEdge is frozen, so rebuild).
    new_edges: list[GraphEdge] = []
    for edge in graph.edges:
        src = dedup_map.get(edge.source, edge.source)
        tgt = dedup_map.get(edge.target, edge.target)
        # Skip self-loops created by dedup.
        if src == tgt:
            continue
        if src != edge.source or tgt != edge.target:
            new_edges.append(
                GraphEdge(
                    source=src,
                    target=tgt,
                    relation=edge.relation,
                    confidence=edge.confidence,
                    confidence_score=edge.confidence_score,
                    file=edge.file,
                    span=edge.span,
                ),
            )
        else:
            new_edges.append(edge)
    graph.edges[:] = new_edges


# endregion: --- Deduplication
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Cross-Service Resolution
# ---------------------------------------------------------------------------


def _resolve_image_cross_service(graph: CodeGraph) -> list[GraphEdge]:
    """Link Docker images built by one service to deployments in another.

    If Service A's CI builds image X and Service B's K8S_DEPLOYMENT
    references image X, emit a BUILDS_IMAGE edge from A's CI_STEP
    to the canonical DOCKER_IMAGE node (which should already exist
    after dedup). Also emit DEPLOYS from B's deployment to the image.

    After dedup, these edges should already exist naturally (since both
    repos' edges point to the same canonical node). This pass catches
    cases where there was no explicit DOCKER_IMAGE node — just an image
    name string in the deployment's properties.
    """
    edges: list[GraphEdge] = []

    # Index: image name → DOCKER_IMAGE node IDs.
    image_nodes: dict[str, str] = {}
    for node in graph.nodes:
        if node.kind == NodeKind.DOCKER_IMAGE:
            img_name = _normalize_image_name(node.label)
            image_nodes[img_name] = node.id

    if not image_nodes:
        return edges

    # Find K8S_DEPLOYMENT nodes that reference images by name in properties.
    for node in graph.nodes:
        if node.kind != NodeKind.K8S_DEPLOYMENT:
            continue
        images_prop = node.properties.get("images", "")
        if not images_prop:
            continue
        # images property is comma-separated list of image refs.
        for img_ref in images_prop.split(","):
            img_name = _normalize_image_name(img_ref.strip())
            target_id = image_nodes.get(img_name)
            if (
                target_id
                and _is_cross_service(node.id, target_id)
                and not _edge_exists(
                    graph, node.id, target_id, EdgeRelation.REFERENCES_IMAGE,
                )
            ):
                edges.append(
                    _make_edge(
                        node.id, target_id,
                        EdgeRelation.REFERENCES_IMAGE, 0.85,
                    ),
                )

    return edges


def _find_matching_k8s_svc(
    app_name: str,
    k8s_svc_by_name: dict[str, GraphNode],
) -> GraphNode | None:
    """Find a K8S_SERVICE matching the app name (exact or prefix)."""
    matched = k8s_svc_by_name.get(app_name)
    if matched:
        return matched
    for k8s_name, k8s_node in k8s_svc_by_name.items():
        if k8s_name.startswith(app_name) or app_name.startswith(k8s_name):
            return k8s_node
    return None


def _resolve_k8s_service_to_route(graph: CodeGraph) -> list[GraphEdge]:
    """Link K8S_SERVICE port to code ROUTE nodes in other services.

    If Service A has a K8S_SERVICE exposing port 8080 targeting
    deployment X, and Service B has ROUTE nodes on port 8080, emit
    a ROUTES_TO edge connecting the infrastructure to the code.
    """
    edges: list[GraphEdge] = []

    # Index: (port, service_name_normalized) → K8S_SERVICE node ID.
    # We use service label name for matching since K8s services
    # are named after the app they front.
    k8s_svc_by_name: dict[str, GraphNode] = {}
    for node in graph.nodes:
        if node.kind == NodeKind.K8S_SERVICE:
            svc_name = _normalize_name(node.label)
            k8s_svc_by_name[svc_name] = node

    if not k8s_svc_by_name:
        return edges

    # Find SERVICE nodes (application services) and try to match.
    for node in graph.nodes:
        if node.kind != NodeKind.SERVICE:
            continue
        app_name = _normalize_name(node.label)
        matched_k8s = _find_matching_k8s_svc(app_name, k8s_svc_by_name)

        if (
            matched_k8s
            and _is_cross_service(matched_k8s.id, node.id)
            and not _edge_exists(
                graph, matched_k8s.id, node.id, EdgeRelation.ROUTES_TO,
            )
        ):
            edges.append(
                _make_edge(
                    matched_k8s.id, node.id,
                    EdgeRelation.ROUTES_TO, 0.8,
                ),
            )

    return edges


# endregion: --- Cross-Service Resolution
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _normalize_image_name(image: str) -> str:
    """Normalize Docker image name for comparison.

    Strips registry prefix and tag, keeps only the image basename.
    """
    # Strip tag/digest.
    name = image.split("@", maxsplit=1)[0]  # strip digest
    name = name.rsplit(":", maxsplit=1)[0]  # strip tag
    # Strip registry (contains '.' or ':').
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]):
        parts = parts[1:]
    return "/".join(parts).lower()


def _normalize_name(name: str) -> str:
    """Normalize a resource/service name for matching."""
    return name.lower().replace("_", "-").strip()


def _is_cross_service(id_a: str, id_b: str) -> bool:
    """Check if two namespaced node IDs belong to different services."""
    svc_a = id_a.split(_SEPARATOR, maxsplit=1)[0] if _SEPARATOR in id_a else ""
    svc_b = id_b.split(_SEPARATOR, maxsplit=1)[0] if _SEPARATOR in id_b else ""
    # SERVICE nodes have no :: prefix (their ID is just the name).
    if not svc_a or not svc_b:
        return True  # One is a SERVICE node — always cross-boundary.
    return svc_a != svc_b


def _edge_exists(
    graph: CodeGraph,
    source: str,
    target: str,
    relation: EdgeRelation,
) -> bool:
    """Check if an edge already exists in the graph."""
    return any(
        e.source == source and e.target == target and e.relation == relation
        for e in graph.edges
    )


def _make_edge(
    source: str,
    target: str,
    relation: EdgeRelation,
    confidence_score: float,
) -> GraphEdge:
    """Create a cross-service IaC edge."""
    return GraphEdge(
        source=source,
        target=target,
        relation=relation,
        confidence=Confidence.INFERRED,
        confidence_score=confidence_score,
    )


# endregion: --- Helpers
# ---------------------------------------------------------------------------
