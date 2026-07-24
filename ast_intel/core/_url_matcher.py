"""Cross-service edge resolution via URL / route matching.

After a :func:`~ast_intel.core._merge.merge_graphs` call the merged
:class:`~ast_intel.models.graph_model.CodeGraph` contains both
``ROUTE`` nodes (server-side endpoints) and ``HTTP_CALL`` nodes
(client-side outgoing requests).  This module compares them and emits
``CALLS_SERVICE`` edges that connect an HTTP_CALL to the ROUTE it
targets, **across** service boundaries.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    EdgeRelation,
    GraphEdge,
    NodeKind,
)

if TYPE_CHECKING:
    from ast_intel.models.graph_model import CodeGraph, GraphNode

__all__: list[str] = [
    "match_route",
    "normalize_url",
    "resolve_cross_service_edges",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- URL normalisation helpers
# ---------------------------------------------------------------------------

# Matches common path-parameter syntaxes:
#   {id}  :id  <id>  <int:id>  {id:path}
_PARAM_RE = re.compile(
    r"""
    \{ [^}]+ \}        # {id}  or {id:path}
  | : [A-Za-z_]\w*     # :id   (Express / Actix style)
  | < [^>]+ >          # <id>  or <int:id>  (Flask / Axum style)
""",
    re.VERBOSE,
)


def normalize_url(raw: str) -> str:
    """Reduce a URL or route path to a canonical, comparable form.

    * Strips scheme, host and port  (``http://host:8080/a`` → ``/a``).
    * Strips query string and fragment.
    * Replaces path-parameter placeholders with ``{_}``.
    * Collapses duplicate ``/`` and removes a trailing ``/``.
    * Lower-cases the result.
    """
    url = raw.strip()

    # Strip scheme + authority --------------------------------------------------
    if "://" in url:
        # Everything after the first `/` that follows `://`
        after_scheme = url.split("://", 1)[1]
        slash_idx = after_scheme.find("/")
        url = after_scheme[slash_idx:] if slash_idx != -1 else "/"

    # Strip query / fragment ----------------------------------------------------
    url = url.split("?", 1)[0]
    url = url.split("#", 1)[0]

    # Normalise path parameters -------------------------------------------------
    url = _PARAM_RE.sub("{_}", url)

    # Collapse duplicate slashes, strip trailing slash --------------------------
    url = re.sub(r"/+", "/", url)
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]

    return url.lower()


# endregion: --- URL normalisation helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Route matching
# ---------------------------------------------------------------------------


def match_route(
    call_url: str,
    call_method: str,
    route_path: str,
    route_method: str,
) -> tuple[bool, float]:
    """Compare an HTTP call against a route definition.

    Returns ``(matched, confidence)`` where *confidence* is:

    * **1.0** – exact path *and* method match.
    * **0.9** – path matches, call method is unknown / empty.
    * **0.85** – paths match after parameter wildcard normalisation.
    * **0.5** – one path is a strict prefix of the other.
    * **0.0** – no match.
    """
    norm_call = normalize_url(call_url)
    norm_route = normalize_url(route_path)

    cm = call_method.upper().strip()
    rm = route_method.upper().strip()

    # --- exact path ---
    if norm_call == norm_route:
        if cm and rm and cm == rm:
            return True, 1.0
        if not cm or not rm:
            return True, 0.9
        # Methods differ ⇒ not a match.
        return False, 0.0

    # --- segment-level wildcard comparison ---
    call_segs = norm_call.strip("/").split("/")
    route_segs = norm_route.strip("/").split("/")
    if len(call_segs) == len(route_segs) and call_segs:
        all_match = True
        for cs, rs in zip(call_segs, route_segs, strict=True):
            if cs == rs:
                continue
            if cs == "{_}" or rs == "{_}":
                continue
            all_match = False
            break
        if all_match:
            methods_ok = (not cm) or (not rm) or (cm == rm)
            return methods_ok, 0.85 if methods_ok else 0.0

    # --- prefix match ---
    shorter, longer = (
        (norm_call, norm_route)
        if len(norm_call) <= len(norm_route)
        else (norm_route, norm_call)
    )
    if shorter != "/" and longer.startswith(shorter + "/"):
        return True, 0.5

    return False, 0.0


# endregion: --- Route matching
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Cross-service edge resolver
# ---------------------------------------------------------------------------


def _find_best_route(
    call_node: GraphNode,
    routes: list[GraphNode],
) -> tuple[GraphNode | None, float]:
    """Find the route with highest match confidence for *call_node*."""
    call_url = call_node.properties.get("url", "")
    call_method = call_node.properties.get("method", "")

    best_route: GraphNode | None = None
    best_confidence = 0.0

    for route_node in routes:
        # Only cross-service
        if (
            call_node.service
            and route_node.service
            and call_node.service == route_node.service
        ):
            continue

        route_path = route_node.properties.get("path", "")
        route_method = route_node.properties.get("method", "")

        matched, confidence = match_route(
            call_url, call_method, route_path, route_method,
        )
        if matched and confidence > best_confidence:
            best_confidence = confidence
            best_route = route_node

    return best_route, best_confidence


def resolve_cross_service_edges(graph: CodeGraph) -> list[GraphEdge]:
    """Scan *graph* for HTTP_CALL ↔ ROUTE matches across services.

    Only nodes whose ``.service`` fields differ are considered (i.e. we
    never create a CALLS_SERVICE edge *within* the same service).

    Returns a list of new :class:`GraphEdge` objects with relation
    ``CALLS_SERVICE``.  Properties on each edge:

    * ``url`` – the normalised URL that caused the match.
    * ``method`` – HTTP method (if known).
    * ``client_service`` – name of the calling service.
    * ``server_service`` – name of the receiving service.
    * ``confidence`` – match confidence score.
    """
    routes: list[GraphNode] = []
    http_calls: list[GraphNode] = []

    for node in graph.nodes:
        if node.kind == NodeKind.ROUTE:
            routes.append(node)
        elif node.kind == NodeKind.HTTP_CALL:
            http_calls.append(node)

    if not routes or not http_calls:
        logger.debug(
            "Nothing to resolve: %d routes, %d http_calls",
            len(routes),
            len(http_calls),
        )
        return []

    logger.info(
        "Resolving cross-service edges: %d HTTP_CALLs x %d ROUTEs",
        len(http_calls),
        len(routes),
    )

    edges: list[GraphEdge] = []

    for call_node in http_calls:
        call_url = call_node.properties.get("url", "")

        # Skip dynamic / unresolvable URLs
        if not call_url or "<dynamic>" in call_url:
            continue

        best_route, best_confidence = _find_best_route(call_node, routes)

        if best_route is not None:
            call_method = call_node.properties.get("method", "")
            edge = GraphEdge(
                source=call_node.id,
                target=best_route.id,
                relation=EdgeRelation.CALLS_SERVICE,
                confidence=Confidence.INFERRED,
                confidence_score=best_confidence,
                file=call_node.file,
                properties={
                    "url": call_url,
                    "method": call_method or "UNKNOWN",
                    "client_service": call_node.service,
                    "server_service": best_route.service,
                    "confidence": str(best_confidence),
                },
            )
            edges.append(edge)
            logger.info(
                "  %s → %s (confidence=%.2f, url=%s)",
                call_node.id,
                best_route.id,
                best_confidence,
                call_url,
            )

    logger.info("Resolved %d cross-service edges", len(edges))
    return edges


# endregion: --- Cross-service edge resolver
