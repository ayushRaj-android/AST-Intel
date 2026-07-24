"""Tests for egress graph enrichment (P5): third-party HTTP_CALL props + SDK_CALL nodes."""

from __future__ import annotations

from pathlib import Path


def _build_graph(source: bytes):
    import ast_intel
    from ast_intel.core.graph_builder import GraphBuilder
    from ast_intel.extractors.python import PythonExtractor
    from ast_intel.models.workspace_model import (
        CrateModel,
        WorkspaceAST,
        WorkspaceMeta,
    )

    file_ast = PythonExtractor().extract(Path("app.py"), source)
    file_ast.file = "app.py"
    file_ast.module_path = "app"

    crate = CrateModel(name="app", language="python", manifest_path="pyproject.toml")
    crate.files.append(file_ast)

    workspace = WorkspaceAST(
        meta=WorkspaceMeta(
            schema_version=ast_intel.SCHEMA_VERSION,
            tool_version=ast_intel.TOOL_VERSION,
            workspace_root="/tmp/test",
        ),
    )
    workspace.crates["app"] = crate
    return GraphBuilder().build(workspace)


def _http_nodes(graph):
    return [n for n in graph.nodes if n.kind == "http_call"]


def _sdk_nodes(graph):
    return [n for n in graph.nodes if n.kind == "sdk_call"]


# ---------------------------------------------------------------------------
# region:    --- HTTP_CALL third-party enrichment
# ---------------------------------------------------------------------------


class TestHttpThirdPartyEnrichment:
    def test_known_vendor(self) -> None:
        source = b"""
import requests
def charge(tok):
    requests.post("https://api.stripe.com/v1/charges",
                  json={"amount": 2000},
                  headers={"Authorization": tok})
"""
        (node,) = _http_nodes(_build_graph(source))
        assert node.properties["third_party"] == "true"
        assert node.properties["vendor"] == "Stripe"
        assert node.properties["category"] == "payments"
        assert node.properties["has_secrets"] == "true"
        assert "amount" in node.properties["payload_fields"]

    def test_internal_host(self) -> None:
        source = b"""
import requests
def sync():
    requests.post("http://user-service/users", json={"a": 1})
"""
        (node,) = _http_nodes(_build_graph(source))
        assert node.properties["third_party"] == "false"
        assert "vendor" not in node.properties

    def test_unknown_external(self) -> None:
        source = b"""
import requests
def hook():
    requests.post("https://api.acme-xyz.io/hook", json={"a": 1})
"""
        (node,) = _http_nodes(_build_graph(source))
        assert node.properties["third_party"] == "true"
        assert node.properties["vendor"] == "unknown"

    def test_payload_summary_props(self) -> None:
        source = b"""
import requests
def send(user):
    requests.post("https://api.acme-xyz.io/u", json={"email": user.email})
"""
        (node,) = _http_nodes(_build_graph(source))
        assert node.properties["payload_count"] == "1"
        assert node.properties["payload_fields"] == "email"
        assert node.properties["payload_sources"] == "computed"
        assert node.properties["has_secrets"] == "false"


# endregion: --- HTTP_CALL third-party enrichment


# ---------------------------------------------------------------------------
# region:    --- SDK_CALL nodes + SENDS_DATA edges
# ---------------------------------------------------------------------------


class TestSdkGraph:
    def test_sdk_node_and_sends_data_edge(self) -> None:
        source = b"""
import stripe
def charge():
    stripe.Charge.create(amount=1, currency="usd")
"""
        graph = _build_graph(source)
        (node,) = _sdk_nodes(graph)
        assert node.properties["vendor"] == "Stripe"
        assert node.properties["category"] == "payments"
        assert node.properties["third_party"] == "true"
        assert "amount" in node.properties["payload_fields"]

        sends = [e for e in graph.edges if e.relation == "sends_data"]
        assert len(sends) == 1
        assert sends[0].source.endswith("::charge")
        assert "sdk_call" in sends[0].target

    def test_sdk_contains_edge(self) -> None:
        source = b"""
import stripe
def charge():
    stripe.Charge.create(amount=1)
"""
        graph = _build_graph(source)
        contains = [
            e for e in graph.edges
            if e.relation == "contains" and "sdk_call" in e.target
        ]
        assert len(contains) == 1

    def test_no_sdk_no_nodes(self) -> None:
        source = b"""
import requests
def load():
    requests.get("http://x.com/u")
"""
        assert _sdk_nodes(_build_graph(source)) == []


# endregion: --- SDK_CALL nodes + SENDS_DATA edges
