"""Tests for HTTP route extraction — Feature 16.

Tests all framework detectors (Axum, Actix, FastAPI, Flask, Express, Spring)
and the graph builder integration for route nodes/edges.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import FIXTURES_DIR

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _extract_rust(source: bytes) -> list:
    from ast_intel.extractors.rust import RustExtractor
    ext = RustExtractor()
    return ext.extract(Path("test.rs"), source).routes


def _extract_python(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    ext = PythonExtractor()
    return ext.extract(Path("test.py"), source).routes


def _extract_typescript(source: bytes) -> list:
    from ast_intel.extractors.typescript import TypeScriptExtractor
    ext = TypeScriptExtractor()
    return ext.extract(Path("test.ts"), source).routes


def _extract_java(source: bytes) -> list:
    from ast_intel.extractors.java import JavaExtractor
    ext = JavaExtractor()
    return ext.extract(Path("test.java"), source).routes


def _extract_csharp(source: bytes) -> list:
    from ast_intel.extractors.csharp import CSharpExtractor
    ext = CSharpExtractor()
    return ext.extract(Path("test.cs"), source).routes


def _routes_as_tuples(routes: list) -> set[tuple[str, str, str]]:
    """Convert routes to a set of (method, path, handler) for assertion."""
    return {(r.method, r.path, r.handler) for r in routes}


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Axum Tests
# ---------------------------------------------------------------------------


class TestAxumRoutes:
    """Tests for Axum ``Router.route()`` detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "rust" / "axum_routes.rs").read_bytes()
        routes = _extract_rust(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/health", "health") in tuples
        assert ("GET", "/api/users", "list_users") in tuples
        assert ("POST", "/api/users", "create_user") in tuples
        assert ("PUT", "/api/users/{id}", "update_user") in tuples
        assert ("DELETE", "/api/users/{id}", "delete_user") in tuples
        assert len(routes) == 5

    def test_all_have_framework_axum(self) -> None:
        source = (FIXTURES_DIR / "rust" / "axum_routes.rs").read_bytes()
        routes = _extract_rust(source)
        assert all(r.framework == "axum" for r in routes)

    def test_all_have_spans(self) -> None:
        source = (FIXTURES_DIR / "rust" / "axum_routes.rs").read_bytes()
        routes = _extract_rust(source)
        assert all(r.span is not None for r in routes)

    def test_single_route(self) -> None:
        source = b"""
use axum::{routing::get, Router};

fn app() -> Router {
    Router::new().route("/ping", get(ping_handler))
}

async fn ping_handler() -> &'static str { "pong" }
"""
        routes = _extract_rust(source)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/ping"
        assert routes[0].handler == "ping_handler"

    def test_no_routes(self) -> None:
        source = b"""
fn main() {
    println!("no routes here");
}
"""
        routes = _extract_rust(source)
        assert routes == []

    def test_chained_routes(self) -> None:
        source = b"""
use axum::{routing::{get, post}, Router};

fn app() -> Router {
    Router::new()
        .route("/a", get(handler_a))
        .route("/b", post(handler_b))
}

async fn handler_a() {}
async fn handler_b() {}
"""
        routes = _extract_rust(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/a", "handler_a") in tuples
        assert ("POST", "/b", "handler_b") in tuples

    def test_qualified_routing(self) -> None:
        """Test axum::routing::post(handler) fully-qualified form."""
        source = b"""
use axum::Router;

fn routes() -> Router {
    axum::Router::new()
        .route("/submit", axum::routing::post(handle_submit))
}

async fn handle_submit() {}
"""
        routes = _extract_rust(source)
        assert len(routes) == 1
        assert routes[0].method == "POST"
        assert routes[0].path == "/submit"


# endregion: --- Axum Tests


# ---------------------------------------------------------------------------
# region:    --- Actix Tests
# ---------------------------------------------------------------------------


class TestActixRoutes:
    """Tests for actix-web ``#[get("/path")]`` attribute detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "rust" / "actix_routes.rs").read_bytes()
        routes = _extract_rust(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/health", "health") in tuples
        assert ("POST", "/api/items", "create_item") in tuples
        assert ("DELETE", "/api/items/{id}", "delete_item") in tuples
        assert len(routes) == 3

    def test_all_have_framework_actix(self) -> None:
        source = (FIXTURES_DIR / "rust" / "actix_routes.rs").read_bytes()
        routes = _extract_rust(source)
        assert all(r.framework == "actix" for r in routes)

    def test_no_route_on_plain_function(self) -> None:
        source = (FIXTURES_DIR / "rust" / "actix_routes.rs").read_bytes()
        routes = _extract_rust(source)
        handlers = {r.handler for r in routes}
        assert "no_route_handler" not in handlers

    def test_inline_actix_attribute(self) -> None:
        source = b"""
#[post("/submit")]
pub async fn submit_form() -> String {
    "ok".to_string()
}
"""
        routes = _extract_rust(source)
        assert len(routes) == 1
        assert routes[0].method == "POST"
        assert routes[0].path == "/submit"
        assert routes[0].handler == "submit_form"


# endregion: --- Actix Tests


# ---------------------------------------------------------------------------
# region:    --- FastAPI Tests
# ---------------------------------------------------------------------------


class TestFastAPIRoutes:
    """Tests for FastAPI ``@app.get("/path")`` decorator detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "python" / "fastapi_app.py").read_bytes()
        routes = _extract_python(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/health", "health") in tuples
        assert ("POST", "/api/users", "create_user") in tuples
        assert ("GET", "/api/users/{user_id}", "get_user") in tuples
        assert ("DELETE", "/api/users/{user_id}", "delete_user") in tuples
        assert len(routes) == 4

    def test_all_have_framework_fastapi(self) -> None:
        source = (FIXTURES_DIR / "python" / "fastapi_app.py").read_bytes()
        routes = _extract_python(source)
        assert all(r.framework == "fastapi" for r in routes)

    def test_no_route_on_plain_function(self) -> None:
        source = (FIXTURES_DIR / "python" / "fastapi_app.py").read_bytes()
        routes = _extract_python(source)
        handlers = {r.handler for r in routes}
        assert "helper_function" not in handlers

    def test_single_route(self) -> None:
        source = b"""
from fastapi import FastAPI
app = FastAPI()

@app.get("/items")
async def list_items():
    return []
"""
        routes = _extract_python(source)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/items"
        assert routes[0].handler == "list_items"

    def test_no_routes(self) -> None:
        source = b"""
def hello():
    return "world"
"""
        routes = _extract_python(source)
        assert routes == []


# endregion: --- FastAPI Tests


# ---------------------------------------------------------------------------
# region:    --- Flask Tests
# ---------------------------------------------------------------------------


class TestFlaskRoutes:
    """Tests for Flask ``@app.route("/path")`` decorator detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "python" / "flask_app.py").read_bytes()
        routes = _extract_python(source)
        tuples = _routes_as_tuples(routes)
        # @app.route("/health") → defaults to GET
        assert ("GET", "/health", "health") in tuples
        # @app.route("/api/users", methods=["GET"])
        assert ("GET", "/api/users", "list_users") in tuples
        # @app.route("/api/users", methods=["GET", "POST"])
        assert ("GET", "/api/users", "users") in tuples
        assert ("POST", "/api/users", "users") in tuples
        # @app.route("/api/items/<item_id>", methods=["PUT"])
        assert ("PUT", "/api/items/<item_id>", "update_item") in tuples

    def test_no_route_on_plain_function(self) -> None:
        source = (FIXTURES_DIR / "python" / "flask_app.py").read_bytes()
        routes = _extract_python(source)
        handlers = {r.handler for r in routes}
        assert "internal_helper" not in handlers

    def test_default_method_is_get(self) -> None:
        source = b"""
from flask import Flask
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "pong"
"""
        routes = _extract_python(source)
        assert len(routes) == 1
        assert routes[0].method == "GET"

    def test_multiple_methods(self) -> None:
        source = b"""
from flask import Flask
app = Flask(__name__)

@app.route("/data", methods=["GET", "POST", "PUT"])
def data():
    return "data"
"""
        routes = _extract_python(source)
        methods = {r.method for r in routes}
        assert methods == {"GET", "POST", "PUT"}
        assert len(routes) == 3
        assert all(r.handler == "data" for r in routes)


# endregion: --- Flask Tests


# ---------------------------------------------------------------------------
# region:    --- Express Tests
# ---------------------------------------------------------------------------


class TestExpressRoutes:
    """Tests for Express ``router.get("/path", handler)`` detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "express_routes.ts").read_bytes()
        routes = _extract_typescript(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/health", "healthHandler") in tuples
        assert ("GET", "/api/users", "listUsers") in tuples
        assert ("POST", "/api/users", "createUser") in tuples
        assert ("DELETE", "/api/users/:id", "deleteUser") in tuples
        assert len(routes) == 4

    def test_all_have_framework_express(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "express_routes.ts").read_bytes()
        routes = _extract_typescript(source)
        assert all(r.framework == "express" for r in routes)

    def test_no_routes_in_plain_file(self) -> None:
        source = b"""
function add(a: number, b: number): number {
    return a + b;
}
"""
        routes = _extract_typescript(source)
        assert routes == []

    def test_single_route(self) -> None:
        source = b"""
import express from "express";
const app = express();
app.get("/status", statusHandler);
function statusHandler(req, res) { res.send("ok"); }
"""
        routes = _extract_typescript(source)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/status"


# endregion: --- Express Tests


# ---------------------------------------------------------------------------
# region:    --- Spring Tests
# ---------------------------------------------------------------------------


class TestSpringRoutes:
    """Tests for Spring ``@GetMapping("/path")`` annotation detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_controller.java").read_bytes()
        routes = _extract_java(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/users", "listUsers") in tuples
        assert ("POST", "/users", "createUser") in tuples
        assert ("PUT", "/users/{id}", "updateUser") in tuples
        assert ("DELETE", "/users/{id}", "deleteUser") in tuples

    def test_all_have_framework_spring(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_controller.java").read_bytes()
        routes = _extract_java(source)
        assert all(r.framework == "spring" for r in routes)

    def test_no_route_on_private_method(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_controller.java").read_bytes()
        routes = _extract_java(source)
        handlers = {r.handler for r in routes}
        assert "internalHelper" not in handlers

    def test_request_mapping(self) -> None:
        source = b"""
package com.example;
import org.springframework.web.bind.annotation.*;

@RestController
public class HealthController {
    @RequestMapping("/health")
    public String health() { return "ok"; }
}
"""
        routes = _extract_java(source)
        assert len(routes) == 1
        assert routes[0].method == "*"
        assert routes[0].path == "/health"


# endregion: --- Spring Tests


# ---------------------------------------------------------------------------
# region:    --- ASP.NET Core Tests
# ---------------------------------------------------------------------------


class TestAspNetRoutes:
    """Tests for ASP.NET Core controller + minimal-API detection."""

    _CONTROLLER = b"""
namespace Api.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class UsersController : ControllerBase
    {
        [HttpGet]
        public IActionResult GetAll() => Ok();

        [HttpGet("{id}")]
        public IActionResult GetById(int id) => Ok();

        [HttpPost]
        public IActionResult Create() => Ok();

        [HttpPut("{id}")]
        [HttpPatch("{id}")]
        public IActionResult Update(int id) => Ok();

        [Route("legacy")]
        public IActionResult Legacy() => Ok();

        [HttpDelete("/absolute/{id}")]
        public IActionResult Remove(int id) => Ok();
    }
}
"""

    def test_controller_prefix_and_token(self) -> None:
        tuples = _routes_as_tuples(_extract_csharp(self._CONTROLLER))
        assert ("GET", "/api/Users", "GetAll") in tuples
        assert ("POST", "/api/Users", "Create") in tuples

    def test_method_template_joined(self) -> None:
        tuples = _routes_as_tuples(_extract_csharp(self._CONTROLLER))
        assert ("GET", "/api/Users/{id}", "GetById") in tuples

    def test_multiple_verbs_on_one_method(self) -> None:
        tuples = _routes_as_tuples(_extract_csharp(self._CONTROLLER))
        assert ("PUT", "/api/Users/{id}", "Update") in tuples
        assert ("PATCH", "/api/Users/{id}", "Update") in tuples

    def test_route_only_is_wildcard(self) -> None:
        tuples = _routes_as_tuples(_extract_csharp(self._CONTROLLER))
        assert ("*", "/api/Users/legacy", "Legacy") in tuples

    def test_absolute_path_overrides_prefix(self) -> None:
        tuples = _routes_as_tuples(_extract_csharp(self._CONTROLLER))
        assert ("DELETE", "/absolute/{id}", "Remove") in tuples

    def test_framework_is_aspnet(self) -> None:
        routes = _extract_csharp(self._CONTROLLER)
        assert routes
        assert all(r.framework == "aspnet" for r in routes)

    def test_spans_present(self) -> None:
        routes = _extract_csharp(self._CONTROLLER)
        assert all(r.span is not None for r in routes)

    def test_minimal_api(self) -> None:
        source = b"""
public static class Endpoints
{
    public static void Map(WebApplication app)
    {
        app.MapGet("/ping", () => "pong");
        app.MapPost("/users", CreateUser);
        app.MapDelete("/users/{id}", DeleteUser);
        app.MapControllers();
    }
}
"""
        routes = _extract_csharp(source)
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/ping", "<anonymous:MapGet:/ping>") in tuples
        assert ("POST", "/users", "CreateUser") in tuples
        assert ("DELETE", "/users/{id}", "DeleteUser") in tuples
        # app.MapControllers() must NOT produce a route.
        assert len(routes) == 3

    def test_map_methods(self) -> None:
        source = b"""
public static class E {
    public static void Map(WebApplication app) {
        app.MapMethods("/multi", new[] { "GET", "POST" }, Handler);
    }
}
"""
        tuples = _routes_as_tuples(_extract_csharp(source))
        assert ("GET", "/multi", "Handler") in tuples
        assert ("POST", "/multi", "Handler") in tuples

    def test_qualified_and_suffix_attribute(self) -> None:
        source = b"""
[Microsoft.AspNetCore.Mvc.Route("api/things")]
public class ThingsController : ControllerBase {
    [HttpGetAttribute]
    public IActionResult All() => Ok();
}
"""
        tuples = _routes_as_tuples(_extract_csharp(source))
        assert ("GET", "/api/things", "All") in tuples

    def test_all_have_framework_aspnet_minimal(self) -> None:
        source = b"""
public class E {
    public void Map(WebApplication app) {
        app.MapGet("/a", H);
    }
}
"""
        routes = _extract_csharp(source)
        assert routes
        assert all(r.framework == "aspnet" for r in routes)

    def test_plain_class_no_routes(self) -> None:
        source = b"""
public class PlainService {
    public int Add(int a, int b) => a + b;
}
"""
        assert _extract_csharp(source) == []

    def test_minimal_api_ignores_non_literal_path(self) -> None:
        source = b"""
public class E {
    public void Map(WebApplication app) {
        var p = "/dyn";
        app.MapGet(p, Handler);
    }
}
"""
        assert _extract_csharp(source) == []


# endregion: --- ASP.NET Core Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Builder Integration Tests
# ---------------------------------------------------------------------------


class TestRouteGraphIntegration:
    """Tests that route nodes and edges appear in the built graph."""

    def _build_graph_from_rust(self, source: bytes):
        import ast_intel
        from ast_intel.core.graph_builder import GraphBuilder
        from ast_intel.extractors.rust import RustExtractor
        from ast_intel.models.workspace_model import (
            CrateModel,
            WorkspaceAST,
            WorkspaceMeta,
        )

        ext = RustExtractor()
        file_ast = ext.extract(Path("src/server.rs"), source)
        file_ast.file = "src/server.rs"
        file_ast.module_path = "my-crate::server"

        crate = CrateModel(
            name="my-crate",
            language="rust",
            manifest_path="Cargo.toml",
        )
        crate.files.append(file_ast)

        workspace = WorkspaceAST(
            meta=WorkspaceMeta(
                schema_version=ast_intel.SCHEMA_VERSION,
                tool_version=ast_intel.TOOL_VERSION,
                workspace_root="/tmp/test",
            ),
        )
        workspace.crates["my-crate"] = crate

        builder = GraphBuilder()
        return builder.build(workspace)

    def test_route_nodes_exist(self) -> None:
        source = b"""
use axum::{routing::get, Router};
fn app() -> Router { Router::new().route("/health", get(health)) }
async fn health() -> &'static str { "ok" }
"""
        graph = self._build_graph_from_rust(source)
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert len(route_nodes) == 1
        assert route_nodes[0].label == "GET /health"
        assert route_nodes[0].properties["path"] == "/health"
        assert route_nodes[0].properties["method"] == "GET"
        assert route_nodes[0].properties["framework"] == "axum"
        assert route_nodes[0].properties["handler"] == "health"

    def test_exposes_edge_exists(self) -> None:
        source = b"""
use axum::{routing::post, Router};
fn app() -> Router { Router::new().route("/submit", post(handle)) }
async fn handle() {}
"""
        graph = self._build_graph_from_rust(source)
        exposes = [e for e in graph.edges if e.relation == "exposes"]
        assert len(exposes) == 1
        assert exposes[0].source.endswith("<file>")
        assert "route:POST:/submit" in exposes[0].target

    def test_handles_edge_exists(self) -> None:
        source = b"""
use axum::{routing::post, Router};
fn app() -> Router { Router::new().route("/submit", post(handle)) }
async fn handle() {}
"""
        graph = self._build_graph_from_rust(source)
        handles = [e for e in graph.edges if e.relation == "handles"]
        assert len(handles) == 1
        assert "route:POST:/submit" in handles[0].source
        assert handles[0].target.endswith("::handle")

    def test_multiple_routes_in_graph(self) -> None:
        source = b"""
use axum::{routing::{get, post}, Router};
fn app() -> Router {
    Router::new()
        .route("/a", get(ha))
        .route("/b", post(hb))
}
async fn ha() {}
async fn hb() {}
"""
        graph = self._build_graph_from_rust(source)
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert len(route_nodes) == 2
        labels = {n.label for n in route_nodes}
        assert labels == {"GET /a", "POST /b"}

    def test_no_routes_no_route_nodes(self) -> None:
        source = b"""
fn main() { println!("hello"); }
"""
        graph = self._build_graph_from_rust(source)
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert route_nodes == []


# endregion: --- Graph Builder Integration Tests
