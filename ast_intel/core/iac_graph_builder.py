"""IaC graph builder — converts IaCGraph results into CodeGraph nodes/edges.

Analogous to ``GraphBuilder`` for code, but operates on ``IaCGraph``
output from IaC extractors rather than ``WorkspaceAST``.
"""

from __future__ import annotations

import logging

from ast_intel.extractors._cloud_taxonomy import classify_iac_type
from ast_intel.models.ast_node import (
    SCORE_EXTRACTED,
    Confidence,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.iac_model import IaCGraph

__all__: list[str] = ["IaCGraphBuilder"]

logger = logging.getLogger(__name__)


def _iac_node_id(file: str, kind: str, name: str, prefix: str = "k8s") -> str:
    """Build a stable node ID for an IaC resource.

    Format: ``"{prefix}://{file}::{kind}/{name}"``
    """
    return f"{prefix}://{file}::{kind}/{name}"


_IAC_KIND_MAP: dict[str, NodeKind] = {
    # Kubernetes
    "Deployment": NodeKind.K8S_DEPLOYMENT,
    "StatefulSet": NodeKind.K8S_DEPLOYMENT,
    "DaemonSet": NodeKind.K8S_DEPLOYMENT,
    "ReplicaSet": NodeKind.K8S_DEPLOYMENT,
    "Service": NodeKind.K8S_SERVICE,
    "ConfigMap": NodeKind.K8S_CONFIGMAP,
    "Secret": NodeKind.K8S_SECRET,
    "Ingress": NodeKind.K8S_INGRESS,
    "IngressRoute": NodeKind.K8S_INGRESS,
    "Namespace": NodeKind.K8S_NAMESPACE,
    # Kubernetes (expanded — Phase 3)
    "CronJob": NodeKind.K8S_CRONJOB,
    "Job": NodeKind.K8S_JOB,
    "PersistentVolumeClaim": NodeKind.K8S_PVC,
    "Role": NodeKind.K8S_RBAC,
    "ClusterRole": NodeKind.K8S_RBAC,
    "RoleBinding": NodeKind.K8S_RBAC,
    "ClusterRoleBinding": NodeKind.K8S_RBAC,
    # Helm
    "HelmChart": NodeKind.HELM_CHART,
    "HelmValue": NodeKind.HELM_VALUE,
    "HelmTemplate": NodeKind.HELM_TEMPLATE,
    # Docker
    "DockerImage": NodeKind.DOCKER_IMAGE,
    "DockerStage": NodeKind.DOCKER_STAGE,
    "DockerService": NodeKind.DOCKER_SERVICE,
    # Ansible
    "AnsiblePlaybook": NodeKind.ANSIBLE_PLAYBOOK,
    "AnsibleTask": NodeKind.ANSIBLE_TASK,
    "AnsibleRole": NodeKind.ANSIBLE_ROLE,
    # CI/CD
    "CI_PIPELINE": NodeKind.CI_PIPELINE,
    "CI_STAGE": NodeKind.CI_STAGE,
    "CI_JOB": NodeKind.CI_JOB,
    "CI_STEP": NodeKind.CI_STEP,
    # Terraform
    "TfResource": NodeKind.TF_RESOURCE,
    "TfData": NodeKind.TF_DATA,
    "TfModule": NodeKind.TF_MODULE,
    "TfVariable": NodeKind.TF_VARIABLE,
    "TfOutput": NodeKind.TF_OUTPUT,
    "TfProvider": NodeKind.TF_PROVIDER,
    # Cloud (from Bicep/ARM extractor)
    "CloudResource": NodeKind.CLOUD_RESOURCE,
}

_KIND_PREFIX_MAP: dict[str, str] = {
    "HelmChart": "helm",
    "HelmValue": "helm",
    "HelmTemplate": "helm",
    "DockerImage": "docker",
    "DockerStage": "docker",
    "DockerService": "docker",
    "AnsiblePlaybook": "ansible",
    "AnsibleTask": "ansible",
    "AnsibleRole": "ansible",
    "CI_PIPELINE": "cicd",
    "CI_STAGE": "cicd",
    "CI_JOB": "cicd",
    "CI_STEP": "cicd",
    # Terraform
    "TfResource": "tf",
    "TfData": "tf",
    "TfModule": "tf",
    "TfVariable": "tf",
    "TfOutput": "tf",
    "TfProvider": "tf",
    # Cloud (from Bicep/ARM extractor)
    "CloudResource": "cloud",
}

_EDGE_RELATION_MAP: dict[str, EdgeRelation] = {
    "routes_to": EdgeRelation.ROUTES_TO,
    "configures": EdgeRelation.CONFIGURES,
    "uses_secret": EdgeRelation.USES_SECRET,
    "deploys": EdgeRelation.DEPLOYS,
    "references_image": EdgeRelation.REFERENCES_IMAGE,
    "templates_to": EdgeRelation.TEMPLATES_TO,
    "value_of": EdgeRelation.VALUE_OF,
    "builds_image": EdgeRelation.BUILDS_IMAGE,
    "exposes_port": EdgeRelation.EXPOSES_PORT,
    "image_of": EdgeRelation.IMAGE_OF,
    "copies_from": EdgeRelation.COPIES_FROM,
    "contains": EdgeRelation.CONTAINS,
    "depends_on": EdgeRelation.DEPENDS_ON,
    # Ansible
    "runs_task": EdgeRelation.RUNS_TASK,
    "uses_role": EdgeRelation.USES_ROLE,
    "notifies_handler": EdgeRelation.NOTIFIES_HANDLER,
    "imports_playbook": EdgeRelation.IMPORTS_PLAYBOOK,
    # CI/CD
    "triggers": EdgeRelation.TRIGGERS,
    "uses_template": EdgeRelation.USES_TEMPLATE,
    # Cross-domain (Phase 5)
    "deploys_via": EdgeRelation.DEPLOYS_VIA,
    "renders_to": EdgeRelation.RENDERS_TO,
    # Terraform
    "provisions": EdgeRelation.PROVISIONS,
}


class IaCGraphBuilder:
    """Convert ``IaCGraph`` results into ``CodeGraph`` nodes and edges.

    Usage::

        builder = IaCGraphBuilder()
        code_graph = builder.build(iac_graphs)
    """

    def build(self, iac_graphs: list[IaCGraph]) -> CodeGraph:  # noqa: C901
        """Build a CodeGraph from all IaC extraction results.

        Args:
            iac_graphs: Results from all IaC extractors.

        Returns:
            A ``CodeGraph`` containing IaC nodes and edges only.
        """
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        seen: set[str] = set()

        for iac_graph in iac_graphs:
            name_to_id: dict[str, str] = {}

            for resource in iac_graph.resources:
                node_kind = _IAC_KIND_MAP.get(
                    resource.kind, NodeKind.K8S_GENERIC,
                )
                prefix = _KIND_PREFIX_MAP.get(resource.kind, "k8s")
                node_id = _iac_node_id(
                    resource.file, resource.kind, resource.name,
                    prefix=prefix,
                )

                if node_id in seen:
                    continue
                seen.add(node_id)
                name_to_id[resource.name] = node_id

                # Build properties dict
                props: dict[str, str] = {
                    str(k): str(v) for k, v in resource.properties.items()
                }
                if resource.namespace:
                    props["namespace"] = resource.namespace
                if resource.api_version:
                    props["apiVersion"] = resource.api_version
                if resource.labels:
                    props["labels"] = ",".join(
                        f"{k}={v}"
                        for k, v in sorted(resource.labels.items())
                    )
                if resource.selector:
                    props["selector"] = ",".join(
                        f"{k}={v}"
                        for k, v in sorted(resource.selector.items())
                    )
                props["k8s_kind"] = resource.kind

                node = GraphNode(
                    id=node_id,
                    label=resource.name,
                    kind=node_kind,
                    file=resource.file,
                    span=resource.span,
                    properties=props,
                )
                nodes.append(node)

            # Convert IaCEdges to GraphEdges
            for iac_edge in iac_graph.edges:
                source_id = name_to_id.get(iac_edge.source_name)
                target_id = name_to_id.get(iac_edge.target_name)
                if source_id is None or target_id is None:
                    logger.debug(
                        "Skipping edge %s→%s: unresolved names",
                        iac_edge.source_name,
                        iac_edge.target_name,
                    )
                    continue

                relation = _EDGE_RELATION_MAP.get(iac_edge.relation)
                if relation is None:
                    logger.debug(
                        "Unknown IaC edge relation: %s",
                        iac_edge.relation,
                    )
                    continue

                edges.append(
                    GraphEdge(
                        source=source_id,
                        target=target_id,
                        relation=relation,
                        confidence=Confidence.EXTRACTED,
                        confidence_score=SCORE_EXTRACTED,
                        file=iac_graph.file,
                    ),
                )

        logger.info(
            "IaC graph built: %d nodes, %d edges",
            len(nodes),
            len(edges),
        )
        graph = CodeGraph(nodes=nodes, edges=edges)
        self._classify_cloud_resources(graph)
        return graph

    def _classify_cloud_resources(self, graph: CodeGraph) -> None:
        """Post-pass: promote TF_RESOURCE / Bicep nodes to CLOUD_RESOURCE.

        For each IaC node whose resource_type maps to a known cloud service
        via the taxonomy, emit a paired CLOUD_RESOURCE node + PROVISIONS edge.
        """
        new_nodes: list[GraphNode] = []
        new_edges: list[GraphEdge] = []
        seen: set[str] = set()

        for node in graph.nodes:
            if node.kind not in (NodeKind.TF_RESOURCE, NodeKind.CLOUD_RESOURCE):
                continue
            # Already a CLOUD_RESOURCE (e.g. from Bicep extractor) — skip
            if node.kind == NodeKind.CLOUD_RESOURCE:
                continue
            resource_type = node.properties.get("resource_type", "")
            if not resource_type:
                continue
            info = classify_iac_type(resource_type)
            if info is None:
                continue
            cr_id = f"cloud://{node.file}::{info.category}/{node.label}"
            if cr_id in seen:
                continue
            seen.add(cr_id)
            new_nodes.append(
                GraphNode(
                    id=cr_id,
                    label=f"{info.service} ({node.label})",
                    kind=NodeKind.CLOUD_RESOURCE,
                    file=node.file,
                    span=node.span,
                    properties={
                        "provider": info.provider,
                        "service": info.service,
                        "category": info.category,
                        "name": node.label,
                        "source": "iac",
                        "iac_type": resource_type,
                        "client": "",
                        "caller": "",
                    },
                ),
            )
            new_edges.append(
                GraphEdge(
                    source=node.id,
                    target=cr_id,
                    relation=EdgeRelation.PROVISIONS,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=node.file,
                    span=node.span,
                ),
            )

        if new_nodes:
            graph.nodes.extend(new_nodes)
            graph.edges.extend(new_edges)
            logger.info(
                "Classified %d TF_RESOURCE → CLOUD_RESOURCE nodes",
                len(new_nodes),
            )
