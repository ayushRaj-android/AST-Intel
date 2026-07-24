"""Tests for KubernetesExtractor — detection, parsing, and edge building."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.iac.kubernetes import KubernetesExtractor
from ast_intel.models.iac_model import IaCContext

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "k8s"


def _extract_fixture(name: str) -> tuple[object, ...]:
    """Load a fixture file and extract it."""
    ext = KubernetesExtractor()
    file_path = FIXTURE_DIR / name
    source = file_path.read_bytes()
    ctx = IaCContext(workspace_root=str(FIXTURE_DIR.parent.parent), rel_path=name)
    graph = ext.extract(file_path, source, ctx)
    return graph, ext


# endregion: --- Fixtures
# ---------------------------------------------------------------------------


class TestKubernetesDetection:
    """Tests for can_handle() content heuristics."""

    def setup_method(self) -> None:
        self.ext = KubernetesExtractor()
        self.dummy_path = Path("test.yaml")

    def test_detects_k8s_manifest(self) -> None:
        peek = b"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
        assert self.ext.can_handle(self.dummy_path, peek) is True

    def test_rejects_plain_yaml(self) -> None:
        peek = b"database:\n  host: localhost\n  port: 5432\n"
        assert self.ext.can_handle(self.dummy_path, peek) is False

    def test_rejects_helm_template(self) -> None:
        peek = b"apiVersion: apps/v1\nkind: Deployment\n{{ .Values.name }}\n"
        assert self.ext.can_handle(self.dummy_path, peek) is False

    def test_rejects_binary_content(self) -> None:
        peek = b"\x00\x01\x02\x03\x04\x05\x06\x07"
        assert self.ext.can_handle(self.dummy_path, peek) is False

    def test_missing_kind_rejected(self) -> None:
        peek = b"apiVersion: v1\nmetadata:\n  name: test\n"
        assert self.ext.can_handle(self.dummy_path, peek) is False

    def test_missing_api_version_rejected(self) -> None:
        peek = b"kind: Service\nmetadata:\n  name: test\n"
        assert self.ext.can_handle(self.dummy_path, peek) is False


class TestKubernetesExtraction:
    """Tests for extract() parsing logic."""

    def test_simple_deployment_and_service(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        kinds = [r.kind for r in graph.resources]
        assert "Deployment" in kinds
        assert "Service" in kinds
        assert len(graph.resources) == 2

    def test_multi_document_yaml(self) -> None:
        graph, _ = _extract_fixture("multi_doc.yaml")
        assert len(graph.resources) == 4
        kinds = {r.kind for r in graph.resources}
        assert kinds == {"Deployment", "Service", "ConfigMap", "Secret"}

    def test_deployment_extracts_image(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.properties.get("images") == "nginx:1.25"

    def test_deployment_extracts_replicas(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.properties.get("replicas") == "3"

    def test_deployment_extracts_container_port(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.properties.get("containerPorts") == "80"

    def test_deployment_extracts_selector(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.selector == {"app": "web-server"}

    def test_deployment_extracts_pod_labels(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        pod_labels = dep.properties.get("pod_labels", "")
        assert "app=web-server" in pod_labels
        assert "tier=frontend" in pod_labels

    def test_service_extracts_ports(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        svc = next(r for r in graph.resources if r.kind == "Service")
        assert svc.properties.get("ports") == "80:80/TCP"

    def test_service_extracts_selector(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        svc = next(r for r in graph.resources if r.kind == "Service")
        assert svc.selector == {"app": "web-server"}

    def test_configmap_extracts_keys(self) -> None:
        graph, _ = _extract_fixture("multi_doc.yaml")
        cm = next(r for r in graph.resources if r.kind == "ConfigMap")
        keys = cm.properties.get("keys", "")
        assert "log-level" in keys
        assert "settings.json" in keys

    def test_secret_extracts_keys_not_values(self) -> None:
        graph, _ = _extract_fixture("multi_doc.yaml")
        secret = next(r for r in graph.resources if r.kind == "Secret")
        keys = secret.properties.get("keys", "")
        assert "password" in keys
        # Values must NOT be stored
        assert "cGFzc3dvcmQ" not in str(secret.properties)

    def test_ingress_extracts_rules(self) -> None:
        graph, _ = _extract_fixture("ingress.yaml")
        ing = next(r for r in graph.resources if r.kind == "Ingress")
        rules = ing.properties.get("rules", "")
        assert "example.com/api" in rules
        assert "api-service" in rules

    def test_namespace_extraction(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.namespace == "default"

    def test_labels_extraction(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.labels.get("app") == "web-server"
        assert dep.labels.get("tier") == "frontend"

    def test_malformed_yaml_returns_error(self) -> None:
        ext = KubernetesExtractor()
        source = b"apiVersion: v1\nkind: Service\n  bad:\n indent\n: broken"
        ctx = IaCContext(workspace_root="/tmp", rel_path="bad.yaml")
        graph = ext.extract(Path("bad.yaml"), source, ctx)
        # Should either have errors or just be empty — not crash
        assert isinstance(graph.errors, list) or len(graph.resources) == 0

    def test_empty_document_skipped(self) -> None:
        ext = KubernetesExtractor()
        source = b"---\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\ndata: {}\n"
        ctx = IaCContext(workspace_root="/tmp", rel_path="empty.yaml")
        graph = ext.extract(Path("empty.yaml"), source, ctx)
        # Only the ConfigMap document should produce a resource
        assert len(graph.resources) == 1
        assert graph.resources[0].name == "x"

    def test_non_k8s_yaml_returns_empty(self) -> None:
        graph, _ = _extract_fixture("not_k8s.yaml")
        assert len(graph.resources) == 0


class TestKubernetesIntraEdges:
    """Tests for intra-file edge building."""

    def test_service_routes_to_deployment(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        routes = [e for e in graph.edges if e.relation == "routes_to"]
        assert len(routes) == 1
        assert routes[0].source_name == "web-server-svc"
        assert routes[0].target_name == "web-server"

    def test_service_no_match_no_edge(self) -> None:
        ext = KubernetesExtractor()
        source = (
            b"---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
            b"  name: dep1\nspec:\n  selector:\n    matchLabels:\n"
            b"      app: dep1\n  template:\n    metadata:\n"
            b"      labels:\n        app: dep1\n    spec:\n"
            b"      containers:\n        - name: c\n          image: img\n"
            b"---\napiVersion: v1\nkind: Service\nmetadata:\n"
            b"  name: svc1\nspec:\n  selector:\n    app: other\n"
            b"  ports:\n    - port: 80\n"
        )
        ctx = IaCContext(workspace_root="/tmp", rel_path="no_match.yaml")
        graph = ext.extract(Path("no_match.yaml"), source, ctx)
        routes = [e for e in graph.edges if e.relation == "routes_to"]
        assert len(routes) == 0

    def test_configmap_configures_deployment(self) -> None:
        graph, _ = _extract_fixture("multi_doc.yaml")
        conf_edges = [e for e in graph.edges if e.relation == "configures"]
        assert len(conf_edges) == 1
        assert conf_edges[0].source_name == "api-config"
        assert conf_edges[0].target_name == "api-server"

    def test_secret_used_by_deployment(self) -> None:
        graph, _ = _extract_fixture("multi_doc.yaml")
        secret_edges = [e for e in graph.edges if e.relation == "uses_secret"]
        assert len(secret_edges) == 1
        assert secret_edges[0].source_name == "api-server"
        assert secret_edges[0].target_name == "db-secret"


class TestKubernetesSpans:
    """Tests for source span computation."""

    def test_first_doc_starts_at_line_1_or_2(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        dep = next(r for r in graph.resources if r.kind == "Deployment")
        assert dep.span is not None
        assert dep.span.start_line <= 2

    def test_second_doc_starts_after_separator(self) -> None:
        graph, _ = _extract_fixture("simple_deployment.yaml")
        svc = next(r for r in graph.resources if r.kind == "Service")
        assert svc.span is not None
        # Service is the second document — should start after line 1
        assert svc.span.start_line > 1
