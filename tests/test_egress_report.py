"""Tests for the data-egress report, MCP tool, and CLI command (P6)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ast_intel.cli import app
from ast_intel.core.egress_report import build_egress_report

runner = CliRunner()


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


def _build_graph_at(source: bytes, file_path: str):
    import ast_intel
    from ast_intel.core.graph_builder import GraphBuilder
    from ast_intel.extractors.python import PythonExtractor
    from ast_intel.models.workspace_model import (
        CrateModel,
        WorkspaceAST,
        WorkspaceMeta,
    )

    file_ast = PythonExtractor().extract(Path(file_path), source)
    file_ast.file = file_path
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


_SOURCE = b"""
import stripe
import requests
def checkout(user, tok):
    profile = request.get_json()
    requests.post("https://api.stripe.com/v1/charges",
                  json={"amount": user.total, "profile": profile},
                  headers={"Authorization": tok})
    requests.post("http://user-service/internal", json={"a": 1})
    stripe.Refund.create(charge="ch_1")
"""


# ---------------------------------------------------------------------------
# region:    --- build_egress_report
# ---------------------------------------------------------------------------


class TestBuildEgressReport:
    def test_third_party_only_excludes_internal(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE))
        # Stripe HTTP + Stripe SDK, but NOT the internal user-service call.
        assert report["egress_count"] == 2
        urls = {e.get("url") for e in report["egress"]}
        assert "http://user-service/internal" not in urls
        assert report["vendors"] == {"Stripe": 2}

    def test_include_internal(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE), third_party_only=False)
        assert report["egress_count"] == 3

    def test_entry_shape_and_provenance(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE))
        http = next(e for e in report["egress"] if e["kind"] == "http")
        assert http["vendor"] == "Stripe"
        assert http["category"] == "payments"
        assert http["host"] == "api.stripe.com"
        assert http["has_secrets"] is True
        by_name = {f["name"]: f for f in http["payload_fields"]}
        assert by_name["profile"]["source_kind"] == "from-input"
        assert by_name["amount"]["source_kind"] == "computed"
        assert set(by_name["profile"]) == {"name", "source_kind", "value_preview"}

    def test_secret_value_redacted_in_report(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE))
        http = next(e for e in report["egress"] if e["kind"] == "http")
        auth = next(f for f in http["payload_fields"] if f["name"] == "Authorization")
        assert auth["value_preview"] == "<redacted>"

    def test_sdk_entry(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE))
        sdk = next(e for e in report["egress"] if e["kind"] == "sdk")
        assert sdk["vendor"] == "Stripe"
        assert sdk["sdk"] == "stripe"
        assert sdk["method"] == "stripe.Refund.create"

    def test_secrets_sent_count(self) -> None:
        report = build_egress_report(_build_graph(_SOURCE))
        assert report["secrets_sent"] == 1


# endregion: --- build_egress_report


# ---------------------------------------------------------------------------
# region:    --- Heuristic unknown third-party SDKs
# ---------------------------------------------------------------------------


class TestHeuristicUnknownSdk:
    def test_unknown_sdk_is_heuristic(self) -> None:
        src = b"import acmesdk\ndef f(x):\n    acmesdk.send_event(x)\n"
        report = build_egress_report(_build_graph(src))
        sdk = next(e for e in report["egress"] if e["kind"] == "sdk")
        assert sdk["vendor"] == "unknown"
        assert sdk["heuristic"] is True

    def test_known_sdk_not_heuristic(self) -> None:
        src = b"import stripe\ndef f():\n    stripe.Charge.create(amount=1)\n"
        report = build_egress_report(_build_graph(src))
        sdk = next(e for e in report["egress"] if e["kind"] == "sdk")
        assert sdk["heuristic"] is False

    def test_include_heuristic_false_drops_unknown(self) -> None:
        src = (
            b"import acmesdk\nimport stripe\n"
            b"def f(x):\n"
            b"    acmesdk.send_event(x)\n"
            b"    stripe.Charge.create(amount=1)\n"
        )
        assert build_egress_report(_build_graph(src))["egress_count"] == 2
        filtered = build_egress_report(_build_graph(src), include_heuristic=False)
        assert filtered["egress_count"] == 1
        assert filtered["egress"][0]["vendor"] == "Stripe"

    def test_first_party_absolute_import_excluded(self) -> None:
        # File lives under myapp/, so 'myapp' is first-party -> not egress.
        src = b"from myapp.core import svc\ndef f(x):\n    svc.run(x)\n"
        report = build_egress_report(_build_graph_at(src, "myapp/handlers.py"))
        assert all(e["kind"] != "sdk" for e in report["egress"])


# endregion: --- Heuristic unknown third-party SDKs


# ---------------------------------------------------------------------------
# region:    --- MCP get_data_egress
# ---------------------------------------------------------------------------


class TestMcpDataEgress:
    def test_dispatch_returns_report(self) -> None:
        from ast_intel.core._query_engine import QueryEngine
        from ast_intel.mcp_server import _dispatch

        graph = _build_graph(_SOURCE)
        result = _dispatch(QueryEngine(graph), graph, "get_data_egress", {})
        data = json.loads(result[0].text)
        assert data["egress_count"] == 2
        assert data["vendors"] == {"Stripe": 2}

    def test_dispatch_vendor_filter(self) -> None:
        from ast_intel.core._query_engine import QueryEngine
        from ast_intel.mcp_server import _dispatch

        graph = _build_graph(_SOURCE)
        result = _dispatch(
            QueryEngine(graph), graph, "get_data_egress", {"vendor": "stripe"},
        )
        data = json.loads(result[0].text)
        assert data["egress_count"] == 2
        assert all(e["vendor"] == "Stripe" for e in data["egress"])

    def test_tool_is_registered(self) -> None:
        from ast_intel.mcp_server import _TOOLS

        assert any(t.name == "get_data_egress" for t in _TOOLS)


# endregion: --- MCP get_data_egress


# ---------------------------------------------------------------------------
# region:    --- CLI egress command
# ---------------------------------------------------------------------------


class TestEgressCli:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_bytes(
            b'import stripe\n'
            b'def charge():\n'
            b'    stripe.Charge.create(amount=1, currency="usd")\n',
        )
        return repo

    def test_help(self) -> None:
        result = runner.invoke(app, ["egress", "--help"])
        assert result.exit_code == 0
        assert "--include-internal" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        result = runner.invoke(
            app,
            ["egress", str(repo), "-o", str(tmp_path / "out"), "--no-cache"],
        )
        assert result.exit_code == 0
        assert "Stripe" in result.output
        assert "get_data_egress" not in result.output  # sanity: not echoing tool
        assert '"vendor"' in result.output

    def test_table_output(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        result = runner.invoke(
            app,
            ["egress", str(repo), "-o", str(tmp_path / "out"),
             "--no-cache", "-f", "table"],
        )
        assert result.exit_code == 0
        assert "Stripe" in result.output


# endregion: --- CLI egress command
