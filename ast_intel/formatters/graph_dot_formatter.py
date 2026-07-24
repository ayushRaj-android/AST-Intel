"""DOT graph formatter — serializes a CodeGraph to Graphviz DOT format.

Produces a ``graph.dot`` file that can be rendered with:

.. code-block:: bash

    dot -Tsvg graph.dot -o graph.svg
    dot -Tpng graph.dot -o graph.png

Node shapes vary by kind, and edge labels show the relationship type.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ast_intel.models.graph_model import CodeGraph, GraphEdge, GraphNode

__all__: list[str] = ["GraphDotFormatter"]

logger = logging.getLogger(__name__)

# Output filename
GRAPH_DOT_FILENAME: str = "graph.dot"

# Graphviz shapes for each node kind
_SHAPE_MAP: dict[str, str] = {
    "crate": "folder",
    "file": "note",
    "struct": "box",
    "enum": "diamond",
    "trait": "hexagon",
    "function": "ellipse",
    "method": "ellipse",
    "impl_block": "tab",
    "type_alias": "parallelogram",
    "constant": "pentagon",
    "macro": "octagon",
    "module": "component",
    "rationale": "plaintext",
    "import": "plain",
    "external_method": "plain",
}

# Edge colors by relation type
_EDGE_COLOR_MAP: dict[str, str] = {
    "contains": "#888888",
    "method_of": "#4444aa",
    "implements": "#00aa00",
    "inherits": "#00aa00",
    "imports": "#aa8800",
    "calls": "#cc0000",
    "uses_method": "#aa4400",
    "depends_on": "#0000cc",
    "super_trait": "#00aa00",
    "rationale_for": "#999999",
    "has_field": "#666666",
}


class GraphDotFormatter:
    """Serialize a ``CodeGraph`` to Graphviz DOT format.

    The output is deterministic: nodes and edges are sorted by ID/key.
    """

    def write(self, graph: CodeGraph, output_path: Path) -> None:
        """Write the code graph to a DOT file.

        Args:
            graph: The code knowledge graph.
            output_path: Absolute path to write ``graph.dot``.
        """
        logger.info("Writing DOT graph to %s", output_path)

        if not graph.nodes:
            logger.warning("Empty graph — no nodes to render")

        content = self._render(graph)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.1f KB)", output_path.name, size_kb)

    @staticmethod
    def _render(graph: CodeGraph) -> str:
        """Render the full DOT source."""
        lines: list[str] = [
            "digraph CodeGraph {",
            "    rankdir=LR;",
            '    node [fontname="Helvetica", fontsize=10];',
            '    edge [fontname="Helvetica", fontsize=8];',
            "",
        ]

        # Nodes — sorted by ID for determinism
        lines.extend(
            _render_node(node)
            for node in sorted(graph.nodes, key=lambda n: n.id)
        )

        lines.append("")

        # Edges — sorted by (source, target, relation)
        lines.extend(
            _render_edge(edge)
            for edge in sorted(
                graph.edges,
                key=lambda e: (e.source, e.target, e.relation),
            )
        )

        lines.append("}")
        lines.append("")  # trailing newline
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# region:    --- Rendering Helpers
# ---------------------------------------------------------------------------


def _dot_escape(s: str) -> str:
    """Escape a string for use inside DOT double-quoted attributes."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_node(node: GraphNode) -> str:
    """Render a single node as a DOT statement."""
    shape = _SHAPE_MAP.get(node.kind, "ellipse")
    label = _dot_escape(node.label)
    node_id = _dot_escape(node.id)
    return (
        f'    "{node_id}" '
        f'[label="{label}", shape={shape}, tooltip="{_dot_escape(node.kind)}"];'
    )


def _render_edge(edge: GraphEdge) -> str:
    """Render a single edge as a DOT statement."""
    src = _dot_escape(edge.source)
    tgt = _dot_escape(edge.target)
    color = _EDGE_COLOR_MAP.get(edge.relation, "#000000")
    label = _dot_escape(edge.relation)
    return (
        f'    "{src}" -> "{tgt}" '
        f'[label="{label}", color="{color}", fontcolor="{color}"];'
    )


# endregion: --- Rendering Helpers
