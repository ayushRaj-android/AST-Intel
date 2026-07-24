"""Third-party data-egress report — derive a structured egress view from the graph.

Consumes the enriched ``HTTP_CALL`` and ``SDK_CALL`` nodes produced by the graph
builder and produces a JSON-serializable report of outbound third-party API calls
and the data sent to them.  Shared by the ``egress`` CLI command and the
``get_data_egress`` MCP tool so both surfaces return identical results.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from ast_intel.extractors._thirdparty_registry import extract_host
from ast_intel.models.graph_model import NodeKind

if TYPE_CHECKING:
    from ast_intel.models.graph_model import CodeGraph, GraphNode

__all__: list[str] = ["build_egress_report"]


def _parse_payload(payload_json: str | None) -> list[dict[str, str]]:
    """Reconstruct structured payload fields from the node's ``payload_json``."""
    if not payload_json:
        return []
    try:
        raw = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return []
    fields: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fields.append({
            "name": item.get("name", ""),
            "source_kind": item.get("source_kind", "unknown"),
            "value_preview": item.get("value", ""),
        })
    return fields


def _line(node: GraphNode) -> int | None:
    return node.span.start_line if node.span else None


def _http_entry(node: GraphNode) -> dict[str, Any]:
    props = node.properties
    url = props.get("url", "")
    return {
        "kind": "http",
        "third_party": props.get("third_party") == "true",
        "vendor": props.get("vendor"),
        "category": props.get("category"),
        "direction": props.get("direction"),
        "host": extract_host(url),
        "url": url or None,
        "method": props.get("method"),
        "library": props.get("library"),
        "caller": props.get("caller"),
        "file": node.file,
        "line": _line(node),
        "confidence": props.get("payload_confidence"),
        "has_secrets": props.get("has_secrets") == "true",
        "heuristic": False,
        "payload_fields": _parse_payload(props.get("payload_json")),
    }


def _sdk_entry(node: GraphNode) -> dict[str, Any]:
    props = node.properties
    return {
        "kind": "sdk",
        "third_party": True,
        "vendor": props.get("vendor"),
        "category": props.get("category"),
        "sdk": props.get("sdk"),
        "method": props.get("method"),
        "caller": props.get("caller"),
        "file": node.file,
        "line": _line(node),
        "confidence": props.get("payload_confidence"),
        "has_secrets": props.get("has_secrets") == "true",
        "heuristic": props.get("heuristic") == "true",
        "payload_fields": _parse_payload(props.get("payload_json")),
    }


def build_egress_report(
    graph: CodeGraph, *, third_party_only: bool = True,
    include_heuristic: bool = True,
) -> dict[str, Any]:
    """Build a structured data-egress report from an enriched code graph.

    Args:
        graph: A :class:`CodeGraph` with enriched ``HTTP_CALL`` / ``SDK_CALL``
            nodes (as produced by the graph builder).
        third_party_only: When ``True`` (default) internal/first-party HTTP
            calls are excluded; SDK calls are always third-party.
        include_heuristic: When ``False``, drop low-confidence heuristic
            detections (``unknown``-vendor SDK calls on unrecognized
            third-party packages).

    Returns:
        A JSON-serializable dict with an ``egress`` list plus summary counts.
    """
    entries: list[dict[str, Any]] = []
    for node in graph.nodes:
        if node.kind == NodeKind.HTTP_CALL:
            if third_party_only and node.properties.get("third_party") != "true":
                continue
            entries.append(_http_entry(node))
        elif node.kind == NodeKind.SDK_CALL:
            entries.append(_sdk_entry(node))

    if not include_heuristic:
        entries = [e for e in entries if not e["heuristic"]]

    entries.sort(key=lambda e: (e["file"], e["line"] or 0, e["method"] or ""))
    vendors = Counter(e["vendor"] for e in entries if e["vendor"])

    return {
        "workspace_root": graph.meta.workspace_root if graph.meta else "",
        "third_party_only": third_party_only,
        "egress_count": len(entries),
        "vendors": dict(vendors),
        "secrets_sent": sum(1 for e in entries if e["has_secrets"]),
        "egress": entries,
    }
