"""Tests for :mod:`ast_intel.core._url_matcher`."""

from __future__ import annotations

import pytest

from ast_intel.core._url_matcher import (
    match_route,
    normalize_url,
    resolve_cross_service_edges,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphNode,
    NodeKind,
)

# ---------------------------------------------------------------------------
# region:    --- TestNormalizeUrl
# ---------------------------------------------------------------------------


class TestNormalizeUrl:
    """Unit tests for :func:`normalize_url`."""

    def test_plain_path(self) -> None:
        assert normalize_url("/api/v1/items") == "/api/v1/items"

    def test_strips_scheme_and_host(self) -> None:
        assert normalize_url("http://localhost:8080/api/v1") == "/api/v1"

    def test_strips_https_host(self) -> None:
        assert normalize_url("https://my-service.example.com/foo") == "/foo"

    def test_strips_query_and_fragment(self) -> None:
        assert normalize_url("/items?page=1&size=10#top") == "/items"

    def test_curly_brace_params(self) -> None:
        assert normalize_url("/items/{id}/detail") == "/items/{_}/detail"

    def test_colon_params(self) -> None:
        assert normalize_url("/items/:id/detail") == "/items/{_}/detail"

    def test_angle_bracket_params(self) -> None:
        assert normalize_url("/items/<id>/detail") == "/items/{_}/detail"

    def test_flask_typed_params(self) -> None:
        assert normalize_url("/items/<int:id>") == "/items/{_}"

    def test_collapse_slashes(self) -> None:
        assert normalize_url("//api///v1//") == "/api/v1"

    def test_trailing_slash_removed(self) -> None:
        assert normalize_url("/items/") == "/items"

    def test_root_path(self) -> None:
        assert normalize_url("/") == "/"

    def test_lowercased(self) -> None:
        assert normalize_url("/API/V1/Items") == "/api/v1/items"

    def test_url_without_path(self) -> None:
        assert normalize_url("http://host:9090") == "/"

    def test_complex_param_syntax(self) -> None:
        assert normalize_url("/files/{id:path}") == "/files/{_}"


# endregion: --- TestNormalizeUrl
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- TestMatchRoute
# ---------------------------------------------------------------------------


class TestMatchRoute:
    """Unit tests for :func:`match_route`."""

    def test_exact_match(self) -> None:
        matched, conf = match_route("/api/v1/items", "GET", "/api/v1/items", "GET")
        assert matched is True
        assert conf == 1.0

    def test_exact_path_unknown_method(self) -> None:
        matched, conf = match_route("/api/v1/items", "", "/api/v1/items", "GET")
        assert matched is True
        assert conf == 0.9

    def test_exact_path_method_mismatch(self) -> None:
        matched, conf = match_route("/api/v1/items", "POST", "/api/v1/items", "GET")
        assert matched is False
        assert conf == 0.0

    def test_wildcard_param_match(self) -> None:
        matched, conf = match_route(
            "/api/v1/items/123", "GET", "/api/v1/items/{id}", "GET"
        )
        assert matched is True
        assert conf == 0.85

    def test_wildcard_both_sides(self) -> None:
        matched, conf = match_route(
            "/api/{version}/items", "GET", "/api/{ver}/items", "GET"
        )
        assert matched is True
        assert conf == 1.0  # both params normalise to {_} → exact match

    def test_wildcard_method_mismatch(self) -> None:
        matched, conf = match_route(
            "/api/v1/items/123", "DELETE", "/api/v1/items/{id}", "GET"
        )
        assert matched is False
        assert conf == 0.0

    def test_prefix_match(self) -> None:
        matched, conf = match_route("/api/v1", "GET", "/api/v1/items", "GET")
        assert matched is True
        assert conf == 0.5

    def test_no_match(self) -> None:
        matched, conf = match_route("/foo/bar", "GET", "/baz/qux", "GET")
        assert matched is False
        assert conf == 0.0

    def test_different_segment_count(self) -> None:
        matched, conf = match_route("/a/b/c", "GET", "/x/y", "GET")
        assert matched is False
        assert conf == 0.0

    def test_case_insensitive(self) -> None:
        matched, conf = match_route("/API/V1", "get", "/api/v1", "GET")
        assert matched is True
        assert conf == 1.0


# endregion: --- TestMatchRoute
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Test helpers
# ---------------------------------------------------------------------------


def _make_route(
    node_id: str,
    path: str,
    method: str,
    service: str,
    file: str = "routes.rs",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        label=f"{method} {path}",
        kind=NodeKind.ROUTE,
        file=file,
        service=service,
        properties={"path": path, "method": method},
    )


def _make_http_call(
    node_id: str,
    url: str,
    method: str,
    service: str,
    file: str = "client.rs",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        label=f"{method} {url}",
        kind=NodeKind.HTTP_CALL,
        file=file,
        service=service,
        properties={"url": url, "method": method},
    )


