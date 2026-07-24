"""Tests for the architecture HTML formatter and its emitter wiring.

Covers :class:`ArchHtmlFormatter.write` (file contents, embedded ``ARCH``
payload, request-flow detail, offline vs CDN bundling) and the ``arch``
output format dispatched through the :class:`Emitter` (standalone and as
part of ``all``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ast_intel.core.emitter import Emitter
from ast_intel.formatters.graph_arch_html_formatter import (
    ARCH_HTML_FILENAME,
    ArchHtmlFormatter,
)
from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from tests.test_formatters import _rich_workspace

if TYPE_CHECKING:
    from pathlib import Path

_VIS_CDN = "unpkg.com/vis-network"
_MERMAID_CDN = "cdn.jsdelivr.net/npm/mermaid"


# ---------------------------------------------------------------------------
# region:    --- Factories
# ---------------------------------------------------------------------------


def _n(
    nid: str,
    label: str,
    kind: NodeKind,
    file: str = "",
    **props: str,
) -> GraphNode:
    return GraphNode(
        id=nid, label=label, kind=kind, file=file, properties=dict(props),
    )


def _e(src: str, tgt: str, rel: EdgeRelation, **props: str) -> GraphEdge:
    return GraphEdge(
        source=src, target=tgt, relation=rel,
        confidence=Confidence.EXTRACTED, confidence_score=1.0,
        properties=dict(props),
    )


def _api_graph() -> CodeGraph:
    """A single C# project exposing one route whose handler calls a service."""
    f = "src/api/users.cs"
    nodes = [
        _n("crate::api", "WebApi", NodeKind.CRATE, "api.csproj", language="csharp"),
        _n(f"{f}::<file>", f, NodeKind.FILE, f),
        _n(f"{f}::UsersController", "UsersController", NodeKind.STRUCT, f),
        _n(f"{f}::UsersController.List", "List", NodeKind.METHOD, f),
        _n(f"{f}::UserService.GetAll", "GetAll", NodeKind.METHOD, f),
        _n(
            f"{f}::route:GET:/api/users", "GET /api/users", NodeKind.ROUTE, f,
            method="GET", path="/api/users", handler="List", framework="aspnet",
        ),
    ]
    edges = [
        _e("crate::api", f"{f}::<file>", EdgeRelation.CONTAINS),
        _e(f"{f}::UsersController.List", f"{f}::GetAll", EdgeRelation.CALLS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _embedded_arch(html: str) -> dict:
    """Extract and parse the ``const ARCH = {...};`` payload from the page."""
    line = next(
        ln for ln in html.splitlines() if ln.lstrip().startswith("const ARCH =")
    )
    raw = line.split("=", 1)[1].strip().rstrip(";")
    return json.loads(raw)


# endregion: --- Factories


# ---------------------------------------------------------------------------
# region:    --- ArchHtmlFormatter
# ---------------------------------------------------------------------------


class TestArchHtmlFormatter:
    def test_writes_self_contained_html(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out)
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert html.lstrip().startswith("<!")
        assert "mermaid" in html.lower()

    def test_contains_module_route_and_controller(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out)
        html = out.read_text(encoding="utf-8")
        assert "WebApi" in html
        assert "GET /api/users" in html
        assert "UsersController" in html

    def test_embeds_arch_payload(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out)
        arch = _embedded_arch(out.read_text(encoding="utf-8"))
        assert arch["repo"]
        assert any(m["label"] == "WebApi" for m in arch["modules"])
        assert "detail" in arch["diagrams"]
        assert "class" in arch["diagrams"]

    def test_detail_shows_request_flow(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out)
        arch = _embedded_arch(out.read_text(encoding="utf-8"))
        detail = arch["diagrams"]["detail"]["WebApi"]
        assert detail.startswith("flowchart")
        assert "GET /api/users" in detail  # route
        assert "List" in detail            # handler
        assert "GetAll" in detail          # resolved call chain

    def test_online_uses_cdn(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out, offline=False)
        html = out.read_text(encoding="utf-8")
        assert _VIS_CDN in html
        assert _MERMAID_CDN in html

    def test_offline_inlines_bundles(self, tmp_output: Path) -> None:
        out = tmp_output / ARCH_HTML_FILENAME
        ArchHtmlFormatter().write(_api_graph(), out, offline=True)
        html = out.read_text(encoding="utf-8")
        assert _VIS_CDN not in html
        assert _MERMAID_CDN not in html
        # Inlined bundles make the page substantially larger than the CDN one.
        assert len(html) > 500_000


# endregion: --- ArchHtmlFormatter


# ---------------------------------------------------------------------------
# region:    --- Emitter wiring
# ---------------------------------------------------------------------------


class TestArchEmitter:
    def test_arch_format_emits_architecture_html(self, tmp_output: Path) -> None:
        emitter = Emitter(output_dir=tmp_output, output_format="arch")
        written = emitter.emit(_rich_workspace())
        arch_path = tmp_output / ARCH_HTML_FILENAME
        assert arch_path.exists()
        assert arch_path in written

    def test_all_includes_architecture_html(self, tmp_output: Path) -> None:
        emitter = Emitter(output_dir=tmp_output, output_format="all")
        written = emitter.emit(_rich_workspace())
        names = {p.name for p in written}
        assert ARCH_HTML_FILENAME in names
        assert "graph.html" in names
        assert (tmp_output / ARCH_HTML_FILENAME).exists()

    def test_arch_offline_has_no_cdn(self, tmp_output: Path) -> None:
        emitter = Emitter(
            output_dir=tmp_output, output_format="arch", offline=True,
        )
        emitter.emit(_rich_workspace())
        html = (tmp_output / ARCH_HTML_FILENAME).read_text(encoding="utf-8")
        assert _VIS_CDN not in html
        assert _MERMAID_CDN not in html


# endregion: --- Emitter wiring
