"""Tests for IaCGraphBuilder — IaCGraph → CodeGraph conversion."""

from __future__ import annotations

from ast_intel.core.iac_graph_builder import IaCGraphBuilder, _iac_node_id
from ast_intel.models.ast_node import Span
from ast_intel.models.graph_model import EdgeRelation, NodeKind
from ast_intel.models.iac_model import IaCEdge, IaCGraph, IaCResource


def _make_resource(
    kind: str = "Deployment",
    name: str = "web",
    file: str = "deploy.yaml",
    **kwargs: object,
) -> IaCResource:
    return IaCResource(kind=kind, name=name, file=file, **kwargs)


class TestIaCGraphBuilder:
    """Tests for IaCGraph → CodeGraph conversion."""

    def test_deployment_becomes_k8s_deployment_node(self) -> None:
        r = _make_resource(kind="Deployment", name="web")
        graph = IaCGraph(file="deploy.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert len(result.nodes) == 1
        assert result.nodes[0].kind == NodeKind.K8S_DEPLOYMENT

    def test_service_becomes_k8s_service_node(self) -> None:
        r = _make_resource(kind="Service", name="web-svc")
        graph = IaCGraph(file="svc.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert result.nodes[0].kind == NodeKind.K8S_SERVICE

    def test_statefulset_maps_to_k8s_deployment(self) -> None:
        r = _make_resource(kind="StatefulSet", name="db")
        graph = IaCGraph(file="db.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert result.nodes[0].kind == NodeKind.K8S_DEPLOYMENT

    def test_unknown_kind_maps_to_generic(self) -> None:
        r = _make_resource(kind="NetworkPolicy", name="deny-all")
        graph = IaCGraph(file="net.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert result.nodes[0].kind == NodeKind.K8S_GENERIC

    def test_cronjob_maps_to_k8s_cronjob(self) -> None:
        r = _make_resource(kind="CronJob", name="cleanup")
        graph = IaCGraph(file="cron.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert result.nodes[0].kind == NodeKind.K8S_CRONJOB

    def test_node_id_format(self) -> None:
        r = _make_resource(kind="Deployment", name="api", file="k8s/api.yaml")
        graph = IaCGraph(file="k8s/api.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        expected = "k8s://k8s/api.yaml::Deployment/api"
        assert result.nodes[0].id == expected

    def test_properties_include_namespace_and_labels(self) -> None:
        r = _make_resource(
            kind="Deployment",
            name="web",
            namespace="prod",
            labels={"app": "web"},
            api_version="apps/v1",
        )
        graph = IaCGraph(file="deploy.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        props = result.nodes[0].properties
        assert props["namespace"] == "prod"
        assert "app=web" in props["labels"]
        assert props["apiVersion"] == "apps/v1"
        assert props["k8s_kind"] == "Deployment"

    def test_routes_to_edge_generated(self) -> None:
        svc = _make_resource(kind="Service", name="web-svc")
        dep = _make_resource(kind="Deployment", name="web")
        edge = IaCEdge(
            source_name="web-svc",
            target_name="web",
            relation="routes_to",
        )
        graph = IaCGraph(
            file="deploy.yaml",
            resources=[svc, dep],
            edges=[edge],
        )
        result = IaCGraphBuilder().build([graph])
        assert len(result.edges) == 1
        assert result.edges[0].relation == EdgeRelation.ROUTES_TO

    def test_duplicate_nodes_deduplicated(self) -> None:
        r = _make_resource(kind="Deployment", name="web")
        g1 = IaCGraph(file="deploy.yaml", resources=[r])
        g2 = IaCGraph(file="deploy.yaml", resources=[r])
        result = IaCGraphBuilder().build([g1, g2])
        assert len(result.nodes) == 1

    def test_unresolved_edge_names_skipped(self) -> None:
        r = _make_resource(kind="Service", name="web-svc")
        edge = IaCEdge(
            source_name="web-svc",
            target_name="nonexistent",
            relation="routes_to",
        )
        graph = IaCGraph(file="deploy.yaml", resources=[r], edges=[edge])
        result = IaCGraphBuilder().build([graph])
        assert len(result.edges) == 0

    def test_empty_input_returns_empty_graph(self) -> None:
        result = IaCGraphBuilder().build([])
        assert len(result.nodes) == 0
        assert len(result.edges) == 0

    def test_configures_edge_generated(self) -> None:
        cm = _make_resource(kind="ConfigMap", name="cfg")
        dep = _make_resource(kind="Deployment", name="web")
        edge = IaCEdge(
            source_name="cfg",
            target_name="web",
            relation="configures",
        )
        graph = IaCGraph(
            file="deploy.yaml",
            resources=[cm, dep],
            edges=[edge],
        )
        result = IaCGraphBuilder().build([graph])
        assert len(result.edges) == 1
        assert result.edges[0].relation == EdgeRelation.CONFIGURES

    def test_uses_secret_edge_generated(self) -> None:
        dep = _make_resource(kind="Deployment", name="web")
        sec = _make_resource(kind="Secret", name="db-pass")
        edge = IaCEdge(
            source_name="web",
            target_name="db-pass",
            relation="uses_secret",
        )
        graph = IaCGraph(
            file="deploy.yaml",
            resources=[dep, sec],
            edges=[edge],
        )
        result = IaCGraphBuilder().build([graph])
        assert len(result.edges) == 1
        assert result.edges[0].relation == EdgeRelation.USES_SECRET

    def test_span_preserved(self) -> None:
        span = Span(start_line=5, start_col=0, end_line=20, end_col=0)
        r = _make_resource(kind="Deployment", name="web", span=span)
        graph = IaCGraph(file="deploy.yaml", resources=[r])
        result = IaCGraphBuilder().build([graph])
        assert result.nodes[0].span == span

    def test_iac_node_id_helper(self) -> None:
        nid = _iac_node_id("deploy.yaml", "Service", "web-svc")
        assert nid == "k8s://deploy.yaml::Service/web-svc"
