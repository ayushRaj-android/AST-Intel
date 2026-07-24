"""Helm chart extractor — parse Chart.yaml, values.yaml, and templates.

Handles Helm v2/v3 charts.  Template files with Go template ``{{ }}``
syntax are parsed via regex extraction, NOT rendered.

The extractor operates on a chart DIRECTORY (not a single file).
Detection is triggered by ``Chart.yaml`` files.
"""

from __future__ import annotations

import logging
import re
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

__all__: list[str] = ["HelmExtractor"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Regex patterns
# ---------------------------------------------------------------------------

_VALUES_RE = re.compile(r"\{\{\s*\.Values\.([\w.]+)\s*[|}]")
_RELEASE_RE = re.compile(r"\{\{\s*\.Release\.([\w.]+)\s*[|}]")
_INCLUDE_RE = re.compile(r'\{\{-?\s*include\s+"([^"]+)"')
_DEFINE_RE = re.compile(r'\{\{-?\s*define\s+"([^"]+)"')
_KIND_RE = re.compile(r"^kind:\s*(\w+)", re.MULTILINE)

# endregion: --- Regex patterns
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


_INTERMEDIATE_THRESHOLD = 3


def _flatten_values(
    data: dict[str, Any],
    prefix: str = "",
    *,
    max_depth: int = 4,
) -> list[tuple[str, Any]]:
    """Flatten nested dict into dot-separated key paths.

    Returns ``(key_path, value)`` tuples for leaf values and
    intermediate dicts with more than 3 children.
    """
    results: list[tuple[str, Any]] = []
    if max_depth <= 0:
        return results
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            if len(value) > _INTERMEDIATE_THRESHOLD:
                results.append((full_key, value))
            results.extend(
                _flatten_values(
                    value, full_key, max_depth=max_depth - 1,
                ),
            )
        else:
            results.append((full_key, value))
    return results


def _truncate(value: object, max_len: int = 80) -> str:
    """Stringify and truncate a value for properties."""
    s = str(value)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _is_sensitive_key(key: str) -> bool:
    """Check if a key path contains sensitive names."""
    lower = key.lower()
    return any(
        s in lower
        for s in ("password", "secret", "token", "key", "cert")
    )


# endregion: --- Helpers
# ---------------------------------------------------------------------------


class HelmExtractor(IaCExtractorBase):
    """Parse Helm chart directories into IaC resources.

    Detection is triggered by ``Chart.yaml`` files.
    Extraction reads the full chart directory.
    """

    format_id: ClassVar[str] = "helm"
    file_patterns: ClassVar[list[str]] = ["**/Chart.yaml"]
    file_extensions: ClassVar[list[str]] = [".yaml", ".yml", ".tpl"]

    # ------------------------------------------------------------------ #
    # region:    --- Detection
    # ------------------------------------------------------------------ #

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:
        """Detect Helm charts by Chart.yaml presence."""
        if file_path.name != "Chart.yaml":
            return False
        try:
            text = source_peek.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False
        return "apiVersion:" in text and "name:" in text

    # endregion: --- Detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- File-level extract (delegates to directory)
    # ------------------------------------------------------------------ #

    def extract(
        self,
        file_path: Path,
        source: bytes,  # noqa: ARG002
        context: IaCContext,
    ) -> IaCGraph:
        """Redirect to directory extraction using Chart.yaml parent."""
        chart_dir = file_path.parent
        return self.extract_directory(chart_dir, context)

    # endregion: --- File-level extract
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Directory extraction
    # ------------------------------------------------------------------ #

    def extract_directory(
        self,
        directory: Path,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse the full Helm chart directory."""
        rel_dir = str(directory.relative_to(context.workspace_root))
        graph = IaCGraph(file=rel_dir)

        # 1. Parse Chart.yaml
        chart_resource = self._parse_chart_yaml(directory, rel_dir)
        if chart_resource is None:
            return graph
        graph.resources.append(chart_resource)

        # 2. Parse values.yaml
        value_resources, value_keys = self._parse_values(
            directory, rel_dir,
        )
        graph.resources.extend(value_resources)

        # 3. Parse templates
        tmpl_resources, tmpl_edges = self._parse_templates(
            directory, rel_dir, value_keys,
        )
        graph.resources.extend(tmpl_resources)
        graph.edges.extend(tmpl_edges)

        # 4. Add CONTAINS edges (chart → template)
        for tmpl in tmpl_resources:
            if tmpl.kind == "HelmTemplate":
                graph.edges.append(
                    IaCEdge(
                        source_name=chart_resource.name,
                        target_name=tmpl.name,
                        relation="contains",
                    ),
                )

        # 5. Parse environment overlay files (values/environments/*.yaml)
        env_dir = directory / "values" / "environments"
        if env_dir.is_dir():
            for env_file in sorted(env_dir.iterdir()):
                if env_file.suffix.lower() in (".yaml", ".yml"):
                    env_resources = self._parse_env_overlay(
                        env_file, rel_dir,
                    )
                    graph.resources.extend(env_resources)
                    for env_res in env_resources:
                        graph.edges.append(
                            IaCEdge(
                                source_name=env_res.name,
                                target_name=chart_resource.name,
                                relation="configures",
                            ),
                        )

        return graph

    # endregion: --- Directory extraction
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Chart.yaml parsing
    # ------------------------------------------------------------------ #

    def _parse_chart_yaml(
        self,
        chart_dir: Path,
        rel_dir: str,
    ) -> IaCResource | None:
        """Parse Chart.yaml into a HELM_CHART resource."""
        chart_path = chart_dir / "Chart.yaml"
        if not chart_path.is_file():
            return None
        try:
            text = chart_path.read_text("utf-8", errors="replace")
            doc = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Cannot parse Chart.yaml: %s", exc)
            return None
        if not isinstance(doc, dict):
            return None

        name = str(doc.get("name", "unknown-chart"))
        props: dict[str, str] = {}
        for prop_field in (
            "version", "appVersion", "description", "type",
        ):
            val = doc.get(prop_field)
            if val is not None:
                props[prop_field] = str(val)

        deps = doc.get("dependencies", [])
        if isinstance(deps, list) and deps:
            props["dependencies"] = ",".join(
                str(d.get("name", "")) for d in deps
                if isinstance(d, dict)
            )

        keywords = doc.get("keywords", [])
        if isinstance(keywords, list) and keywords:
            props["keywords"] = ",".join(str(k) for k in keywords)

        return IaCResource(
            kind="HelmChart",
            name=name,
            file=f"{rel_dir}/Chart.yaml",
            span=Span(
                start_line=1, start_col=0, end_line=1, end_col=0,
            ),
            properties=props,
        )

    # endregion: --- Chart.yaml parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- values.yaml parsing
    # ------------------------------------------------------------------ #

    def _parse_values(
        self,
        chart_dir: Path,
        rel_dir: str,
    ) -> tuple[list[IaCResource], set[str]]:
        """Parse values.yaml into HELM_VALUE resources.

        Returns ``(resources, set_of_key_paths)``.
        """
        values_path = chart_dir / "values.yaml"
        resources: list[IaCResource] = []
        key_set: set[str] = set()

        if not values_path.is_file():
            return resources, key_set

        try:
            text = values_path.read_text("utf-8", errors="replace")
            doc = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Cannot parse values.yaml: %s", exc)
            return resources, key_set

        if not isinstance(doc, dict):
            return resources, key_set

        total_lines = text.count("\n") + 1
        flat = _flatten_values(doc)

        for key_path, value in flat:
            key_set.add(key_path)
            props: dict[str, str] = {
                "value_type": type(value).__name__,
                "depth": str(key_path.count(".") + 1),
            }
            if _is_sensitive_key(key_path):
                props["default_value"] = "[REDACTED]"
            elif not isinstance(value, dict):
                props["default_value"] = _truncate(value)

            resources.append(
                IaCResource(
                    kind="HelmValue",
                    name=key_path,
                    file=f"{rel_dir}/values.yaml",
                    span=Span(
                        start_line=1, start_col=0,
                        end_line=total_lines, end_col=0,
                    ),
                    properties=props,
                ),
            )

        return resources, key_set

    # endregion: --- values.yaml parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Template parsing
    # ------------------------------------------------------------------ #

    def _parse_templates(
        self,
        chart_dir: Path,
        rel_dir: str,
        value_keys: set[str],
    ) -> tuple[list[IaCResource], list[IaCEdge]]:
        """Parse all template files in ``templates/`` directory."""
        templates_dir = chart_dir / "templates"
        resources: list[IaCResource] = []
        edges: list[IaCEdge] = []

        if not templates_dir.is_dir():
            return resources, edges

        for tmpl_path in sorted(templates_dir.iterdir()):
            suffix = tmpl_path.suffix.lower()
            if suffix not in (".yaml", ".yml", ".tpl"):
                continue

            tmpl_res, tmpl_edges = self._parse_single_template(
                tmpl_path, rel_dir, value_keys,
            )
            resources.append(tmpl_res)
            edges.extend(tmpl_edges)

        return resources, edges

    def _parse_single_template(
        self,
        tmpl_path: Path,
        rel_dir: str,
        value_keys: set[str],
    ) -> tuple[IaCResource, list[IaCEdge]]:
        """Parse one template file."""
        edges: list[IaCEdge] = []
        name = tmpl_path.name

        try:
            text = tmpl_path.read_text("utf-8", errors="replace")
        except OSError:
            text = ""

        total_lines = text.count("\n") + 1
        rel_path = f"{rel_dir}/templates/{name}"

        # Extract Go template references
        value_refs = set(_VALUES_RE.findall(text))
        release_refs = set(_RELEASE_RE.findall(text))
        includes = set(_INCLUDE_RE.findall(text))
        defines = set(_DEFINE_RE.findall(text))
        k8s_kinds = _KIND_RE.findall(text)

        props: dict[str, str] = {
            "value_refs": str(len(value_refs)),
        }
        if k8s_kinds:
            props["k8s_kinds"] = ",".join(dict.fromkeys(k8s_kinds))
        if defines:
            props["defines"] = ",".join(sorted(defines))
        if includes:
            props["includes"] = ",".join(sorted(includes))
        if release_refs:
            props["release_refs"] = ",".join(sorted(release_refs))

        resource = IaCResource(
            kind="HelmTemplate",
            name=name,
            file=rel_path,
            span=Span(
                start_line=1, start_col=0,
                end_line=total_lines, end_col=0,
            ),
            properties=props,
        )

        # VALUE_OF edges: value → template
        for vref in value_refs:
            matched = self._match_value_key(vref, value_keys)
            if matched:
                edges.append(
                    IaCEdge(
                        source_name=matched,
                        target_name=name,
                        relation="value_of",
                    ),
                )

        # TEMPLATES_TO edges: template → K8s resource type
        edges.extend(
            IaCEdge(
                source_name=name,
                target_name=f"{name}::{k8s_kind}",
                relation="templates_to",
                properties={"k8s_kind": k8s_kind},
            )
            for k8s_kind in dict.fromkeys(k8s_kinds)
        )

        return resource, edges

    @staticmethod
    def _match_value_key(ref: str, value_keys: set[str]) -> str:
        """Match a ``.Values.X.Y`` reference to a known value key.

        Tries exact match first, then prefix match.
        """
        if ref in value_keys:
            return ref
        # Try prefix: approvalEngine.config.app → approvalEngine.config
        parts = ref.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in value_keys:
                return prefix
        return ""

    # endregion: --- Template parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Environment overlay parsing
    # ------------------------------------------------------------------ #

    def _parse_env_overlay(
        self,
        env_path: Path,
        rel_dir: str,
    ) -> list[IaCResource]:
        """Parse an environment overlay values file."""
        try:
            text = env_path.read_text("utf-8", errors="replace")
            doc = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            return []

        if not isinstance(doc, dict):
            return []

        env_name = env_path.stem
        total_lines = text.count("\n") + 1
        rel_path = f"{rel_dir}/values/environments/{env_path.name}"

        props: dict[str, str] = {
            "environment": env_name,
            "key_count": str(len(doc)),
        }

        return [
            IaCResource(
                kind="HelmValue",
                name=f"env/{env_name}",
                file=rel_path,
                span=Span(
                    start_line=1, start_col=0,
                    end_line=total_lines, end_col=0,
                ),
                properties=props,
            ),
        ]

    # endregion: --- Environment overlay parsing
    # ------------------------------------------------------------------ #