# endregion: --- Test helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- TestResolveCrossServiceEdges
# ---------------------------------------------------------------------------


class TestResolveCrossServiceEdges:
    """Unit tests for :func:`resolve_cross_service_edges`."""

    def test_basic_cross_service_match(self) -> None:
        """HTTP_CALL in svc-a matches ROUTE in svc-b."""
        route = _make_route("svc-b::r1", "/api/v1/items", "GET", "svc-b")
        call = _make_http_call(
            "svc-a::c1", "http://svc-b:8080/api/v1/items", "GET", "svc-a"
        )
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)

        assert len(edges) == 1
        assert edges[0].source == "svc-a::c1"
        assert edges[0].target == "svc-b::r1"
        assert edges[0].relation == EdgeRelation.CALLS_SERVICE
        assert edges[0].properties["client_service"] == "svc-a"
        assert edges[0].properties["server_service"] == "svc-b"

    def test_same_service_not_matched(self) -> None:
        """HTTP_CALL and ROUTE in same service → no edge."""
        route = _make_route("svc-a::r1", "/api/v1/items", "GET", "svc-a")
        call = _make_http_call(
            "svc-a::c1", "http://localhost/api/v1/items", "GET", "svc-a"
        )
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 0

    def test_no_routes(self) -> None:
        """No ROUTE nodes → empty result."""
        call = _make_http_call("svc-a::c1", "/foo", "GET", "svc-a")
        graph = CodeGraph(nodes=[call], edges=[])
        assert resolve_cross_service_edges(graph) == []

    def test_no_http_calls(self) -> None:
        """No HTTP_CALL nodes → empty result."""
        route = _make_route("svc-b::r1", "/foo", "GET", "svc-b")
        graph = CodeGraph(nodes=[route], edges=[])
        assert resolve_cross_service_edges(graph) == []

    def test_dynamic_url_skipped(self) -> None:
        """URLs containing <dynamic> are skipped."""
        route = _make_route("svc-b::r1", "/api/v1/items", "GET", "svc-b")
        call = _make_http_call(
            "svc-a::c1", "<dynamic>/api/v1/items", "GET", "svc-a"
        )
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 0

    def test_best_confidence_wins(self) -> None:
        """When multiple routes match, highest confidence wins."""
        route_exact = _make_route("svc-b::r1", "/api/v1/items", "GET", "svc-b")
        route_prefix = _make_route(
            "svc-b::r2", "/api/v1/items/detail", "GET", "svc-b"
        )
        call = _make_http_call("svc-a::c1", "/api/v1/items", "GET", "svc-a")
        graph = CodeGraph(nodes=[route_exact, route_prefix, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 1
        assert edges[0].target == "svc-b::r1"  # exact match wins

    def test_param_wildcard_match(self) -> None:
        """Route with path params matches concrete URL segments."""
        route = _make_route("svc-b::r1", "/items/{id}", "GET", "svc-b")
        call = _make_http_call("svc-a::c1", "/items/42", "GET", "svc-a")
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 1
        assert edges[0].confidence_score == pytest.approx(0.85)

    def test_multiple_calls_resolved(self) -> None:
        """Multiple HTTP_CALLs each resolve independently."""
        route1 = _make_route("svc-b::r1", "/api/items", "GET", "svc-b")
        route2 = _make_route("svc-c::r2", "/api/orders", "POST", "svc-c")
        call1 = _make_http_call("svc-a::c1", "/api/items", "GET", "svc-a")
        call2 = _make_http_call("svc-a::c2", "/api/orders", "POST", "svc-a")
        graph = CodeGraph(
            nodes=[route1, route2, call1, call2],
            edges=[],
        )

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 2

        sources = {e.source for e in edges}
        assert sources == {"svc-a::c1", "svc-a::c2"}

    def test_confidence_in_properties(self) -> None:
        """Edge properties include confidence score string."""
        route = _make_route("svc-b::r1", "/api/v1", "GET", "svc-b")
        call = _make_http_call("svc-a::c1", "/api/v1", "GET", "svc-a")
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        assert len(edges) == 1
        assert edges[0].properties["confidence"] == "1.0"

    def test_empty_service_field_allows_match(self) -> None:
        """When service is empty string, nodes are treated as different."""
        route = _make_route("r1", "/api", "GET", "svc-b")
        call = _make_http_call("c1", "/api", "GET", "")
        graph = CodeGraph(nodes=[route, call], edges=[])

        edges = resolve_cross_service_edges(graph)
        # Empty service != "svc-b", so match happens
        assert len(edges) == 1


# endregion: --- TestResolveCrossServiceEdges
# ---------------------------------------------------------------------------
