"""Kubernetes manifest extractor — parse K8s YAML into IaC resources.

Handles ``Deployment``, ``StatefulSet``, ``DaemonSet``, ``Service``,
``ConfigMap``, ``Secret``, ``Ingress``, ``Namespace``, and generic
resources.  Multi-document YAML (``---`` separated) is supported.

Helm templates (files containing ``{{ }}``) are explicitly excluded;
those are handled by a dedicated ``HelmExtractor`` in Phase 2.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.ast_node import Span
from ast_intel.models.iac_model import (
    IaCContext,
    IaCEdge,
    IaCGraph,
    IaCResource,
)

__all__: list[str] = ["KubernetesExtractor"]

logger = logging.getLogger(__name__)

# K8s kinds whose pod template structure is identical to Deployment.
_DEPLOYMENT_KINDS: frozenset[str] = frozenset({
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
})


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _compute_doc_line_offsets(text: str) -> list[int]:
    """Return the 1-based start line of each YAML document.

    Documents are separated by ``---`` on its own line.
    """
    offsets: list[int] = [1]
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "---":
            offsets.append(line_no + 1)
    return offsets


def _span_for_doc(
    line_offsets: list[int],
    doc_idx: int,
    total_lines: int,
) -> Span | None:
    """Build a Span for the *doc_idx*-th YAML document."""
    if doc_idx >= len(line_offsets):
        return None
    start = line_offsets[doc_idx]
    if doc_idx + 1 < len(line_offsets):
        end = max(line_offsets[doc_idx + 1] - 1, start)
    else:
        end = total_lines
    return Span(start_line=start, start_col=0, end_line=end, end_col=0)


def _selector_matches(
    selector: dict[str, str],
    pod_labels: dict[str, str],
) -> bool:
    """Return ``True`` if all selector key-value pairs exist in *pod_labels*."""
    return bool(selector) and all(
        pod_labels.get(k) == v for k, v in selector.items()
    )


def _parse_label_string(raw: str) -> dict[str, str]:
    """Parse ``"app=web,tier=frontend"`` into a dict."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


# endregion: --- Helpers
# ---------------------------------------------------------------------------


