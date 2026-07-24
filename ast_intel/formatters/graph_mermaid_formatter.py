"""Mermaid graph formatter — serializes a CodeGraph to Mermaid diagram syntax.

Produces a ``graph.mermaid.md`` file embeddable in GitHub READMEs
and documentation:

.. code-block:: markdown

    ```mermaid
    graph TD
        subgraph crate_name
            node1[StructName]
            node2([FunctionName])
        end
        node1 -->|calls| node2
    ```

Node shapes vary by kind using Mermaid's bracket syntax.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ast_intel.models.graph_model import CodeGraph, GraphEdge, GraphNode

__all__: list[str] = ["GraphMermaidFormatter"]

logger = logging.getLogger(__name__)

# Output filename
GRAPH_MERMAID_FILENAME: str = "graph.mermaid.md"

# Maximum nodes before we switch to a simplified view to avoid
# rendering issues in browsers / GitHub preview.
_MAX_NODES_FULL: int = 500


class GraphMermaidFormatter:
    """Serialize a ``CodeGraph`` to Mermaid diagram syntax.

    The output is wrapped in a fenced code block with the ``mermaid``
    language tag for GitHub rendering.
    """

    def write(self, graph: CodeGraph, output_path: Path) -> None:
        """Write the code graph as a Mermaid diagram.

        Args:
            graph: The code knowledge graph.
            output_path: Absolute path to write ``graph.mermaid.md``.
        """
        logger.info("Writing Mermaid graph to %s", output_path)

        if not graph.nodes:
            logger.warning("Empty graph — no nodes to render")

        content = self._render(graph)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.1f KB)", output_path.name, size_kb)

    @staticmethod
    def _render(graph: CodeGraph) -> str:
        """Render the full Mermaid diagram source."""
        lines: list[str] = ["```mermaid", "graph TD"]

        too_large = len(graph.nodes) > _MAX_NODES_FULL
        if too_large:
            lines.append(
                "    %% Graph has >500 nodes — showing crates + "
                "top-level symbols only"
            )

        # Group nodes by category
        file_nodes, crate_nodes, other_nodes = _classify_nodes(
            graph.nodes, too_large=too_large,
        )

        # Emit crate nodes
        for cn in crate_nodes:
            safe_id = _mermaid_id(cn.id)
            lines.append(f"    {safe_id}{{{{{_mermaid_escape(cn.label)}}}}}")

        # Emit file subgraphs
        _emit_file_subgraphs(lines, file_nodes)

        # Emit other nodes (imports, external methods)
        lines.extend(
            f"    {_render_mermaid_node(node)}" for node in other_nodes
        )

        lines.append("")

        # Emit edges — sorted for determinism
        _emit_mermaid_edges(lines, graph.edges, too_large=too_large)

        lines.append("```")
        lines.append("")  # trailing newline
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# region:    --- Node Classification
# ---------------------------------------------------------------------------

# Kinds that are skipped in large-graph mode for readability
_DETAIL_KINDS: frozenset[str] = frozenset({
    "method", "constant", "type_alias", "macro", "module", "rationale",
})

# Kinds that appear as standalone "other" nodes
_STANDALONE_KINDS: frozenset[str] = frozenset({"import", "external_method"})


def _classify_nodes(
    nodes: list[GraphNode],
    *,
    too_large: bool,
) -> tuple[dict[str, list[GraphNode]], list[GraphNode], list[GraphNode]]:
    """Partition graph nodes into file-grouped, crate, and other buckets."""
    file_nodes: dict[str, list[GraphNode]] = {}
    crate_nodes: list[GraphNode] = []
    other_nodes: list[GraphNode] = []

    for node in sorted(nodes, key=lambda n: n.id):
        if node.kind == "crate":
            crate_nodes.append(node)
        elif node.file and node.kind not in _STANDALONE_KINDS:
            if too_large and node.kind in _DETAIL_KINDS:
                continue
            file_nodes.setdefault(node.file, []).append(node)
        elif not too_large:
            other_nodes.append(node)

    return file_nodes, crate_nodes, other_nodes


def _emit_file_subgraphs(
    lines: list[str],
    file_nodes: dict[str, list[GraphNode]],
) -> None:
    """Emit Mermaid subgraph blocks, one per source file."""
    for fp in sorted(file_nodes):
        safe_subgraph = _mermaid_id(f"file_{fp}")
        lines.append(
            f"    subgraph {safe_subgraph}[{_mermaid_escape(fp)}]",
        )
        lines.extend(
            f"        {_render_mermaid_node(node)}"
            for node in file_nodes[fp]
        )
        lines.append("    end")


def _emit_mermaid_edges(
    lines: list[str],
    edges: list[GraphEdge],
    *,
    too_large: bool,
) -> None:
    """Emit Mermaid edges, sorted for determinism."""
    for edge in sorted(
        edges,
        key=lambda e: (e.source, e.target, e.relation),
    ):
        if too_large and edge.relation == "contains":
            continue
        rendered = _render_mermaid_edge(edge)
        if rendered:
            lines.append(f"    {rendered}")


# endregion: --- Node Classification


# ---------------------------------------------------------------------------
# region:    --- Rendering Helpers
# ---------------------------------------------------------------------------

# Mermaid node shape brackets by kind
_SHAPE_BRACKETS: dict[str, tuple[str, str]] = {
    "crate": ("{{{", "}}}"),        # Double curly = hexagon
    "file": ("[[", "]]"),           # Stadium shape
    "struct": ("[", "]"),           # Rectangle
    "enum": ("{{", "}}"),           # Rhombus
    "trait": ("{{", "}}"),          # Rhombus
    "function": ("([", "])"),       # Stadium (rounded)
    "method": ("([", "])"),         # Stadium (rounded)
    "impl_block": ("[/", "\\]"),    # Trapezoid
    "type_alias": ("[\\", "/]"),    # Inverse trapezoid
    "constant": ("((", "))"),       # Circle
    "macro": (">", "]"),            # Asymmetric
    "module": ("[[", "]]"),         # Stadium
    "rationale": ("(", ")"),        # Rounded rectangle
    "import": ("(", ")"),           # Rounded rectangle
    "external_method": ("(", ")"),  # Rounded rectangle
}

# Characters that need escaping/replacing in Mermaid labels
_MERMAID_SPECIAL_RE = re.compile(r'["\[\]{}()<>|#&/\\]')


def _mermaid_escape(s: str) -> str:
    """Escape a string for use in Mermaid labels.

    Replaces characters that break Mermaid syntax with
    safe alternatives.
    """
    # Wrap in double quotes to handle special characters
    safe = s.replace('"', "'")
    # If contains special chars, wrap in quotes
    if _MERMAID_SPECIAL_RE.search(safe):
        return f'"{safe}"'
    return safe


def _mermaid_id(raw: str) -> str:
    """Convert a raw node ID to a Mermaid-safe identifier.

    Mermaid node IDs must be alphanumeric + underscores.
    We replace all special characters with underscores and
    collapse runs.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    safe = re.sub(r"__+", "_", safe)
    safe = safe.strip("_")
    return safe or "node"


def _render_mermaid_node(node: GraphNode) -> str:
    """Render a single node with kind-appropriate shape."""
    safe_id = _mermaid_id(node.id)
    label = _mermaid_escape(node.label)
    open_br, close_br = _SHAPE_BRACKETS.get(node.kind, ("[", "]"))
    return f"{safe_id}{open_br}{label}{close_br}"


def _render_mermaid_edge(edge: GraphEdge) -> str:
    """Render a single edge as a Mermaid link."""
    src = _mermaid_id(edge.source)
    tgt = _mermaid_id(edge.target)
    label = edge.relation
    return f"{src} -->|{label}| {tgt}"


# endregion: --- Rendering Helpers
