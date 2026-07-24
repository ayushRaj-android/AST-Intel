"""MCP server — expose the code graph as tools for AI agents.

Wraps :class:`~ast_intel.core._query_engine.QueryEngine` methods as
`Model Context Protocol <https://modelcontextprotocol.io>`_ tools so
that GitHub Copilot, Cursor, Claude, and other MCP-aware agents can
query the code graph interactively.

Usage::

    ast-intel serve /path/to/repo                 # stdio transport (default)
    ast-intel serve /path/to/repo --port 7500     # Streamable HTTP at /mcp

Programmatic::

    import asyncio
    from ast_intel.mcp_server import run_stdio, run_http

    asyncio.run(run_stdio(repo_path))
    asyncio.run(run_http(repo_path, host="0.0.0.0", port=7500))
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from ast_intel.core._graph_loader import load_or_build_graph
from ast_intel.core._query_engine import (
    AmbiguousSymbolError,
    CommunityResult,
    DependencyResult,
    FileInfo,
    ImpactResult,
    NodeExplanation,
    QueryEngine,
    RouteInfo,
    SymbolContext,
    UsageEntry,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

if TYPE_CHECKING:
    from ast_intel.core._watcher import FileWatcher

__all__: list[str] = ["create_server", "run_stdio"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Serialization helpers
# ---------------------------------------------------------------------------


def _node_dict(n: GraphNode) -> dict[str, Any]:
    """Serialize a :class:`GraphNode` to a plain dict."""
    d: dict[str, Any] = {
        "id": n.id,
        "label": n.label,
        "kind": n.kind.value,
        "file": n.file,
    }
    if n.span:
        d["span"] = {"start_line": n.span.start_line, "end_line": n.span.end_line}
    if n.properties:
        d["properties"] = n.properties
    return d


def _edge_dict(e: GraphEdge) -> dict[str, Any]:
    """Serialize a :class:`GraphEdge` to a plain dict."""
    d: dict[str, Any] = {
        "source": e.source,
        "target": e.target,
        "relation": e.relation.value,
        "confidence": e.confidence.value,
        "confidence_score": e.confidence_score,
    }
    if e.file:
        d["file"] = e.file
    if e.span:
        d["span"] = {"start_line": e.span.start_line, "end_line": e.span.end_line}
    return d


def _dep_dict(dep: DependencyResult) -> dict[str, Any]:
    """Serialize a :class:`DependencyResult` to a plain dict."""
    out: dict[str, Any] = {"node": _node_dict(dep.node)}
    for field_name in (
        "calls", "imports", "uses_methods", "inherits",
        "implements", "contains", "other",
    ):
        nodes: tuple[GraphNode, ...] = getattr(dep, field_name)
        if nodes:
            out[field_name] = [_node_dict(n) for n in nodes]
    return out


def _explanation_dict(exp: NodeExplanation) -> dict[str, Any]:
    """Serialize a :class:`NodeExplanation` to a plain dict."""
    return {
        "node": _node_dict(exp.node),
        "degree": exp.degree,
        "in_degree": exp.in_degree,
        "out_degree": exp.out_degree,
        "callers": list(exp.callers),
        "callees": list(exp.callees),
        "community": exp.community,
        "role": exp.role,
        "summary": exp.summary,
    }


def _impact_dict(imp: ImpactResult) -> dict[str, Any]:
    """Serialize an :class:`ImpactResult` to a plain dict."""
    return {
        "root": _node_dict(imp.root),
        "affected_count": len(imp.affected),
        "affected": [_node_dict(n) for n in imp.affected],
        "distances": imp.distances,
        "depth_limit": imp.depth_limit,
    }


def _context_dict(ctx: SymbolContext) -> dict[str, Any]:
    """Serialize a :class:`SymbolContext` to a plain dict."""
    return {
        "node": _node_dict(ctx.node),
        "explanation": _explanation_dict(ctx.explanation),
        "dependencies": _dep_dict(ctx.dependencies),
        "dependents": _dep_dict(ctx.dependents),
        "siblings": [_node_dict(n) for n in ctx.siblings],
        "community_peers": [_node_dict(n) for n in ctx.community_peers],
        "similar": [_node_dict(n) for n in ctx.similar],
    }


def _usage_dict(u: UsageEntry) -> dict[str, Any]:
    """Serialize a :class:`UsageEntry` to a plain dict."""
    d: dict[str, Any] = {
        "node": _node_dict(u.node),
        "relation": u.relation.value,
        "file": u.file,
    }
    if u.span:
        d["span"] = {"start_line": u.span.start_line, "end_line": u.span.end_line}
    return d


def _file_info_dict(f: FileInfo) -> dict[str, Any]:
    """Serialize a :class:`FileInfo` to a plain dict."""
    return {
        "file": f.node.file,
        "symbol_count": f.symbol_count,
        "key_symbols": list(f.key_symbols),
        "structs": f.structs,
        "functions": f.functions,
        "methods": f.methods,
        "traits": f.traits,
    }


def _community_dict(c: CommunityResult) -> dict[str, Any]:
    """Serialize a :class:`CommunityResult` to a plain dict."""
    return {
        "community_id": c.community_id,
        "member_count": c.member_count,
        "hub_nodes": [_node_dict(n) for n in c.hub_nodes],
        "members": [_node_dict(n) for n in c.members],
    }


def _route_dict(r: RouteInfo) -> dict[str, Any]:
    """Serialize a :class:`RouteInfo` to a plain dict."""
    d: dict[str, Any] = {
        "method": r.method,
        "path": r.path,
        "handler": r.handler,
        "framework": r.framework,
        "file": r.file,
    }
    if r.service:
        d["service"] = r.service
    if r.node.span:
        d["span"] = {
            "start_line": r.node.span.start_line,
            "end_line": r.node.span.end_line,
        }
    if r.handler_node is not None:
        d["handler_node"] = _node_dict(r.handler_node)
    return d


def _text(data: object) -> list[TextContent]:
    """Wrap *data* as a JSON-serialized MCP text response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _error(message: str) -> list[TextContent]:
    """Return a structured error response."""
    return [TextContent(type="text", text=json.dumps({"error": message}))]


# endregion: --- Serialization helpers


# ---------------------------------------------------------------------------
# region:    --- History serialization & dispatch (Phase 3)
# ---------------------------------------------------------------------------


def _commit_dict(c: Any) -> dict[str, Any]:
    """Serialize a :class:`GitCommitInfo`."""
    return {
        "sha": c.sha,
        "author": {"name": c.author_name, "email": c.author_email},
        "authored_at": c.authored_at,
        "committed_at": c.committed_at,
        "subject": c.subject,
        "body": c.body,
        "files_changed": list(c.files_changed),
        "lines_added": c.lines_added,
        "lines_removed": c.lines_removed,
    }


def _history_dict(record: Any, *, max_commits: int) -> dict[str, Any]:
    """Serialize a :class:`HistoryRecord`."""
    commits = list(record.commits)[:max_commits]
    return {
        "symbol": {
            "id": record.symbol.id,
            "label": record.symbol.label,
            "kind": record.symbol.kind.value,
            "file": record.symbol.file,
            "start_line": record.symbol.start_line,
            "end_line": record.symbol.end_line,
        },
        "head_sha": record.head_sha,
        "total_lines": record.total_lines,
        "total_commits": len(record.commits),
        "commits": [_commit_dict(c) for c in commits],
        "truncated": len(record.commits) > max_commits,
    }


def _ownership_dict(record: Any) -> dict[str, Any]:
    """Serialize an :class:`OwnershipRecord`."""
    return {
        "symbol": {
            "id": record.symbol.id,
            "label": record.symbol.label,
            "file": record.symbol.file,
        },
        "head_sha": record.head_sha,
        "total_commits": record.total_commits,
        "total_lines": record.total_lines,
        "scores": [
            {
                "author": {"name": s.author_name, "email": s.author_email},
                "commit_count": s.commit_count,
                "last_commit_at": s.last_commit_at,
                "blame_lines": s.blame_lines,
                "commit_frequency": round(s.commit_frequency, 4),
                "recency_factor": round(s.recency_factor, 4),
                "blame_share": round(s.blame_share, 4),
                "score": round(s.score, 4),
            }
            for s in record.scores
        ],
    }


def _signal_dict(s: Any) -> dict[str, Any]:
    """Serialize a :class:`DecisionSignal` with its mandatory citation."""
    return {
        "kind": s.kind.value,
        "summary": s.summary,
        "detail": s.detail,
        "confidence": round(s.confidence, 4),
        "citation": {
            "type": s.citation.type,
            "url": s.citation.url,
            "author": s.citation.author,
            "date": s.citation.date,
            "sha": s.citation.sha,
            "pr_id": s.citation.pr_id,
        },
    }


def _decision_dict(enriched: Any, *, max_signals: int) -> dict[str, Any]:
    """Serialize an :class:`EnrichedHistoryRecord` for MCP output."""
    signals = list(enriched.signals)[:max_signals]
    pr_summaries: list[dict[str, Any]] = []
    seen_prs: set[str] = set()
    for ec in enriched.enriched_commits:
        if ec.pr is None or ec.pr.id in seen_prs:
            continue
        seen_prs.add(ec.pr.id)
        pr_summaries.append(
            {
                "id": ec.pr.id,
                "title": ec.pr.title,
                "url": ec.pr.url,
                "author": ec.pr.author_name,
                "merged_at": ec.pr.merged_at,
                "state": ec.pr.state,
            },
        )
    return {
        "symbol": {
            "id": enriched.record.symbol.id,
            "label": enriched.record.symbol.label,
            "file": enriched.record.symbol.file,
        },
        "head_sha": enriched.record.head_sha,
        "total_commits": len(enriched.record.commits),
        "pull_requests": pr_summaries,
        "signals": [_signal_dict(s) for s in signals],
        "warnings": list(enriched.warnings),
        "truncated": len(enriched.signals) > max_signals,
    }


