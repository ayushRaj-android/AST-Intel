"""Architecture HTML formatter — renders a CodeGraph as ``architecture.html``.

Builds an :class:`~ast_intel.formatters._service_graph.ArchModel`, pre-renders
the vis.js overview payload and the Mermaid diagram sources, and injects them
into :data:`~ast_intel.formatters._arch_html_template.ARCH_HTML_TEMPLATE`.

By default vis.js and mermaid load from a CDN.  Pass ``offline=True`` to inline
the vendored copies so the page works without network access.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ast_intel.formatters._arch_html_template import ARCH_HTML_TEMPLATE
from ast_intel.formatters._arch_mermaid import (
    class_diagram,
    cloud_infra_diagram,
    cross_service_sequence,
    deployment_topology,
    detail_flowchart,
    service_map_mermaid,
)
from ast_intel.formatters._service_graph import build_arch_model

if TYPE_CHECKING:
    from ast_intel.formatters._service_graph import ArchModel, Module
    from ast_intel.models.graph_model import CodeGraph

__all__: list[str] = ["ARCH_HTML_FILENAME", "ArchHtmlFormatter"]

logger = logging.getLogger(__name__)

ARCH_HTML_FILENAME: str = "architecture.html"

_VIS_CDN_TAG: str = (
    '<script src="https://unpkg.com/vis-network@9.1.9'
    '/standalone/umd/vis-network.min.js"></script>'
)
_MERMAID_CDN_TAG: str = (
    '<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1'
    '/dist/mermaid.min.js"></script>'
)
_VIS_VENDOR_PATH: Path = Path(__file__).with_name("_vis_network.min.js")
_MERMAID_VENDOR_PATH: Path = Path(__file__).with_name("_mermaid.min.js")

_EXTERNAL = "External"
_MAX_OVERVIEW_NODES = 80
_MAX_OVERVIEW_EDGES = 150
_MAX_MODULE_DIAGRAMS = 120


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


class ArchHtmlFormatter:
    """Serialize a :class:`CodeGraph` to a service-architecture HTML page."""

    def write(
        self,
        graph: CodeGraph,
        output_path: Path,
        *,
        offline: bool = False,
    ) -> None:
        """Render *graph* as ``architecture.html`` at *output_path*.

        Args:
            graph: The code knowledge graph.
            output_path: Destination file path.
            offline: Inline vendored vis.js + mermaid instead of CDN tags.
        """
        model = build_arch_model(graph, repo_name=_repo_name(graph))
        payload = _build_payload(model, graph)
        data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        html = ARCH_HTML_TEMPLATE
        html = html.replace("{title}", _html_escape(f"{model.repo_name} — Architecture"))
        html = html.replace("{arch_data}", data_json)
        html = html.replace("{vis_js_script}", _vis_script_tag(offline=offline))
        html = html.replace("{mermaid_js_script}", _mermaid_script_tag(offline=offline))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.0f KB)", output_path.name, size_kb)


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Payload assembly
# ---------------------------------------------------------------------------


def _build_payload(model: ArchModel, graph: CodeGraph) -> dict[str, Any]:
    """Assemble the JSON payload embedded in the page."""
    diagrams: dict[str, Any] = {
        "service_map": service_map_mermaid(model),
        "sequence": cross_service_sequence(model),
        "deployment": deployment_topology(model),
        "class": {},
        "detail": {},
        "cloud": {},
    }
    for mod in model.modules[:_MAX_MODULE_DIAGRAMS]:
        diagrams["class"][mod.label] = class_diagram(model, mod.label)
        diagrams["detail"][mod.label] = detail_flowchart(model, mod.label)

    # Cloud infra diagrams per module (from CLOUD_RESOURCE nodes in graph)
    cloud_by_module = _cloud_nodes_by_module(graph, model)
    # Merge shared infra into the module with the most routes (primary service)
    shared = cloud_by_module.pop("(shared infrastructure)", [])
    if shared and model.modules:
        primary = max(model.modules, key=lambda m: m.route_count)
        cloud_by_module.setdefault(primary.label, []).extend(shared)
    # Build per-module diagrams
    for mod_label, cr_list in cloud_by_module.items():
        diagrams["cloud"][mod_label] = cloud_infra_diagram(cr_list, mod_label)
    # Build repo-wide "All services" diagram (all resources combined)
    all_resources = [entry for entries in cloud_by_module.values() for entry in entries]
    if all_resources:
        diagrams["cloud"]["_all"] = cloud_infra_diagram(
            all_resources, "All Services",
        )

    return {
        "repo": model.repo_name,
        "mode": model.mode,
        "stats": model.stats,
        "modules": [_module_dict(m) for m in model.modules],
        "overview": _overview(model),
        "diagrams": diagrams,
    }


def _overview(model: ArchModel) -> dict[str, Any]:  # noqa: C901
    """Build the vis.js overview (module nodes + aggregated edges)."""
    nodes: list[dict[str, Any]] = []
    shown: set[str] = set()
    for m in model.modules[:_MAX_OVERVIEW_NODES]:
        shown.add(m.label)
        tip = (
            f"{m.label}\n{m.language or 'code'} · {m.file_count} files · "
            f"{m.symbol_count} symbols · {m.route_count} routes"
        )
        nodes.append({
            "id": m.label,
            "label": m.label,
            "value": max(1, m.symbol_count),
            "group": "test" if m.is_test else "module",
            "module": m.label,
            "title": tip,
        })

    needs_ext = any(
        i.server == _EXTERNAL and i.client in shown for i in model.interactions
    )
    if needs_ext:
        nodes.append({
            "id": _EXTERNAL, "label": _EXTERNAL, "value": 6,
            "group": "external", "title": "External / third-party systems",
        })
        shown.add(_EXTERNAL)

    edges: list[dict[str, Any]] = []
    for e in model.module_edges:
        if len(edges) >= _MAX_OVERVIEW_EDGES:
            break
        if e.source in shown and e.target in shown:
            edges.append({
                "from": e.source, "to": e.target,
                "value": e.weight, "label": str(e.weight),
            })

    seen: set[tuple[str, str]] = set()
    for i in model.interactions:
        if len(edges) >= _MAX_OVERVIEW_EDGES:
            break
        if i.client not in shown:
            continue
        if i.server != _EXTERNAL and i.server not in shown:
            continue
        pair = (i.client, i.server)
        if pair in seen:
            continue
        seen.add(pair)
        edges.append({
            "from": i.client, "to": i.server, "dashes": True,
            "label": "http", "color": {"color": "#94e2d5"},
        })

    return {"nodes": nodes, "edges": edges}


def _module_dict(m: Module) -> dict[str, Any]:
    return {
        "label": m.label,
        "language": m.language,
        "kind": m.kind,
        "is_test": m.is_test,
        "file_count": m.file_count,
        "symbol_count": m.symbol_count,
        "route_count": m.route_count,
    }


# endregion: --- Payload assembly


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _cloud_nodes_by_module(  # noqa: C901
    graph: CodeGraph, model: ArchModel,
) -> dict[str, list[tuple[str, str, str, str, str]]]:
    """Group CLOUD_RESOURCE nodes by their owning module.

    Returns {module_label: [(service, category, client, caller, file), ...]}.
    Assigns a resource to a module by matching its file path against the
    module's file membership (same as the arch model's file→module mapping).
    IaC resources that can't be attributed to a module are distributed to
    modules that use the same (service, category) from code.
    """
    from collections import defaultdict

    from ast_intel.models.graph_model import NodeKind

    # Match cloud node's file against module label (project name)
    mod_labels = {m.label for m in model.modules}

    result: dict[str, list[tuple[str, str, str, str, str]]] = defaultdict(list)
    unattributed: list[tuple[str, str, str, str, str]] = []

    for node in graph.nodes:
        if node.kind != NodeKind.CLOUD_RESOURCE:
            continue
        p = node.properties
        entry = (
            p.get("service", ""),
            p.get("category", ""),
            p.get("client", ""),
            p.get("caller", ""),
            node.file,
        )
        # Try to match file path to a module
        matched = False
        for ml in mod_labels:
            if ml.lower() in node.file.lower():
                result[ml].append(entry)
                matched = True
                break
        if not matched:
            unattributed.append(entry)

    # Distribute unattributed IaC resources to modules that use the same
    # (service, category) from code. This links provisioned infra to consumers.
    if unattributed:
        code_keys_by_mod: dict[str, set[tuple[str, str]]] = {}
        for mod_label, entries in result.items():
            keys = {(svc, cat) for svc, cat, client, *_ in entries if client}
            if keys:
                code_keys_by_mod[mod_label] = keys

        for entry in unattributed:
            svc, cat = entry[0], entry[1]
            distributed = False
            for mod_label, keys in code_keys_by_mod.items():
                if (svc, cat) in keys:
                    result[mod_label].append(entry)
                    distributed = True
            if not distributed:
                result.setdefault("(shared infrastructure)", []).append(entry)

    return dict(result)


def _repo_name(graph: CodeGraph) -> str:
    """Best-effort repository display name from the graph metadata."""
    meta = graph.meta
    root = getattr(meta, "workspace_root", "") if meta else ""
    if root:
        name = root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name
    return "Architecture"


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _vis_script_tag(*, offline: bool) -> str:
    if not offline:
        return _VIS_CDN_TAG
    if _VIS_VENDOR_PATH.exists():
        return f"<script>{_VIS_VENDOR_PATH.read_text(encoding='utf-8')}</script>"
    logger.warning("Vendored vis-network.min.js missing — using CDN.")
    return _VIS_CDN_TAG


def _mermaid_script_tag(*, offline: bool) -> str:
    if not offline:
        return _MERMAID_CDN_TAG
    if _MERMAID_VENDOR_PATH.exists():
        return f"<script>{_MERMAID_VENDOR_PATH.read_text(encoding='utf-8')}</script>"
    logger.warning("Vendored mermaid.min.js missing — using CDN.")
    return _MERMAID_CDN_TAG


# endregion: --- Helpers
