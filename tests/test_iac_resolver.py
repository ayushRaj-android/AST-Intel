"""Tests for the IaC cross-domain resolver."""

from __future__ import annotations

from ast_intel.core._iac_resolver import IaCResolver
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphNode,
    NodeKind,
)


def _make_node(
    node_id: str,
    label: str,
    kind: NodeKind,
    properties: dict[str, str] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id, label=label, kind=kind, file="test.rs",
        properties=properties or {},
    )


def _make_graph(nodes: list[GraphNode]) -> CodeGraph:
    return CodeGraph(nodes=nodes, edges=[])


class TestImageToServiceResolution:
    """Test DOCKER_IMAGE -> SERVICE matching."""

    def test_exact_name_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node("code::svc/myapp", "myapp", NodeKind.SERVICE),
        ])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/myapp",
                "myapp",
                NodeKind.DOCKER_IMAGE,
            ),
        ])
        edges = resolver.resolve(code, iac)
        relations = {e.relation for e in edges}
        assert EdgeRelation.IMAGE_OF in relations

    def test_substring_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
        ])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/engine",
                "engine",
                NodeKind.DOCKER_IMAGE,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.IMAGE_OF for e in edges)

    def test_registry_stripping(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node("code::svc/myapp", "myapp", NodeKind.SERVICE),
        ])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/registry.io/org/myapp:latest",
                "registry.io/org/myapp:latest",
                NodeKind.DOCKER_IMAGE,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.IMAGE_OF for e in edges)

    def test_no_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node("code::svc/web", "web", NodeKind.SERVICE),
        ])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/redis",
                "redis",
                NodeKind.DOCKER_IMAGE,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert len(edges) == 0

    def test_no_services(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/myapp",
                "myapp",
                NodeKind.DOCKER_IMAGE,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert len(edges) == 0

    def test_no_images(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node("code::svc/myapp", "myapp", NodeKind.SERVICE),
        ])
        iac = _make_graph([])
        edges = resolver.resolve(code, iac)
        assert len(edges) == 0


class TestDeploymentToServiceResolution:
    """Test K8S_DEPLOYMENT -> SERVICE matching."""

    def test_exact_name_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine",
                "approval-engine",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.DEPLOYS for e in edges)

    def test_normalized_suffix_strip(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine-deployment",
                "approval-engine-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.DEPLOYS for e in edges)

    def test_image_based_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://deploy.yaml::Deployment/my-deploy",
                "my-deploy",
                NodeKind.K8S_DEPLOYMENT,
                properties={"images": "registry/approval-engine:latest"},
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.DEPLOYS for e in edges)