def _build_history_stack(
    repo_path: Path,
) -> tuple[Any | None, Any | None, Any | None]:
    """Build a (HistoryBuilder, EnrichmentPipeline, SignalAggregator) triple.

    All three components are optional — if anything fails we return
    ``(None, None, None)`` and the graph-only MCP surface keeps working.
    """
    try:
        from ast_intel.history.enrichment import EnrichmentPipeline
        from ast_intel.history.history_builder import HistoryBuilder
        from ast_intel.history.providers.registry import build_provider
        from ast_intel.history.signals.signal_aggregator import (
            SignalAggregator,
        )
    except ImportError as exc:  # pragma: no cover — defensive.
        logger.debug("History stack unavailable: %s", exc)
        return None, None, None

    try:
        builder = HistoryBuilder(repo_root=repo_path)
        provider = build_provider(repo_path)
        pipeline = EnrichmentPipeline(provider)
        aggregator = SignalAggregator(
            repo_path,
            history_builder=builder,
            enrichment=pipeline,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("History stack init failed: %s", exc)
        return None, None, None
    return builder, pipeline, aggregator


def _history_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """``get_symbol_history`` handler."""
    symbol = arguments["symbol"]
    max_commits = int(arguments.get("max_commits", 20))
    record = engine.get_symbol_history(symbol)
    return _text(_history_dict(record, max_commits=max_commits))


def _ownership_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """``get_ownership`` handler."""
    record = engine.get_ownership(arguments["symbol"])
    return _text(_ownership_dict(record))


def _decision_context_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """``get_decision_context`` handler."""
    max_signals = int(arguments.get("max_signals", 10))
    enriched = engine.get_decision_context(arguments["symbol"])
    return _text(_decision_dict(enriched, max_signals=max_signals))


# ---------------------------------------------------------------------------
# Phase 4 — rich-signal serialisation & handlers
# ---------------------------------------------------------------------------


def _rfc_dict(link: Any) -> dict[str, Any]:
    return {
        "rfc_id": link.rfc_id,
        "rfc_title": link.rfc_title,
        "rfc_url": link.rfc_url,
        "section": link.section,
        "excerpt": link.excerpt,
        "symbol_label": link.symbol_label,
        "author": link.author,
        "created_at": link.created_at,
        "status": link.status,
    }


def _incident_dict(inc: Any) -> dict[str, Any]:
    return {
        "incident_id": inc.incident_id,
        "title": inc.title,
        "url": inc.url,
        "severity": inc.severity,
        "status": inc.status,
        "symbol_label": inc.symbol_label,
        "occurred_at": inc.occurred_at,
        "resolved_at": inc.resolved_at,
        "fix_commit": inc.fix_commit,
        "fix_pr": inc.fix_pr,
        "postmortem_url": inc.postmortem_url,
    }


def _tribal_dict(note: Any) -> dict[str, Any]:
    return {
        "id": note.id,
        "symbol_label": note.symbol_label,
        "author": note.author,
        "text": note.text,
        "tags": list(note.tags),
        "upvotes": note.upvotes,
        "downvotes": note.downvotes,
        "created_at": note.created_at,
    }


def _scored_signal_dict(s: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "signal_type": s.signal_type.value,
        "summary": s.summary,
        "detail": s.detail,
        "confidence": round(s.confidence, 4),
        "citation": {
            "type": s.citation.type,
            "url": s.citation.url,
            "author": s.citation.author,
            "date": s.citation.date,
            "sha": s.citation.sha,
            "pr_id": s.citation.pr_id,
        },
        "conflicts_with": list(s.conflicts_with),
    }


def _conflict_dict(c: Any) -> dict[str, Any]:
    return {
        "symbol_id": c.symbol_id,
        "signal_ids": list(c.signal_ids),
        "conflict_type": c.conflict_type,
        "description": c.description,
        "recommended_resolution": c.recommended_resolution,
    }


def _rich_context_dict(ctx: Any, *, max_signals: int) -> dict[str, Any]:
    signals = list(ctx.signals)[:max_signals]
    return {
        "symbol_id": ctx.symbol_id,
        "head_sha": ctx.enriched.record.head_sha,
        "ownership": _ownership_dict(ctx.ownership),
        "rfcs": [_rfc_dict(r) for r in ctx.rfcs],
        "incidents": [_incident_dict(i) for i in ctx.incidents],
        "tribal_knowledge": [_tribal_dict(t) for t in ctx.tribal],
        "signals": [_scored_signal_dict(s) for s in signals],
        "conflicts": [_conflict_dict(c) for c in ctx.conflicts],
        "hidden_signal_count": ctx.hidden_signal_count,
        "truncated": len(ctx.signals) > max_signals,
    }


def _rich_context_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """``get_rich_context`` handler — fuses all Phase 1-4 signals."""
    max_signals = int(arguments.get("max_signals", 20))
    include_hidden = bool(arguments.get("include_hidden", False))
    ctx = engine.get_rich_context(
        arguments["symbol"],
        include_hidden=include_hidden,
    )
    return _text(_rich_context_dict(ctx, max_signals=max_signals))


def _list_rfcs_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    rfcs = engine.list_rfcs(arguments["symbol"])
    return _text({"rfcs": [_rfc_dict(r) for r in rfcs]})


def _list_incidents_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    incidents = engine.list_incidents(arguments["symbol"])
    return _text({"incidents": [_incident_dict(i) for i in incidents]})


def _list_tribal_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    notes = engine.list_tribal_knowledge(arguments["symbol"])
    return _text({"notes": [_tribal_dict(n) for n in notes]})


def _add_tribal_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    tags_raw = arguments.get("tags") or []
    note = engine.add_tribal_knowledge(
        arguments["symbol"],
        author=arguments["author"],
        text=arguments["text"],
        tags=tuple(tags_raw),
    )
    return _text({"ok": True, "note": _tribal_dict(note)})


def _add_incident_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    inc = engine.add_incident(
        arguments["symbol"],
        title=arguments["title"],
        severity=arguments.get("severity", "sev3"),
        status=arguments.get("status", "investigating"),
        url=arguments.get("url", ""),
        postmortem_url=arguments.get("postmortem_url", ""),
        fix_commit=arguments.get("fix_commit", ""),
        fix_pr=arguments.get("fix_pr", ""),
    )
    return _text({"ok": True, "incident": _incident_dict(inc)})


def _submit_feedback_tool(
    engine: QueryEngine,
    arguments: dict[str, Any],
) -> list[TextContent]:
    entry = engine.submit_feedback(
        arguments["signal_id"],
        arguments["verdict"],
        author=arguments["author"],
        comment=arguments.get("comment", ""),
        replacement_text=arguments.get("replacement_text", ""),
    )
    return _text(
        {
            "ok": True,
            "signal_id": entry.signal_id,
            "verdict": entry.verdict.value,
            "created_at": entry.created_at,
        },
    )


# endregion: --- History serialization & dispatch


# ---------------------------------------------------------------------------
# region:    --- Resource helpers
# ---------------------------------------------------------------------------

_SUMMARY_URI: str = "ast-intel://summary"


def _build_summary(graph: CodeGraph) -> dict[str, Any]:
    """Build a codebase summary dict for the resource endpoint."""
    node_counts = Counter(n.kind.value for n in graph.nodes)
    edge_counts = Counter(e.relation.value for e in graph.edges)

    crates = sorted(
        n.label for n in graph.nodes if n.kind == NodeKind.CRATE
    )

    # Top-connected: nodes with the highest total degree.
    degree: Counter[str] = Counter()
    for e in graph.edges:
        degree[e.source] += 1
        degree[e.target] += 1
    node_map = {n.id: n for n in graph.nodes}
    top_connected = [
        {
            "id": nid,
            "label": node_map[nid].label if nid in node_map else nid,
            "kind": node_map[nid].kind.value if nid in node_map else "unknown",
            "degree": deg,
        }
        for nid, deg in degree.most_common(10)
        if nid in node_map
    ]

    return {
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
        "nodes_by_kind": dict(node_counts.most_common()),
        "edges_by_relation": dict(edge_counts.most_common()),
        "crates": crates,
        "file_count": node_counts.get(NodeKind.FILE.value, 0),
        "top_connected": top_connected,
    }


# endregion: --- Resource helpers


# ---------------------------------------------------------------------------
# region:    --- Tool Definitions
# ---------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    Tool(
        name="search_symbols",
        description=(
            "Search for symbols (functions, structs, traits, etc.) "
            "by name pattern. Returns up to 50 matches sorted by "
            "connectivity (most-connected first)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Substring or regex to match against symbol names.",
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Optional filter by node kind: "
                        + ", ".join(k.value for k in NodeKind)
                    ),
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat pattern as a Python regex. Default: false.",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    ),
    Tool(
        name="find_path",
        description=(
            "Find the shortest dependency path between two symbols "
            "in the code graph. Shows how symbol A reaches symbol B "
            "through calls, imports, inheritance, etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source symbol name or ID.",
                },
                "target": {
                    "type": "string",
                    "description": "Target symbol name or ID.",
                },
            },
            "required": ["source", "target"],
        },
    ),
    Tool(
        name="explain_symbol",
        description=(
            "Get a structural explanation of a symbol's role in the "
            "codebase: degree, callers, callees, community membership, "
            "structural role (hub/bridge/leaf/isolated), and a "
            "human-readable summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID to explain.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_impact",
        description=(
            "Compute the blast radius of changing a symbol. Returns "
            "all symbols that transitively depend on the target, "
            "sorted by distance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max traversal depth (0 = unlimited). Default: 0.",
                    "default": 0,
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_dependencies",
        description=(
            "Get the forward (outgoing) dependencies of a symbol, "
            "grouped by relationship type: calls, imports, inherits, "
            "implements, contains, etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_dependents",
        description=(
            "Get the reverse (incoming) dependents of a symbol — "
            "everything that depends on it, grouped by relationship type."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_context",
        description=(
            "Get full unified context for a symbol in one call: "
            "structural explanation, forward dependencies, reverse "
            "dependents, same-file siblings, community peers, and "
            "structurally similar symbols."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="find_usages",
        description=(
            "Find all references to a symbol across the codebase. "
            "Returns each referencing node with the relationship type, "
            "file, and source location."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="list_files",
        description=(
            "List files in the code graph with symbol counts "
            "and key symbols. Supports glob filtering and "
            "pagination. Omit offset/limit to get all files."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Optional glob pattern to filter files "
                        "(e.g. '*redis*', 'src/services/*'). "
                        "Empty string returns all."
                    ),
                    "default": "",
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Number of files to skip (for pagination). "
                        "Default: 0."
                    ),
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of files to return. "
                        "Omit to return all files."
                    ),
                },
            },
        },
    ),
    Tool(
        name="find_dead_code",
        description=(
            "List symbols with zero incoming reference edges "
            "(dead-code candidates). Structural ownership edges "
            "(contains, method_of) are ignored; only true symbols "
            "(functions, methods, types, constants, macros) are reported."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_implementors",
        description=(
            "Find all types that implement a trait or interface. "
            "Returns each implementor with its file path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trait": {
                    "type": "string",
                    "description": "Trait/interface name or node ID.",
                },
            },
            "required": ["trait"],
        },
    ),
    Tool(
        name="find_similar",
        description=(
            "Find symbols that are structurally similar to the given "
            "symbol (based on Jaccard similarity of structural features). "
            "Useful for finding duplicate implementations or refactoring "
            "candidates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return. Default: 5.",
                    "default": 5,
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_community",
        description=(
            "Get the community cluster that a symbol belongs to. "
            "Returns all members and hub nodes of the community. "
            "Requires that the graph was built with --analyze."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name or node ID.",
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_hyperedges",
        description=(
            "List hyperedges (higher-order relationships) detected in "
            "the graph. Returns groups such as interface implementors, "
            "route clusters, data-flow chains, and communities."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "relation": {
                    "type": "string",
                    "description": (
                        "Filter by hyperedge relation value "
                        "(implements_group, flow, route_group, "
                        "field_group, community). Case-insensitive. "
                        "Optional."
                    ),
                },
                "member": {
                    "type": "string",
                    "description": (
                        "Filter to hyperedges containing this node ID "
                        "or substring. Optional."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return. Default: 50.",
                    "default": 50,
                },
            },
        },
    ),
    Tool(
        name="get_routes",
        description=(
            "List HTTP routes / endpoints defined in the repository "
            "(Axum, Actix, FastAPI, Flask, Express, Spring). Each entry "
            "gives the HTTP method, URL path, handler, framework, file, "
            "and owning service. Optionally filter by service."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "Filter to routes owned by this service "
                        "(set by 'ast-intel merge' on multi-repo graphs). "
                        "Omit to list every route in the repository."
                    ),
                },
            },
        },
    ),
    # ---------------------------------------------------------------
    # region:    --- IaC Tools
    # ---------------------------------------------------------------
    Tool(
        name="iac_overview",
        description=(
            "Get a summary of all Infrastructure-as-Code resources "
            "in the project, grouped by domain (K8s, Helm, Docker, "
            "Ansible, CI/CD, Terraform). Shows resource counts and "
            "key items per domain."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Filter to a specific IaC domain: "
                        "k8s, helm, docker, ansible, cicd, terraform. "
                        "Omit for all domains."
                    ),
                },
            },
        },
    ),
    Tool(
        name="get_helm_chart",
        description=(
            "Get detailed info about a Helm chart: metadata, "
            "templates with K8s resource kinds they produce, "
            "values tree, and dependency edges. "
            "Pass chart name or use 'all' to list all charts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "chart": {
                    "type": "string",
                    "description": (
                        "Helm chart name (e.g. 'safeguard') "
                        "or 'all' to list all charts."
                    ),
                },
            },
            "required": ["chart"],
        },
    ),
    Tool(
        name="get_k8s_resources",
        description=(
            "List Kubernetes resources (Deployments, Services, "
            "ConfigMaps, Secrets, RBAC, etc.) with their "
            "relationships: Service→Deployment routing, "
            "ConfigMap/Secret usage, RBAC bindings. "
            "Includes resources defined in standalone manifests "
            "and those templated by Helm charts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Filter by K8s kind: Deployment, Service, "
                        "ConfigMap, Secret, RBAC, Ingress, CronJob, "
                        "Job, PVC, or 'all'. Default: all."
                    ),
                    "default": "all",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Filter by resource name substring. Optional."
                    ),
                },
            },
        },
    ),
    Tool(
        name="get_pipeline",
        description=(
            "Get CI/CD pipeline structure: stages, jobs, steps, "
            "triggers, and deployment targets. "
            "Pass pipeline name or 'all' to list everything."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Pipeline name or substring, or 'all'. "
                        "Default: all."
                    ),
                    "default": "all",
                },
            },
        },
    ),
    Tool(
        name="get_docker_info",
        description=(
            "List Docker images, build stages, services, "
            "exposed ports, and inter-stage dependencies "
            "across all Dockerfiles and docker-compose files."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Filter by image/stage/service name. Optional."
                    ),
                },
            },
        },
    ),
    Tool(
        name="get_cloud_resources",
        description=(
            "List cloud infrastructure resources (databases, caches, "
            "queues, topics, streams, storage, secrets) used by the repo. "
            "Detected from both application code (SDK client instantiation) "
            "and IaC (Terraform, Bicep, ARM). Each resource is attributed "
            "to its caller function. Use this to answer 'what cloud infra "
            "does service X use?'"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Filter by category: database, cache, queue, "
                        "topic, stream, storage, secret. Omit for all."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Filter by provider: azure, aws, gcp, generic. "
                        "Omit for all."
                    ),
                },
                "service": {
                    "type": "string",
                    "description": (
                        "Filter by module/service name (substring match "
                        "on file path). Omit for all."
                    ),
                },
            },
        },
    ),
    # endregion: --- IaC Tools

    # region:    --- History / Decision-Context Tools (Phase 3)
    Tool(
        name="get_symbol_history",
        description=(
            "Return the raw git commit history of a symbol — every "
            "commit that touched its source range, newest first. "
            "Use this when the user asks WHO last changed a function, "
            "WHEN it was introduced, or HOW often it churns."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name, file::name, or full GraphNode id."
                    ),
                },
                "max_commits": {
                    "type": "integer",
                    "description": "Cap on returned commits. Default 20.",
                    "default": 20,
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_ownership",
        description=(
            "Return the per-author ownership ranking for a symbol. "
            "Combines commit frequency (40%), per-author recency (35%), "
            "and blame share (25%). Use this to find the best person "
            "to ask about a piece of code."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name, file::name, or full GraphNode id."
                    ),
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_decision_context",
        description=(
            "Return the decision-context evidence for a symbol: PR "
            "descriptions and inline review threads that explain WHY "
            "the code looks the way it does. Every signal carries a "
            "citation (URL + author + date) — never fabricate."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name, file::name, or full GraphNode id."
                    ),
                },
                "max_signals": {
                    "type": "integer",
                    "description": "Cap on returned signals. Default 10.",
                    "default": 10,
                },
            },
            "required": ["symbol"],
        },
    ),
    # endregion: --- History tools

    # region:    --- Phase 4 — Rich Signals & Feedback
    Tool(
        name="get_rich_context",
        description=(
            "Return ALL evidence for a symbol — git history, PR/review "
            "signals, RFC links, incident records, and tribal-knowledge "
            "notes — confidence-scored and de-duplicated, with heuristic "
            "conflict detection applied. Every signal carries a citation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name, file::name, or full GraphNode id."
                    ),
                },
                "max_signals": {
                    "type": "integer",
                    "description": "Cap on returned signals. Default 20.",
                    "default": 20,
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": (
                        "Include signals below the confidence floor. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_rfcs_for_symbol",
        description=(
            "Return markdown RFC/design-doc links discovered for a symbol. "
            "Each link includes the source section + ±3-paragraph excerpt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_incident_history",
        description=(
            "Return manually-recorded incident reports linked to a symbol — "
            "severity, status, fix commit/PR, and postmortem URL."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="get_tribal_knowledge",
        description=(
            "Return developer-authored notes about a symbol with upvote "
            "and downvote counts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
            },
            "required": ["symbol"],
        },
    ),
    Tool(
        name="add_tribal_knowledge",
        description=(
            "Persist a developer note about a symbol. Other developers "
            "and agents can upvote/downvote it later."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "author": {"type": "string"},
                "text": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional tags e.g. ['perf-sensitive', 'legacy']."
                    ),
                },
            },
            "required": ["symbol", "author", "text"],
        },
    ),
    Tool(
        name="add_incident",
        description=(
            "Record a manually-entered incident linked to a symbol. "
            "Used today; PagerDuty/Jira API adapters land in Phase 4.1."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "title": {"type": "string"},
                "severity": {
                    "type": "string",
                    "enum": ["sev1", "sev2", "sev3", "sev4"],
                    "default": "sev3",
                },
                "status": {
                    "type": "string",
                    "default": "investigating",
                },
                "url": {"type": "string"},
                "postmortem_url": {"type": "string"},
                "fix_commit": {"type": "string"},
                "fix_pr": {"type": "string"},
            },
            "required": ["symbol", "title"],
        },
    ),
    Tool(
        name="submit_feedback",
        description=(
            "Accept, reject, edit, or mark-outdated a previously-returned "
            "signal. Adjusts confidence scoring so future queries rank "
            "rejected signals lower (or hide them entirely)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "verdict": {
                    "type": "string",
                    "enum": ["accept", "reject", "edit", "outdated"],
                },
                "author": {"type": "string"},
                "comment": {"type": "string"},
                "replacement_text": {
                    "type": "string",
                    "description": "Required if verdict='edit'.",
                },
            },
            "required": ["signal_id", "verdict", "author"],
        },
    ),
    # endregion: --- Phase 4 tools

    # region:    --- Minimalism Protocol Tools (Phase 3)
    Tool(
        name="find_reusable",
        description=(
            "Before writing new code, search the code graph for existing "
            "symbols that already do what you need. Returns ranked reuse "
            "candidates. Call this FIRST when implementing any new "
            "function, class, or utility. If strong matches are returned, "
            "REUSE them instead of creating a new implementation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "What you want to accomplish, described as a "
                        "likely function/class name or short phrase. "
                        "E.g. 'validate email', 'parse config', "
                        "'retry with backoff'."
                    ),
                },
                "context_file": {
                    "type": "string",
                    "description": (
                        "File you're currently editing (for relevance "
                        "ranking). Optional."
                    ),
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Filter by symbol kind: function, class, method. "
                        "Omit to search all kinds."
                    ),
                },
            },
            "required": ["intent"],
        },
    ),
    # endregion: --- Minimalism Protocol Tools

    # region:    --- Audit Tools (Phase 4 — Minimalism Protocol)
    Tool(
        name="audit_codebase",
        description=(
            "Structural audit of the codebase for over-engineering "
            "patterns. Identifies dead abstractions (0 dependents), "
            "unnecessary wrappers, duplicate implementations, "
            "over-abstracted interfaces (1 implementor), and bloat "
            "hotspots. Returns actionable findings ranked by impact. "
            "Use this to find code that can be deleted, inlined, or "
            "consolidated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "File path or directory to scope the audit. "
                        "Omit to audit the entire repository."
                    ),
                },
                "checks": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of checks to run: "
                        "dead, wrappers, duplicates, over_abstracted, "
                        "bloat. Or 'all' for everything. Default: all."
                    ),
                    "default": "all",
                },
                "max_findings": {
                    "type": "integer",
                    "description": "Max findings to return. Default: 20.",
                    "default": 20,
                },
            },
        },
    ),
    # endregion: --- Audit Tools

    # region:    --- Tracking Tools (Phase 5 — Minimalism Protocol)
    Tool(
        name="track_minimalism",
        description=(
            "Log a minimalism decision after choosing to reuse, create, "
            "or defer. Builds a ledger of decisions for later review. "
            "Call this AFTER implementing code: 'reused' if you used an "
            "existing symbol, 'created' if you wrote new code, or "
            "'deferred' if you took a shortcut with a known upgrade path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["reused", "created", "deferred"],
                    "description": (
                        "What you did: reused an existing symbol, "
                        "created new code, or deferred (took a shortcut)."
                    ),
                },
                "symbol": {
                    "type": "string",
                    "description": (
                        "The symbol you reused, created, or deferred."
                    ),
                },
                "file": {
                    "type": "string",
                    "description": "File where the action took place.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why: e.g. 'found via find_reusable', "
                        "'no match in graph', "
                        "'minimal: using dict, upgrade if >1000 items'."
                    ),
                },
            },
            "required": ["action", "symbol", "file"],
        },
    ),
    Tool(
        name="review_minimalism_ledger",
        description=(
            "Review the minimalism decision ledger: shows all logged "
            "reuse/create/defer decisions with session stats and "
            "reuse rate. Use to assess how well the minimalism "
            "protocol is being followed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "last_n": {
                    "type": "integer",
                    "description": (
                        "Number of most recent entries to return. "
                        "Default: 20."
                    ),
                    "default": 20,
                },
            },
        },
    ),
    # endregion: --- Tracking Tools
    Tool(
        name="get_data_egress",
        description=(
            "List outbound third-party API calls (raw HTTP and SaaS SDK) and "
            "the data sent to them: vendor, category, destination host/method, "
            "payload field names with data-flow provenance (from-input / "
            "from-db / from-env / literal / computed), and whether secrets are "
            "transmitted. Use to audit what data leaves the codebase to "
            "external services."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "include_internal": {
                    "type": "boolean",
                    "description": (
                        "Include internal/first-party HTTP calls too. "
                        "Default: false (third-party only)."
                    ),
                    "default": False,
                },
                "include_heuristic": {
                    "type": "boolean",
                    "description": (
                        "Include low-confidence heuristic 'unknown vendor' SDK "
                        "detections (calls on unrecognized third-party "
                        "packages). Default: true."
                    ),
                    "default": True,
                },
                "vendor": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive vendor filter "
                        "(e.g. 'Stripe')."
                    ),
                },
            },
        },
    ),
    Tool(
        name="health_check",
        description=(
            "Health check endpoint: returns the loaded graph's node "
            "count and edge count as JSON."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]

# endregion: --- Tool Definitions


# ---------------------------------------------------------------------------
# region:    --- Server Factory
# ---------------------------------------------------------------------------


class _GraphState:
    """Mutable holder for engine + graph, enabling hot-reload."""

    __slots__ = ("engine", "graph", "repo_path")

    def __init__(
        self,
        engine: QueryEngine,
        graph: CodeGraph,
        repo_path: Path | None = None,
    ) -> None:
        self.engine = engine
        self.graph = graph
        self.repo_path = repo_path


def create_server(
    engine: QueryEngine,
    graph: CodeGraph,
    *,
    state: _GraphState | None = None,
) -> Server:
    """Create an MCP :class:`Server` wired to the given *engine*.

    Each ``QueryEngine`` method is exposed as an MCP tool.  The server
    object is ready to be run via :func:`run_stdio` or passed to an
    SSE transport.

    Args:
        engine: Pre-built query engine instance.
        graph: The code graph (kept for metadata in responses).
        state: Optional mutable state holder.  When provided the
            server reads engine/graph from it, enabling hot-reload.

    Returns:
        A configured :class:`mcp.server.Server`.
    """
    if state is None:
        state = _GraphState(engine, graph)

    server = Server("ast-intel")

    @server.list_tools()  # type: ignore[misc,no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()  # type: ignore[misc]
    async def _call_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> list[TextContent]:
        try:
            return _dispatch(
                state.engine, state.graph, name, arguments,
                repo_path=state.repo_path,
            )
        except AmbiguousSymbolError as exc:
            return _text({
                "status": "ambiguous",
                "message": str(exc),
                "total_matches": exc.total,
                "top_matches": [
                    _node_dict(n) for n in exc.top_matches
                ],
            })
        except KeyError as exc:
            return _error(str(exc))
        except ValueError as exc:
            return _error(str(exc))
        except RuntimeError as exc:
            return _error(str(exc))
        except Exception as exc:
            logger.exception("Tool %r failed", name)
            return _error(f"Internal error: {exc}")

    @server.list_resources()  # type: ignore[misc,no-untyped-call]
    async def _list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl(_SUMMARY_URI),
                name="Codebase Summary",
                description=(
                    "Overview of the analyzed codebase graph: node/edge "
                    "counts by kind, crate list, file count, and "
                    "top-connected symbols."
                ),
                mimeType="application/json",
            ),
        ]

    @server.read_resource()  # type: ignore[misc,no-untyped-call]
    async def _read_resource(
        uri: AnyUrl,
    ) -> list[ReadResourceContents]:
        if str(uri) != _SUMMARY_URI:
            msg = f"Unknown resource: {uri}"
            raise ValueError(msg)
        return [
            ReadResourceContents(
                content=json.dumps(_build_summary(state.graph)),
                mime_type="application/json",
            ),
        ]

    return server