class KubernetesExtractor(IaCExtractorBase):
    """Parse raw Kubernetes YAML manifests into IaC resources."""

    format_id = "kubernetes"
    file_patterns: ClassVar[list[str]] = ["**/*.yaml", "**/*.yml"]
    file_extensions: ClassVar[list[str]] = [".yaml", ".yml"]

    # ------------------------------------------------------------------ #
    # region:    --- Detection
    # ------------------------------------------------------------------ #

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:  # noqa: ARG002
        """Detect K8s manifests by ``apiVersion`` + ``kind``.

        Rejects Helm templates (files containing Go template ``{{ }}``
        directives).
        """
        try:
            text = source_peek.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False

        # Skip Helm templates
        if "{{" in text and "}}" in text:
            return False

        return "apiVersion:" in text and "kind:" in text

    # endregion: --- Detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Extraction
    # ------------------------------------------------------------------ #

    def extract(
        self,
        file_path: Path,  # noqa: ARG002
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse a K8s YAML file into an :class:`IaCGraph`."""
        graph = IaCGraph(file=context.rel_path)
        text = source.decode("utf-8", errors="replace")
        total_lines = text.count("\n") + 1
        line_offsets = _compute_doc_line_offsets(text)

        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            graph.errors.append(f"YAML parse error: {exc}")
            return graph

        for doc_idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                continue
            if "apiVersion" not in doc or "kind" not in doc:
                continue

            resource = self._parse_resource(
                doc, context.rel_path, line_offsets, doc_idx, total_lines,
            )
            if resource is not None:
                graph.resources.append(resource)

        graph.edges = self._build_intra_edges(graph.resources)
        return graph

    # endregion: --- Extraction
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Resource parsing
    # ------------------------------------------------------------------ #

    def _parse_resource(
        self,
        doc: dict[str, Any],
        rel_path: str,
        line_offsets: list[int],
        doc_idx: int,
        total_lines: int,
    ) -> IaCResource | None:
        """Parse a single YAML document into an :class:`IaCResource`."""
        kind: str = doc.get("kind", "")
        metadata: dict[str, Any] = doc.get("metadata", {}) or {}
        spec: dict[str, Any] = doc.get("spec", {}) or {}

        name = str(metadata.get("name", f"unnamed-{kind.lower()}-{doc_idx}"))
        namespace = str(metadata.get("namespace", "") or "")
        labels: dict[str, str] = {
            str(k): str(v)
            for k, v in (metadata.get("labels", {}) or {}).items()
        }
        api_version = str(doc.get("apiVersion", ""))
        span = _span_for_doc(line_offsets, doc_idx, total_lines)

        resource = IaCResource(
            kind=kind,
            name=name,
            api_version=api_version,
            namespace=namespace,
            file=rel_path,
            span=span,
            labels=labels,
        )

        if kind in _DEPLOYMENT_KINDS:
            self._extract_deployment_info(resource, spec)
        elif kind == "Service":
            self._extract_service_info(resource, spec)
        elif kind in ("ConfigMap", "Secret"):
            self._extract_configmap_info(resource, doc)
        elif kind in ("Ingress", "IngressRoute"):
            self._extract_ingress_info(resource, spec)
        elif kind in ("CronJob", "Job"):
            self._extract_job_info(resource, spec)
        elif kind == "PersistentVolumeClaim":
            self._extract_pvc_info(resource, spec)
        elif kind in (
            "Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding",
        ):
            self._extract_rbac_info(resource, doc)
        # Namespace and everything else: name + kind + labels suffice

        return resource

    def _extract_deployment_info(  # noqa: C901
        self,
        resource: IaCResource,
        spec: dict[str, Any],
    ) -> None:
        """Extract replicas, images, ports, selectors from Deployment-like."""
        selector = spec.get("selector", {}) or {}
        resource.selector = {
            str(k): str(v)
            for k, v in (selector.get("matchLabels", {}) or {}).items()
        }

        replicas = spec.get("replicas")
        if replicas is not None:
            resource.properties["replicas"] = str(replicas)

        template: dict[str, Any] = spec.get("template", {}) or {}
        template_spec: dict[str, Any] = template.get("spec", {}) or {}
        containers: list[dict[str, Any]] = template_spec.get("containers", []) or []

        images: list[str] = []
        ports: list[str] = []
        for container in containers:
            image = container.get("image", "")
            if image:
                images.append(str(image))
            for port in container.get("ports", []) or []:
                cp = port.get("containerPort")
                if cp is not None:
                    ports.append(str(cp))

        if images:
            resource.properties["images"] = ",".join(images)
        if ports:
            resource.properties["containerPorts"] = ",".join(ports)

        # Template labels (pod labels)
        template_labels: dict[str, str] = {
            str(k): str(v)
            for k, v in (
                (template.get("metadata", {}) or {}).get("labels", {}) or {}
            ).items()
        }
        if template_labels:
            resource.properties["pod_labels"] = ",".join(
                f"{k}={v}" for k, v in sorted(template_labels.items())
            )

        # Secret and ConfigMap references
        secrets = self._find_secret_refs(template_spec)
        if secrets:
            resource.properties["secret_refs"] = ",".join(sorted(secrets))

        configmaps = self._find_configmap_refs(template_spec)
        if configmaps:
            resource.properties["configmap_refs"] = ",".join(sorted(configmaps))

    def _extract_service_info(
        self,
        resource: IaCResource,
        spec: dict[str, Any],
    ) -> None:
        """Extract ports and selector from Service."""
        resource.selector = {
            str(k): str(v)
            for k, v in (spec.get("selector", {}) or {}).items()
        }
        svc_type = spec.get("type", "ClusterIP")
        resource.properties["type"] = str(svc_type)

        port_strs: list[str] = []
        for port in spec.get("ports", []) or []:
            p = port.get("port", "?")
            tp = port.get("targetPort", "?")
            proto = port.get("protocol", "TCP")
            port_strs.append(f"{p}:{tp}/{proto}")

        if port_strs:
            resource.properties["ports"] = ",".join(port_strs)

    def _extract_configmap_info(
        self,
        resource: IaCResource,
        doc: dict[str, Any],
    ) -> None:
        """Extract data keys from ConfigMap or Secret."""
        data: dict[str, Any] = doc.get("data", {}) or {}
        if data:
            resource.properties["keys"] = ",".join(sorted(str(k) for k in data))

    def _extract_ingress_info(
        self,
        resource: IaCResource,
        spec: dict[str, Any],
    ) -> None:
        """Extract rules from Ingress."""
        rules: list[dict[str, Any]] = spec.get("rules", []) or []
        backends: list[str] = []
        for rule in rules:
            host = rule.get("host", "")
            http: dict[str, Any] = rule.get("http", {}) or {}
            for path_entry in http.get("paths", []) or []:
                path = path_entry.get("path", "/")
                backend: dict[str, Any] = path_entry.get("backend", {}) or {}
                svc: dict[str, Any] = backend.get("service", {}) or {}
                svc_name = svc.get("name", "")
                backends.append(f"{host}{path}\u2192{svc_name}")
        if backends:
            resource.properties["rules"] = ";".join(backends)

    def _extract_job_info(
        self,
        resource: IaCResource,
        spec: dict[str, Any],
    ) -> None:
        """Extract schedule and job template info from CronJob/Job."""
        schedule = spec.get("schedule")
        if schedule:
            resource.properties["schedule"] = str(schedule)

        # CronJob wraps a Job in jobTemplate
        job_template = spec.get("jobTemplate", {}) or {}
        job_spec = job_template.get("spec", spec)

        template = job_spec.get("template", {}) or {}
        template_spec = template.get("spec", {}) or {}
        containers = template_spec.get("containers", []) or []

        images: list[str] = []
        for container in containers:
            image = container.get("image", "")
            if image:
                images.append(str(image))

        if images:
            resource.properties["images"] = ",".join(images)

        completions = job_spec.get("completions")
        if completions is not None:
            resource.properties["completions"] = str(completions)

        parallelism = job_spec.get("parallelism")
        if parallelism is not None:
            resource.properties["parallelism"] = str(parallelism)

    def _extract_pvc_info(
        self,
        resource: IaCResource,
        spec: dict[str, Any],
    ) -> None:
        """Extract storage request and access modes from PVC."""
        access_modes = spec.get("accessModes", [])
        if access_modes:
            resource.properties["accessModes"] = ",".join(
                str(m) for m in access_modes
            )

        resources = spec.get("resources", {}) or {}
        requests = resources.get("requests", {}) or {}
        storage = requests.get("storage")
        if storage:
            resource.properties["storage"] = str(storage)

        storage_class = spec.get("storageClassName")
        if storage_class:
            resource.properties["storageClassName"] = str(storage_class)

    def _extract_rbac_info(  # noqa: C901
        self,
        resource: IaCResource,
        doc: dict[str, Any],
    ) -> None:
        """Extract rules from Role/ClusterRole, subjects from Bindings."""
        rules = doc.get("rules", [])
        if isinstance(rules, list) and rules:
            resource.properties["rule_count"] = str(len(rules))
            api_groups: set[str] = set()
            k8s_resources: set[str] = set()
            for rule in rules:
                if isinstance(rule, dict):
                    for g in rule.get("apiGroups", []) or []:
                        api_groups.add(str(g) or "core")
                    for r_val in rule.get("resources", []) or []:
                        k8s_resources.add(str(r_val))
            if api_groups:
                resource.properties["apiGroups"] = ",".join(
                    sorted(api_groups),
                )
            if k8s_resources:
                resource.properties["resources"] = ",".join(
                    sorted(k8s_resources),
                )

        # RoleBinding/ClusterRoleBinding subjects
        subjects = doc.get("subjects", [])
        if isinstance(subjects, list) and subjects:
            subject_strs: list[str] = []
            for subj in subjects:
                if isinstance(subj, dict):
                    s_kind = subj.get("kind", "")
                    s_name = subj.get("name", "")
                    subject_strs.append(f"{s_kind}/{s_name}")
            if subject_strs:
                resource.properties["subjects"] = ",".join(subject_strs)

        role_ref = doc.get("roleRef", {})
        if isinstance(role_ref, dict) and role_ref:
            resource.properties["roleRef"] = str(
                role_ref.get("name", ""),
            )

    # endregion: --- Resource parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Reference detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_secret_refs(template_spec: dict[str, Any]) -> set[str]:
        """Find all Secret names referenced in a pod template spec."""
        refs: set[str] = set()
        for vol in template_spec.get("volumes", []) or []:
            secret: dict[str, Any] = vol.get("secret", {}) or {}
            name = secret.get("secretName", "")
            if name:
                refs.add(str(name))
        for container in template_spec.get("containers", []) or []:
            for env_from in container.get("envFrom", []) or []:
                secret_ref: dict[str, Any] = env_from.get("secretRef", {}) or {}
                name = secret_ref.get("name", "")
                if name:
                    refs.add(str(name))
            for env in container.get("env", []) or []:
                value_from: dict[str, Any] = env.get("valueFrom", {}) or {}
                secret_key: dict[str, Any] = (
                    value_from.get("secretKeyRef", {}) or {}
                )
                name = secret_key.get("name", "")
                if name:
                    refs.add(str(name))
        return refs

    @staticmethod
    def _find_configmap_refs(template_spec: dict[str, Any]) -> set[str]:
        """Find all ConfigMap names referenced in a pod template spec."""
        refs: set[str] = set()
        for vol in template_spec.get("volumes", []) or []:
            cm: dict[str, Any] = vol.get("configMap", {}) or {}
            name = cm.get("name", "")
            if name:
                refs.add(str(name))
        for container in template_spec.get("containers", []) or []:
            for env_from in container.get("envFrom", []) or []:
                cm_ref: dict[str, Any] = env_from.get("configMapRef", {}) or {}
                name = cm_ref.get("name", "")
                if name:
                    refs.add(str(name))
            for env in container.get("env", []) or []:
                value_from: dict[str, Any] = env.get("valueFrom", {}) or {}
                cm_key: dict[str, Any] = (
                    value_from.get("configMapKeyRef", {}) or {}
                )
                name = cm_key.get("name", "")
                if name:
                    refs.add(str(name))
        return refs

    # endregion: --- Reference detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Intra-file edge building
    # ------------------------------------------------------------------ #

    def _build_intra_edges(  # noqa: C901, PLR0912
        self,
        resources: list[IaCResource],
    ) -> list[IaCEdge]:
        """Build edges between resources within the same file.

        * Service → Deployment via label selector matching (``routes_to``).
        * ConfigMap → Deployment via configmap_refs (``configures``).
        * Deployment → Secret via secret_refs (``uses_secret``).
        """
        edges: list[IaCEdge] = []

        deployments: list[IaCResource] = []
        services: list[IaCResource] = []
        configmaps: dict[str, IaCResource] = {}
        secrets: dict[str, IaCResource] = {}

        for r in resources:
            if r.kind in _DEPLOYMENT_KINDS:
                deployments.append(r)
            elif r.kind == "Service":
                services.append(r)
            elif r.kind == "ConfigMap":
                configmaps[r.name] = r
            elif r.kind == "Secret":
                secrets[r.name] = r

        # Service → Deployment (ROUTES_TO)
        for svc in services:
            if not svc.selector:
                continue
            for dep in deployments:
                pod_labels = _parse_label_string(
                    dep.properties.get("pod_labels", ""),
                )
                if _selector_matches(svc.selector, pod_labels):
                    edges.append(
                        IaCEdge(
                            source_name=svc.name,
                            target_name=dep.name,
                            relation="routes_to",
                        ),
                    )

        # ConfigMap → Deployment (CONFIGURES)
        for dep in deployments:
            for cm_raw in dep.properties.get("configmap_refs", "").split(","):
                cm_name = cm_raw.strip()
                if cm_name and cm_name in configmaps:
                    edges.append(
                        IaCEdge(
                            source_name=configmaps[cm_name].name,
                            target_name=dep.name,
                            relation="configures",
                        ),
                    )

        # Deployment → Secret (USES_SECRET)
        for dep in deployments:
            for sec_raw in dep.properties.get("secret_refs", "").split(","):
                sec_name = sec_raw.strip()
                if sec_name and sec_name in secrets:
                    edges.append(
                        IaCEdge(
                            source_name=dep.name,
                            target_name=secrets[sec_name].name,
                            relation="uses_secret",
                        ),
                    )
        # Namespace -> resources (CONTAINS) by matching namespace field
        namespaces: dict[str, IaCResource] = {}
        for r_ns in resources:
            if r_ns.kind == "Namespace":
                namespaces[r_ns.name] = r_ns

        edges.extend(
            IaCEdge(
                source_name=namespaces[r_ns.namespace].name,
                target_name=r_ns.name,
                relation="contains",
            )
            for r_ns in resources
            if (
                r_ns.namespace
                and r_ns.namespace in namespaces
                and r_ns.kind != "Namespace"
            )
        )
        return edges

    # endregion: --- Intra-file edge building
    # ------------------------------------------------------------------ #
