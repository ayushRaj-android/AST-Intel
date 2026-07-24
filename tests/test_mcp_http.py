"""Tests for Streamable HTTP MCP transport (``create_http_app`` / ``--port``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")


def _mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "hello.py").write_text("def greet(name: str) -> str:\n    return name\n")
    return tmp_path


def _mcp_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _parse_rpc(response: httpx.Response) -> dict[str, Any]:
    """Parse a JSON or SSE Streamable HTTP response into a JSON-RPC object."""
    ctype = response.headers.get("content-type", "")
    text = response.text
    if "application/json" in ctype:
        data = json.loads(text)
        assert isinstance(data, dict)
        return data
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            data = json.loads(payload)
            if isinstance(data, dict) and (
                "result" in data or "error" in data or "id" in data
            ):
                return data
    pytest.fail(f"No JSON-RPC payload in response: {text[:500]!r}")


def test_http_initialize_and_tools_list(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    from ast_intel.mcp_server import create_http_app

    repo = _mini_repo(tmp_path)
    app = create_http_app(
        repo,
        host="127.0.0.1",
        port=7500,
        watch=False,
        similarity=False,
        no_cache=True,
    )

    with TestClient(app) as client:
        init = client.post(
            "/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ast-intel-test", "version": "0"},
                },
            },
        )
        assert init.status_code == 200, init.text
        init_body = _parse_rpc(init)
        assert "result" in init_body, init_body
        assert init_body["result"]["serverInfo"]["name"] == "ast-intel"

        client.post(
            "/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )

        listed = client.post(
            "/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert listed.status_code == 200, listed.text
        tools_body = _parse_rpc(listed)
        assert "result" in tools_body, tools_body
        names = {t["name"] for t in tools_body["result"]["tools"]}
        assert "search_symbols" in names
        assert "get_context" in names


def test_serve_help_documents_http_flags() -> None:
    from typer.testing import CliRunner

    from ast_intel.cli import app

    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--host" in result.output