# ---------------------------------------------------------------------------
# region:    --- Reuse Signals (Phase 2 — Minimalism Protocol)
# ---------------------------------------------------------------------------

_REUSE_SIGNALS_ENV = os.environ.get("ASTINTEL_REUSE_SIGNALS", "on").lower()


def _reuse_signals_enabled() -> bool:
    """Check if reuse signals should be included in responses."""
    return _REUSE_SIGNALS_ENV in ("on", "verbose")


def _enrich_with_reuse_signals(
    engine: QueryEngine,
    target_node: GraphNode,
    *,
    max_signals: int = 3,
) -> list[dict[str, Any]]:
    """Find symbols structurally similar to *target_node* that could be reused.

    Returns up to *max_signals* signal dicts, each with a type, relevant
    metadata, and a human-readable suggestion.  Returns an empty list if
    reuse signals are disabled via ``ASTINTEL_REUSE_SIGNALS=off``.
    """
    if not _reuse_signals_enabled():
        return []

    signals: list[dict[str, Any]] = []

    # 1. Find symbols with same structural pattern in different files.
    try:
        similar = engine.find_similar(target_node.id, limit=5)
        for node, score in similar:
            if node.file != target_node.file:
                signals.append({
                    "type": "similar_exists",
                    "symbol": node.label,
                    "file": node.file,
                    "line": node.span.start_line if node.span else None,
                    "score": round(score, 3),
                    "suggestion": (
                        f"Consider reusing {node.label} at {node.file}"
                        f" instead of creating a new implementation."
                    ),
                })
    except (KeyError, ValueError):
        pass

    # 2. Detect thin wrappers (single outgoing CALLS edge, no other deps).
    try:
        deps = engine.get_dependencies(target_node.id)
        if len(deps.calls) == 1 and not deps.imports and not deps.inherits:
            sole_callee = deps.calls[0]
            signals.append({
                "type": "thin_wrapper",
                "wraps": sole_callee.label,
                "wraps_file": sole_callee.file,
                "suggestion": (
                    f"This is a thin wrapper over {sole_callee.label}."
                    f" Callers could call it directly."
                ),
            })
    except (KeyError, ValueError):
        pass

    # 3. Surface community peers doing related work.
    try:
        community = engine.get_community(target_node.id)
        peer_labels = [
            n.label for n in community.members
            if n.id != target_node.id
        ][:5]
        if peer_labels:
            signals.append({
                "type": "community_peers",
                "peers": peer_labels,
                "suggestion": (
                    "These symbols are in the same functional cluster."
                    " Check for overlap before writing new code."
                ),
            })
    except (KeyError, ValueError):
        pass

    return signals[:max_signals]


