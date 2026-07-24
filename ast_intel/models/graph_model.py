"""Graph data model — nodes, edges, and the full code knowledge graph.

These dataclasses represent an explicit graph view of a codebase where
**relationships are first-class edges**.  The graph is built from a
fully indexed ``WorkspaceAST`` by :class:`~ast_intel.core.graph_builder.GraphBuilder`.

Output formats:
- **JSON graph** (``graph.json``) — programmatic consumption / MCP servers
- **DOT** (``graph.dot``) — Graphviz rendering → SVG / PNG
- **Mermaid** (``graph.mermaid.md``) — embeddable in GitHub READMEs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ast_intel.models.ast_node import Confidence, Span
    from ast_intel.models.workspace_model import WorkspaceMeta

__all__: list[str] = [
    "RELATION_BELONGS_TO",
    "RELATION_BUILDS_IMAGE",
    "RELATION_CALLS",
    "RELATION_CALLS_HTTP",
    "RELATION_CALLS_SERVICE",
    "RELATION_CONFIGURES",
    "RELATION_CONTAINS",
    "RELATION_COPIES_FROM",
    "RELATION_DEPENDS_ON",
    "RELATION_DEPLOYS",
    "RELATION_DEPLOYS_VIA",
    "RELATION_EXPOSES",
    "RELATION_EXPOSES_PORT",
    "RELATION_HANDLES",
    "RELATION_HAS_FIELD",
    "RELATION_IMAGE_OF",
    "RELATION_IMPLEMENTS",
    "RELATION_IMPORTS",
    "RELATION_IMPORTS_PLAYBOOK",
    "RELATION_INHERITS",
    "RELATION_METHOD_OF",
    "RELATION_NOTIFIES_HANDLER",
    "RELATION_PROVISIONS",
    "RELATION_RATIONALE_FOR",
    "RELATION_REFERENCES_IMAGE",
    "RELATION_RENDERS_TO",
    "RELATION_RESOLVES_TO",
    "RELATION_ROUTES_TO",
    "RELATION_RUNS_TASK",
    "RELATION_SENDS_DATA",
    "RELATION_SIMILAR_TO",
    "RELATION_SUPER_TRAIT",
    "RELATION_TEMPLATES_TO",
    "RELATION_TRIGGERS",
    "RELATION_USES_METHOD",
    "RELATION_USES_ROLE",
    "RELATION_USES_SECRET",
    "RELATION_USES_TEMPLATE",
    "RELATION_VALUE_OF",
    "CodeGraph",
    "EdgeRelation",
    "GraphEdge",
    "GraphNode",
    "HyperEdge",
    "HyperRelation",
    "NodeKind",
    "compute_impact_score",
]


# ---------------------------------------------------------------------------
# region:    --- Enumerations
# ---------------------------------------------------------------------------


class NodeKind(StrEnum):
    """Category of a node in the code knowledge graph.

    Using a StrEnum guarantees that only valid kind values can be
    assigned to ``GraphNode.kind``, while remaining JSON-serializable
    as plain strings.
    """

    CRATE = "crate"
    FILE = "file"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    FUNCTION = "function"
    METHOD = "method"
    IMPL_BLOCK = "impl_block"
    TYPE_ALIAS = "type_alias"
    CONSTANT = "constant"
    MACRO = "macro"
    MODULE = "module"
    RATIONALE = "rationale"
    IMPORT = "import"
    EXTERNAL_METHOD = "external_method"
    ROUTE = "route"
    HTTP_CALL = "http_call"
    SDK_CALL = "sdk_call"
    SERVICE = "service"

    # --- IaC: Kubernetes ---
    K8S_DEPLOYMENT = "k8s_deployment"
    """Kubernetes Deployment / StatefulSet / DaemonSet resource."""

    K8S_SERVICE = "k8s_service"
    """Kubernetes Service resource (network endpoint abstraction)."""

    K8S_CONFIGMAP = "k8s_configmap"
    """Kubernetes ConfigMap resource."""

    K8S_SECRET = "k8s_secret"
    """Kubernetes Secret resource."""

    K8S_INGRESS = "k8s_ingress"
    """Kubernetes Ingress / IngressRoute resource."""

    K8S_NAMESPACE = "k8s_namespace"
    """Kubernetes Namespace resource."""

    K8S_GENERIC = "k8s_generic"
    """Catch-all for other Kubernetes resource kinds."""

    K8S_CRONJOB = "k8s_cronjob"
    """Kubernetes CronJob resource (scheduled job execution)."""

    K8S_JOB = "k8s_job"
    """Kubernetes Job resource (run-to-completion workload)."""

    K8S_PVC = "k8s_pvc"
    """Kubernetes PersistentVolumeClaim resource (storage request)."""

    K8S_RBAC = "k8s_rbac"
    """Kubernetes RBAC resource (Role, ClusterRole, RoleBinding, ClusterRoleBinding)."""

    # --- IaC: Helm ---
    HELM_CHART = "helm_chart"
    """Helm chart definition (from Chart.yaml)."""

    HELM_VALUE = "helm_value"
    """A resolved Helm value key path (e.g. approvalEngine.replicas)."""

    HELM_TEMPLATE = "helm_template"
    """A single Helm template file."""

    # --- IaC: Docker ---
    DOCKER_IMAGE = "docker_image"
    """Docker image (FROM line or docker-compose image reference)."""

    DOCKER_STAGE = "docker_stage"
    """Named stage in a multi-stage Docker build (FROM x AS name)."""

    DOCKER_SERVICE = "docker_service"
    """docker-compose service definition."""

    # --- IaC: Ansible ---
    ANSIBLE_PLAYBOOK = "ansible_playbook"
    """An Ansible playbook file (YAML with hosts + tasks/roles)."""

    ANSIBLE_TASK = "ansible_task"
    """A single Ansible task (module invocation within a playbook/role)."""

    ANSIBLE_ROLE = "ansible_role"
    """An Ansible role reference (directory-based reusable automation unit)."""

    # --- IaC: CI/CD ---
    CI_PIPELINE = "ci_pipeline"
    """CI/CD pipeline definition (Azure Pipelines workflow or GitHub Actions workflow)."""

    CI_STAGE = "ci_stage"
    """CI/CD pipeline stage (a logical grouping of jobs)."""

    CI_JOB = "ci_job"
    """CI/CD pipeline job (a unit of work within a stage)."""

    CI_STEP = "ci_step"
    """CI/CD pipeline step (a single command, script, or task)."""

    # --- IaC: Terraform ---
    TF_RESOURCE = "tf_resource"
    """Terraform resource (e.g. aws_ecs_service.app, azurerm_linux_virtual_machine)."""

    TF_DATA = "tf_data"
    """Terraform data source (read-only external data reference)."""

    TF_MODULE = "tf_module"
    """Terraform module reference (reusable infrastructure component)."""

    TF_VARIABLE = "tf_variable"
    """Terraform input variable declaration."""

    TF_OUTPUT = "tf_output"
    """Terraform output value declaration."""

    TF_PROVIDER = "tf_provider"
    """Terraform provider configuration (aws, azurerm, google, etc.)."""

    # --- Cloud Infrastructure ---
    CLOUD_RESOURCE = "cloud_resource"
    """Cloud infrastructure resource (database, cache, queue, storage, secret, etc.).

    Properties: provider, service, category, name, source (code|iac), client, caller, operation.
    Detected from application code (SDK client instantiation) or classified from IaC.
    """


class EdgeRelation(StrEnum):
    """Type of a directed edge in the code knowledge graph.

    Using a StrEnum ensures exhaustiveness checking with ``match``
    statements and IDE autocomplete, while staying backward-compatible
    (``StrEnum`` values compare equal to their plain string).
    """

    CONTAINS = "contains"
    """File → symbol (struct, function, enum, trait, etc.)."""

    METHOD_OF = "method_of"
    """Method → impl block / class."""

    IMPLEMENTS = "implements"
    """Type → trait (explicit ``impl Trait for Type``)."""

    INHERITS = "inherits"
    """Subclass → base class (Python heuristic or explicit syntax)."""

    IMPORTS = "imports"
    """File → module / package (``use`` / ``import`` statement)."""

    CALLS = "calls"
    """Function → function (call edge from Feature 2)."""

    USES_METHOD = "uses_method"
    """File → package method (``bytes::BytesMut::freeze``, etc.)."""

    DEPENDS_ON = "depends_on"
    """Crate → crate (manifest dependency)."""

    SUPER_TRAIT = "super_trait"
    """Trait → parent trait (``trait Sub: Super``)."""

    RATIONALE_FOR = "rationale_for"
    """Rationale comment → enclosing symbol (from Feature 4)."""

    HAS_FIELD = "has_field"
    """Struct → known struct type referenced by a field."""

    RESOLVES_TO = "resolves_to"
    """Import node → actual definition (cross-crate symbol resolution)."""

    SIMILAR_TO = "similar_to"
    """Structurally similar symbols (Jaccard heuristic)."""

    HANDLES = "handles"
    """Route → handler function (HTTP endpoint dispatches to this function)."""

    EXPOSES = "exposes"
    """File → route (this file defines the HTTP endpoint)."""

    CALLS_HTTP = "calls_http"
    """Function → HTTP client call (outgoing HTTP request)."""

    SENDS_DATA = "sends_data"
    """Function → SDK egress call (sends data to a third-party SDK)."""

    BELONGS_TO = "belongs_to"
    """File/symbol → SERVICE node (assigned during graph merge)."""

    CALLS_SERVICE = "calls_service"
    """Cross-service call: HTTP client call → HTTP route (resolved by URL matching)."""
    # --- IaC: Kubernetes ---
    ROUTES_TO = "routes_to"
    """K8S_SERVICE / K8S_INGRESS \u2192 K8S_DEPLOYMENT (network routing via label selectors)."""

    CONFIGURES = "configures"
    """K8S_CONFIGMAP \u2192 K8S_DEPLOYMENT (provides configuration)."""

    USES_SECRET = "uses_secret"
    """K8S_DEPLOYMENT \u2192 K8S_SECRET (mounts or references a secret)."""

    DEPLOYS = "deploys"
    """K8S_DEPLOYMENT \u2192 DOCKER_IMAGE (runs this container image)."""

    REFERENCES_IMAGE = "references_image"
    """K8S_DEPLOYMENT \u2192 container image name (image reference)."""

    # --- IaC: Helm ---
    TEMPLATES_TO = "templates_to"
    """HELM_TEMPLATE \u2192 K8S_DEPLOYMENT/SERVICE (template renders to K8s resource)."""

    VALUE_OF = "value_of"
    """HELM_VALUE \u2192 HELM_TEMPLATE (value is consumed by this template)."""

    # --- IaC: Docker ---
    BUILDS_IMAGE = "builds_image"
    """DOCKER_STAGE \u2192 DOCKER_IMAGE (build stage produces this image)."""

    EXPOSES_PORT = "exposes_port"
    """DOCKER_STAGE/DOCKER_SERVICE \u2192 port (container exposes this port)."""

    IMAGE_OF = "image_of"
    """DOCKER_IMAGE \u2192 SERVICE (image packages this code service)."""

    COPIES_FROM = "copies_from"
    """DOCKER_STAGE \u2192 DOCKER_STAGE (COPY --from=stage inter-stage dependency)."""

    # --- IaC: Ansible ---
    RUNS_TASK = "runs_task"
    """ANSIBLE_PLAYBOOK \u2192 ANSIBLE_TASK (playbook contains and runs this task)."""

    USES_ROLE = "uses_role"
    """ANSIBLE_PLAYBOOK \u2192 ANSIBLE_ROLE (playbook includes this role)."""

    NOTIFIES_HANDLER = "notifies_handler"
    """ANSIBLE_TASK \u2192 ANSIBLE_TASK (task triggers a handler via notify)."""

    IMPORTS_PLAYBOOK = "imports_playbook"
    """ANSIBLE_PLAYBOOK \u2192 ANSIBLE_PLAYBOOK (import_playbook / include_playbook)."""

    # --- IaC: CI/CD ---
    TRIGGERS = "triggers"
    """CI_PIPELINE \u2192 CI_PIPELINE (pipeline resource trigger)."""

    USES_TEMPLATE = "uses_template"
    """CI node \u2192 CI node (template reference / reusable workflow)."""
    DEPLOYS_VIA = "deploys_via"
    """CI_STEP / ANSIBLE_TASK → HELM_CHART / K8S_DEPLOYMENT (deploys via command)."""

    RENDERS_TO = "renders_to"
    """HELM_TEMPLATE → K8S_* (template renders to a specific named K8s resource)."""

    # --- IaC: Terraform ---
    PROVISIONS = "provisions"
    """TF_RESOURCE / IaC → CLOUD_RESOURCE / K8S_SERVICE / DOCKER_SERVICE (creates infra for)."""

    # --- Cloud Infrastructure ---
    USES_RESOURCE = "uses_resource"
    """Function/method → CLOUD_RESOURCE (code uses this cloud infrastructure)."""

    BACKED_BY = "backed_by"
    """CLOUD_RESOURCE (code-detected) → CLOUD_RESOURCE (IaC-provisioned) (same logical resource)."""
# Backward-compatible module-level aliases — kept so existing imports
# (``from .graph_model import RELATION_CALLS``) continue to work.
RELATION_CONTAINS: str = EdgeRelation.CONTAINS
RELATION_METHOD_OF: str = EdgeRelation.METHOD_OF
RELATION_IMPLEMENTS: str = EdgeRelation.IMPLEMENTS
RELATION_INHERITS: str = EdgeRelation.INHERITS
RELATION_IMPORTS: str = EdgeRelation.IMPORTS
RELATION_CALLS: str = EdgeRelation.CALLS
RELATION_USES_METHOD: str = EdgeRelation.USES_METHOD
RELATION_DEPENDS_ON: str = EdgeRelation.DEPENDS_ON
RELATION_SUPER_TRAIT: str = EdgeRelation.SUPER_TRAIT
RELATION_RATIONALE_FOR: str = EdgeRelation.RATIONALE_FOR
RELATION_HAS_FIELD: str = EdgeRelation.HAS_FIELD
RELATION_RESOLVES_TO: str = EdgeRelation.RESOLVES_TO
RELATION_SIMILAR_TO: str = EdgeRelation.SIMILAR_TO
RELATION_HANDLES: str = EdgeRelation.HANDLES
RELATION_EXPOSES: str = EdgeRelation.EXPOSES
RELATION_CALLS_HTTP: str = EdgeRelation.CALLS_HTTP
RELATION_SENDS_DATA: str = EdgeRelation.SENDS_DATA
RELATION_BELONGS_TO: str = EdgeRelation.BELONGS_TO
RELATION_CALLS_SERVICE: str = EdgeRelation.CALLS_SERVICE
RELATION_ROUTES_TO: str = EdgeRelation.ROUTES_TO
RELATION_CONFIGURES: str = EdgeRelation.CONFIGURES
RELATION_USES_SECRET: str = EdgeRelation.USES_SECRET
RELATION_DEPLOYS: str = EdgeRelation.DEPLOYS
RELATION_REFERENCES_IMAGE: str = EdgeRelation.REFERENCES_IMAGE
RELATION_TEMPLATES_TO: str = EdgeRelation.TEMPLATES_TO
RELATION_VALUE_OF: str = EdgeRelation.VALUE_OF
RELATION_BUILDS_IMAGE: str = EdgeRelation.BUILDS_IMAGE
RELATION_EXPOSES_PORT: str = EdgeRelation.EXPOSES_PORT
RELATION_IMAGE_OF: str = EdgeRelation.IMAGE_OF
RELATION_COPIES_FROM: str = EdgeRelation.COPIES_FROM
RELATION_RUNS_TASK: str = EdgeRelation.RUNS_TASK
RELATION_USES_ROLE: str = EdgeRelation.USES_ROLE
RELATION_NOTIFIES_HANDLER: str = EdgeRelation.NOTIFIES_HANDLER
RELATION_IMPORTS_PLAYBOOK: str = EdgeRelation.IMPORTS_PLAYBOOK
RELATION_TRIGGERS: str = EdgeRelation.TRIGGERS
RELATION_USES_TEMPLATE: str = EdgeRelation.USES_TEMPLATE
RELATION_DEPLOYS_VIA: str = EdgeRelation.DEPLOYS_VIA
RELATION_PROVISIONS: str = EdgeRelation.PROVISIONS
RELATION_RENDERS_TO: str = EdgeRelation.RENDERS_TO

# endregion: --- Enumerations


# ---------------------------------------------------------------------------
# region:    --- Graph Node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphNode:
    """A node in the code knowledge graph.

    Node IDs follow the scheme ``"{file_path}::{symbol_name}"`` —
    deterministic, human-readable, and stable across runs.

    Attributes:
        id: Unique, stable identifier (e.g., ``"src/parser.py::Parser"``).
        label: Human-readable display name.
        kind: Node category — one of the :class:`NodeKind` enum values.
        file: Workspace-relative source file path.
        span: Source location (from Feature 1), or ``None``.
        properties: Extra metadata (visibility, async, generics, etc.).
    """

    id: str
    label: str
    kind: NodeKind
    file: str = ""
    span: Span | None = None
    properties: dict[str, str] = field(default_factory=dict)
    service: str = ""
    origin_id: str = ""


def compute_impact_score(node: GraphNode) -> int:
    """Compute an impact score for a node by counting lines in its span.

    Returns the number of source lines the node covers (1-based, inclusive),
    or 1 when the node has no span.
    """
    if node.span:
        return max(node.span.end_line - node.span.start_line + 1, 1)
    return 1


# endregion: --- Graph Node


# ---------------------------------------------------------------------------
# region:    --- Graph Edge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed edge in the code knowledge graph.

    Carries confidence metadata from Feature 3 to allow consumers
    to filter or weight edges by extraction quality.

    Attributes:
        source: Source node ID.
        target: Target node ID.
        relation: Edge type — one of the :class:`EdgeRelation` enum values.
        confidence: How the relationship was determined.
        confidence_score: Numeric confidence in ``[0.0, 1.0]``.
        file: Workspace-relative file where this relationship was found.
        span: Call-site or import line location, or ``None``.
    """

    source: str
    target: str
    relation: EdgeRelation
    confidence: Confidence
    confidence_score: float
    file: str = ""
    span: Span | None = None
    properties: dict[str, str] = field(default_factory=dict)


