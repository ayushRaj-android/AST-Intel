"""HTML graph formatter — renders a CodeGraph as an interactive visualization.

Produces a self-contained ``graph.html`` with a force-directed network
powered by `vis.js <https://visjs.github.io/vis-network/>`_.

By default vis.js is loaded from a CDN.  Pass ``offline=True`` to inline
a vendored copy so the HTML works without network access.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ast_intel.formatters._html_template import HTML_TEMPLATE
from ast_intel.models.graph_model import CodeGraph

__all__: list[str] = ["GRAPH_HTML_FILENAME", "GraphHtmlFormatter"]

logger = logging.getLogger(__name__)

# Output filename used by the emitter.
GRAPH_HTML_FILENAME: str = "graph.html"

# vis.js CDN tag (pinned version).
_VIS_CDN_TAG: str = (
    '<script src="https://unpkg.com/vis-network@9.1.9'
    '/standalone/umd/vis-network.min.js"></script>'
)

# Location of the vendored vis.js (next to this file).
_VIS_VENDOR_PATH: Path = Path(__file__).with_name("_vis_network.min.js")


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


class GraphHtmlFormatter:
    """Serialize a :class:`CodeGraph` to an interactive HTML file."""

    def write(
        self,
        graph: CodeGraph,
        output_path: Path,
        *,
        offline: bool = False,
    ) -> None:
        """Render *graph* and write the HTML to *output_path*.

        Args:
            graph: The code graph to visualise.
            output_path: Destination file path.
            offline: When ``True``, inline vis.js from the vendored
                copy instead of using a CDN ``<script>`` tag.
        """
        graph_json = _serialize_graph(graph)
        vis_tag = _vis_script_tag(offline)
        title = _page_title(graph)

        html = HTML_TEMPLATE
        html = html.replace("{graph_data}", graph_json)
        html = html.replace("{vis_js_script}", vis_tag)
        html = html.replace("{title}", title)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.0f KB)", output_path.name, size_kb)


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Serialization Helpers
# ---------------------------------------------------------------------------


def _serialize_graph(graph: CodeGraph) -> str:
    """Convert *graph* to the JSON blob consumed by the HTML template."""
    nodes: list[dict[str, Any]] = []
    for n in graph.nodes:
        entry: dict[str, Any] = {
            "id": n.id,
            "label": n.label,
            "kind": n.kind,
            "group": n.kind,
            "file": n.file,
        }
        if n.properties:
            entry["properties"] = dict(n.properties)
        nodes.append(entry)

    edges: list[dict[str, Any]] = [
        {
            "from": e.source,
            "to": e.target,
            "relation": e.relation,
        }
        for e in graph.edges
    ]

    payload: dict[str, Any] = {
        "nodes": nodes,
        "edges": edges,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _vis_script_tag(offline: bool) -> str:  # noqa: FBT001
    """Return the ``<script>`` tag for vis-network.

    In *offline* mode the vendored JS is inlined within the tag.
    Otherwise a CDN ``<script src=…>`` is returned.
    """
    if not offline:
        return _VIS_CDN_TAG

    if _VIS_VENDOR_PATH.exists():
        js = _VIS_VENDOR_PATH.read_text(encoding="utf-8")
        return f"<script>{js}</script>"

    logger.warning(
        "Vendored vis-network.min.js not found at %s — "
        "falling back to CDN.",
        _VIS_VENDOR_PATH,
    )
    return _VIS_CDN_TAG


def _page_title(graph: CodeGraph) -> str:
    """Derive a page ``<title>`` from the graph metadata."""
    if graph.meta and graph.meta.workspace_root:
        root = graph.meta.workspace_root
        name = Path(root).name if "/" in root or "\\" in root else root
        return f"AST Intel — {name}"
    return "AST Intel — Code Graph"


# endregion: --- Serialization Helpers