# endregion: --- Reuse Signals


# ---------------------------------------------------------------------------
# region:    --- find_reusable helpers (Phase 3 — Minimalism Protocol)
# ---------------------------------------------------------------------------


class _ReuseCandidate:
    """A scored reuse candidate returned by find_reusable."""

    __slots__ = ("node", "score", "reason")

    def __init__(self, node: GraphNode, score: float, reason: str) -> None:
        self.node = node
        self.score = score
        self.reason = reason


def _tokenize_intent(intent: str) -> list[str]:
    """Split a natural-language intent into search tokens.

    Handles snake_case, camelCase, and space-separated words.
    Returns lowercase tokens with length >= 3.
    """
    import re as _re

    # Split on spaces, underscores, hyphens, camelCase boundaries
    raw = _re.sub(r"([a-z])([A-Z])", r"\1 \2", intent)
    parts = _re.split(r"[\s_\-/]+", raw)
    return [p.lower() for p in parts if len(p) >= 3]


def _score_node_against_intent(
    node: GraphNode,
    tokens: list[str],
    context_file: str,
) -> float:
    """Score a node's relevance to the intent tokens.

    Scoring factors:
    - Name token overlap (0.0 – 0.7)
    - Same-file bonus (0.1)
    - Same-directory bonus (0.05)
    """
    label_lower = node.label.lower()
    if not tokens:
        return 0.0

    # Token overlap score
    matches = sum(1 for t in tokens if t in label_lower)
    token_score = min(matches / len(tokens), 1.0) * 0.7

    # Proximity bonus
    proximity = 0.0
    if context_file and node.file:
        if node.file == context_file:
            proximity = 0.1
        elif "/" in context_file and "/" in node.file:
            # Same directory
            ctx_dir = context_file.rsplit("/", 1)[0]
            node_dir = node.file.rsplit("/", 1)[0]
            if ctx_dir == node_dir:
                proximity = 0.05

    return token_score + proximity


def _find_reusable_candidates(
    engine: QueryEngine,
    intent: str,
    context_file: str,
    kind_filter: NodeKind | None,
) -> list[_ReuseCandidate]:
    """Core logic for the find_reusable tool.

    Strategy:
    1. Search by name pattern (primary signal).
    2. If context_file given, pull similar-to neighbors of file symbols.
    3. Deduplicate, score, and rank.
    """
    tokens = _tokenize_intent(intent)
    seen_ids: set[str] = set()
    candidates: list[_ReuseCandidate] = []

    # Strategy 1: Direct name search using each token and the full intent.
    search_patterns = list(tokens)
    # Also try common compound forms: "validate_email" from ["validate","email"]
    if len(tokens) >= 2:
        search_patterns.append("_".join(tokens[:3]))
        search_patterns.append("".join(t.capitalize() for t in tokens[:3]))

    for pattern in search_patterns:
        try:
            hits = engine.search(pattern, kind=kind_filter, regex=False)
            for node in hits[:10]:
                if node.id in seen_ids:
                    continue
                seen_ids.add(node.id)
                score = _score_node_against_intent(node, tokens, context_file)
                if score > 0.1:
                    candidates.append(_ReuseCandidate(
                        node=node, score=score, reason="name match",
                    ))
        except (KeyError, ValueError):
            continue

    # Strategy 2: Structural similarity from context file symbols.
    if context_file:
        try:
            # Find symbols in the context file and look for their peers
            file_nodes = [
                n for n in engine._graph.nodes
                if n.file == context_file
                and n.kind != NodeKind.IMPORT
                and n.kind != NodeKind.FILE
            ]
            for fnode in file_nodes[:5]:
                try:
                    similar = engine.find_similar(fnode.id, limit=3)
                    for snode, sim_score in similar:
                        if snode.id in seen_ids:
                            continue
                        seen_ids.add(snode.id)
                        # Boost if the similar node's name also matches tokens
                        name_bonus = _score_node_against_intent(
                            snode, tokens, context_file,
                        )
                        combined = sim_score * 0.5 + name_bonus
                        if combined > 0.1:
                            candidates.append(_ReuseCandidate(
                                node=snode,
                                score=combined,
                                reason="structural similar",
                            ))
                except (KeyError, ValueError):
                    continue
        except Exception:
            pass

    # Strategy 3: Community peers of top candidates (expand search).
    top_ids = [c.node.id for c in sorted(
        candidates, key=lambda c: c.score, reverse=True,
    )[:3]]
    for nid in top_ids:
        try:
            community = engine.get_community(nid)
            for member in community.members[:5]:
                if member.id in seen_ids:
                    continue
                seen_ids.add(member.id)
                name_score = _score_node_against_intent(
                    member, tokens, context_file,
                )
                if name_score > 0.15:
                    candidates.append(_ReuseCandidate(
                        node=member,
                        score=name_score * 0.9,  # slight discount
                        reason="community peer",
                    ))
        except (KeyError, ValueError):
            continue

    # Deduplicate and rank
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _build_reusable_recommendation(candidates: list[_ReuseCandidate]) -> str:
    """Generate a one-line recommendation from ranked candidates."""
    if not candidates:
        return "Nothing found. Proceed with minimal implementation."
    top = candidates[0]
    loc = ""
    if top.node.span:
        loc = f":{top.node.span.start_line}"
    if top.score > 0.5:
        return (
            f"Strong match: reuse {top.node.label} "
            f"at {top.node.file}{loc}."
        )
    if top.score > 0.25:
        return (
            f"Possible match: check {top.node.label} "
            f"at {top.node.file}{loc} — it may already do what you need."
        )
    return "Weak matches only. Consider implementing, but review candidates first."


