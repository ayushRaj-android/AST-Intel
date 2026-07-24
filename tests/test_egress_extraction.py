"""Tests for HTTP payload extraction (L1) — third-party data-egress feature.

Verifies that outgoing HTTP calls capture *what data is sent* (body fields,
headers, query params) across languages, and that secret-looking values are
redacted.
"""

from __future__ import annotations

from pathlib import Path

from ast_intel.models.ast_node import Confidence, PayloadField

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _extract_python(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor().extract(Path("test.py"), source).http_calls


def _extract_typescript(source: bytes) -> list:
    from ast_intel.extractors.typescript import TypeScriptExtractor
    return TypeScriptExtractor().extract(Path("test.ts"), source).http_calls


def _extract_go(source: bytes) -> list:
    from ast_intel.extractors.go import GoExtractor
    return GoExtractor().extract(Path("test.go"), source).http_calls


def _extract_java(source: bytes) -> list:
    from ast_intel.extractors.java import JavaExtractor
    return JavaExtractor().extract(Path("test.java"), source).http_calls


def _payload_map(call) -> dict[tuple[str, str], PayloadField]:
    """Index a call's payload by ``(location, name)``."""
    return {(f.location, f.name): f for f in call.payload}


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Python
# ---------------------------------------------------------------------------


class TestPythonPayload:
    def test_json_dict_keys_captured(self) -> None:
        source = b"""
import requests
def send(user):
    requests.post("http://api.x.com/u", json={"user_id": 1, "email": user.email})
"""
        (call,) = _extract_python(source)
        pm = _payload_map(call)
        assert ("body", "user_id") in pm
        assert pm[("body", "user_id")].value == "1"
        assert pm[("body", "user_id")].value_kind == "literal"
        assert ("body", "email") in pm
        assert pm[("body", "email")].value_kind == "expression"
        assert pm[("body", "email")].value == "user.email"

    def test_query_params_captured(self) -> None:
        source = b"""
import requests
def search(query):
    requests.get("http://api.x.com/s", params={"q": query, "limit": 10})
"""
        (call,) = _extract_python(source)
        pm = _payload_map(call)
        assert pm[("query", "q")].value_kind == "variable"
        assert pm[("query", "q")].value == "query"
        assert pm[("query", "limit")].value == "10"

    def test_header_secret_redacted_by_name(self) -> None:
        source = b"""
import requests
def send(token):
    requests.post("http://api.x.com/u", headers={"Authorization": token})
"""
        (call,) = _extract_python(source)
        field = _payload_map(call)[("header", "Authorization")]
        assert field.redacted is True
        assert field.value == "<redacted>"

    def test_secret_redacted_by_value(self) -> None:
        source = b"""
import requests
def send():
    requests.post("http://api.x.com/u", json={"note": "sk_live_0123456789abcdefghij"})
"""
        (call,) = _extract_python(source)
        field = _payload_map(call)[("body", "note")]
        assert field.redacted is True
        assert field.value == "<redacted>"

    def test_whole_variable_body_is_inferred(self) -> None:
        source = b"""
import requests
def send(payload):
    requests.post("http://api.x.com/u", json=payload)
"""
        (call,) = _extract_python(source)
        (field,) = call.payload
        assert field.location == "body"
        assert field.name == ""
        assert field.value == "payload"
        assert field.value_kind == "variable"
        assert call.payload_confidence == Confidence.INFERRED

    def test_literal_only_payload_is_extracted(self) -> None:
        source = b"""
import requests
def send():
    requests.post("http://api.x.com/u", json={"a": 1, "b": "two"})
"""
        (call,) = _extract_python(source)
        assert call.payload_confidence == Confidence.EXTRACTED

    def test_get_without_payload_is_empty(self) -> None:
        source = b"""
import requests
def load():
    requests.get("http://api.x.com/u")
"""
        (call,) = _extract_python(source)
        assert call.payload == ()
        assert call.payload_confidence == Confidence.EXTRACTED


# endregion: --- Python


# ---------------------------------------------------------------------------
# region:    --- TypeScript: fetch + axios
# ---------------------------------------------------------------------------


class TestFetchPayload:
    def test_body_via_json_stringify_and_headers(self) -> None:
        source = b"""
async function send(user: any, token: string) {
    await fetch("http://api.x.com/u", {
        method: "POST",
        body: JSON.stringify({ name: "x", ssn: user.ssn }),
        headers: { Authorization: token },
    });
}
"""
        (call,) = _extract_typescript(source)
        pm = _payload_map(call)
        assert pm[("body", "name")].value == "x"
        assert pm[("body", "name")].value_kind == "literal"
        assert ("body", "ssn") in pm
        assert pm[("body", "ssn")].value_kind == "expression"
        assert pm[("header", "Authorization")].redacted is True


class TestAxiosPayload:
    def test_data_object_and_config_headers(self) -> None:
        source = b"""
async function pay(secret: string, k: string) {
    await axios.post("http://api.x.com/pay",
        { amount: 100, token: secret },
        { headers: { "X-Api-Key": k } });
}
"""
        (call,) = _extract_typescript(source)
        pm = _payload_map(call)
        assert pm[("body", "amount")].value == "100"
        assert pm[("body", "token")].redacted is True
        assert pm[("header", "X-Api-Key")].redacted is True


# endregion: --- TypeScript: fetch + axios


# ---------------------------------------------------------------------------
# region:    --- Go + Spring (best-effort body variable)
# ---------------------------------------------------------------------------


class TestGoPayload:
    def test_post_body_variable(self) -> None:
        source = b"""
package main
import "net/http"
func send(body io.Reader) {
    http.Post("http://api.x.com/u", "application/json", body)
}
"""
        (call,) = _extract_go(source)
        (field,) = call.payload
        assert field.location == "body"
        assert field.value == "body"
        assert field.value_kind == "variable"

    def test_get_has_no_body(self) -> None:
        source = b"""
package main
import "net/http"
func load() {
    http.Get("http://api.x.com/u")
}
"""
        (call,) = _extract_go(source)
        assert call.payload == ()


class TestSpringPayload:
    def test_post_request_body_variable(self) -> None:
        source = b"""
class C {
    void m(Object request) {
        restTemplate.postForObject("http://api.x.com/u", request, String.class);
    }
}
"""
        (call,) = _extract_java(source)
        (field,) = call.payload
        assert field.location == "body"
        assert field.value == "request"
        assert field.value_kind == "variable"


# endregion: --- Go + Spring (best-effort body variable)
