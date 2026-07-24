"""Cross-domain IaC resolver — link IaC nodes to code nodes.

Runs as a post-processing pass after both the code graph and
IaC graph are built.  Creates cross-domain edges:

- DOCKER_IMAGE name → code SERVICE with matching name (IMAGE_OF)
- K8S_DEPLOYMENT name/labels → code SERVICE (DEPLOYS)
- K8S_SERVICE target port → code ROUTE (ROUTES_TO cross-domain)
- IaC nodes → SERVICE by name heuristic (BELONGS_TO)
- HELM_TEMPLATE → K8S_* (RENDERS_TO)
- HELM_VALUE → K8S_DEPLOYMENT (CONFIGURES via value chain)
- CI_STEP → HELM_CHART / K8S_DEPLOYMENT (DEPLOYS_VIA)
- ANSIBLE_TASK → HELM_CHART / K8S_DEPLOYMENT (DEPLOYS_VIA)
"""

from __future__ import annotations

import logging
import re

from ast_intel.models.ast_node import SCORE_INFERRED, Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

__all__: list[str] = ["IaCResolver"]

logger = logging.getLogger(__name__)

# Patterns to strip from names for fuzzy matching
_STRIP_SUFFIXES: tuple[str, ...] = (
    "-deployment", "-deploy", "-svc", "-service",
    "-configmap", "-cm", "-secret", "-ingress",
)

# Port string pattern: "port:targetPort/protocol"
_PORT_RE = re.compile(r"(\d+):(\d+)/(\w+)")


def _normalize_name(name: str) -> str:
    """Normalize a resource name for fuzzy matching.

    Strips common K8s suffixes, lowercases, and replaces
    separators with hyphens.
    """
    n = name.lower().strip()
    for suffix in _STRIP_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    # Replace underscores with hyphens for comparison
    return n.replace("_", "-")


def _extract_target_ports(props: dict[str, str]) -> set[str]:
    """Extract targetPort values from a K8S_SERVICE properties dict.

    The ``ports`` property has format ``"8080:8080/TCP,443:8443/TCP"``.
    Returns the set of targetPort strings (second number).
    """
    ports_str = props.get("ports", "")
    if not ports_str:
        return set()
    result: set[str] = set()
    for match in _PORT_RE.finditer(ports_str):
        result.add(match.group(2))  # targetPort
    return result


# ---------------------------------------------------------------------------
# Phase 5 helpers
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_VALUE_NAME_MIN_LEN = 4
_HELM_CMD_RE = re.compile(
    r"helm\s+(?:upgrade\s+--install|install|upgrade|template)\s+(\S+)\s+(\S+)",
)
_KUBECTL_APPLY_RE = re.compile(
    r"kubectl\s+(?:.*\s)?apply\s+(?:.*-f\s+(\S+)|.*--filename[= ](\S+))",
)
_DOCKER_BUILD_F_RE = re.compile(r"docker\s+build\s+.*-f\s+(\S+)")

# Map from K8s kind string (as found in YAML) to NodeKind
_K8S_KIND_STR_MAP: dict[str, NodeKind] = {
    "Deployment": NodeKind.K8S_DEPLOYMENT,
    "StatefulSet": NodeKind.K8S_DEPLOYMENT,
    "DaemonSet": NodeKind.K8S_DEPLOYMENT,
    "Service": NodeKind.K8S_SERVICE,
    "ConfigMap": NodeKind.K8S_CONFIGMAP,
    "Secret": NodeKind.K8S_SECRET,
    "Ingress": NodeKind.K8S_INGRESS,
    "Namespace": NodeKind.K8S_NAMESPACE,
    "PersistentVolumeClaim": NodeKind.K8S_PVC,
    "NetworkPolicy": NodeKind.K8S_GENERIC,
    "CronJob": NodeKind.K8S_CRONJOB,
    "Job": NodeKind.K8S_JOB,
    "Pod": NodeKind.K8S_DEPLOYMENT,
}


def _camel_to_kebab(name: str) -> str:
    """Convert camelCase to kebab-case.

    Example: ``approvalEngine`` → ``approval-engine``.
    """
    return _CAMEL_RE.sub("-", name).lower()


# Ansible module categories for K8s/Helm detection
_ANSIBLE_K8S_MODULES: tuple[str, ...] = (
    "kubernetes.core.k8s",
    "community.kubernetes.k8s",
    "k8s",
)
_ANSIBLE_HELM_MODULES: tuple[str, ...] = (
    "kubernetes.core.helm",
    "community.kubernetes.helm",
    "helm",
)
_ANSIBLE_SHELL_MODULES: tuple[str, ...] = (
    "ansible.builtin.shell",
    "ansible.builtin.command",
    "shell",
    "command",
)