class TestPortToRouteResolution:
    """Test K8S_SERVICE port -> ROUTE matching."""

    def test_port_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::route/get_health",
                "/health",
                NodeKind.ROUTE,
                properties={"port": "8080"},
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://svc.yaml::Service/web",
                "web",
                NodeKind.K8S_SERVICE,
                properties={"ports": "8080:8080/TCP"},
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.ROUTES_TO for e in edges)

    def test_no_port_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::route/get_health",
                "/health",
                NodeKind.ROUTE,
                properties={"port": "9090"},
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://svc.yaml::Service/web",
                "web",
                NodeKind.K8S_SERVICE,
                properties={"ports": "8080:8080/TCP"},
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert not any(e.relation == EdgeRelation.ROUTES_TO for e in edges)

    def test_no_routes(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([])
        iac = _make_graph([
            _make_node(
                "k8s://svc.yaml::Service/web",
                "web",
                NodeKind.K8S_SERVICE,
                properties={"ports": "8080:8080/TCP"},
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert not any(e.relation == EdgeRelation.ROUTES_TO for e in edges)


class TestBelongsToAssignment:
    """Test IaC node -> SERVICE BELONGS_TO assignment."""

    def test_name_contains_service(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://cm.yaml::ConfigMap/approval-engine-configmap",
                "approval-engine-configmap",
                NodeKind.K8S_CONFIGMAP,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert any(e.relation == EdgeRelation.BELONGS_TO for e in edges)

    def test_no_services(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([])
        iac = _make_graph([
            _make_node(
                "k8s://cm.yaml::ConfigMap/my-config",
                "my-config",
                NodeKind.K8S_CONFIGMAP,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert len(edges) == 0

    def test_no_match(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node("code::svc/web", "web", NodeKind.SERVICE),
        ])
        iac = _make_graph([
            _make_node(
                "k8s://cm.yaml::ConfigMap/database-config",
                "database-config",
                NodeKind.K8S_CONFIGMAP,
            ),
        ])
        edges = resolver.resolve(code, iac)
        assert not any(e.relation == EdgeRelation.BELONGS_TO for e in edges)


class TestFullResolve:
    """Test combined resolve output."""

    def test_full_resolve(self) -> None:
        resolver = IaCResolver()
        code = _make_graph([
            _make_node(
                "code::svc/approval-engine",
                "approval-engine",
                NodeKind.SERVICE,
            ),
            _make_node(
                "code::route/health",
                "/health",
                NodeKind.ROUTE,
                properties={"port": "8080"},
            ),
        ])
        iac = _make_graph([
            _make_node(
                "docker://Dockerfile::DockerImage/approval-engine:latest",
                "approval-engine:latest",
                NodeKind.DOCKER_IMAGE,
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine",
                "approval-engine",
                NodeKind.K8S_DEPLOYMENT,
            ),
            _make_node(
                "k8s://svc.yaml::Service/approval-engine-svc",
                "approval-engine-svc",
                NodeKind.K8S_SERVICE,
                properties={"ports": "80:8080/TCP"},
            ),
        ])
        edges = resolver.resolve(code, iac)
        relations = {e.relation for e in edges}
        assert EdgeRelation.IMAGE_OF in relations
        assert EdgeRelation.DEPLOYS in relations
        assert EdgeRelation.ROUTES_TO in relations
        assert EdgeRelation.BELONGS_TO in relations
        assert len(edges) >= 4


# =========================================================================
# Phase 5: Enhanced cross-domain resolution tests
# =========================================================================


class TestHelmTemplateToK8s:
    """Test HELM_TEMPLATE -> K8S_* matching via k8s_kinds + name."""

    def test_template_matches_deployment_by_stem(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/templates/approval-engine.yaml::HelmTemplate/approval-engine.yaml",
                "approval-engine.yaml",
                NodeKind.HELM_TEMPLATE,
                properties={"k8s_kinds": "Deployment"},
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine-deployment",
                "approval-engine-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_template_to_k8s(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.RENDERS_TO

    def test_template_matches_multiple_kinds(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/templates/redis.yaml::HelmTemplate/redis.yaml",
                "redis.yaml",
                NodeKind.HELM_TEMPLATE,
                properties={"k8s_kinds": "Deployment,Service"},
            ),
            _make_node(
                "k8s://redis.yaml::Deployment/redis-deployment",
                "redis-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
            _make_node(
                "k8s://redis.yaml::Service/redis-svc",
                "redis-svc",
                NodeKind.K8S_SERVICE,
            ),
        ])
        edges = resolver._resolve_helm_template_to_k8s(iac)
        assert len(edges) == 2
        targets = {e.target for e in edges}
        assert "k8s://redis.yaml::Deployment/redis-deployment" in targets
        assert "k8s://redis.yaml::Service/redis-svc" in targets

    def test_no_match_for_unrelated_names(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/templates/frontend.yaml::HelmTemplate/frontend.yaml",
                "frontend.yaml",
                NodeKind.HELM_TEMPLATE,
                properties={"k8s_kinds": "Deployment"},
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/backend-deployment",
                "backend-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_template_to_k8s(iac)
        assert len(edges) == 0

    def test_no_k8s_kinds_skips(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/templates/_helpers.tpl::HelmTemplate/_helpers.tpl",
                "_helpers.tpl",
                NodeKind.HELM_TEMPLATE,
                properties={},
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/app",
                "app",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_template_to_k8s(iac)
        assert len(edges) == 0


class TestHelmValueChain:
    """Test HELM_VALUE -> K8S_DEPLOYMENT via camelCase -> kebab."""

    def test_camel_to_kebab_match(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/values.yaml::HelmValue/approvalEngine",
                "approvalEngine",
                NodeKind.HELM_VALUE,
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine-deployment",
                "approval-engine-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_value_chain(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.CONFIGURES

    def test_nested_value_uses_first_segment(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/values.yaml::HelmValue/fileReconstruction.config.port",
                "fileReconstruction.config.port",
                NodeKind.HELM_VALUE,
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/file-reconstruction-deployment",
                "file-reconstruction-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_value_chain(iac)
        assert len(edges) == 1
        assert edges[0].target == (
            "k8s://deploy.yaml::Deployment/file-reconstruction-deployment"
        )

    def test_no_match_for_unrelated_value(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/values.yaml::HelmValue/global.namespace",
                "global.namespace",
                NodeKind.HELM_VALUE,
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/approval-engine",
                "approval-engine",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_value_chain(iac)
        assert len(edges) == 0

    def test_already_kebab_value(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "helm://chart/values.yaml::HelmValue/redis",
                "redis",
                NodeKind.HELM_VALUE,
            ),
            _make_node(
                "k8s://deploy.yaml::Deployment/redis-deployment",
                "redis-deployment",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_helm_value_chain(iac)
        assert len(edges) == 1


class TestCICDToDockerPrecise:
    """Test precise CI_STEP -> DOCKER_IMAGE matching."""

    def test_docker_build_f_matches_specific_image(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/build-image",
                "build-image",
                NodeKind.CI_STEP,
                properties={"script": "docker build -f Dockerfile_github ."},
            ),
            _make_node(
                "docker://Dockerfile_github::DockerImage/app",
                "app",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "Dockerfile_github"},
            ),
            _make_node(
                "docker://Dockerfile::DockerImage/other",
                "other",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "Dockerfile"},
            ),
        ])
        edges = resolver._resolve_cicd_to_docker(iac)
        assert len(edges) == 1
        assert edges[0].target == "docker://Dockerfile_github::DockerImage/app"

    def test_default_dockerfile_match(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/build",
                "build",
                NodeKind.CI_STEP,
                properties={"script": "docker build ."},
            ),
            _make_node(
                "docker://Dockerfile::DockerImage/app",
                "app",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "Dockerfile"},
            ),
        ])
        edges = resolver._resolve_cicd_to_docker(iac)
        assert len(edges) == 1
        assert edges[0].target == "docker://Dockerfile::DockerImage/app"

    def test_service_name_param_matches(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/imagebuildinfo",
                "imagebuildinfo",
                NodeKind.CI_STEP,
                properties={
                    "task": "imagebuildinfo@1",
                    "param_serviceName": "approval-engine",
                },
            ),
            _make_node(
                "docker://Dockerfile::DockerImage/approval-engine",
                "approval-engine",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "Dockerfile"},
            ),
            _make_node(
                "docker://Dockerfile::DockerImage/other-service",
                "other-service",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "Dockerfile_other"},
            ),
        ])
        edges = resolver._resolve_cicd_to_docker(iac)
        assert len(edges) == 1
        assert edges[0].target == (
            "docker://Dockerfile::DockerImage/approval-engine"
        )

    def test_fallback_links_all_when_no_specific_match(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/build",
                "build",
                NodeKind.CI_STEP,
                properties={"script": "docker build -t something ."},
            ),
            _make_node(
                "docker://Dockerfile::DockerImage/app1",
                "app1",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "NonExistent"},
            ),
            _make_node(
                "docker://Dockerfile2::DockerImage/app2",
                "app2",
                NodeKind.DOCKER_IMAGE,
                properties={"file": "AlsoNonExistent"},
            ),
        ])
        edges = resolver._resolve_cicd_to_docker(iac)
        # No -f flag, "docker build" present, looks for "dockerfile"
        # Neither file matches "dockerfile", so fallback to all
        assert len(edges) == 2