# endregion: --- Graph Edge


# ---------------------------------------------------------------------------
# region:    --- Hyperedges
# ---------------------------------------------------------------------------


class HyperRelation(StrEnum):
    """Type of a hyperedge group relationship."""

    IMPLEMENTS_GROUP = "implements_group"
    FLOW = "flow"
    ROUTE_GROUP = "route_group"
    FIELD_GROUP = "field_group"
    COMMUNITY = "community"


@dataclass(slots=True)
class HyperEdge:
    """A group relationship connecting multiple nodes.

    Unlike binary edges, a hyperedge represents a shared property
    or ordered sequence involving 3+ nodes.

    Attributes:
        id: Unique deterministic identifier.
        relation: The kind of group relationship.
        members: Ordered list of node IDs participating.
        label: Human-readable description.
        metadata: Extra key-value pairs.
    """

    id: str
    relation: HyperRelation
    members: list[str]
    label: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


# endregion: --- Hyperedges


# ---------------------------------------------------------------------------
# region:    --- Code Graph
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CodeGraph:
    """The full code knowledge graph for a workspace.

    Built by :class:`~ast_intel.core.graph_builder.GraphBuilder` from a
    fully indexed ``WorkspaceAST``.

    Attributes:
        nodes: All nodes in the graph.
        edges: All directed edges in the graph.
        meta: Workspace analysis metadata.
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    meta: WorkspaceMeta | None = None
    hyperedges: list[HyperEdge] = field(default_factory=list)


# endregion: --- Code Graph
