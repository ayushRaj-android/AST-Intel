"""JSON graph formatter — serializes a CodeGraph to ``graph.json``.

Produces a JSON structure compatible with vis.js, D3, Cytoscape, and
other graph visualization libraries::

    {
        "meta": { ... },
        "nodes": [ { "id": ..., "label": ..., ... }, ... ],
        "edges": [ { "source": ..., "target": ..., ... }, ... ]
    }

Same input always produces **byte-identical** output (sorted keys,
deterministic ordering).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ast_intel.models.ast_node import Confidence, Span
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    HyperEdge,
    HyperRelation,
    NodeKind,
)
from ast_intel.models.workspace_model import WorkspaceMeta

__all__: list[str] = ["GraphJsonFormatter"]

logger = logging.getLogger(__name__)

# Output filename
GRAPH_JSON_FILENAME: str = "graph.json"


class _GraphEncoder(json.JSONEncoder):
    """Custom JSON encoder for graph dataclasses and special types."""

    def default(self, o: Any) -> Any:  # noqa: ANN401
        """Encode non-standard types."""
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, set | frozenset):
            return sorted(o)
        if isinstance(o, tuple):
            return list(o)
        return super().default(o)


class GraphJsonFormatter:
    """Serialize a ``CodeGraph`` to a JSON file.

    The output is deterministic: sorted keys, consistent formatting,
    and stable ordering of all collections.
    """

    def write(self, graph: CodeGraph, output_path: Path) -> None:
        """Write the code graph to a JSON file.

        Args:
            graph: The code knowledge graph.
            output_path: Absolute path to write ``graph.json``.
        """
        logger.info("Writing graph JSON to %s", output_path)

        data = self._serialize(graph)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                cls=_GraphEncoder,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            f.write("\n")  # Trailing newline

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.1f KB)", output_path.name, size_kb)

    @staticmethod
    def _serialize(graph: CodeGraph) -> dict[str, Any]:  # noqa: C901, PLR0912
        """Convert CodeGraph to a JSON-serializable dict.

        Returns:
            A nested dict with ``meta``, ``nodes``, and ``edges`` keys.
        """
        meta_dict: dict[str, Any] = {}
        if graph.meta is not None:
            meta_dict = asdict(graph.meta)

        # Serialize nodes — sorted by ID for determinism
        nodes_list: list[dict[str, Any]] = []
        for node in sorted(graph.nodes, key=lambda n: n.id):
            nd: dict[str, Any] = {
                "id": node.id,
                "label": node.label,
                "kind": node.kind,
                "file": node.file,
            }
            if node.span is not None:
                if is_dataclass(node.span) and not isinstance(node.span, type):
                    nd["span"] = asdict(node.span)
                elif hasattr(node.span, "__dict__"):
                    nd["span"] = node.span.__dict__
                else:
                    nd["span"] = node.span
            if node.properties:
                nd["properties"] = dict(sorted(node.properties.items()))
            if node.service:
                nd["service"] = node.service
            if node.origin_id:
                nd["origin_id"] = node.origin_id
            nodes_list.append(nd)

        # Serialize edges — sorted by (source, target, relation)
        edges_list: list[dict[str, Any]] = []
        for edge in sorted(
            graph.edges,
            key=lambda e: (e.source, e.target, e.relation),
        ):
            ed: dict[str, Any] = {
                "source": edge.source,
                "target": edge.target,
                "relation": edge.relation,
                "confidence": edge.confidence.value
                if hasattr(edge.confidence, "value") else edge.confidence,
                "confidence_score": edge.confidence_score,
            }
            if edge.file:
                ed["file"] = edge.file
            if edge.span is not None:
                if is_dataclass(edge.span) and not isinstance(edge.span, type):
                    ed["span"] = asdict(edge.span)
                elif hasattr(edge.span, "__dict__"):
                    ed["span"] = edge.span.__dict__
                else:
                    ed["span"] = edge.span
            if edge.properties:
                ed["properties"] = dict(sorted(edge.properties.items()))
            edges_list.append(ed)

        # Serialize hyperedges — sorted by ID for determinism
        result: dict[str, Any] = {
            "meta": meta_dict,
            "nodes": nodes_list,
            "edges": edges_list,
        }
        if graph.hyperedges:
            hyperedges_list: list[dict[str, Any]] = []
            for he in sorted(graph.hyperedges, key=lambda h: h.id):
                hed: dict[str, Any] = {
                    "id": he.id,
                    "relation": he.relation.value,
                    "members": he.members,
                    "label": he.label,
                }
                if he.metadata:
                    hed["metadata"] = dict(sorted(he.metadata.items()))
                hyperedges_list.append(hed)
            result["hyperedges"] = hyperedges_list

        return result

    # ------------------------------------------------------------------
    # Deserialization
    # ------------------------------------------------------------------

    @staticmethod
    def read(path: Path) -> CodeGraph:
        """Deserialize a ``graph.json`` file back into a :class:`CodeGraph`.

        Args:
            path: Path to a JSON file previously written by :meth:`write`.

        Returns:
            A reconstructed :class:`CodeGraph`.

        Raises:
            ValueError: If the JSON structure is missing required keys.
            FileNotFoundError: If *path* does not exist.
        """
        logger.info("Reading graph JSON from %s", path)
        with path.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        if not isinstance(data, dict):
            msg = f"Expected JSON object at top level, got {type(data).__name__}"
            raise TypeError(msg)

        nodes = [
            _deserialize_node(nd) for nd in data.get("nodes", [])
        ]
        edges = [
            _deserialize_edge(ed) for ed in data.get("edges", [])
        ]
        meta = _deserialize_meta(data.get("meta")) if data.get("meta") else None

        hyperedges = [
            _deserialize_hyperedge(he) for he in data.get("hyperedges", [])
        ]

        return CodeGraph(
            nodes=nodes, edges=edges, meta=meta, hyperedges=hyperedges,
        )


# ------------------------------------------------------------------
# region:    --- Deserialization helpers
# ------------------------------------------------------------------


def _deserialize_span(raw: dict[str, Any] | None) -> Span | None:
    """Reconstruct a ``Span`` from its JSON dict, or ``None``."""
    if raw is None:
        return None
    return Span(
        start_line=int(raw["start_line"]),
        start_col=int(raw["start_col"]),
        end_line=int(raw["end_line"]),
        end_col=int(raw["end_col"]),
    )


def _deserialize_node(raw: dict[str, Any]) -> GraphNode:
    """Reconstruct a ``GraphNode`` from its JSON dict."""
    return GraphNode(
        id=str(raw["id"]),
        label=str(raw["label"]),
        kind=NodeKind(raw["kind"]),
        file=str(raw.get("file", "")),
        span=_deserialize_span(raw.get("span")),
        properties={
            str(k): str(v) for k, v in raw.get("properties", {}).items()
        },
        service=str(raw.get("service", "")),
        origin_id=str(raw.get("origin_id", "")),
    )


def _deserialize_edge(raw: dict[str, Any]) -> GraphEdge:
    """Reconstruct a ``GraphEdge`` from its JSON dict."""
    return GraphEdge(
        source=str(raw["source"]),
        target=str(raw["target"]),
        relation=EdgeRelation(raw["relation"]),
        confidence=Confidence(raw["confidence"]),
        confidence_score=float(raw["confidence_score"]),
        file=str(raw.get("file", "")),
        span=_deserialize_span(raw.get("span")),
        properties={
            str(k): str(v) for k, v in raw.get("properties", {}).items()
        },
    )


def _deserialize_meta(raw: dict[str, Any] | None) -> WorkspaceMeta | None:
    """Reconstruct a ``WorkspaceMeta`` from its JSON dict."""
    if not raw:
        return None
    return WorkspaceMeta(
        schema_version=str(raw.get("schema_version", "")),
        tool_version=str(raw.get("tool_version", "")),
        generated_at=str(raw.get("generated_at", "")),
        workspace_root=str(raw.get("workspace_root", "")),
        total_crates=int(raw.get("total_crates", 0)),
        total_files=int(raw.get("total_files", 0)),
        total_structs=int(raw.get("total_structs", 0)),
        total_enums=int(raw.get("total_enums", 0)),
        total_traits=int(raw.get("total_traits", 0)),
        total_functions=int(raw.get("total_functions", 0)),
        total_impl_blocks=int(raw.get("total_impl_blocks", 0)),
        total_self_methods=int(raw.get("total_self_methods", 0)),
        total_pkg_method_call_sites=int(
            raw.get("total_pkg_method_call_sites", 0),
        ),
        total_call_edges=int(raw.get("total_call_edges", 0)),
        total_rationale_comments=int(
            raw.get("total_rationale_comments", 0),
        ),
        total_errors=int(raw.get("total_errors", 0)),
    )


def _deserialize_hyperedge(raw: dict[str, Any]) -> HyperEdge:
    """Reconstruct a :class:`HyperEdge` from a JSON dict."""
    return HyperEdge(
        id=str(raw["id"]),
        relation=HyperRelation(raw["relation"]),
        members=list(raw["members"]),
        label=str(raw["label"]),
        metadata=dict(raw.get("metadata") or {}),
    )


# endregion: --- Deserialization helpers