class TestCICDToK8sHelm:
    """Test CI_STEP -> HELM_CHART / K8S_DEPLOYMENT."""

    def test_helm_upgrade_links_to_chart(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/deploy",
                "deploy",
                NodeKind.CI_STEP,
                properties={
                    "script": (
                        "helm upgrade --install safeguard"
                        " ./deploy/helm-charts/safeguard"
                    ),
                },
            ),
            _make_node(
                "helm://deploy/helm-charts/safeguard::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
                properties={"file": "deploy/helm-charts/safeguard"},
            ),
        ])
        edges = resolver._resolve_cicd_to_k8s_helm(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.DEPLOYS_VIA

    def test_kubectl_apply_links_to_deployment(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/apply",
                "apply",
                NodeKind.CI_STEP,
                properties={"script": "kubectl apply -f deploy/redis.yaml"},
            ),
            _make_node(
                "k8s://deploy/redis.yaml::Deployment/redis",
                "redis",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_cicd_to_k8s_helm(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.DEPLOYS_VIA
        assert edges[0].target == "k8s://deploy/redis.yaml::Deployment/redis"

    def test_no_match_for_unrelated_script(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/test",
                "test",
                NodeKind.CI_STEP,
                properties={"script": "make test"},
            ),
            _make_node(
                "helm://chart::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
            ),
        ])
        edges = resolver._resolve_cicd_to_k8s_helm(iac)
        assert len(edges) == 0

    def test_helm_install_matches_chart_name(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "cicd://pipe.yml::Step/install",
                "install",
                NodeKind.CI_STEP,
                properties={"script": "helm install my-release safeguard"},
            ),
            _make_node(
                "helm://chart::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
            ),
        ])
        edges = resolver._resolve_cicd_to_k8s_helm(iac)
        assert len(edges) == 1