# endregion: --- find_reusable helpers


# ---------------------------------------------------------------------------
# region:    --- audit_codebase helpers (Phase 4 — Minimalism Protocol)
# ---------------------------------------------------------------------------

_AUDITABLE_KINDS = frozenset({
    NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.STRUCT,
    NodeKind.ENUM, NodeKind.TRAIT,
})


def _estimate_lines(node: GraphNode) -> int:
    """Estimate lines of code for a node from its span."""
    if node.span:
        return max(node.span.end_line - node.span.start_line + 1, 1)
    return 10  # conservative default


def _find_dead_abstractions(
    engine: QueryEngine,
    nodes: list[GraphNode],
) -> list[dict[str, Any]]:
    """Symbols with 0 incoming edges (nobody uses them)."""
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.kind not in _AUDITABLE_KINDS:
            continue
        try:
            dep = engine.get_dependents(node.id)
        except (KeyError, ValueError):
            continue
        # Check all incoming relation buckets
        incoming = (
            len(dep.calls) + len(dep.imports) + len(dep.uses_methods)
            + len(dep.inherits) + len(dep.implements) + len(dep.other)
        )
        if incoming == 0:
            findings.append({
                "type": "dead_abstraction",
                "symbol": node.label,
                "file": node.file,
                "line": node.span.start_line if node.span else None,
                "kind": node.kind.value,
                "reason": "Zero dependents — nothing in the codebase uses this.",
                "action": "DELETE or verify it's an entry point / public API.",
                "impact_score": _estimate_lines(node),
            })
    return findings


def _find_unnecessary_wrappers(
    engine: QueryEngine,
    nodes: list[GraphNode],
) -> list[dict[str, Any]]:
    """Functions that just delegate to a single other function."""
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
            continue
        try:
            deps = engine.get_dependencies(node.id)
        except (KeyError, ValueError):
            continue
        # Single outgoing CALLS edge, no imports/inheritance
        if (
            len(deps.calls) == 1
            and not deps.imports
            and not deps.inherits
            and not deps.implements
            and not deps.uses_methods
        ):
            sole_target = deps.calls[0]
            # Also check the wrapper is small (< 10 lines)
            if _estimate_lines(node) > 15:
                continue
            # Count callers for impact
            try:
                dependents = engine.get_dependents(node.id)
                caller_count = len(dependents.calls)
            except (KeyError, ValueError):
                caller_count = 0
            findings.append({
                "type": "unnecessary_wrapper",
                "symbol": node.label,
                "file": node.file,
                "line": node.span.start_line if node.span else None,
                "wraps": sole_target.label,
                "wraps_file": sole_target.file,
                "reason": (
                    f"Thin wrapper over {sole_target.label}. "
                    f"Callers ({caller_count}) could call it directly."
                ),
                "action": (
                    f"INLINE — replace calls to {node.label} "
                    f"with direct calls to {sole_target.label}."
                ),
                "impact_score": max(caller_count * 2, _estimate_lines(node)),
            })
    return findings


def _find_structural_duplicates(
    engine: QueryEngine,
    nodes: list[GraphNode],
) -> list[dict[str, Any]]:
    """Groups of symbols that are structurally similar (consolidation candidates)."""
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            continue
        if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
            continue
        try:
            similar = engine.find_similar(node.id, limit=3)
        except (KeyError, ValueError):
            continue
        # Filter to genuinely similar (different file, same kind, high score)
        dupes = [
            (s, score) for s, score in similar
            if s.file != node.file and s.kind == node.kind and score > 0.4
        ]
        if dupes:
            group_nodes = [node] + [s for s, _ in dupes]
            for n in group_nodes:
                seen.add(n.id)
            findings.append({
                "type": "duplicate_implementations",
                "symbols": [
                    {"label": n.label, "file": n.file, "kind": n.kind.value}
                    for n in group_nodes
                ],
                "similarity_scores": [
                    round(score, 3) for _, score in dupes
                ],
                "reason": (
                    f"{len(group_nodes)} structurally similar "
                    f"implementations. Consolidate into one."
                ),
                "action": (
                    "MERGE into a shared utility and have all "
                    "call sites reference it."
                ),
                "impact_score": sum(
                    _estimate_lines(n) for n in group_nodes
                ),
            })
    return findings


def _find_over_abstractions(
    engine: QueryEngine,
    nodes: list[GraphNode],
) -> list[dict[str, Any]]:
    """Interfaces/traits with exactly 1 implementor (premature abstraction)."""
    findings: list[dict[str, Any]] = []
    for node in nodes:
        if node.kind != NodeKind.TRAIT:
            continue
        try:
            implementors = engine.get_implementors(node.id)
        except (KeyError, ValueError):
            continue
        if len(implementors) == 1:
            impl_node, impl_file = implementors[0]
            findings.append({
                "type": "over_abstraction",
                "symbol": node.label,
                "file": node.file,
                "line": node.span.start_line if node.span else None,
                "single_implementor": impl_node.label,
                "implementor_file": impl_file,
                "reason": (
                    "Interface/trait with exactly 1 implementor. "
                    "The abstraction adds no value yet."
                ),
                "action": (
                    "COLLAPSE — inline the interface into its sole "
                    "implementor until a second one appears."
                ),
                "impact_score": _estimate_lines(node),
            })
    return findings


def _find_bloat_hotspots(
    engine: QueryEngine,
    nodes: list[GraphNode],
) -> list[dict[str, Any]]:
    """Files with many symbols but low external reference count."""
    file_stats: dict[str, dict[str, int]] = {}
    for node in nodes:
        if not node.file or node.kind in (NodeKind.FILE, NodeKind.IMPORT, NodeKind.CRATE):
            continue
        if node.file not in file_stats:
            file_stats[node.file] = {"symbols": 0, "external_refs": 0}
        file_stats[node.file]["symbols"] += 1
        try:
            dep = engine.get_dependents(node.id)
            ext_refs = sum(
                1 for n in dep.calls if n.file != node.file
            )
            file_stats[node.file]["external_refs"] += ext_refs
        except (KeyError, ValueError):
            pass

    findings: list[dict[str, Any]] = []
    for file, stats in file_stats.items():
        sym_count = stats["symbols"]
        ext_refs = stats["external_refs"]
        if sym_count > 10 and ext_refs < sym_count * 0.3:
            findings.append({
                "type": "bloat_hotspot",
                "file": file,
                "symbol_count": sym_count,
                "external_references": ext_refs,
                "reuse_ratio": round(ext_refs / max(sym_count, 1), 2),
                "reason": (
                    f"{sym_count} symbols but only {ext_refs} external "
                    f"references. Most code here is unused externally."
                ),
                "action": "REVIEW — split, delete unused, or mark as internal-only.",
                "impact_score": sym_count - ext_refs,
            })
    return findings


