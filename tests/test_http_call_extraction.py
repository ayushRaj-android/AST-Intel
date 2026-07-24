"""Tests for HTTP client call detection — Feature 17.

Tests all detectors (reqwest, requests/httpx, fetch, axios, Spring, Go)
and the graph builder integration for HTTP_CALL nodes/edges.
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
    return ext.extract(Path("test.rs"), source).http_calls


def _extract_python(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    ext = PythonExtractor()
    return ext.extract(Path("test.py"), source).http_calls


def _extract_typescript(source: bytes) -> list:
    from ast_intel.extractors.typescript import TypeScriptExtractor
    ext = TypeScriptExtractor()
    return ext.extract(Path("test.ts"), source).http_calls


def _extract_java(source: bytes) -> list:
    from ast_intel.extractors.java import JavaExtractor
    ext = JavaExtractor()
    return ext.extract(Path("test.java"), source).http_calls


def _extract_go(source: bytes) -> list:
    from ast_intel.extractors.go import GoExtractor
    ext = GoExtractor()
    return ext.extract(Path("test.go"), source).http_calls


def _calls_as_tuples(calls: list) -> set[tuple[str, str, str]]:
    """Convert calls to a set of (method, url, library) for assertion."""
    return {(c.method, c.url, c.library) for c in calls}


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Reqwest Tests
# ---------------------------------------------------------------------------


class TestReqwestCalls:
    """Tests for Rust reqwest HTTP call detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        tuples = _calls_as_tuples(calls)
        assert ("GET", "http://example.com/api/users", "reqwest") in tuples
        assert ("POST", "http://example.com/api/users", "reqwest") in tuples
        assert ("GET", "http://example.com/api/health", "reqwest") in tuples
        assert len(calls) >= 3

    def test_all_have_library_reqwest(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        assert all(c.library == "reqwest" for c in calls)

    def test_all_have_spans(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        assert all(c.span is not None for c in calls)

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        callers = {c.caller for c in calls}
        assert "not_http" not in callers

    def test_caller_attribution(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        callers = {c.caller for c in calls}
        assert "fetch_users" in callers
        assert "create_user" in callers
        assert "fetch_scoped" in callers

    def test_scoped_call(self) -> None:
        source = b"""
use reqwest;
async fn check() { let r = reqwest::get("http://localhost/ping").await.unwrap(); }
"""
        calls = _extract_rust(source)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url == "http://localhost/ping"

    def test_field_call(self) -> None:
        source = b"""
async fn f(c: &reqwest::Client) { c.post("http://a.com/b").send().await; }
"""
        calls = _extract_rust(source)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_dynamic_url(self) -> None:
        source = (FIXTURES_DIR / "rust" / "reqwest_calls.rs").read_bytes()
        calls = _extract_rust(source)
        dynamic = [c for c in calls if c.caller == "dynamic_url"]
        assert len(dynamic) == 1
        assert dynamic[0].url == "<dynamic>"

    def test_empty_source(self) -> None:
        calls = _extract_rust(b"fn main() {}")
        assert calls == []


# endregion: --- Reqwest Tests


# ---------------------------------------------------------------------------
# region:    --- Python HTTP Tests
# ---------------------------------------------------------------------------


class TestPythonHttpCalls:
    """Tests for Python requests/httpx detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "python" / "requests_app.py").read_bytes()
        calls = _extract_python(source)
        tuples = _calls_as_tuples(calls)
        assert ("GET", "http://example.com/api/users", "requests") in tuples
        assert ("POST", "http://example.com/api/users", "requests") in tuples
        assert ("GET", "http://example.com/api/health", "httpx") in tuples
        assert ("GET", "http://example.com/api/orders", "httpx") in tuples

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "python" / "requests_app.py").read_bytes()
        calls = _extract_python(source)
        callers = {c.caller for c in calls}
        assert "not_http" not in callers

    def test_caller_attribution(self) -> None:
        source = (FIXTURES_DIR / "python" / "requests_app.py").read_bytes()
        calls = _extract_python(source)
        callers = {c.caller for c in calls}
        assert "fetch_users" in callers
        assert "create_user" in callers
        assert "fetch_with_httpx" in callers

    def test_fstring_url(self) -> None:
        source = (FIXTURES_DIR / "python" / "requests_app.py").read_bytes()
        calls = _extract_python(source)
        fstr = [c for c in calls if c.caller == "fetch_with_fstring"]
        assert len(fstr) == 1
        assert "{param}" in fstr[0].url or fstr[0].url == "<dynamic>"

    def test_all_have_spans(self) -> None:
        source = (FIXTURES_DIR / "python" / "requests_app.py").read_bytes()
        calls = _extract_python(source)
        assert all(c.span is not None for c in calls)

    def test_inline_get(self) -> None:
        source = b"""
import requests
def f():
    return requests.get("http://api.example.com/data")
"""
        calls = _extract_python(source)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].library == "requests"

    def test_empty_source(self) -> None:
        calls = _extract_python(b"def main(): pass")
        assert calls == []


# endregion: --- Python HTTP Tests


# ---------------------------------------------------------------------------
# region:    --- Fetch Tests
# ---------------------------------------------------------------------------


class TestFetchCalls:
    """Tests for TypeScript fetch() detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        fetch_calls = [c for c in calls if c.library == "fetch"]
        assert len(fetch_calls) >= 2

    def test_simple_get(self) -> None:
        source = b"""
async function f() { const r = await fetch("/test"); }
"""
        calls = _extract_typescript(source)
        fetch_calls = [c for c in calls if c.library == "fetch"]
        assert len(fetch_calls) == 1
        assert fetch_calls[0].method == "GET"
        assert fetch_calls[0].url == "/test"

    def test_post_with_options(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        posts = [c for c in calls if c.library == "fetch" and c.method == "POST"]
        assert len(posts) >= 1

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        callers = {c.caller for c in calls}
        assert "notHttp" not in callers


# endregion: --- Fetch Tests


# ---------------------------------------------------------------------------
# region:    --- Axios Tests
# ---------------------------------------------------------------------------


class TestAxiosCalls:
    """Tests for TypeScript axios detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        axios_calls = [c for c in calls if c.library == "axios"]
        assert len(axios_calls) >= 2

    def test_get_call(self) -> None:
        source = b"""
import axios from "axios";
async function f() { const r = await axios.get("/health"); }
"""
        calls = _extract_typescript(source)
        axios_calls = [c for c in calls if c.library == "axios"]
        assert len(axios_calls) == 1
        assert axios_calls[0].method == "GET"
        assert axios_calls[0].url == "/health"

    def test_template_literal_url(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        template = [c for c in calls if c.library == "axios" and c.method == "PUT"]
        assert len(template) >= 1
        assert "{param}" in template[0].url or template[0].url == "<dynamic>"

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "typescript" / "fetch_axios.ts").read_bytes()
        calls = _extract_typescript(source)
        callers = {c.caller for c in calls}
        assert "notHttp" not in callers


# endregion: --- Axios Tests


# ---------------------------------------------------------------------------
# region:    --- Spring RestTemplate Tests
# ---------------------------------------------------------------------------


class TestSpringHttpCalls:
    """Tests for Java RestTemplate detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_client.java").read_bytes()
        calls = _extract_java(source)
        tuples = _calls_as_tuples(calls)
        assert ("GET", "http://user-service/api/users", "spring") in tuples
        assert ("POST", "http://user-service/api/users", "spring") in tuples
        assert len(calls) >= 3

    def test_delete_call(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_client.java").read_bytes()
        calls = _extract_java(source)
        deletes = [c for c in calls if c.method == "DELETE"]
        assert len(deletes) == 1

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_client.java").read_bytes()
        calls = _extract_java(source)
        callers = {c.caller for c in calls}
        assert "notHttp" not in callers

    def test_all_have_spans(self) -> None:
        source = (FIXTURES_DIR / "java" / "spring_client.java").read_bytes()
        calls = _extract_java(source)
        assert all(c.span is not None for c in calls)

    def test_inline_call(self) -> None:
        source = b"""
public class C {
    void f() { restTemplate.getForObject("http://x/y", String.class); }
}
"""
        calls = _extract_java(source)
        assert len(calls) == 1
        assert calls[0].method == "GET"

    def test_empty_class(self) -> None:
        source = b"public class Empty {}"
        calls = _extract_java(source)
        assert calls == []


# endregion: --- Spring RestTemplate Tests


# ---------------------------------------------------------------------------
# region:    --- Go HTTP Tests
# ---------------------------------------------------------------------------


class TestGoHttpCalls:
    """Tests for Go net/http detection."""

    def test_fixture_file(self) -> None:
        source = (FIXTURES_DIR / "go" / "http_client.go").read_bytes()
        calls = _extract_go(source)
        tuples = _calls_as_tuples(calls)
        assert ("GET", "http://example.com/api/users", "net/http") in tuples
        assert ("POST", "http://example.com/api/users", "net/http") in tuples
        assert len(calls) >= 3

    def test_new_request(self) -> None:
        source = (FIXTURES_DIR / "go" / "http_client.go").read_bytes()
        calls = _extract_go(source)
        new_req = [c for c in calls if c.caller == "customRequest"]
        assert len(new_req) == 1
        # NewRequest maps to UNKNOWN unless first arg is parsed.
        assert new_req[0].method in {"UNKNOWN", "DELETE"}

    def test_no_false_positives(self) -> None:
        source = (FIXTURES_DIR / "go" / "http_client.go").read_bytes()
        calls = _extract_go(source)
        callers = {c.caller for c in calls}
        assert "notHttp" not in callers

    def test_all_have_spans(self) -> None:
        source = (FIXTURES_DIR / "go" / "http_client.go").read_bytes()
        calls = _extract_go(source)
        assert all(c.span is not None for c in calls)

    def test_inline_get(self) -> None:
        source = b"""
package main
import "net/http"
func f() { http.Get("http://example.com") }
"""
        calls = _extract_go(source)
        assert len(calls) == 1
        assert calls[0].method == "GET"

    def test_empty_source(self) -> None:
        calls = _extract_go(b"package main\nfunc main() {}")
        assert calls == []


# endregion: --- Go HTTP Tests


# ---------------------------------------------------------------------------
# region:    --- URL Extraction Tests
# ---------------------------------------------------------------------------


class TestUrlExtraction:
    """Tests for URL extraction quality across detectors."""

    def test_literal_url_preserved(self) -> None:
        source = b"""
import requests
def f(): requests.get("http://exact.com/path")
"""
        calls = _extract_python(source)
        assert calls[0].url == "http://exact.com/path"

    def test_fstring_has_param_marker(self) -> None:
        source = b"""
import requests
def f(uid):
    requests.get(f"/users/{uid}")
"""
        calls = _extract_python(source)
        assert len(calls) == 1
        # Should be "{param}" or "<dynamic>" — not the literal f-string.
        assert "uid" not in calls[0].url

    def test_rust_format_macro_dynamic(self) -> None:
        """Rust format!(...) URLs should be detected as dynamic."""
        source = b"""
async fn f(c: &reqwest::Client, base: &str) {
    let u = format!("{}/api/jobs", base);
    c.get(&u).send().await;
}
"""
        calls = _extract_rust(source)
        # The URL arg is a variable reference (&u), so it should be <dynamic>.
        assert len(calls) == 1
        assert calls[0].url == "<dynamic>"

    def test_template_literal_has_param(self) -> None:
        source = b"""
import axios from "axios";
async function f(id) { await axios.get(`/users/${id}`); }
"""
        calls = _extract_typescript(source)
        axios_calls = [c for c in calls if c.library == "axios"]
        assert len(axios_calls) == 1
        assert "{param}" in axios_calls[0].url or axios_calls[0].url == "<dynamic>"


# endregion: --- URL Extraction Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Builder Integration Tests
# ---------------------------------------------------------------------------


class TestHttpCallGraphIntegration:
    """Tests that HTTP_CALL nodes and CALLS_HTTP edges appear in the graph."""

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
        file_ast = ext.extract(Path("src/client.rs"), source)
        file_ast.file = "src/client.rs"
        file_ast.module_path = "my-crate::client"

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

    def test_http_call_nodes_exist(self) -> None:
        source = b"""
use reqwest;
async fn check(c: &reqwest::Client) {
    c.get("http://example.com/health").send().await;
}
"""
        graph = self._build_graph_from_rust(source)
        hc_nodes = [n for n in graph.nodes if n.kind == "http_call"]
        assert len(hc_nodes) == 1
        assert "GET" in hc_nodes[0].label
        assert hc_nodes[0].properties["method"] == "GET"
        assert hc_nodes[0].properties["library"] == "reqwest"

    def test_contains_edge_exists(self) -> None:
        source = b"""
use reqwest;
async fn f(c: &reqwest::Client) {
    c.post("http://a.com/b").send().await;
}
"""
        graph = self._build_graph_from_rust(source)
        contains = [
            e for e in graph.edges
            if e.relation == "contains" and "http_call" in e.target
        ]
        assert len(contains) == 1
        assert contains[0].source.endswith("<file>")

    def test_calls_http_edge_exists(self) -> None:
        source = b"""
use reqwest;
async fn do_request(c: &reqwest::Client) {
    c.get("http://x.com/y").send().await;
}
"""
        graph = self._build_graph_from_rust(source)
        calls_http = [
            e for e in graph.edges if e.relation == "calls_http"
        ]
        assert len(calls_http) == 1
        assert calls_http[0].source.endswith("::do_request")
        assert "http_call" in calls_http[0].target

    def test_no_http_calls_no_nodes(self) -> None:
        source = b'fn main() { println!("hello"); }'
        graph = self._build_graph_from_rust(source)
        hc_nodes = [n for n in graph.nodes if n.kind == "http_call"]
        assert hc_nodes == []

    def test_multiple_calls_in_graph(self) -> None:
        source = b"""
use reqwest;
async fn a(c: &reqwest::Client) { c.get("http://a.com/x").send().await; }
async fn b(c: &reqwest::Client) { c.post("http://a.com/y").send().await; }
"""
        graph = self._build_graph_from_rust(source)
        hc_nodes = [n for n in graph.nodes if n.kind == "http_call"]
        assert len(hc_nodes) == 2
        labels = {n.label for n in hc_nodes}
        assert "GET http://a.com/x" in labels
        assert "POST http://a.com/y" in labels


# endregion: --- Graph Builder Integration Tests