class TestAnsibleToK8s:
    """Test ANSIBLE_TASK -> HELM_CHART / K8S_DEPLOYMENT."""

    def test_helm_module_links_to_chart(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "ansible://playbook.yml::Task/install-safeguard",
                "install-safeguard",
                NodeKind.ANSIBLE_TASK,
                properties={"module": "kubernetes.core.helm"},
            ),
            _make_node(
                "helm://chart::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
            ),
        ])
        edges = resolver._resolve_ansible_to_k8s(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.DEPLOYS_VIA
        assert edges[0].target == "helm://chart::HelmChart/safeguard"

    def test_k8s_module_links_to_deployment(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "ansible://playbook.yml::Task/deploy-redis",
                "deploy-redis",
                NodeKind.ANSIBLE_TASK,
                properties={"module": "kubernetes.core.k8s"},
            ),
            _make_node(
                "k8s://redis.yaml::Deployment/redis",
                "redis",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_ansible_to_k8s(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.DEPLOYS_VIA

    def test_shell_module_with_helm_command(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "ansible://playbook.yml::Task/helm-deploy",
                "helm-deploy",
                NodeKind.ANSIBLE_TASK,
                properties={
                    "module": "ansible.builtin.shell",
                    "script": (
                        "helm upgrade --install safeguard"
                        " ./charts/safeguard"
                    ),
                },
            ),
            _make_node(
                "helm://charts/safeguard::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
            ),
        ])
        edges = resolver._resolve_ansible_to_k8s(iac)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.DEPLOYS_VIA

    def test_no_match_for_unrelated_module(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "ansible://playbook.yml::Task/copy-files",
                "copy-files",
                NodeKind.ANSIBLE_TASK,
                properties={"module": "ansible.builtin.copy"},
            ),
            _make_node(
                "helm://chart::HelmChart/safeguard",
                "safeguard",
                NodeKind.HELM_CHART,
            ),
        ])
        edges = resolver._resolve_ansible_to_k8s(iac)
        assert len(edges) == 0

    def test_community_kubernetes_module(self) -> None:
        resolver = IaCResolver()
        iac = _make_graph([
            _make_node(
                "ansible://playbook.yml::Task/apply-redis",
                "apply-redis",
                NodeKind.ANSIBLE_TASK,
                properties={"module": "community.kubernetes.k8s"},
            ),
            _make_node(
                "k8s://redis.yaml::Deployment/redis",
                "redis",
                NodeKind.K8S_DEPLOYMENT,
            ),
        ])
        edges = resolver._resolve_ansible_to_k8s(iac)
        assert len(edges) == 1


class TestCamelToKebab:
    """Test the _camel_to_kebab helper."""

    def test_simple_camel(self) -> None:
        from ast_intel.core._iac_resolver import _camel_to_kebab
        assert _camel_to_kebab("approvalEngine") == "approval-engine"

    def test_multi_word_camel(self) -> None:
        from ast_intel.core._iac_resolver import _camel_to_kebab
        assert _camel_to_kebab("fileReconstruction") == "file-reconstruction"

    def test_already_lowercase(self) -> None:
        from ast_intel.core._iac_resolver import _camel_to_kebab
        assert _camel_to_kebab("redis") == "redis"

    def test_all_caps_prefix(self) -> None:
        from ast_intel.core._iac_resolver import _camel_to_kebab
        # Only splits at lowercase→uppercase boundary
        assert _camel_to_kebab("HTTPClient") == "httpclient"