def _audit_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate audit findings into a summary dict."""
    from collections import Counter as _Counter

    type_counts = _Counter(f["type"] for f in findings)
    total_impact = sum(f.get("impact_score", 0) for f in findings)
    return {
        "dead_abstractions": type_counts.get("dead_abstraction", 0),
        "unnecessary_wrappers": type_counts.get("unnecessary_wrapper", 0),
        "duplicate_implementations": type_counts.get("duplicate_implementations", 0),
        "over_abstractions": type_counts.get("over_abstraction", 0),
        "bloat_hotspots": type_counts.get("bloat_hotspot", 0),
        "total_impact_score": total_impact,
    }


def _run_audit(
    engine: QueryEngine,
    graph: CodeGraph,
    scope: str,
    checks: str,
    max_findings: int,
) -> dict[str, Any]:
    """Execute the codebase audit and return structured results."""
    check_set: set[str]
    if checks == "all" or not checks:
        check_set = {"dead", "wrappers", "duplicates", "over_abstracted", "bloat"}
    else:
        check_set = {c.strip() for c in checks.split(",")}

    # Scope filtering
    all_nodes = list(graph.nodes)
    if scope:
        all_nodes = [n for n in all_nodes if n.file and scope in n.file]

    findings: list[dict[str, Any]] = []

    if "dead" in check_set:
        findings.extend(_find_dead_abstractions(engine, all_nodes))

    if "wrappers" in check_set:
        findings.extend(_find_unnecessary_wrappers(engine, all_nodes))

    if "duplicates" in check_set:
        findings.extend(_find_structural_duplicates(engine, all_nodes))

    if "over_abstracted" in check_set:
        findings.extend(_find_over_abstractions(engine, all_nodes))

    if "bloat" in check_set:
        findings.extend(_find_bloat_hotspots(engine, all_nodes))

    # Rank by impact
    findings.sort(key=lambda f: f.get("impact_score", 0), reverse=True)

    return {
        "scope": scope or "entire repository",
        "checks_run": sorted(check_set),
        "total_findings": len(findings),
        "summary": _audit_summary(findings),
        "findings": findings[:max_findings],
    }


# endregion: --- audit_codebase helpers


# ---------------------------------------------------------------------------
# region:    --- track_minimalism helpers (Phase 5 — Minimalism Protocol)
# ---------------------------------------------------------------------------

_LEDGER_FILENAME = "minimalism-ledger.jsonl"
_LEDGER_DIR = ".ast-intel"


def _ledger_path(repo_path: Path) -> Path:
    """Return the path to the minimalism ledger file."""
    return repo_path / _LEDGER_DIR / _LEDGER_FILENAME


def _now_iso() -> str:
    """Return current UTC time in ISO format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_to_ledger(repo_path: Path, entry: dict[str, Any]) -> None:
    """Append a single JSON entry to the minimalism ledger."""
    ledger = _ledger_path(repo_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _read_ledger(repo_path: Path, last_n: int = 0) -> list[dict[str, Any]]:
    """Read ledger entries. If last_n > 0, return only the last N entries."""
    ledger = _ledger_path(repo_path)
    if not ledger.exists():
        return []
    entries: list[dict[str, Any]] = []
    with ledger.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if last_n > 0:
        return entries[-last_n:]
    return entries


def _ledger_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute stats from ledger entries."""
    reused = sum(1 for e in entries if e.get("action") == "reused")
    created = sum(1 for e in entries if e.get("action") == "created")
    deferred = sum(1 for e in entries if e.get("action") == "deferred")
    total = reused + created + deferred
    return {
        "reused": reused,
        "created": created,
        "deferred": deferred,
        "total": total,
        "reuse_rate": round(reused / max(total, 1), 2),
    }


# endregion: --- track_minimalism helpers


def _dispatch(  # noqa: C901, PLR0911, PLR0912, PLR0915
    engine: QueryEngine,
    graph: CodeGraph,
    name: str,
    arguments: dict[str, Any],
    *,
    repo_path: Path | None = None,
) -> list[TextContent]:
    """Route an MCP tool call to the corresponding QueryEngine method."""
    if name == "search_symbols":
        pattern: str = arguments["pattern"]
        kind_str = arguments.get("kind")
        kind = NodeKind(kind_str) if kind_str else None
        regex = bool(arguments.get("regex", False))
        results = engine.search(pattern, kind=kind, regex=regex)
        return _text([_node_dict(n) for n in results])

    if name == "find_path":
        path = engine.shortest_path(arguments["source"], arguments["target"])
        if path is None:
            return _text({"path": None, "message": "No path found."})
        steps = []
        for node, relation in path:
            step: dict[str, Any] = _node_dict(node)
            if relation is not None:
                step["edge_to_next"] = relation.value
            steps.append(step)
        return _text({"path": steps, "length": len(steps)})

    if name == "explain_symbol":
        exp = engine.explain(arguments["symbol"])
        result = _explanation_dict(exp)
        signals = _enrich_with_reuse_signals(engine, exp.node)
        if signals:
            result["reuse_signals"] = signals
        return _text(result)

    if name == "get_impact":
        depth = int(arguments.get("depth", 0))
        imp = engine.impact(arguments["symbol"], depth=depth)
        return _text(_impact_dict(imp))

    if name == "get_dependencies":
        dep = engine.get_dependencies(arguments["symbol"])
        result = _dep_dict(dep)
        signals = _enrich_with_reuse_signals(engine, dep.node)
        if signals:
            result["reuse_signals"] = signals
        return _text(result)

    if name == "get_dependents":
        dep = engine.get_dependents(arguments["symbol"])
        return _text(_dep_dict(dep))

    if name == "get_context":
        ctx = engine.get_context(arguments["symbol"])
        result = _context_dict(ctx)
        signals = _enrich_with_reuse_signals(engine, ctx.node)
        if signals:
            result["reuse_signals"] = signals
        return _text(result)

    if name == "find_usages":
        usages = engine.find_usages(arguments["symbol"])
        return _text([_usage_dict(u) for u in usages])

    if name == "get_data_egress":
        from ast_intel.core.egress_report import build_egress_report

        include_internal = bool(arguments.get("include_internal", False))
        include_heuristic = bool(arguments.get("include_heuristic", True))
        report = build_egress_report(
            graph,
            third_party_only=not include_internal,
            include_heuristic=include_heuristic,
        )
        vendor_filter = arguments.get("vendor")
        if vendor_filter:
            vf = str(vendor_filter).lower()
            report["egress"] = [
                e for e in report["egress"]
                if (e.get("vendor") or "").lower() == vf
            ]
            report["egress_count"] = len(report["egress"])
        return _text(report)

    if name == "list_files":
        pattern = arguments.get("pattern", "")
        offset = int(arguments.get("offset", 0))
        limit_raw = arguments.get("limit")
        limit = int(limit_raw) if limit_raw is not None else None
        files = engine.list_files(
            pattern, offset=offset, limit=limit,
        )
        return _text([_file_info_dict(f) for f in files])

    if name == "find_dead_code":
        dead = engine.find_dead_code()
        return _text([
            {"node": _node_dict(d.node), "in_degree": d.in_degree}
            for d in dead
        ])

    if name == "get_routes":
        routes = engine.list_routes(service=arguments.get("service"))
        return _text([_route_dict(r) for r in routes])

    if name == "get_implementors":
        impls = engine.get_implementors(arguments["trait"])
        return _text([
            {"node": _node_dict(n), "file": f} for n, f in impls
        ])

    if name == "find_similar":
        limit = int(arguments.get("limit", 5))
        similar = engine.find_similar(arguments["symbol"], limit=limit)
        return _text([
            {"node": _node_dict(n), "score": score} for n, score in similar
        ])

    if name == "get_community":
        comm = engine.get_community(arguments["symbol"])
        return _text(_community_dict(comm))

    if name == "get_hyperedges":
        from ast_intel.models.graph_model import HyperRelation

        edges = list(graph.hyperedges)
        rel_filter = arguments.get("relation")
        if rel_filter:
            try:
                # Accept both the enum value ("route_group") and the
                # member name ("ROUTE_GROUP"), case-insensitively.
                rel_val = HyperRelation(str(rel_filter).lower())
            except ValueError:
                try:
                    rel_val = HyperRelation[str(rel_filter).upper()]
                except KeyError:
                    valid = ", ".join(r.value for r in HyperRelation)
                    return _error(
                        f"{rel_filter!r} is not a valid hyperedge "
                        f"relation. Valid values: {valid}"
                    )
            edges = [he for he in edges if he.relation == rel_val]
        member_filter = arguments.get("member")
        if member_filter:
            edges = [
                he for he in edges
                if any(member_filter in m for m in he.members)
            ]
        limit = int(arguments.get("limit", 50))
        edges = edges[:limit]
        return _text([
            {
                "id": he.id,
                "relation": he.relation.value,
                "members": he.members,
                "label": he.label,
                "metadata": he.metadata,
            }
            for he in edges
        ])

    # ---------------------------------------------------------------
    # region:    --- IaC dispatch
    # ---------------------------------------------------------------

    if name == "iac_overview":
        return _iac_overview(graph, arguments)

    if name == "get_helm_chart":
        return _iac_helm_chart(graph, arguments)

    if name == "get_k8s_resources":
        return _iac_k8s_resources(graph, arguments)

    if name == "get_pipeline":
        return _iac_pipeline(graph, arguments)

    if name == "get_docker_info":
        return _iac_docker_info(graph, arguments)

    if name == "get_cloud_resources":
        return _cloud_resources(graph, arguments)

    # endregion: --- IaC dispatch

    # region:    --- History dispatch (Phase 3)
    if name == "get_symbol_history":
        return _history_tool(engine, arguments)
    if name == "get_ownership":
        return _ownership_tool(engine, arguments)
    if name == "get_decision_context":
        return _decision_context_tool(engine, arguments)
    # endregion: --- History dispatch

    # region:    --- Phase 4 dispatch
    if name == "get_rich_context":
        return _rich_context_tool(engine, arguments)
    if name == "get_rfcs_for_symbol":
        return _list_rfcs_tool(engine, arguments)
    if name == "get_incident_history":
        return _list_incidents_tool(engine, arguments)
    if name == "get_tribal_knowledge":
        return _list_tribal_tool(engine, arguments)
    if name == "add_tribal_knowledge":
        return _add_tribal_tool(engine, arguments)
    if name == "add_incident":
        return _add_incident_tool(engine, arguments)
    if name == "submit_feedback":
        return _submit_feedback_tool(engine, arguments)
    # endregion: --- Phase 4 dispatch

    # region:    --- Minimalism Protocol dispatch
    if name == "find_reusable":
        intent: str = arguments["intent"]
        context_file = arguments.get("context_file", "")
        kind_str_r = arguments.get("kind")
        kind_r = NodeKind(kind_str_r) if kind_str_r else None

        candidates = _find_reusable_candidates(
            engine, intent, context_file, kind_r,
        )
        top = candidates[:7]
        result: dict[str, Any] = {
            "intent": intent,
            "candidates_found": len(candidates),
            "recommendation": _build_reusable_recommendation(candidates),
            "candidates": [
                {
                    "symbol": c.node.label,
                    "file": c.node.file,
                    "line": c.node.span.start_line if c.node.span else None,
                    "kind": c.node.kind.value,
                    "relevance_score": round(c.score, 3),
                    "reason": c.reason,
                }
                for c in top
            ],
        }
        return _text(result)
    # endregion: --- Minimalism Protocol dispatch

    # region:    --- Audit dispatch
    if name == "audit_codebase":
        scope = arguments.get("scope", "")
        checks = arguments.get("checks", "all")
        max_findings = int(arguments.get("max_findings", 20))
        audit_result = _run_audit(engine, graph, scope, checks, max_findings)
        return _text(audit_result)
    # endregion: --- Audit dispatch

    # region:    --- Tracking dispatch
    if name == "track_minimalism":
        if repo_path is None:
            return _error(
                "track_minimalism requires repo_path "
                "(only available when running via 'ast-intel serve')."
            )
        action_val = arguments["action"]
        if action_val not in ("reused", "created", "deferred"):
            return _error(
                f"Invalid action: {action_val!r}. "
                "Must be 'reused', 'created', or 'deferred'."
            )
        entry: dict[str, Any] = {
            "timestamp": _now_iso(),
            "action": action_val,
            "symbol": arguments["symbol"],
            "file": arguments["file"],
            "reason": arguments.get("reason", ""),
        }
        _append_to_ledger(repo_path, entry)
        all_entries = _read_ledger(repo_path)
        stats = _ledger_stats(all_entries)
        return _text({"logged": entry, "session_stats": stats})

    if name == "review_minimalism_ledger":
        if repo_path is None:
            return _error(
                "review_minimalism_ledger requires repo_path "
                "(only available when running via 'ast-intel serve')."
            )
        last_n = int(arguments.get("last_n", 20))
        entries = _read_ledger(repo_path, last_n=last_n)
        stats = _ledger_stats(_read_ledger(repo_path))
        return _text({
            "total_entries": stats["total"],
            "stats": stats,
            "entries": entries,
        })
    # endregion: --- Tracking dispatch

    if name == "health_check":
        return _text({
            "status": "ok",
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
        })

    return _error(f"Unknown tool: {name!r}")


# endregion: --- Server Factory


# ---------------------------------------------------------------------------
# region:    --- IaC Tool Handlers
# ---------------------------------------------------------------------------

_IAC_DOMAINS: dict[str, tuple[str, ...]] = {
    "k8s": (
        "k8s_deployment", "k8s_service", "k8s_configmap", "k8s_secret",
        "k8s_ingress", "k8s_namespace", "k8s_generic", "k8s_cronjob",
        "k8s_job", "k8s_pvc", "k8s_rbac",
    ),
    "helm": ("helm_chart", "helm_value", "helm_template"),
    "docker": ("docker_image", "docker_stage", "docker_service"),
    "ansible": ("ansible_playbook", "ansible_task", "ansible_role"),
    "cicd": ("ci_pipeline", "ci_stage", "ci_job", "ci_step"),
    "terraform": (
        "tf_resource", "tf_data", "tf_variable", "tf_output",
        "tf_module", "tf_provider", "tf_local",
    ),
}


def _iac_nodes(
    graph: CodeGraph,
    domain: str | None = None,
) -> list[GraphNode]:
    """Return IaC nodes, optionally filtered by domain."""
    if domain and domain in _IAC_DOMAINS:
        kinds = frozenset(_IAC_DOMAINS[domain])
        return [n for n in graph.nodes if n.kind.value in kinds]
    all_kinds = frozenset(
        k for kinds in _IAC_DOMAINS.values() for k in kinds
    )
    return [n for n in graph.nodes if n.kind.value in all_kinds]


def _iac_edges_for(
    graph: CodeGraph,
    node_ids: frozenset[str],
) -> list[GraphEdge]:
    """Return edges touching any of the given node IDs."""
    return [
        e for e in graph.edges
        if e.source in node_ids or e.target in node_ids
    ]


def _iac_overview(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """iac_overview handler."""
    domain = arguments.get("domain")
    nodes = _iac_nodes(graph, domain)
    if not nodes:
        return _text({
            "total": 0,
            "message": "No IaC resources found in graph.",
        })

    domains: dict[str, Any] = {}
    for d_name, d_kinds in _IAC_DOMAINS.items():
        if domain and d_name != domain:
            continue
        d_nodes = [n for n in nodes if n.kind.value in d_kinds]
        if not d_nodes:
            continue
        per_kind: dict[str, list[str]] = {}
        for n in d_nodes:
            per_kind.setdefault(n.kind.value, []).append(n.label)
        # Show up to 10 names per kind, sorted
        summary: dict[str, Any] = {}
        for k, names in sorted(per_kind.items()):
            names_sorted = sorted(names)
            max_items = 10
            entry: dict[str, Any] = {"count": len(names)}
            if len(names) <= max_items:
                entry["items"] = names_sorted
            else:
                entry["items"] = names_sorted[:max_items]
                entry["truncated"] = len(names) - max_items
            summary[k] = entry
        domains[d_name] = {
            "total": len(d_nodes),
            "kinds": summary,
        }

    return _text({
        "total": len(nodes),
        "domains": domains,
    })


def _cloud_resources(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """get_cloud_resources handler."""
    category = arguments.get("category")
    provider = arguments.get("provider")
    service_filter = arguments.get("service")

    cr_nodes = [n for n in graph.nodes if n.kind == NodeKind.CLOUD_RESOURCE]

    if category:
        cr_nodes = [n for n in cr_nodes if n.properties.get("category") == category]
    if provider:
        cr_nodes = [n for n in cr_nodes if n.properties.get("provider") == provider]
    if service_filter:
        svc_lower = service_filter.lower()
        cr_nodes = [
            n for n in cr_nodes if svc_lower in n.file.lower()
        ]

    if not cr_nodes:
        return _text({"total": 0, "message": "No cloud resources found."})

    by_category: dict[str, list[dict[str, str]]] = {}
    for n in cr_nodes:
        cat = n.properties.get("category", "other")
        entry = {
            "service": n.properties.get("service", ""),
            "provider": n.properties.get("provider", ""),
            "client": n.properties.get("client", ""),
            "caller": n.properties.get("caller", ""),
            "name": n.properties.get("name", ""),
            "source": n.properties.get("source", ""),
            "file": n.file,
        }
        by_category.setdefault(cat, []).append(entry)

    return _text({
        "total": len(cr_nodes),
        "categories": {
            cat: {"count": len(items), "resources": items}
            for cat, items in sorted(by_category.items())
        },
    })


def _iac_helm_chart(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """get_helm_chart handler."""
    chart_arg = arguments["chart"]
    charts = [
        n for n in graph.nodes
        if n.kind == NodeKind.HELM_CHART
    ]
    if not charts:
        return _text({"message": "No Helm charts found in graph."})

    if chart_arg.lower() == "all":
        return _text([
            {
                "name": c.label,
                "id": c.id,
                "version": c.properties.get("version", ""),
                "appVersion": c.properties.get("appVersion", ""),
                "description": c.properties.get("description", ""),
            }
            for c in charts
        ])

    # Find matching chart — prefer exact label match, then substring
    match = None
    for c in charts:
        if chart_arg.lower() == c.label.lower():
            match = c
            break
    if match is None:
        for c in charts:
            if (chart_arg.lower() in c.label.lower()
                    or chart_arg.lower() in c.id.lower()):
                match = c
                break
    if match is None:
        return _error(
            f"Chart {chart_arg!r} not found. "
            f"Available: {', '.join(c.label for c in charts)}"
        )

    chart_id = match.id
    # Chart dir prefix: strip the filename portion (Chart.yaml)
    # e.g. "helm://deploy/helm-charts/safeguard/Chart.yaml::..."
    #    → "helm://deploy/helm-charts/safeguard/"
    file_part = chart_id.split("::")[0] if "::" in chart_id else chart_id
    # Remove trailing filename to get directory
    chart_dir = file_part.rsplit("/", 1)[0] + "/" if "/" in file_part else ""

    # Find templates belonging to this chart
    templates = [
        n for n in graph.nodes
        if n.kind == NodeKind.HELM_TEMPLATE
        and chart_dir
        and n.id.startswith(chart_dir)
    ]
    # Find values belonging to this chart
    values = [
        n for n in graph.nodes
        if n.kind == NodeKind.HELM_VALUE
        and chart_dir
        and n.id.startswith(chart_dir)
    ]
    # Chart edges
    chart_node_ids = frozenset(
        {chart_id}
        | {t.id for t in templates}
        | {v.id for v in values}
    )
    edges = _iac_edges_for(graph, chart_node_ids)

    return _text({
        "chart": {
            "name": match.label,
            "id": chart_id,
            "properties": match.properties,
        },
        "templates": [
            {
                "name": t.label,
                "k8s_kinds": t.properties.get("k8s_kinds", ""),
                "value_refs": t.properties.get("value_refs", ""),
                "includes": t.properties.get("includes", ""),
            }
            for t in sorted(templates, key=lambda t: t.label)
        ],
        "values_count": len(values),
        "top_values": [
            {
                "name": v.label,
                "default": v.properties.get("default_value", ""),
                "type": v.properties.get("value_type", ""),
            }
            for v in sorted(values, key=lambda v: v.label)[:30]
        ],
        "edges": [
            {
                "relation": e.relation.value,
                "source": e.source.split("::")[-1] if "::" in e.source else e.source,
                "target": e.target.split("::")[-1] if "::" in e.target else e.target,
            }
            for e in edges[:100]
        ],
    })


def _iac_k8s_resources(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """get_k8s_resources handler."""
    kind_filter = arguments.get("kind", "all").lower()
    name_filter = arguments.get("name", "")

    kind_map: dict[str, frozenset[str]] = {
        "deployment": frozenset({"k8s_deployment"}),
        "service": frozenset({"k8s_service"}),
        "configmap": frozenset({"k8s_configmap"}),
        "secret": frozenset({"k8s_secret"}),
        "rbac": frozenset({"k8s_rbac"}),
        "ingress": frozenset({"k8s_ingress"}),
        "cronjob": frozenset({"k8s_cronjob"}),
        "job": frozenset({"k8s_job"}),
        "pvc": frozenset({"k8s_pvc"}),
        "namespace": frozenset({"k8s_namespace"}),
    }
    if kind_filter == "all":
        target_kinds = frozenset(
            k for kinds in kind_map.values() for k in kinds
        ) | frozenset({"k8s_generic"})
    else:
        target_kinds = kind_map.get(
            kind_filter, frozenset({f"k8s_{kind_filter}"}),
        )

    # Direct K8s nodes
    k8s_nodes = [
        n for n in graph.nodes
        if n.kind.value in target_kinds
        and (not name_filter or name_filter.lower() in n.label.lower())
    ]
    # Also gather Helm templates that produce matching K8s kinds
    helm_as_k8s: list[dict[str, Any]] = []
    if kind_filter == "all" or kind_filter in kind_map:
        for n in graph.nodes:
            if n.kind != NodeKind.HELM_TEMPLATE:
                continue
            k8s_kinds_str = n.properties.get("k8s_kinds", "")
            if not k8s_kinds_str:
                continue
            template_kinds = [
                k.strip() for k in k8s_kinds_str.split(",")
            ]
            if kind_filter != "all" and not any(
                kind_filter == tk.lower() for tk in template_kinds
            ):
                continue
            if name_filter and name_filter.lower() not in n.label.lower():
                continue
            helm_as_k8s.append({
                "template": n.label,
                "file": n.file,
                "k8s_kinds": template_kinds,
                "value_refs": n.properties.get("value_refs", ""),
                "includes": n.properties.get("includes", ""),
            })

    node_ids = frozenset(n.id for n in k8s_nodes)
    edges = _iac_edges_for(graph, node_ids)

    return _text({
        "standalone_resources": [
            {
                "name": n.label,
                "kind": n.properties.get("k8s_kind", n.kind.value),
                "namespace": n.properties.get("namespace", ""),
                "file": n.file,
                "properties": {
                    k: v for k, v in n.properties.items()
                    if k not in ("k8s_kind", "namespace")
                },
            }
            for n in sorted(k8s_nodes, key=lambda n: n.label)
        ],
        "helm_templated": sorted(
            helm_as_k8s, key=lambda h: h["template"],
        ),
        "relationships": [
            {
                "relation": e.relation.value,
                "source": e.source.split("::")[-1] if "::" in e.source else e.source,
                "target": e.target.split("::")[-1] if "::" in e.target else e.target,
            }
            for e in edges[:100]
        ],
    })


def _iac_pipeline(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """get_pipeline handler."""
    name_filter = arguments.get("name", "all")

    ci_kinds = frozenset({
        "ci_pipeline", "ci_stage", "ci_job", "ci_step",
    })
    ci_nodes = [
        n for n in graph.nodes if n.kind.value in ci_kinds
    ]
    if not ci_nodes:
        return _text({
            "pipelines": [],
            "stages": [],
            "jobs": [],
            "steps": [],
            "relationships": [],
            "message": "No CI/CD pipeline nodes found.",
        })

    if name_filter.lower() != "all":
        ci_nodes = [
            n for n in ci_nodes
            if name_filter.lower() in n.label.lower()
            or name_filter.lower() in n.id.lower()
        ]

    # Group by kind
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for n in ci_nodes:
        entry = {
            "name": n.label,
            "file": n.file,
            "properties": n.properties,
        }
        by_kind.setdefault(n.kind.value, []).append(entry)

    # Pipeline edges
    node_ids = frozenset(n.id for n in ci_nodes)
    edges = _iac_edges_for(graph, node_ids)

    return _text({
        "pipelines": sorted(
            by_kind.get("ci_pipeline", []),
            key=lambda p: p["name"],
        ),
        "stages": sorted(
            by_kind.get("ci_stage", []),
            key=lambda s: s["name"],
        ),
        "jobs": sorted(
            by_kind.get("ci_job", []),
            key=lambda j: j["name"],
        ),
        "steps": by_kind.get("ci_step", [])[:50],
        "relationships": [
            {
                "relation": e.relation.value,
                "source": e.source.split("::")[-1] if "::" in e.source else e.source,
                "target": e.target.split("::")[-1] if "::" in e.target else e.target,
            }
            for e in edges[:100]
        ],
    })


def _iac_docker_info(
    graph: CodeGraph,
    arguments: dict[str, Any],
) -> list[TextContent]:
    """get_docker_info handler."""
    name_filter = arguments.get("name", "")

    docker_kinds = frozenset({
        "docker_image", "docker_stage", "docker_service",
    })
    docker_nodes = [
        n for n in graph.nodes if n.kind.value in docker_kinds
    ]
    if not docker_nodes:
        return _text({
            "images": [],
            "stages": [],
            "services": [],
            "relationships": [],
            "message": "No Docker resources found in graph.",
        })

    if name_filter:
        docker_nodes = [
            n for n in docker_nodes
            if name_filter.lower() in n.label.lower()
            or name_filter.lower() in n.id.lower()
        ]

    node_ids = frozenset(n.id for n in docker_nodes)
    edges = _iac_edges_for(graph, node_ids)

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for n in docker_nodes:
        entry: dict[str, Any] = {
            "name": n.label,
            "file": n.file,
        }
        if n.properties:
            entry["properties"] = n.properties
        by_kind.setdefault(n.kind.value, []).append(entry)

    return _text({
        "images": sorted(
            by_kind.get("docker_image", []),
            key=lambda i: i["name"],
        ),
        "stages": sorted(
            by_kind.get("docker_stage", []),
            key=lambda s: s["name"],
        ),
        "services": sorted(
            by_kind.get("docker_service", []),
            key=lambda s: s["name"],
        ),
        "relationships": [
            {
                "relation": e.relation.value,
                "source": e.source.split("::")[-1] if "::" in e.source else e.source,
                "target": e.target.split("::")[-1] if "::" in e.target else e.target,
            }
            for e in edges[:100]
        ],
    })


# endregion: --- IaC Tool Handlers


# ---------------------------------------------------------------------------
# region:    --- Transport Runners
# ---------------------------------------------------------------------------


def _bootstrap_server(  # noqa: PLR0913
    repo_path: Path,
    *,
    output_dir: Path | None = None,
    no_cache: bool = False,
    analyze: bool = False,
    similarity: bool = True,
    similarity_threshold: float = 0.4,
) -> tuple[Any, _GraphState, Path]:
    """Load graph + engine and return ``(server, state, resolved_output)``."""
    from mcp.server import Server as McpServer

    resolved_output = output_dir if output_dir is not None else repo_path
    graph = load_or_build_graph(repo_path, output_dir=output_dir, no_cache=no_cache)

    if similarity:
        has_similar = any(
            e.relation == EdgeRelation.SIMILAR_TO for e in graph.edges
        )
        if not has_similar:
            from ast_intel.core._similarity import compute_similarity_edges

            sim_edges = compute_similarity_edges(
                graph, threshold=similarity_threshold,
            )
            graph.edges.extend(sim_edges)
            logger.info(
                "Auto-computed %d SIMILAR_TO edges (threshold=%.2f)",
                len(sim_edges),
                similarity_threshold,
            )

    analysis = None
    if analyze:
        from ast_intel.core.analyzer import GraphAnalyzer

        analysis = GraphAnalyzer().analyze(graph)

    history_builder, enrichment, aggregator = _build_history_stack(repo_path)
    engine = QueryEngine(
        graph,
        analysis=analysis,
        history_builder=history_builder,
        enrichment=enrichment,
        signal_aggregator=aggregator,
    )
    state = _GraphState(engine, graph, repo_path=repo_path)
    server: McpServer[Any, Any] = create_server(engine, graph, state=state)
    return server, state, resolved_output


async def run_stdio(  # noqa: PLR0913
    repo_path: Path,
    *,
    output_dir: Path | None = None,
    no_cache: bool = False,
    analyze: bool = False,
    similarity: bool = True,
    similarity_threshold: float = 0.4,
    watch: bool = True,
) -> None:
    """Load the graph, build the engine, and run the MCP server over stdio.

    This is the primary entry point used by the ``ast-intel serve``
    CLI subcommand when ``--port`` is not set.

    Args:
        repo_path: Repository root.
        output_dir: Directory containing ``graph.json``.
        no_cache: Force graph rebuild.
        analyze: Run community detection for ``get_community`` support.
        similarity: Auto-compute ``SIMILAR_TO`` edges if the graph
            does not already contain them (default ``True``).
        similarity_threshold: Minimum Jaccard score for similarity
            edges (default ``0.4``).
        watch: Start a background file watcher to auto-rebuild the
            graph when source files change (default ``True``).
    """
    import asyncio

    from mcp.server.stdio import stdio_server

    server, state, resolved_output = _bootstrap_server(
        repo_path,
        output_dir=output_dir,
        no_cache=no_cache,
        analyze=analyze,
        similarity=similarity,
        similarity_threshold=similarity_threshold,
    )

    logger.info(
        "Starting MCP server (stdio) for %s — %d nodes, %d edges",
        repo_path,
        len(state.graph.nodes),
        len(state.graph.edges),
    )

    watcher_thread: threading.Thread | None = None
    reload_task: asyncio.Task[None] | None = None
    if watch:
        watcher_thread = _start_watcher(
            repo_path,
            resolved_output,
            similarity=similarity,
            similarity_threshold=similarity_threshold,
        )
        reload_task = asyncio.create_task(
            _graph_reload_loop(
                state,
                resolved_output,
                analyze=analyze,
                similarity=similarity,
                similarity_threshold=similarity_threshold,
            ),
        )

    try:
        init_options = server.create_initialization_options()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    finally:
        if reload_task is not None:
            reload_task.cancel()
        if watcher_thread is not None:
            _stop_watcher()


def create_http_app(  # noqa: PLR0913
    repo_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 7500,
    output_dir: Path | None = None,
    no_cache: bool = False,
    analyze: bool = False,
    similarity: bool = True,
    similarity_threshold: float = 0.4,
    watch: bool = True,
) -> Any:
    """Build an ASGI app exposing Streamable HTTP MCP at ``/mcp``.

    Used by :func:`run_http` and by tests.  Callers that start the app
    themselves must keep the process alive (e.g. via uvicorn).
    """
    import asyncio
    import contextlib
    from collections.abc import AsyncIterator

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount

    server, state, resolved_output = _bootstrap_server(
        repo_path,
        output_dir=output_dir,
        no_cache=no_cache,
        analyze=analyze,
        similarity=similarity,
        similarity_threshold=similarity_threshold,
    )

    allowed_hosts = [
        f"localhost:{port}",
        f"127.0.0.1:{port}",
        "localhost:*",
        "127.0.0.1:*",
        "testserver",  # Starlette / httpx TestClient default Host
        "testserver:*",
    ]
    if host not in {"0.0.0.0", "::", "[::]"}:  # noqa: S104
        allowed_hosts.append(f"{host}:{port}")
        allowed_hosts.append(f"{host}:*")

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    )

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=security,
    )

    class _McpPathApp:
        """Map ``/mcp`` and ``/mcp/*`` onto the Streamable HTTP transport."""

        def __init__(self, handler: Any) -> None:
            self._handler = handler

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self._handler(scope, receive, send)
                return
            path = scope.get("path", "")
            if path == "/mcp" or path.startswith("/mcp/"):
                new_scope = dict(scope)
                rest = path[len("/mcp") :] or "/"
                if not rest.startswith("/"):
                    rest = "/" + rest
                new_scope["path"] = rest
                new_scope["root_path"] = scope.get("root_path", "") + "/mcp"
                await self._handler(new_scope, receive, send)
                return
            await PlainTextResponse("Not Found", status_code=404)(
                scope, receive, send,
            )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        watcher_thread: threading.Thread | None = None
        reload_task: asyncio.Task[None] | None = None
        if watch:
            watcher_thread = _start_watcher(
                repo_path,
                resolved_output,
                similarity=similarity,
                similarity_threshold=similarity_threshold,
            )
            reload_task = asyncio.create_task(
                _graph_reload_loop(
                    state,
                    resolved_output,
                    analyze=analyze,
                    similarity=similarity,
                    similarity_threshold=similarity_threshold,
                ),
            )
        try:
            async with session_manager.run():
                yield
        finally:
            if reload_task is not None:
                reload_task.cancel()
            if watcher_thread is not None:
                _stop_watcher()

    logger.info(
        "Prepared MCP HTTP app for %s — %d nodes, %d edges (bind %s:%d/mcp)",
        repo_path,
        len(state.graph.nodes),
        len(state.graph.edges),
        host,
        port,
    )

    return Starlette(
        routes=[Mount("/", app=_McpPathApp(session_manager.handle_request))],
        lifespan=lifespan,
    )


async def run_http(  # noqa: PLR0913
    repo_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 7500,
    output_dir: Path | None = None,
    no_cache: bool = False,
    analyze: bool = False,
    similarity: bool = True,
    similarity_threshold: float = 0.4,
    watch: bool = True,
) -> None:
    """Run the MCP server over Streamable HTTP (single ``/mcp`` endpoint).

    Intended for container / remote agents.  Local IDEs should keep using
    :func:`run_stdio`.
    """
    import uvicorn

    app = create_http_app(
        repo_path,
        host=host,
        port=port,
        output_dir=output_dir,
        no_cache=no_cache,
        analyze=analyze,
        similarity=similarity,
        similarity_threshold=similarity_threshold,
        watch=watch,
    )

    logger.info("Starting MCP server (HTTP) at http://%s:%d/mcp", host, port)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


# ---------------------------------------------------------------------------
# region:    --- Watcher Integration
# ---------------------------------------------------------------------------

_active_watcher: FileWatcher | None = None


def _start_watcher(
    repo_path: Path,
    output_dir: Path,
    *,
    similarity: bool,
    similarity_threshold: float,
) -> threading.Thread:
    """Start the file watcher in a daemon thread."""
    global _active_watcher  # noqa: PLW0603

    from ast_intel.core._watcher import FileWatcher, WatchConfig

    config = WatchConfig(
        repo_root=repo_path,
        output_dir=output_dir,
        similarity=similarity,
        similarity_threshold=similarity_threshold,
    )
    _active_watcher = FileWatcher(config)

    def _run() -> None:
        assert _active_watcher is not None
        _active_watcher.run(quiet=True)

    t = threading.Thread(target=_run, name="ast-intel-watcher", daemon=True)
    t.start()
    logger.info("File watcher started (background thread)")
    return t


def _stop_watcher() -> None:
    """Stop the background watcher if active."""
    global _active_watcher  # noqa: PLW0603
    if _active_watcher is not None:
        _active_watcher.stop()
        _active_watcher = None


async def _graph_reload_loop(  # noqa: PLR0913
    state: _GraphState,
    output_dir: Path,
    *,
    analyze: bool,
    similarity: bool,
    similarity_threshold: float,
    poll_interval: float = 2.0,
) -> None:
    """Poll the sentinel file and reload graph when it changes."""
    import asyncio

    from ast_intel.formatters.graph_json_formatter import (
        GRAPH_JSON_FILENAME,
        GraphJsonFormatter,
    )

    sentinel = output_dir / ".graph-updated"
    last_mtime: float = sentinel.stat().st_mtime if sentinel.exists() else 0.0

    while True:
        await asyncio.sleep(poll_interval)
        try:
            if not sentinel.exists():
                continue
            current_mtime = sentinel.stat().st_mtime
            if current_mtime <= last_mtime:
                continue
            last_mtime = current_mtime

            # Reload graph from disk
            graph_path = output_dir / GRAPH_JSON_FILENAME
            if not graph_path.is_file():
                continue

            logger.info("Reloading graph after watcher rebuild...")
            graph = GraphJsonFormatter.read(graph_path)

            # Re-compute similarity edges if needed
            if similarity:
                has_similar = any(
                    e.relation == EdgeRelation.SIMILAR_TO
                    for e in graph.edges
                )
                if not has_similar:
                    from ast_intel.core._similarity import (
                        compute_similarity_edges,
                    )

                    sim_edges = compute_similarity_edges(
                        graph, threshold=similarity_threshold,
                    )
                    graph.edges.extend(sim_edges)

            analysis = None
            if analyze:
                from ast_intel.core.analyzer import GraphAnalyzer

                analysis = GraphAnalyzer().analyze(graph)

            reload_root = state.repo_path
            if reload_root is None:
                logger.warning("Graph reload skipped: repo_path unset on state")
                continue
            history_builder, enrichment, aggregator = _build_history_stack(
                reload_root,
            )
            engine = QueryEngine(
                graph,
                analysis=analysis,
                history_builder=history_builder,
                enrichment=enrichment,
                signal_aggregator=aggregator,
            )

            # Hot-swap state
            state.engine = engine
            state.graph = graph

            logger.info(
                "Graph reloaded: %d nodes, %d edges",
                len(graph.nodes),
                len(graph.edges),
            )

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Graph reload failed")


# endregion: --- Watcher Integration

# endregion: --- Transport Runners
