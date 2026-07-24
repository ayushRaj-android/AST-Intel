# AST_INTEL — Infrastructure-as-Code (IaC) Indexing

> Generated 2026-05-07 · Architect Document
> Prerequisite: All code extractors (Python, TS, Rust, C#, Go, C/C++, Java), graph merge, cross-service edge resolution, and MCP server are **complete**.

---

## Executive Summary

This plan adds IaC indexing to AST_INTEL, turning it from a code-only scanner into a **full-stack architecture scanner**. The design introduces a new `IaCExtractorBase` (parallel to the existing `ExtractorBase`), new IaC-specific `NodeKind`/`EdgeRelation` enums, a cross-domain edge resolver, and modified file discovery — all integrated into the existing `scan` pipeline with zero disruption to current code-graph functionality.

**Target audience**: DevOps & platform engineering teams.
**Killer feature**: Cross-domain edges linking K8s Services to code ROUTE nodes, Docker images to code SERVICE nodes, Helm values to deployment configs.

---

## Table of Contents

1. [Scope](#scope)
2. [New Model Types](#1-new-model-types)
3. [IaC Extractor Design](#2-iac-extractor-design)
4. [Cross-Domain Edge Resolution](#3-cross-domain-edge-resolution)
5. [File Discovery Changes](#4-file-discovery-changes)
6. [Phased Implementation Plan](#5-phased-implementation-plan)
7. [Visualization](#6-visualization)
8. [Dependencies & File Layout](#7-dependencies--file-layout)
9. [Key Design Decisions](#8-key-design-decisions)

---

## Scope

| Format | Example Files |
|--------|--------------|
| **Kubernetes manifests** | Deployment, Service, ConfigMap, Ingress, Secret |
| **Helm charts** | Chart.yaml + values.yaml + templates with `{{ .Values.x }}` |
| **Dockerfile / docker-compose** | Multi-stage builds, compose services |
| **Terraform** | `.tf` HCL files (resources, modules, variables, outputs) |
| **Ansible playbooks** | Playbooks, roles, tasks, variable files |
| **CI/CD Pipelines** | GitHub Actions workflows, Azure Pipelines YAML |

---

## 1. New Model Types

### 1.1 New NodeKinds

Add to `NodeKind` in `graph_model.py`:

| NodeKind | Value | Description |
|---|---|---|
| `K8S_DEPLOYMENT` | `"k8s_deployment"` | Kubernetes Deployment resource |
| `K8S_SERVICE` | `"k8s_service"` | Kubernetes Service resource (network endpoint abstraction) |
| `K8S_CONFIGMAP` | `"k8s_configmap"` | Kubernetes ConfigMap |
| `K8S_SECRET` | `"k8s_secret"` | Kubernetes Secret resource |
| `K8S_INGRESS` | `"k8s_ingress"` | Kubernetes Ingress / IngressRoute |
| `K8S_NAMESPACE` | `"k8s_namespace"` | Kubernetes Namespace |
| `K8S_GENERIC` | `"k8s_generic"` | Catch-all for PVC, CronJob, DaemonSet, StatefulSet, RBAC, etc. |
| `HELM_CHART` | `"helm_chart"` | Helm chart (from Chart.yaml) |
| `HELM_VALUE` | `"helm_value"` | A resolved Helm value key path (e.g., `approvalEngine.replicas`) |
| `HELM_TEMPLATE` | `"helm_template"` | A single Helm template file |
| `DOCKER_IMAGE` | `"docker_image"` | Docker image (FROM line or docker-compose service image) |
| `DOCKER_STAGE` | `"docker_stage"` | Multi-stage Docker build stage |
| `DOCKER_SERVICE` | `"docker_service"` | docker-compose service definition |
| `TF_RESOURCE` | `"tf_resource"` | Terraform resource (e.g., `aws_ecs_service.app`) |
| `TF_DATA` | `"tf_data"` | Terraform data source |
| `TF_MODULE` | `"tf_module"` | Terraform module reference |
| `TF_VARIABLE` | `"tf_variable"` | Terraform input variable |
| `TF_OUTPUT` | `"tf_output"` | Terraform output |
| `TF_PROVIDER` | `"tf_provider"` | Terraform provider (aws, azurerm, etc.) |
| `ANSIBLE_PLAYBOOK` | `"ansible_playbook"` | Ansible playbook |
| `ANSIBLE_ROLE` | `"ansible_role"` | Ansible role |
| `ANSIBLE_TASK` | `"ansible_task"` | Individual Ansible task |
| `CI_PIPELINE` | `"ci_pipeline"` | CI/CD pipeline definition (GitHub Actions / Azure Pipelines) |
| `CI_JOB` | `"ci_job"` | A job/stage within a pipeline |
| `CI_STEP` | `"ci_step"` | An individual step within a job |

**Rationale**: K8s gets individual kinds for Deployment/Service/Ingress/ConfigMap/Secret because these produce meaningful cross-domain edges. Others go into `K8S_GENERIC`.

### 1.2 New EdgeRelations

| EdgeRelation | Value | Source → Target | Description |
|---|---|---|---|
| `DEPLOYS` | `"deploys"` | K8S_DEPLOYMENT → DOCKER_IMAGE | Deployment runs this image |
| `CONFIGURES` | `"configures"` | K8S_CONFIGMAP / HELM_VALUE → K8S_DEPLOYMENT | Provides configuration to |
| `ROUTES_TO` | `"routes_to"` | K8S_SERVICE / K8S_INGRESS → K8S_DEPLOYMENT | Network routing |
| `REFERENCES_IMAGE` | `"references_image"` | DOCKER_SERVICE / K8S_DEPLOYMENT → DOCKER_IMAGE | Uses this container image |
| `BUILDS_IMAGE` | `"builds_image"` | CI_STEP → DOCKER_IMAGE | CI step builds/pushes this image |
| `PROVISIONS` | `"provisions"` | TF_RESOURCE → K8S_SERVICE / DOCKER_SERVICE | Terraform creates infra for |
| `EXPOSES_PORT` | `"exposes_port"` | K8S_SERVICE → ROUTE | **Cross-domain**: K8s port maps to code HTTP route |
| `IMAGE_OF` | `"image_of"` | DOCKER_IMAGE → SERVICE | **Cross-domain**: Container image packages this service |
| `DEPLOYS_CHART` | `"deploys_chart"` | ANSIBLE_TASK → HELM_CHART | Ansible deploys Helm chart |
| `TEMPLATES_TO` | `"templates_to"` | HELM_TEMPLATE → K8S_DEPLOYMENT/SERVICE/etc. | Helm template renders to K8s resource |
| `VALUE_OF` | `"value_of"` | HELM_VALUE → HELM_TEMPLATE | Value is consumed by template |
| `TRIGGERS` | `"triggers"` | CI_PIPELINE → CI_JOB | Pipeline triggers job |
| `USES_SECRET` | `"uses_secret"` | K8S_DEPLOYMENT → K8S_SECRET | Mounts/references secret |

### 1.3 Data Model Extensions

**No new top-level graph dataclasses needed.** `GraphNode.kind` accepts new enum values, `GraphNode.properties` and `GraphEdge.properties` carry IaC-specific metadata.

**New intermediate dataclasses** (in `ast_intel/models/iac_model.py`):

```python
@dataclass(slots=True)
class IaCResource:
    """A single parsed IaC resource — intermediate form before graph emission."""
    kind: str                      # "Deployment", "Service", "helm_chart", etc.
    name: str                      # Resource name
    namespace: str = ""            # K8s namespace
    file: str = ""                 # Workspace-relative file path
    span: Span | None = None
    labels: dict[str, str] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    children: list[IaCResource] = field(default_factory=list)

@dataclass(slots=True)
class IaCGraph:
    """Intermediate container for IaC extraction results (one per file/chart)."""
    resources: list[IaCResource] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
```

---

## 2. IaC Extractor Design

### 2.1 New Base Class: `IaCExtractorBase`

IaC extractors **do not** reuse `ExtractorBase`. Reasons:
- `ExtractorBase.extract()` returns `FileAST` — a code-centric model
- IaC extractors don't use tree-sitter; they use YAML/HCL parsers
- IaC files are themselves the "manifests" (no separate manifest parsing)

```python
# ast_intel/extractors/iac_base.py
class IaCExtractorBase(ABC):
    """Base class for Infrastructure-as-Code extractors."""

    format_id: str                    # "kubernetes", "helm", "docker", etc.
    file_patterns: list[str]          # Glob patterns, not just extensions
    file_extensions: list[str]        # Simple extension match

    @abstractmethod
    def extract(self, file_path: Path, source: bytes, context: IaCContext) -> IaCGraph:
        """Parse an IaC file and return resources + intra-file edges."""

    @abstractmethod
    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:
        """Heuristic check: can this extractor parse this file?

        Needed because many IaC files share .yaml/.yml extensions.
        Peek at first 1KB to check for kind:/apiVersion: (K8s),
        stages:/trigger: (Azure Pipelines), etc.
        """
```

### 2.2 Per-Format Details

#### 2.2.1 Kubernetes Manifests

| Aspect | Detail |
|---|---|
| **Parser** | `PyYAML` (`yaml.safe_load_all` for multi-document) |
| **File detection** | `.yaml`/`.yml` with `apiVersion:` + `kind:` in first 1KB |
| **Nodes** | K8S_DEPLOYMENT, K8S_SERVICE, K8S_CONFIGMAP, K8S_SECRET, K8S_INGRESS, K8S_NAMESPACE, K8S_GENERIC |
| **Edges** | ROUTES_TO (Service→Deployment via label selectors), CONFIGURES (ConfigMap→Deployment), USES_SECRET, DEPLOYS (Deployment→image), REFERENCES_IMAGE |
| **Properties** | namespace, labels, selector, ports, image, replicas, containerPort, servicePort, targetPort |
| **Key challenge** | Multi-doc YAML. Label selector matching for Service→Deployment. Port chain: Service.port → targetPort → containerPort |

**Safeguard example**: `approval-engine.yaml` contains Deployment + Service. Service selects `app: approval-engine`, containerPort 8080 maps to health endpoint.

#### 2.2.2 Helm Charts

| Aspect | Detail |
|---|---|
| **Parser** | `PyYAML` for Chart.yaml/values.yaml. Template files: **regex** extraction of `{{ .Values.x.y }}` references (no Go template rendering) |
| **File detection** | Directory containing `Chart.yaml`. Templates in `templates/` subdirectory |
| **Nodes** | HELM_CHART, HELM_TEMPLATE, HELM_VALUE (each leaf in values.yaml) |
| **Edges** | CONTAINS (chart→template), VALUE_OF (value→template), TEMPLATES_TO (template→K8s resource) |
| **Key challenge** | Go template syntax `{{ }}` mixed with YAML breaks standard parsers. Solution: extract `{{ .Values.X.Y }}` via regex; parse values.yaml as pure YAML separately |

**Safeguard example**: Chart `safeguard` v1.0.0. `values.yaml` has `approvalEngine.config.webSettings.httpListenPort`. `_helpers.tpl` defines `safeguard.namespace`, `safeguard.labels`.

#### 2.2.3 Dockerfile / docker-compose

| Aspect | Detail |
|---|---|
| **Parser** | Dockerfile: line-by-line regex (FROM, COPY, RUN, EXPOSE, etc.). docker-compose: `PyYAML` |
| **File detection** | Filenames matching `Dockerfile*` or `docker-compose*.yml/yaml` |
| **Nodes** | DOCKER_IMAGE, DOCKER_STAGE, DOCKER_SERVICE |
| **Edges** | CONTAINS (stage→instructions as properties), REFERENCES_IMAGE, inter-stage edges for multi-stage builds |
| **Key challenge** | Build ARGs make image names dynamic. Multi-stage `COPY --from=build` creates inter-stage deps |

**Safeguard example**: Multi-stage build. `ubuntu:24.04` base, `ARG services` parameterizes which services are built.

#### 2.2.4 Terraform (.tf)

| Aspect | Detail |
|---|---|
| **Parser** | `python-hcl2` (parses HCL2 to Python dicts) |
| **File detection** | `.tf` extension |
| **Nodes** | TF_RESOURCE, TF_DATA, TF_MODULE, TF_VARIABLE, TF_OUTPUT, TF_PROVIDER |
| **Edges** | DEPENDS_ON (explicit), PROVISIONS (resource→service), implicit reference edges via `${resource.name.attr}` |
| **Key challenge** | HCL2 expression interpolation. `for_each`/`count` produce multiple instances. Module references need recursive resolution |

#### 2.2.5 Ansible Playbooks

| Aspect | Detail |
|---|---|
| **Parser** | `PyYAML` |
| **File detection** | `.yml`/`.yaml` with `hosts:` at top level, or files under `roles/` |
| **Nodes** | ANSIBLE_PLAYBOOK, ANSIBLE_ROLE, ANSIBLE_TASK |
| **Edges** | CONTAINS (playbook→task), DEPLOYS_CHART (helm module→chart), CALLS (import_playbook) |
| **Key challenge** | Jinja2 in values. Role directory conventions. `import_playbook` vs `include_playbook`. Variable files under `group_vars/`/`host_vars/` |

**Safeguard example**: `site.yml` imports 11 sub-playbooks. `install-safeguard.yml` uses Helm to deploy the chart.

#### 2.2.6 CI/CD Pipelines

| Aspect | Detail |
|---|---|
| **Parser** | `PyYAML` |
| **File detection** | `.github/workflows/*.yml` (GHA) or `azure-pipelines.yml`/`.pipelines/*.yml` with `trigger:`/`stages:` |
| **Nodes** | CI_PIPELINE, CI_JOB, CI_STEP |
| **Edges** | TRIGGERS (pipeline→job), CONTAINS (job→step), BUILDS_IMAGE (docker step→image) |
| **Key challenge** | Two different YAML schemas (GHA vs Azure). Detecting docker build/push in script steps. Azure Pipelines template references |

---

## 3. Cross-Domain Edge Resolution

The **killer feature** — linking IaC nodes to code nodes. Follows the pattern established by `_url_matcher.py`: runs as a **post-processing pass** after both code and IaC graphs are built.

New module: `ast_intel/core/_iac_resolver.py`

### 3.1 K8s Service Ports → Code ROUTE Nodes

1. Collect `K8S_SERVICE` nodes. Extract `targetPort` and label selectors.
2. Collect `K8S_DEPLOYMENT` nodes. Match by label selector. Extract `containerPort`.
3. Collect code `ROUTE` nodes. Extract port from properties or parent service config.
4. Match: K8s service routes to Deployment whose image corresponds to a code SERVICE, and port chain resolves to a known route → emit `EXPOSES_PORT` edge.

**Confidence**: exact port+image+path=1.0, port+image=0.85, image only=0.7, namespace heuristic=0.5.

### 3.2 Docker Image Names → SERVICE Nodes

1. Extract image names from DOCKER_IMAGE/K8S_DEPLOYMENT.
2. Compare image basename (strip registry/tag) against SERVICE node labels.
3. Emit `IMAGE_OF` edge.

**Confidence**: exact name=1.0, contains as substring=0.9, COPY path match=0.7, fuzzy=0.5.

### 3.3 Helm Values → Configuration

1. Parse `values.yaml` into flat key-path map.
2. For each `{{ .Values.X.Y }}` in templates, create `VALUE_OF` edge.
3. Where a value matches a code config struct field name → cross-domain edge at confidence 0.7.

### 3.4 Ansible → Helm Chart

1. For tasks with `kubernetes.core.helm` module or `helm upgrade/install` in script, extract chart name.
2. Match against `HELM_CHART` nodes.
3. Emit `DEPLOYS_CHART` edge.

### 3.5 Confidence Scoring

All cross-domain edges use `Confidence.INFERRED` (never `EXTRACTED`). Scores:

| Score | Meaning |
|---|---|
| 0.9–1.0 | Near-certain: exact name + port + path match |
| 0.7–0.9 | High: strong name correlation |
| 0.5–0.7 | Moderate: partial match, namespace heuristics |
| < 0.5 | Not emitted (too speculative) |

---

## 4. File Discovery Changes

### 4.1 Problem

Current `WorkspaceDiscovery` uses `_ALL_SOURCE_EXTENSIONS` (code extensions). IaC files are:
- `.yaml`/`.yml` — ambiguous (K8s, Helm, Ansible, CI, or irrelevant)
- `.tf` — unambiguous
- `Dockerfile` — no extension
- `.tpl` — Helm template helpers

### 4.2 Solution: `IaCDiscovery`

New `ast_intel/core/iac_discovery.py` runs **alongside** `WorkspaceDiscovery`:

```python
class IaCDiscovery:
    _IAC_EXTENSIONS = frozenset({".tf", ".tfvars", ".tpl"})
    _IAC_FILENAMES = frozenset({
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "Chart.yaml", "values.yaml",
    })
    _IAC_PREFIXES = ("Dockerfile",)
    _IAC_DIRECTORIES = frozenset({
        "helm-charts", "charts", "templates", "terraform",
        "ansible", "playbooks", "roles", ".github/workflows",
        ".pipelines", "deploy", "infrastructure", "k8s", "kubernetes",
    })
```

For ambiguous `.yaml`/`.yml`, peek at first 1KB:
- `apiVersion:` + `kind:` → Kubernetes
- `hosts:` at top level → Ansible
- `trigger:` / `stages:` / `jobs:` → Azure Pipelines
- `on:` + `jobs:` → GitHub Actions

### 4.3 CLI Integration

IaC scanning integrated into `ast-intel scan` by default. New `--no-iac` flag disables it.

### 4.4 Modified Pipeline

```
Discover → Extract ──────────────┐
                                  ├→ Merge → Cross-Domain Resolve → Emit
IaC Discover → IaC Extract ──────┘
```

---

## 5. Phased Implementation Plan

### Phase 1: Foundation + Kubernetes (MVP)

**Goal**: Parse raw K8s YAML manifests, emit IaC nodes, demonstrate cross-domain edges.

| # | Deliverable | File |
|---|---|---|
| 1 | New NodeKind + EdgeRelation enums | `models/graph_model.py` |
| 2 | IaCResource / IaCGraph dataclasses | `models/iac_model.py` |
| 3 | IaCExtractorBase ABC | `extractors/iac_base.py` |
| 4 | KubernetesExtractor | `extractors/iac/kubernetes.py` |
| 5 | IaCDiscovery | `core/iac_discovery.py` |
| 6 | IaCGraphBuilder (IaCGraph → CodeGraph) | `core/iac_graph_builder.py` |
| 7 | Integrate into scan pipeline + `--no-iac` | `cli.py` |
| 8 | HTML colors/shapes for IaC nodes | `formatters/_html_template.py` |
| 9 | Unit tests with Safeguard K8s manifests | `tests/test_kubernetes_extractor.py` |

**Depends on**: Nothing.
**Test**: `ast-intel scan /path/to/safeguard -f html` shows K8s nodes linked to code SERVICE nodes.

### Phase 2: Helm Charts + Docker

**Goal**: Parse Helm charts and Dockerfiles. Enable cross-domain resolution.

| # | Deliverable | File |
|---|---|---|
| 1 | HelmExtractor (Chart.yaml + values.yaml + template refs) | `extractors/iac/helm.py` |
| 2 | DockerExtractor (Dockerfile + docker-compose) | `extractors/iac/docker.py` |
| 3 | Cross-domain resolver (ports→routes, images→services) | `core/_iac_resolver.py` |
| 4 | Helm value → K8s resource edges | Integrated in HelmExtractor |

**Depends on**: Phase 1.
**Test**: Safeguard Helm chart renders full topology with cross-domain edges.

### Phase 3: Terraform + Ansible

| # | Deliverable | File |
|---|---|---|
| 1 | TerraformExtractor (python-hcl2) | `extractors/iac/terraform.py` |
| 2 | AnsibleExtractor | `extractors/iac/ansible.py` |
| 3 | Ansible helm task → HELM_CHART edges | Integrated |

**Depends on**: Phase 1 + 2. New dep: `python-hcl2`.
**Test**: Safeguard Ansible playbooks link to Helm charts.

### Phase 4: CI/CD Pipelines

| # | Deliverable | File |
|---|---|---|
| 1 | CIPipelineExtractor (GHA + Azure Pipelines) | `extractors/iac/ci_pipeline.py` |
| 2 | CI docker build → DOCKER_IMAGE edges | Integrated |

**Depends on**: Phase 2.
**Test**: Safeguard Azure Pipelines connect to Docker build chain.

### Phase 5: Merge Integration

**Goal**: IaC graphs work with `ast-intel merge` for multi-repo scenarios.

| # | Deliverable |
|---|---|
| 1 | Extend `merge_graphs()` for IaC nodes with namespace awareness |
| 2 | Cross-service IaC resolution: Helm deploys Service A which calls Service B |
| 3 | End-to-end integration test with Safeguard |

**Depends on**: All prior phases.

---

## 6. Visualization

### 6.1 Colors (Catppuccin Mocha palette)

```javascript
// Kubernetes (blue family)
k8s_deployment:  "#74c7ec",  k8s_service: "#89dceb",  k8s_configmap: "#94e2d5",
k8s_secret:      "#f38ba8",  k8s_ingress: "#89b4fa",  k8s_namespace: "#b4befe",
k8s_generic:     "#9399b2",

// Helm (purple family)
helm_chart: "#cba6f7",  helm_value: "#b4befe",  helm_template: "#c6a0f6",

// Docker (green family)
docker_image: "#a6e3a1",  docker_stage: "#94e2d5",  docker_service: "#a6e3a1",

// Terraform (orange family)
tf_resource: "#fab387",  tf_data:     "#f9e2af",  tf_module:   "#f5c2e7",
tf_variable: "#eba0ac",  tf_output:   "#f2cdcd",  tf_provider: "#f5e0dc",

// Ansible (yellow family)
ansible_playbook: "#f9e2af",  ansible_role: "#f5c2e7",  ansible_task: "#eba0ac",

// CI/CD (green family)
ci_pipeline: "#a6e3a1",  ci_job: "#94e2d5",  ci_step: "#89dceb",
```

### 6.2 Shapes

K8s Service/Ingress → `hexagon` (matches code routes). Helm Chart → `diamond` (like crate). Docker Image → `database` (cylinder). Others: `box`/`dot`/`square` by hierarchy level.

### 6.3 Cross-Domain Edges

`exposes_port`, `image_of`, `provisions` render as **dashed lines** to visually distinguish cross-domain links.

---

## 7. Dependencies & File Layout

### New Dependencies

| Package | Phase | Purpose | Optional |
|---|---|---|---|
| `PyYAML` | 1 | Already available | No |
| `python-hcl2` | 3 | Terraform HCL2 parsing | Yes (extra) |

Dockerfile parsing uses custom regex (no new dependency).

### pyproject.toml

```toml
[project.optional-dependencies]
iac = ["python-hcl2>=4.0"]
```

### File Layout

```
ast_intel/
├── extractors/
│   ├── iac_base.py
│   └── iac/
│       ├── __init__.py
│       ├── kubernetes.py     # Phase 1
│       ├── helm.py           # Phase 2
│       ├── docker.py         # Phase 2
│       ├── terraform.py      # Phase 3
│       ├── ansible.py        # Phase 3
│       └── ci_pipeline.py    # Phase 4
├── models/
│   └── iac_model.py
└── core/
    ├── iac_discovery.py
    ├── iac_dispatcher.py
    ├── iac_graph_builder.py
    └── _iac_resolver.py
```

---

## 8. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Separate base class vs reuse ExtractorBase | **Separate IaCExtractorBase** | FileAST is code-centric; IaC needs different structure |
| Content-based detection vs extension-only | **Content heuristics for .yaml** | .yaml is overloaded; must peek to classify |
| Full Helm rendering vs regex | **Regex extraction (static)** | No Go template engine in Python; static is good enough |
| IaC scanning default on vs opt-in | **Default on, `--no-iac` to disable** | Lower friction; IaC discovery is cheap |
| Per-resource NodeKinds vs single K8S_RESOURCE | **Specific kinds for key resources** | Enables precise cross-domain edges |
| Cross-domain resolution timing | **Post-processing pass** | Clean separation; follows `_url_matcher.py` pattern |
