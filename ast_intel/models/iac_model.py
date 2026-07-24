"""IaC intermediate data model — parsed resources before graph emission.

These dataclasses represent the output of IaC extractors.  They are
pure data containers analogous to ``FileAST`` for code extractors,
but tailored for Infrastructure-as-Code manifests.

The ``IaCGraphBuilder`` converts ``IaCGraph`` instances into
``GraphNode`` / ``GraphEdge`` objects for the unified ``CodeGraph``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ast_intel.models.ast_node import Span

__all__: list[str] = [
    "IaCContext",
    "IaCEdge",
    "IaCGraph",
    "IaCResource",
]


@dataclass(slots=True)
class IaCContext:
    """Contextual info passed to IaC extractors during parsing.

    Attributes:
        workspace_root: Absolute path to the workspace root.
        rel_path: Workspace-relative path of the file being parsed.
    """

    workspace_root: str = ""
    rel_path: str = ""


@dataclass(slots=True)
class IaCResource:
    """A single parsed IaC resource — intermediate form before graph emission.

    Each resource maps to one ``GraphNode``.  The ``kind`` field stores
    the raw K8s ``kind`` value (e.g. ``"Deployment"``, ``"Service"``),
    and the ``IaCGraphBuilder`` maps it to the appropriate ``NodeKind``.

    Attributes:
        kind: Resource kind string (e.g. ``"Deployment"``, ``"ConfigMap"``).
        name: Resource name from ``metadata.name``.
        api_version: Kubernetes apiVersion (e.g. ``"apps/v1"``).
        namespace: Kubernetes namespace (from ``metadata.namespace`` or empty).
        file: Workspace-relative file path.
        span: Source location span (start/end line of the YAML document).
        labels: Labels from ``metadata.labels``.
        selector: Label selector (from ``spec.selector.matchLabels``).
        properties: Extra metadata extracted from the manifest.
        children: Nested resources (reserved for Helm templates in Phase 2).
    """

    kind: str
    name: str
    api_version: str = ""
    namespace: str = ""
    file: str = ""
    span: Span | None = None
    labels: dict[str, str] = field(default_factory=dict)
    selector: dict[str, str] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    children: list[IaCResource] = field(default_factory=list)


@dataclass(slots=True)
class IaCEdge:
    """An intra-file edge discovered during IaC extraction.

    Attributes:
        source_name: Name of the source resource (resolved to node ID later).
        target_name: Name of the target resource.
        relation: Edge relation string (must match an ``EdgeRelation`` value).
        properties: Optional edge metadata.
    """

    source_name: str
    target_name: str
    relation: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class IaCGraph:
    """Container for IaC extraction results from one file or chart.

    Returned by ``IaCExtractorBase.extract()``.  The ``IaCGraphBuilder``
    converts this into ``CodeGraph`` nodes and edges.

    Attributes:
        file: Workspace-relative file path.
        resources: All parsed resources from the file.
        edges: Intra-file edges between resources (e.g. Service→Deployment).
        errors: Parse errors encountered (non-fatal).
    """

    file: str = ""
    resources: list[IaCResource] = field(default_factory=list)
    edges: list[IaCEdge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
