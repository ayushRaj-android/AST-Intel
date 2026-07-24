"""Tests for IaC-aware cross-service merge resolver."""

from __future__ import annotations

from ast_intel.core._iac_merge_resolver import (
    _compute_identity_key,
    _is_cross_service,
    _normalize_image_name,
    _normalize_name,
    resolve_cross_service_iac,
)
from ast_intel.models.ast_node import SCORE_EXTRACTED, Confidence
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


def _node(
    node_id: str,
    kind: NodeKind,
    label: str = "",
    service: str = "",
    **props: str,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        label=label or node_id.rsplit("/", maxsplit=1)[-1],
        kind=kind,
        service=service,
        properties=dict(props),
    )


def _edge(
    source: str,
    target: str,
    relation: EdgeRelation,
) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relation=relation,
        confidence=Confidence.EXTRACTED,
        confidence_score=SCORE_EXTRACTED,
    )


def _graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge] | None = None,
) -> CodeGraph:
    return CodeGraph(nodes=nodes, edges=edges or [])


# endregion: --- Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Identity Key Tests
# ---------------------------------------------------------------------------


class TestComputeIdentityKey:
    """Test canonical identity key computation."""

    def test_docker_image(self) -> None:
        node = _node(
            "svc-a::docker://Dockerfile::DockerImage/myapp",
            NodeKind.DOCKER_IMAGE, label="myapp",
        )
        assert _compute_identity_key(node) == "docker-image::myapp"

    def test_docker_image_case_insensitive(self) -> None:
        node = _node(
            "svc-a::docker://Dockerfile::DockerImage/MyApp",
            NodeKind.DOCKER_IMAGE, label="MyApp",
        )
        assert _compute_identity_key(node) == "docker-image::myapp"

    def test_k8s_deployment(self) -> None:
        node = _node(
            "svc-a::k8s://deploy.yaml::Deployment/web",
            NodeKind.K8S_DEPLOYMENT, label="web",
            namespace="production", k8s_kind="Deployment",
        )
        assert _compute_identity_key(node) == "k8s::Deployment::production::web"

    def test_k8s_deployment_default_namespace(self) -> None:
        node = _node(
            "svc-a::k8s://deploy.yaml::Deployment/web",
            NodeKind.K8S_DEPLOYMENT, label="web",
            k8s_kind="Deployment",
        )
        assert _compute_identity_key(node) == "k8s::Deployment::default::web"

    def test_k8s_service(self) -> None:
        node = _node(
            "svc-a::k8s://svc.yaml::Service/api",
            NodeKind.K8S_SERVICE, label="api",
            namespace="default", k8s_kind="Service",
        )
        assert _compute_identity_key(node) == "k8s::Service::default::api"

    def test_helm_chart(self) -> None:
        node = _node(
            "svc-a::helm://Chart.yaml::HelmChart/redis",
            NodeKind.HELM_CHART, label="redis",
        )
        assert _compute_identity_key(node) == "helm-chart::redis"

    def test_non_dedup_kind_returns_none(self) -> None:
        node = _node(
            "svc-a::cicd://pipe.yml::CI_STEP/build",
            NodeKind.CI_STEP, label="build",
        )
        assert _compute_identity_key(node) is None