class IaCResolver:
    """Resolve cross-domain edges between IaC and code nodes.

    Usage::

        resolver = IaCResolver()
        new_edges = resolver.resolve(code_graph, iac_graph)
        code_graph.edges.extend(new_edges)
    """

    def resolve(
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Find cross-domain edges between code and IaC.

        Args:
            code_graph: The code-only knowledge graph.
            iac_graph: The IaC-only knowledge graph.

        Returns:
            New edges linking IaC nodes to code nodes.
        """
        edges: list[GraphEdge] = []
        edges.extend(self._resolve_image_to_service(code_graph, iac_graph))
        edges.extend(
            self._resolve_deployment_to_service(code_graph, iac_graph),
        )
        edges.extend(self._resolve_port_to_route(code_graph, iac_graph))
        edges.extend(self._resolve_cicd_to_docker(iac_graph))
        edges.extend(self._assign_belongs_to(code_graph, iac_graph))
        # Phase 4: link code-detected cloud resources to IaC-provisioned ones
        edges.extend(self._resolve_cloud_code_to_iac(code_graph, iac_graph))
        # Phase 5: enhanced cross-domain resolution
        edges.extend(self._resolve_helm_template_to_k8s(iac_graph))
        edges.extend(self._resolve_helm_value_chain(iac_graph))
        edges.extend(self._resolve_cicd_to_k8s_helm(iac_graph))
        edges.extend(self._resolve_ansible_to_k8s(iac_graph))
        logger.info("IaC resolver found %d cross-domain edges", len(edges))
        return edges

    def _resolve_image_to_service(
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Match Docker image names to code SERVICE nodes.

        Heuristic: strip registry and tag from image name,
        compare against SERVICE node labels.
        """
        services: dict[str, GraphNode] = {}
        for node in code_graph.nodes:
            if node.kind == NodeKind.SERVICE:
                services[node.label.lower()] = node

        if not services:
            return []

        edges: list[GraphEdge] = []
        for node in iac_graph.nodes:
            if node.kind != NodeKind.DOCKER_IMAGE:
                continue
            # Extract basename: "registry/path/name:tag" → "name"
            image_name = node.label.rsplit("/", 1)[-1]
            image_name = image_name.split(":")[0].lower()

            for svc_label, svc_node in services.items():
                if image_name == svc_label or image_name in svc_label:
                    edges.append(
                        GraphEdge(
                            source=node.id,
                            target=svc_node.id,
                            relation=EdgeRelation.IMAGE_OF,
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                        ),
                    )

        return edges

    def _resolve_deployment_to_service(  # noqa: C901
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Match K8S_DEPLOYMENT names/labels to code SERVICE nodes.

        Heuristic chain:
        1. Exact match: deployment name == service label (case-insensitive)
        2. Normalized match: strip -deployment/-deploy suffixes
        3. Image match: deployment's images property contains service name
        """
        services: dict[str, GraphNode] = {}
        service_names_normalized: dict[str, GraphNode] = {}
        for node in code_graph.nodes:
            if node.kind == NodeKind.SERVICE:
                services[node.label.lower()] = node
                service_names_normalized[
                    _normalize_name(node.label)
                ] = node

        if not services:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.K8S_DEPLOYMENT:
                continue

            dep_name = _normalize_name(node.label)

            # 1. Exact or normalized name match
            svc_node = services.get(
                node.label.lower(),
            ) or service_names_normalized.get(dep_name)

            # 2. Image-based match
            if svc_node is None:
                images = node.properties.get("images", "")
                for img in images.split(","):
                    img_base = (
                        img.rsplit("/", 1)[-1].split(":")[0].lower()
                    )
                    img_normalized = _normalize_name(img_base)
                    svc_node = services.get(
                        img_base,
                    ) or service_names_normalized.get(img_normalized)
                    if svc_node is not None:
                        break

            if svc_node is not None:
                edge_key = f"{node.id}\u2192{svc_node.id}"
                if edge_key not in matched:
                    matched.add(edge_key)
                    edges.append(
                        GraphEdge(
                            source=node.id,
                            target=svc_node.id,
                            relation=EdgeRelation.DEPLOYS,
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                        ),
                    )

        return edges

    def _resolve_port_to_route(
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Match K8S_SERVICE target ports to code ROUTE nodes.

        Resolution chain:
        1. K8S_SERVICE → targetPort (from ``ports`` property)
        2. Code ROUTE → port (from ``port`` property)
        3. Match by port number equality
        """
        routes_by_port: dict[str, list[GraphNode]] = {}
        for node in code_graph.nodes:
            if node.kind != NodeKind.ROUTE:
                continue
            port = node.properties.get("port", "")
            if port:
                routes_by_port.setdefault(port, []).append(node)

        if not routes_by_port:
            return []

        edges: list[GraphEdge] = []
        for node in iac_graph.nodes:
            if node.kind != NodeKind.K8S_SERVICE:
                continue
            target_ports = _extract_target_ports(node.properties)
            for tp in target_ports:
                route_nodes = routes_by_port.get(tp, [])
                edges.extend(
                    GraphEdge(
                        source=node.id,
                        target=route_node.id,
                        relation=EdgeRelation.ROUTES_TO,
                        confidence=Confidence.INFERRED,
                        confidence_score=SCORE_INFERRED,
                        properties={"target_port": tp},
                    )
                    for route_node in route_nodes
                )

        return edges

    def _resolve_cicd_to_docker(  # noqa: C901, PLR0912
        self,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link CI_STEP nodes to DOCKER_IMAGE/DOCKER_SERVICE precisely.

        Parses step scripts for ``docker build -f <path>`` and task inputs
        for ``serviceName``/``dockerFileRelPath`` to match specific images.
        Falls back to linking all docker nodes only when no specific match.
        """
        docker_by_file: dict[str, GraphNode] = {}
        docker_by_name: dict[str, GraphNode] = {}
        for node in iac_graph.nodes:
            if node.kind in (NodeKind.DOCKER_IMAGE, NodeKind.DOCKER_SERVICE):
                docker_by_name[_normalize_name(node.label)] = node
                file_prop = node.properties.get("file", "")
                if file_prop:
                    docker_by_file[file_prop.lower()] = node
                    # Also index by basename
                    basename = file_prop.rsplit("/", 1)[-1].lower()
                    docker_by_file[basename] = node

        if not docker_by_name:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.CI_STEP:
                continue
            script = node.properties.get("script", "")
            task = node.properties.get("task", "")
            is_docker = (
                "docker build" in script
                or "docker-compose" in script
                or "docker" in task.lower()
                or "imagebuildinfo" in task.lower()
            )
            if not is_docker:
                continue

            # Try precise matching
            target: GraphNode | None = None

            # 1. docker build -f <path>
            m = _DOCKER_BUILD_F_RE.search(script)
            if m:
                dockerfile_path = m.group(1).lower()
                target = docker_by_file.get(
                    dockerfile_path,
                ) or docker_by_file.get(
                    dockerfile_path.rsplit("/", 1)[-1],
                )

            # 2. Task inputs: serviceName / dockerFileRelPath
            if target is None:
                svc_name = node.properties.get("param_serviceName", "")
                if svc_name:
                    target = docker_by_name.get(_normalize_name(svc_name))

            # 3. Default Dockerfile (docker build .)
            if target is None and "docker build" in script and "-f" not in script:
                target = docker_by_file.get("dockerfile")

            # Emit precise edge or fallback to all
            if target is not None:
                edge_key = f"{node.id}\u2192{target.id}"
                if edge_key not in matched:
                    matched.add(edge_key)
                    edges.append(
                        GraphEdge(
                            source=node.id,
                            target=target.id,
                            relation=EdgeRelation.BUILDS_IMAGE,
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                        ),
                    )
            else:
                # Fallback: link to all docker nodes
                for dk_node in docker_by_name.values():
                    edge_key = f"{node.id}\u2192{dk_node.id}"
                    if edge_key not in matched:
                        matched.add(edge_key)
                        edges.append(
                            GraphEdge(
                                source=node.id,
                                target=dk_node.id,
                                relation=EdgeRelation.BUILDS_IMAGE,
                                confidence=Confidence.INFERRED,
                                confidence_score=SCORE_INFERRED,
                            ),
                        )

        return edges

    def _assign_belongs_to(
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Assign IaC nodes to code SERVICE nodes by name heuristic.

        For each IaC node whose normalized name matches (or contains) a
        service name, emit a BELONGS_TO edge.  This groups IaC resources
        with the code service they configure/deploy.
        """
        services: dict[str, GraphNode] = {}
        for node in code_graph.nodes:
            if node.kind == NodeKind.SERVICE:
                services[_normalize_name(node.label)] = node

        if not services:
            return []

        edges: list[GraphEdge] = []
        for node in iac_graph.nodes:
            node_normalized = _normalize_name(node.label)

            for svc_name, svc_node in services.items():
                if svc_name in node_normalized or node_normalized in svc_name:
                    edges.append(
                        GraphEdge(
                            source=node.id,
                            target=svc_node.id,
                            relation=EdgeRelation.BELONGS_TO,
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                        ),
                    )
                    break  # one assignment per IaC node

        return edges

    def _resolve_cloud_code_to_iac(
        self,
        code_graph: CodeGraph,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link code-detected CLOUD_RESOURCE to IaC-provisioned CLOUD_RESOURCE.

        Matches by ``(provider, service, category)`` — if a code resource
        (source=code) shares the same cloud service type as an IaC resource
        (source=iac), emit a ``BACKED_BY`` edge connecting them.  This lets
        consumers know "this code client is backed by that specific infra
        resource."

        When multiple IaC resources match, all are linked (a single code
        client can be backed by multiple IaC instances).
        """
        # Collect IaC-provisioned cloud resources indexed by (provider, service, category)
        iac_by_key: dict[tuple[str, str, str], list[GraphNode]] = {}
        for node in iac_graph.nodes:
            if node.kind != NodeKind.CLOUD_RESOURCE:
                continue
            if node.properties.get("source") != "iac":
                continue
            key = (
                node.properties.get("provider", ""),
                node.properties.get("service", ""),
                node.properties.get("category", ""),
            )
            iac_by_key.setdefault(key, []).append(node)

        if not iac_by_key:
            return []

        # Collect code-detected cloud resources
        code_resources = [
            n for n in code_graph.nodes
            if n.kind == NodeKind.CLOUD_RESOURCE
            and n.properties.get("source") == "code"
        ]

        edges: list[GraphEdge] = []
        for code_node in code_resources:
            key = (
                code_node.properties.get("provider", ""),
                code_node.properties.get("service", ""),
                code_node.properties.get("category", ""),
            )
            iac_matches = iac_by_key.get(key, [])
            edges.extend(
                GraphEdge(
                    source=code_node.id,
                    target=iac_node.id,
                    relation=EdgeRelation.BACKED_BY,
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED,
                    file=code_node.file,
                )
                for iac_node in iac_matches
            )

        if edges:
            logger.info(
                "Cloud code→IaC resolver: %d BACKED_BY edges", len(edges),
            )
        return edges

    # ------------------------------------------------------------------
    # Phase 5: Enhanced cross-domain resolution
    # ------------------------------------------------------------------

    def _resolve_helm_template_to_k8s(  # noqa: C901
        self,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link HELM_TEMPLATE nodes to specific K8S_* nodes they render.

        Uses the template's ``k8s_kinds`` property plus file-stem matching
        against K8S resource names.
        """
        # Index K8S nodes by (kind, normalized_name)
        k8s_by_kind: dict[NodeKind, dict[str, GraphNode]] = {}
        for node in iac_graph.nodes:
            if node.kind.value.startswith("k8s_"):
                bucket = k8s_by_kind.setdefault(node.kind, {})
                bucket[_normalize_name(node.label)] = node

        if not k8s_by_kind:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.HELM_TEMPLATE:
                continue
            kinds_str = node.properties.get("k8s_kinds", "")
            if not kinds_str:
                continue

            # Template file stem as base name
            tmpl_stem = _normalize_name(
                node.label.rsplit(".", 1)[0] if "." in node.label else node.label,
            )

            for kind_raw in kinds_str.split(","):
                kind_str = kind_raw.strip()
                target_kind = _K8S_KIND_STR_MAP.get(kind_str)
                if target_kind is None:
                    continue
                bucket = k8s_by_kind.get(target_kind, {})
                # Try matching template stem against K8S resource names
                for k8s_name, k8s_node in bucket.items():
                    if (
                        tmpl_stem == k8s_name
                        or tmpl_stem in k8s_name
                        or k8s_name in tmpl_stem
                    ):
                        edge_key = f"{node.id}\u2192{k8s_node.id}"
                        if edge_key not in matched:
                            matched.add(edge_key)
                            edges.append(
                                GraphEdge(
                                    source=node.id,
                                    target=k8s_node.id,
                                    relation=EdgeRelation.RENDERS_TO,
                                    confidence=Confidence.INFERRED,
                                    confidence_score=SCORE_INFERRED,
                                ),
                            )

        return edges

    def _resolve_helm_value_chain(  # noqa: C901
        self,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link HELM_VALUE nodes to K8S_DEPLOYMENT or HELM_TEMPLATE.

        The first dot-segment of a value key (e.g., ``approvalEngine``)
        is converted to kebab-case and compared against K8S_DEPLOYMENT
        normalized names.  Falls back to HELM_TEMPLATE matching when no
        K8S_DEPLOYMENT nodes exist (Helm-only repos).
        """
        # Index deployments by normalized name
        deployments: dict[str, GraphNode] = {}
        for node in iac_graph.nodes:
            if node.kind == NodeKind.K8S_DEPLOYMENT:
                deployments[_normalize_name(node.label)] = node

        # Fallback: index HELM_TEMPLATE by normalized file stem
        templates: dict[str, GraphNode] = {}
        if not deployments:
            for node in iac_graph.nodes:
                if node.kind == NodeKind.HELM_TEMPLATE:
                    stem = node.label.rsplit(".", 1)[0] if "." in node.label else node.label
                    # Use simple lowercasing for template stems (no suffix stripping)
                    templates[stem.lower()] = node

        targets = deployments or templates
        if not targets:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.HELM_VALUE:
                continue
            # First segment: "approvalEngine.config.x" → "approvalEngine"
            first_segment = node.label.split(".")[0]
            kebab_name = _camel_to_kebab(first_segment)

            # Skip very short/generic names that cause false matches
            if len(kebab_name) < _VALUE_NAME_MIN_LEN:
                continue

            for tgt_name, tgt_node in targets.items():
                # Require prefix or exact match to avoid spurious substring hits
                if tgt_name == kebab_name or tgt_name.startswith(
                    kebab_name + "-",
                ):
                    edge_key = f"{node.id}\u2192{tgt_node.id}"
                    if edge_key not in matched:
                        matched.add(edge_key)
                        edges.append(
                            GraphEdge(
                                source=node.id,
                                target=tgt_node.id,
                                relation=EdgeRelation.CONFIGURES,
                                confidence=Confidence.INFERRED,
                                confidence_score=SCORE_INFERRED,
                            ),
                        )
                    break  # one target per value

        return edges

    def _resolve_cicd_to_k8s_helm(  # noqa: C901, PLR0912
        self,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link CI_STEP nodes to HELM_CHART or K8S_DEPLOYMENT.

        Scans step ``script`` for ``helm install/upgrade`` and
        ``kubectl apply`` commands.
        """
        helm_charts: dict[str, GraphNode] = {}
        k8s_deployments: dict[str, GraphNode] = {}
        for node in iac_graph.nodes:
            if node.kind == NodeKind.HELM_CHART:
                helm_charts[_normalize_name(node.label)] = node
                # Also index by chart file path
                file_prop = node.properties.get("file", "")
                if file_prop:
                    helm_charts[file_prop.lower()] = node
            elif node.kind == NodeKind.K8S_DEPLOYMENT:
                k8s_deployments[_normalize_name(node.label)] = node

        if not helm_charts and not k8s_deployments:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.CI_STEP:
                continue
            script = node.properties.get("script", "")
            if not script:
                continue

            # Helm commands
            for m in _HELM_CMD_RE.finditer(script):
                release_name = m.group(1)
                chart_ref = m.group(2)
                # Match chart_ref against chart path or name
                target = helm_charts.get(
                    chart_ref.lower(),
                ) or helm_charts.get(
                    _normalize_name(release_name),
                ) or helm_charts.get(
                    _normalize_name(chart_ref.rsplit("/", 1)[-1]),
                )
                if target is not None:
                    edge_key = f"{node.id}\u2192{target.id}"
                    if edge_key not in matched:
                        matched.add(edge_key)
                        edges.append(
                            GraphEdge(
                                source=node.id,
                                target=target.id,
                                relation=EdgeRelation.DEPLOYS_VIA,
                                confidence=Confidence.INFERRED,
                                confidence_score=SCORE_INFERRED,
                            ),
                        )

            # kubectl apply
            for m in _KUBECTL_APPLY_RE.finditer(script):
                file_ref = m.group(1) or m.group(2) or ""
                file_base = file_ref.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                target = k8s_deployments.get(
                    _normalize_name(file_base),
                )
                if target is not None:
                    edge_key = f"{node.id}\u2192{target.id}"
                    if edge_key not in matched:
                        matched.add(edge_key)
                        edges.append(
                            GraphEdge(
                                source=node.id,
                                target=target.id,
                                relation=EdgeRelation.DEPLOYS_VIA,
                                confidence=Confidence.INFERRED,
                                confidence_score=SCORE_INFERRED,
                            ),
                        )

        return edges

    def _resolve_ansible_to_k8s(  # noqa: C901, PLR0912
        self,
        iac_graph: CodeGraph,
    ) -> list[GraphEdge]:
        """Link ANSIBLE_TASK nodes to HELM_CHART or K8S_DEPLOYMENT.

        Detects ``kubernetes.core.k8s``, ``kubernetes.core.helm``,
        and shell tasks running ``helm``/``kubectl`` commands.
        """
        helm_charts: dict[str, GraphNode] = {}
        k8s_deployments: dict[str, GraphNode] = {}
        for node in iac_graph.nodes:
            if node.kind == NodeKind.HELM_CHART:
                helm_charts[_normalize_name(node.label)] = node
            elif node.kind == NodeKind.K8S_DEPLOYMENT:
                k8s_deployments[_normalize_name(node.label)] = node

        if not helm_charts and not k8s_deployments:
            return []

        edges: list[GraphEdge] = []
        matched: set[str] = set()

        for node in iac_graph.nodes:
            if node.kind != NodeKind.ANSIBLE_TASK:
                continue
            module = node.properties.get("module", "")

            # kubernetes.core.helm → link to HELM_CHART
            if module in _ANSIBLE_HELM_MODULES:
                # Try matching task name to chart
                task_name = _normalize_name(node.label)
                for chart_name, chart_node in helm_charts.items():
                    if chart_name in task_name or task_name in chart_name:
                        edge_key = f"{node.id}\u2192{chart_node.id}"
                        if edge_key not in matched:
                            matched.add(edge_key)
                            edges.append(
                                GraphEdge(
                                    source=node.id,
                                    target=chart_node.id,
                                    relation=EdgeRelation.DEPLOYS_VIA,
                                    confidence=Confidence.INFERRED,
                                    confidence_score=SCORE_INFERRED,
                                ),
                            )
                        break

            # kubernetes.core.k8s → link to K8S_DEPLOYMENT
            elif module in _ANSIBLE_K8S_MODULES:
                task_name = _normalize_name(node.label)
                for dep_name, dep_node in k8s_deployments.items():
                    if dep_name in task_name or task_name in dep_name:
                        edge_key = f"{node.id}\u2192{dep_node.id}"
                        if edge_key not in matched:
                            matched.add(edge_key)
                            edges.append(
                                GraphEdge(
                                    source=node.id,
                                    target=dep_node.id,
                                    relation=EdgeRelation.DEPLOYS_VIA,
                                    confidence=Confidence.INFERRED,
                                    confidence_score=SCORE_INFERRED,
                                ),
                            )
                        break

            # Shell tasks with helm/kubectl
            elif module in _ANSIBLE_SHELL_MODULES:
                script = node.properties.get("script", "")
                # Try parsing script for helm commands
                found = False
                for m in _HELM_CMD_RE.finditer(script):
                    release_name = m.group(1)
                    chart_ref = m.group(2)
                    target = helm_charts.get(
                        _normalize_name(release_name),
                    ) or helm_charts.get(
                        _normalize_name(chart_ref.rsplit("/", 1)[-1]),
                    )
                    if target is not None:
                        edge_key = f"{node.id}\u2192{target.id}"
                        if edge_key not in matched:
                            matched.add(edge_key)
                            edges.append(
                                GraphEdge(
                                    source=node.id,
                                    target=target.id,
                                    relation=EdgeRelation.DEPLOYS_VIA,
                                    confidence=Confidence.INFERRED,
                                    confidence_score=SCORE_INFERRED,
                                ),
                            )
                        found = True
                        break
                # Fallback: task name mentions "helm" + chart name
                if not found and "helm" in node.label.lower():
                    task_name = _normalize_name(node.label)
                    for chart_name, chart_node in helm_charts.items():
                        if chart_name in task_name:
                            edge_key = f"{node.id}\u2192{chart_node.id}"
                            if edge_key not in matched:
                                matched.add(edge_key)
                                edges.append(
                                    GraphEdge(
                                        source=node.id,
                                        target=chart_node.id,
                                        relation=EdgeRelation.DEPLOYS_VIA,
                                        confidence=Confidence.INFERRED,
                                        confidence_score=SCORE_INFERRED,
                                    ),
                                )
                            break

        return edges
