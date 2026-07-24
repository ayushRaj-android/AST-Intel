"""Terraform extractor — parse .tf HCL2 files into IaC resources.

Handles ``resource``, ``data``, ``module``, ``variable``, ``output``,
and ``provider`` blocks.  Implicit dependency edges are derived from
``${resource_type.name.attr}`` references in attribute values.

Requires ``python-hcl2`` (optional dependency).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, ClassVar

from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.iac_model import (
    IaCContext,
    IaCEdge,
    IaCGraph,
    IaCResource,
)

__all__: list[str] = ["TerraformExtractor"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants & regex
# ---------------------------------------------------------------------------

# Match ${resource_type.name.attr} or ${data.type.name.attr} references.
_REF_RE = re.compile(
    r"\$\{([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+)\}",
)

# Match var.name references (without ${}).
_VAR_REF_RE = re.compile(
    r"\bvar\.([a-zA-Z_][\w]*)\b",
)

# Match module.name references.
_MODULE_REF_RE = re.compile(
    r"\bmodule\.([a-zA-Z_][\w]*)\b",
)

# HCL2 string literals come with extra quotes from python-hcl2.
_STRIP_QUOTES_RE = re.compile(r'^"(.*)"$')

# endregion: --- Constants & regex
# ---------------------------------------------------------------------------


def _strip_quotes(val: str) -> str:
    """Remove the extra quotes that python-hcl2 adds around string values."""
    m = _STRIP_QUOTES_RE.match(val)
    return m.group(1) if m else val


def _collect_refs(value: object) -> set[str]:
    """Recursively collect all ${...} references from a parsed HCL value."""
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(_REF_RE.findall(value))
        refs.update(f"var.{m}" for m in _VAR_REF_RE.findall(value))
        refs.update(f"module.{m}" for m in _MODULE_REF_RE.findall(value))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_refs(item))
    elif isinstance(value, dict):
        for v in value.values():
            refs.update(_collect_refs(v))
    return refs


def _ref_to_resource_id(ref: str) -> str | None:
    """Convert a reference like 'aws_instance.web.public_ip' to 'aws_instance.web'.

    For data sources: 'data.aws_ami.ubuntu.id' → 'data.aws_ami.ubuntu'
    For variables: 'var.region' → 'var.region'
    For modules: 'module.vpc' → 'module.vpc'
    """
    parts = ref.split(".")
    if len(parts) < 2:  # noqa: PLR2004
        return None
    if parts[0] == "data" and len(parts) >= 3:  # noqa: PLR2004
        return f"data.{parts[1]}.{parts[2]}"
    if parts[0] in ("var", "module"):
        return f"{parts[0]}.{parts[1]}"
    # resource_type.name
    return f"{parts[0]}.{parts[1]}"


class TerraformExtractor(IaCExtractorBase):
    """Parse Terraform HCL2 files into IaC resources and dependency edges.

    Produces ``TfResource``, ``TfData``, ``TfModule``, ``TfVariable``,
    ``TfOutput``, and ``TfProvider`` resources.  Implicit dependency
    edges are derived from ``${...}`` expression references.
    """

    format_id: ClassVar[str] = "terraform"
    file_patterns: ClassVar[list[str]] = ["*.tf", "*.tfvars"]
    file_extensions: ClassVar[list[str]] = [".tf", ".tfvars"]

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:  # noqa: ARG002
        """Return True for .tf and .tfvars files."""
        suffix = file_path.suffix.lower()
        return suffix in (".tf", ".tfvars")

    def extract(
        self,
        file_path: Path,  # noqa: ARG002
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse a Terraform file and emit resources + dependency edges."""
        graph = IaCGraph(file=context.rel_path)

        try:
            import hcl2  # type: ignore[import-untyped]
        except ImportError:
            graph.errors.append(
                "python-hcl2 not installed; cannot parse Terraform files",
            )
            return graph

        try:
            import io

            parsed = hcl2.load(io.StringIO(source.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            graph.errors.append(f"HCL2 parse error: {exc}")
            return graph

        # Track resource names for edge resolution.
        # Maps "type.name" → resource-name used in IaCResource.
        name_map: dict[str, str] = {}

        # --- resources ---
        for block in parsed.get("resource", []):
            self._extract_resources(block, context, graph, name_map)

        # --- data sources ---
        for block in parsed.get("data", []):
            self._extract_data(block, context, graph, name_map)

        # --- modules ---
        for block in parsed.get("module", []):
            self._extract_modules(block, context, graph, name_map)

        # --- variables ---
        for block in parsed.get("variable", []):
            self._extract_variables(block, context, graph, name_map)

        # --- outputs ---
        for block in parsed.get("output", []):
            self._extract_outputs(block, context, graph, name_map)

        # --- providers ---
        for block in parsed.get("provider", []):
            self._extract_providers(block, context, graph, name_map)

        # --- Resolve implicit dependency edges ---
        self._resolve_dependency_edges(parsed, graph, name_map)

        return graph

    # ------------------------------------------------------------------
    # region:    --- Block extractors
    # ------------------------------------------------------------------

    def _extract_resources(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract resource blocks: resource "type" "name" { ... }."""
        for type_key, type_body in block.items():
            res_type = _strip_quotes(type_key)
            if not isinstance(type_body, dict):
                continue
            for name_key, body in type_body.items():
                res_name = _strip_quotes(name_key)
                full_name = f"{res_type}.{res_name}"
                name_map[full_name] = full_name

                props = self._extract_properties(body)
                props["resource_type"] = res_type

                graph.resources.append(
                    IaCResource(
                        kind="TfResource",
                        name=full_name,
                        file=context.rel_path,
                        properties=props,
                    ),
                )

    def _extract_data(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract data source blocks: data "type" "name" { ... }."""
        for type_key, type_body in block.items():
            data_type = _strip_quotes(type_key)
            if not isinstance(type_body, dict):
                continue
            for name_key, body in type_body.items():
                data_name = _strip_quotes(name_key)
                full_name = f"data.{data_type}.{data_name}"
                name_map[full_name] = full_name

                props = self._extract_properties(body)
                props["data_type"] = data_type

                graph.resources.append(
                    IaCResource(
                        kind="TfData",
                        name=full_name,
                        file=context.rel_path,
                        properties=props,
                    ),
                )

    def _extract_modules(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract module blocks: module "name" { source = "..." }."""
        for name_key, body in block.items():
            mod_name = _strip_quotes(name_key)
            full_name = f"module.{mod_name}"
            name_map[full_name] = full_name

            props = self._extract_properties(body)
            source_val = props.get("source", "")
            props["module_source"] = source_val

            graph.resources.append(
                IaCResource(
                    kind="TfModule",
                    name=full_name,
                    file=context.rel_path,
                    properties=props,
                ),
            )

    def _extract_variables(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract variable blocks: variable "name" { ... }."""
        for name_key, body in block.items():
            var_name = _strip_quotes(name_key)
            full_name = f"var.{var_name}"
            name_map[full_name] = full_name

            props = self._extract_properties(body)

            graph.resources.append(
                IaCResource(
                    kind="TfVariable",
                    name=full_name,
                    file=context.rel_path,
                    properties=props,
                ),
            )

    def _extract_outputs(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract output blocks: output "name" { value = ... }."""
        for name_key, body in block.items():
            out_name = _strip_quotes(name_key)
            full_name = f"output.{out_name}"
            name_map[full_name] = full_name

            props = self._extract_properties(body)

            graph.resources.append(
                IaCResource(
                    kind="TfOutput",
                    name=full_name,
                    file=context.rel_path,
                    properties=props,
                ),
            )

    def _extract_providers(
        self,
        block: dict[str, Any],
        context: IaCContext,
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Extract provider blocks: provider "name" { ... }."""
        for name_key, body in block.items():
            provider_name = _strip_quotes(name_key)
            full_name = f"provider.{provider_name}"
            name_map[full_name] = full_name

            props = self._extract_properties(body)

            graph.resources.append(
                IaCResource(
                    kind="TfProvider",
                    name=full_name,
                    file=context.rel_path,
                    properties=props,
                ),
            )

    # endregion: --- Block extractors
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # region:    --- Helpers
    # ------------------------------------------------------------------

    def _extract_properties(self, body: object) -> dict[str, Any]:
        """Extract scalar properties from a block body dict."""
        props: dict[str, Any] = {}
        if not isinstance(body, dict):
            return props
        for key, val in body.items():
            if key == "__is_block__":
                continue
            if isinstance(val, str):
                props[key] = _strip_quotes(val)
            elif isinstance(val, (bool, int, float)):
                props[key] = val
            elif isinstance(val, list) and all(
                isinstance(v, str) for v in val
            ):
                props[key] = [_strip_quotes(v) for v in val]
        return props

    def _resolve_dependency_edges(
        self,
        parsed: dict[str, Any],
        graph: IaCGraph,
        name_map: dict[str, str],
    ) -> None:
        """Derive implicit depends_on edges from ${...} references.

        Scans all resource/data/output/module/provider blocks for
        attribute references and emits ``depends_on`` edges to the
        referenced resource.
        """
        known_ids: set[str] = set(name_map.values())

        # Two-level blocks: resource "type" "name" { body }
        # and data "type" "name" { body }
        for block_type in ("resource", "data"):
            for block in parsed.get(block_type, []):
                self._edges_from_two_level_block(
                    block, block_type, graph, known_ids,
                )

        # Single-level blocks: module/output/variable/provider "name" { body }
        for block_type in ("module", "output", "variable", "provider"):
            for block in parsed.get(block_type, []):
                self._edges_from_single_level_block(
                    block, block_type, graph, known_ids,
                )

    def _edges_from_two_level_block(
        self,
        block: dict[str, Any],
        block_type: str,
        graph: IaCGraph,
        known_ids: set[str],
    ) -> None:
        """Emit edges for resource/data blocks (type → name → body)."""
        for type_key, type_body in block.items():
            type_name = _strip_quotes(type_key)
            if not isinstance(type_body, dict):
                continue
            for name_key, body in type_body.items():
                res_name = _strip_quotes(name_key)
                if block_type == "data":
                    source_id = f"data.{type_name}.{res_name}"
                else:
                    source_id = f"{type_name}.{res_name}"
                self._emit_edges_for_body(
                    source_id=source_id,
                    body=body,
                    graph=graph,
                    known_ids=known_ids,
                )

    def _edges_from_single_level_block(
        self,
        block: dict[str, Any],
        block_type: str,
        graph: IaCGraph,
        known_ids: set[str],
    ) -> None:
        """Emit edges for module/output/variable/provider blocks (name → body)."""
        for name_key, body in block.items():
            name = _strip_quotes(name_key)
            source_id = f"{block_type}.{name}"
            # "variable" blocks use "var." prefix in our naming.
            if block_type == "variable":
                source_id = f"var.{name}"
            self._emit_edges_for_body(
                source_id=source_id,
                body=body,
                graph=graph,
                known_ids=known_ids,
            )

    def _emit_edges_for_body(
        self,
        source_id: str,
        body: object,
        graph: IaCGraph,
        known_ids: set[str],
    ) -> None:
        """Collect refs from body and emit depends_on edges."""
        if source_id not in known_ids:
            return

        refs = _collect_refs(body)
        seen_targets: set[str] = set()
        for ref in refs:
            target_id = _ref_to_resource_id(ref)
            if (
                target_id is None
                or target_id == source_id
                or target_id not in known_ids
                or target_id in seen_targets
            ):
                continue
            seen_targets.add(target_id)
            graph.edges.append(
                IaCEdge(
                    source_name=source_id,
                    target_name=target_id,
                    relation="depends_on",
                ),
            )

    # endregion: --- Helpers
    # ------------------------------------------------------------------