# endregion: --- Identity Key Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Deduplication Tests
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Test IaC node deduplication across services."""

    def test_same_docker_image_two_services(self) -> None:
        """Same image in two repos → one canonical node after dedup."""
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(
                "svc-a::docker://Dockerfile::DockerImage/myapp",
                NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a",
            ),
            _node(
                "svc-b::docker://k8s.yaml::DockerImage/myapp",
                NodeKind.DOCKER_IMAGE, label="myapp", service="svc-b",
            ),
        ]
        graph = _graph(nodes)
        resolve_cross_service_iac(graph)

        docker_nodes = [
            n for n in graph.nodes if n.kind == NodeKind.DOCKER_IMAGE
        ]
        assert len(docker_nodes) == 1

    def test_canonical_prefers_builder(self) -> None:
        """Node with BUILDS_IMAGE edge wins canonical selection."""
        builder_id = "svc-a::docker://Dockerfile::DockerImage/myapp"
        deployer_id = "svc-b::docker://k8s.yaml::DockerImage/myapp"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(builder_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a"),
            _node(deployer_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-b"),
            _node(
                "svc-a::cicd://pipe.yml::CI_STEP/build",
                NodeKind.CI_STEP, label="build", service="svc-a",
            ),
        ]
        edges = [
            _edge(
                "svc-a::cicd://pipe.yml::CI_STEP/build",
                builder_id,
                EdgeRelation.BUILDS_IMAGE,
            ),
        ]
        graph = _graph(nodes, edges)
        resolve_cross_service_iac(graph)

        # The builder's node should be kept.
        docker_nodes = [
            n for n in graph.nodes if n.kind == NodeKind.DOCKER_IMAGE
        ]
        assert len(docker_nodes) == 1
        assert docker_nodes[0].id == builder_id

    def test_edge_redirect_after_dedup(self) -> None:
        """Edges from duplicate node are redirected to canonical."""
        canonical_id = "svc-a::docker://Dockerfile::DockerImage/myapp"
        dup_id = "svc-b::docker://k8s.yaml::DockerImage/myapp"
        deploy_id = "svc-b::k8s://deploy.yaml::Deployment/web"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(canonical_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a"),
            _node(dup_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-b"),
            _node(deploy_id, NodeKind.K8S_DEPLOYMENT, label="web", service="svc-b"),
        ]
        edges = [
            _edge(deploy_id, dup_id, EdgeRelation.REFERENCES_IMAGE),
        ]
        graph = _graph(nodes, edges)
        resolve_cross_service_iac(graph)

        # Edge should now point to canonical.
        ref_edges = [
            e for e in graph.edges
            if e.relation == EdgeRelation.REFERENCES_IMAGE
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].target == canonical_id

    def test_no_dedup_different_namespaces(self) -> None:
        """Same name but different K8s namespace → no dedup."""
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(
                "svc-a::k8s://a.yaml::Deployment/web",
                NodeKind.K8S_DEPLOYMENT, label="web", service="svc-a",
                namespace="staging", k8s_kind="Deployment",
            ),
            _node(
                "svc-b::k8s://b.yaml::Deployment/web",
                NodeKind.K8S_DEPLOYMENT, label="web", service="svc-b",
                namespace="production", k8s_kind="Deployment",
            ),
        ]
        graph = _graph(nodes)
        resolve_cross_service_iac(graph)

        deploy_nodes = [
            n for n in graph.nodes if n.kind == NodeKind.K8S_DEPLOYMENT
        ]
        assert len(deploy_nodes) == 2

    def test_no_dedup_same_service(self) -> None:
        """Two nodes from same service with same key → no dedup."""
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node(
                "svc-a::docker://Dockerfile::DockerImage/myapp",
                NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a",
            ),
            _node(
                "svc-a::docker://compose.yml::DockerImage/myapp",
                NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a",
            ),
        ]
        graph = _graph(nodes)
        resolve_cross_service_iac(graph)

        docker_nodes = [
            n for n in graph.nodes if n.kind == NodeKind.DOCKER_IMAGE
        ]
        assert len(docker_nodes) == 2

    def test_self_loops_removed_after_dedup(self) -> None:
        """Edges that become self-loops after dedup are removed."""
        node_a = "svc-a::docker://Dockerfile::DockerImage/myapp"
        node_b = "svc-b::docker://k8s.yaml::DockerImage/myapp"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(node_a, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a"),
            _node(node_b, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-b"),
        ]
        # An edge between the two duplicates becomes a self-loop.
        edges = [_edge(node_a, node_b, EdgeRelation.DEPLOYS)]
        graph = _graph(nodes, edges)
        resolve_cross_service_iac(graph)

        assert not any(
            e.source == e.target for e in graph.edges
        )


# endregion: --- Deduplication Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Cross-Service Resolution Tests
# ---------------------------------------------------------------------------


class TestCrossServiceImageResolution:
    """Test cross-service Docker image linking."""

    def test_deployment_references_image_cross_service(self) -> None:
        """K8S_DEPLOYMENT in svc-b referencing image from svc-a."""
        img_id = "svc-a::docker://Dockerfile::DockerImage/myapp"
        deploy_id = "svc-b::k8s://deploy.yaml::Deployment/web"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(img_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a"),
            _node(
                deploy_id, NodeKind.K8S_DEPLOYMENT, label="web",
                service="svc-b", images="myapp:latest",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)

        ref_edges = [
            e for e in edges if e.relation == EdgeRelation.REFERENCES_IMAGE
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0].source == deploy_id
        assert ref_edges[0].target == img_id

    def test_no_edge_same_service(self) -> None:
        """No cross-service edge when image and deployment same service."""
        img_id = "svc-a::docker://Dockerfile::DockerImage/myapp"
        deploy_id = "svc-a::k8s://deploy.yaml::Deployment/web"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node(img_id, NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a"),
            _node(
                deploy_id, NodeKind.K8S_DEPLOYMENT, label="web",
                service="svc-a", images="myapp:latest",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)

        ref_edges = [
            e for e in edges if e.relation == EdgeRelation.REFERENCES_IMAGE
        ]
        assert len(ref_edges) == 0

    def test_registry_stripped_for_matching(self) -> None:
        """Registry prefix is stripped for image matching."""
        img_id = "svc-a::docker://Dockerfile::DockerImage/ghcr.io/org/myapp"
        deploy_id = "svc-b::k8s://deploy.yaml::Deployment/web"
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-b", NodeKind.SERVICE, label="svc-b", service="svc-b"),
            _node(
                img_id, NodeKind.DOCKER_IMAGE,
                label="ghcr.io/org/myapp", service="svc-a",
            ),
            _node(
                deploy_id, NodeKind.K8S_DEPLOYMENT, label="web",
                service="svc-b", images="ghcr.io/org/myapp:v2",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)

        ref_edges = [
            e for e in edges if e.relation == EdgeRelation.REFERENCES_IMAGE
        ]
        assert len(ref_edges) == 1


class TestCrossServiceK8sToRoute:
    """Test K8S_SERVICE → SERVICE app routing."""

    def test_k8s_service_matches_app_service(self) -> None:
        """K8S_SERVICE named same as app SERVICE → ROUTES_TO edge."""
        k8s_id = "svc-a::k8s://svc.yaml::Service/order-service"
        app_id = "order-service"  # SERVICE node (no prefix)
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node(app_id, NodeKind.SERVICE, label="order-service"),
            _node(
                k8s_id, NodeKind.K8S_SERVICE, label="order-service",
                service="svc-a",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)

        route_edges = [
            e for e in edges if e.relation == EdgeRelation.ROUTES_TO
        ]
        assert len(route_edges) == 1
        assert route_edges[0].source == k8s_id
        assert route_edges[0].target == app_id

    def test_prefix_matching(self) -> None:
        """K8S_SERVICE 'api' matches SERVICE 'api-gateway' via prefix."""
        k8s_id = "infra::k8s://svc.yaml::Service/api"
        app_id = "api-gateway"
        nodes = [
            _node("infra", NodeKind.SERVICE, label="infra", service="infra"),
            _node(app_id, NodeKind.SERVICE, label="api-gateway"),
            _node(
                k8s_id, NodeKind.K8S_SERVICE, label="api",
                service="infra",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)

        route_edges = [
            e for e in edges if e.relation == EdgeRelation.ROUTES_TO
        ]
        assert len(route_edges) == 1


# endregion: --- Cross-Service Resolution Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helper Tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test helper functions."""

    def test_normalize_image_name_strips_registry(self) -> None:
        assert _normalize_image_name("ghcr.io/org/myapp:v1") == "org/myapp"

    def test_normalize_image_name_strips_tag(self) -> None:
        assert _normalize_image_name("myapp:latest") == "myapp"

    def test_normalize_image_name_simple(self) -> None:
        assert _normalize_image_name("redis") == "redis"

    def test_normalize_image_name_digest(self) -> None:
        assert _normalize_image_name("myapp@sha256:abc") == "myapp"

    def test_normalize_name(self) -> None:
        assert _normalize_name("Order_Service") == "order-service"

    def test_is_cross_service_different(self) -> None:
        assert _is_cross_service("svc-a::node1", "svc-b::node2")

    def test_is_cross_service_same(self) -> None:
        assert not _is_cross_service("svc-a::node1", "svc-a::node2")

    def test_is_cross_service_unprefixed(self) -> None:
        """SERVICE nodes (no prefix) are always cross-boundary."""
        assert _is_cross_service("svc-a::node1", "order-service")


# endregion: --- Helper Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- No-op Tests
# ---------------------------------------------------------------------------


class TestNoOpCases:
    """Test early-exit and no-op scenarios."""

    def test_no_iac_nodes(self) -> None:
        """Pure code graph → no-op."""
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node("svc-a::file.rs", NodeKind.FILE, label="file.rs", service="svc-a"),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)
        assert edges == []

    def test_single_service_no_cross_edges(self) -> None:
        """IaC nodes from single service → no cross-service edges."""
        nodes = [
            _node("svc-a", NodeKind.SERVICE, label="svc-a", service="svc-a"),
            _node(
                "svc-a::docker://Dockerfile::DockerImage/myapp",
                NodeKind.DOCKER_IMAGE, label="myapp", service="svc-a",
            ),
            _node(
                "svc-a::k8s://deploy.yaml::Deployment/web",
                NodeKind.K8S_DEPLOYMENT, label="web", service="svc-a",
                images="myapp:latest",
            ),
        ]
        graph = _graph(nodes)
        edges = resolve_cross_service_iac(graph)
        assert edges == []


# endregion: --- No-op Tests
# ---------------------------------------------------------------------------
