"""Emitter — orchestrates output serialization.

The emitter is Stage 4 of the pipeline. It takes a fully indexed
``WorkspaceAST`` and writes it to disk in the requested format(s):

- ``ast.json`` — machine-readable semantic index
- ``summary.md`` — human/LLM-readable reference document
- ``graph.json`` — code knowledge graph (JSON)
- ``graph.dot`` — code knowledge graph (Graphviz DOT)
- ``graph.mermaid.md`` — code knowledge graph (Mermaid diagram)
- ``graph.html`` — interactive vis.js graph visualization
- ``architecture.html`` — service architecture viewer (modules, request
  flows, UML, sequence, deployment)
- ``GRAPH_REPORT.md`` — graph analysis report (god nodes, communities, etc.)
- ``analysis.json`` — machine-readable analysis results

The emitter delegates actual serialization to:
- :class:`~ast_intel.formatters.json_formatter.JsonFormatter`
- :class:`~ast_intel.formatters.markdown_formatter.MarkdownFormatter`
- :class:`~ast_intel.formatters.graph_json_formatter.GraphJsonFormatter`
- :class:`~ast_intel.formatters.graph_dot_formatter.GraphDotFormatter`
- :class:`~ast_intel.formatters.graph_mermaid_formatter.GraphMermaidFormatter`
- :class:`~ast_intel.formatters.graph_html_formatter.GraphHtmlFormatter`
- :class:`~ast_intel.formatters.report_formatter.ReportFormatter`
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from ast_intel.core.analyzer import GraphAnalyzer
from ast_intel.core.graph_builder import GraphBuilder
from ast_intel.formatters.graph_arch_html_formatter import (
    ARCH_HTML_FILENAME,
    ArchHtmlFormatter,
)
from ast_intel.formatters.graph_dot_formatter import (
    GRAPH_DOT_FILENAME,
    GraphDotFormatter,
)
from ast_intel.formatters.graph_html_formatter import (
    GRAPH_HTML_FILENAME,
    GraphHtmlFormatter,
)
from ast_intel.formatters.graph_json_formatter import (
    GRAPH_JSON_FILENAME,
    GraphJsonFormatter,
)
from ast_intel.formatters.graph_mermaid_formatter import (
    GRAPH_MERMAID_FILENAME,
    GraphMermaidFormatter,
)
from ast_intel.formatters.json_formatter import JsonFormatter
from ast_intel.formatters.markdown_formatter import MarkdownFormatter
from ast_intel.formatters.report_formatter import ReportFormatter
from ast_intel.models.graph_model import CodeGraph
from ast_intel.models.workspace_model import WorkspaceAST

__all__: list[str] = ["Emitter"]

logger = logging.getLogger(__name__)

# Output filenames
AST_JSON_FILENAME: str = "ast.json"
SUMMARY_MD_FILENAME: str = "summary.md"

# Valid output format values
_VALID_FORMATS: frozenset[str] = frozenset({
    "json", "md", "both", "graph-json", "dot", "mermaid", "html", "arch",
    "all",
})

# Formats that require the graph builder
_GRAPH_FORMATS: frozenset[str] = frozenset(
    {"graph-json", "dot", "mermaid", "html", "arch"},
)

# Formats that emit ast.json + summary.md (and graph)
_COMBO_FORMATS: frozenset[str] = frozenset({"both", "all"})

# Type alias for output format literals
OutputFormatLiteral = Literal[
    "json", "md", "both", "graph-json", "dot", "mermaid", "html", "arch",
    "all",
]


class Emitter:
    """Write the workspace AST to output files.

    Attributes:
        output_dir: Directory to write output files into.
        output_format: ``"json"``, ``"md"``, ``"both"``, ``"graph-json"``,
            ``"dot"``, ``"mermaid"``, ``"html"``, ``"arch"``, or ``"all"``.
        run_analysis: When ``True``, also run graph analysis and produce
            ``GRAPH_REPORT.md`` + ``analysis.json``.
        offline: When ``True``, inline vis.js in the HTML output instead
            of loading from CDN.
    """

    def __init__(  # noqa: PLR0913
        self,
        output_dir: Path,
        output_format: OutputFormatLiteral = "both",
        *,
        run_analysis: bool = False,
        similarity: bool = False,
        similarity_threshold: float = 0.4,
        offline: bool = False,
    ) -> None:
        if output_format not in _VALID_FORMATS:
            msg = (
                f"Invalid output_format {output_format!r}. "
                f"Expected one of: {', '.join(sorted(_VALID_FORMATS))}"
            )
            raise ValueError(msg)
        self.output_dir = output_dir
        self.output_format = output_format
        self.run_analysis = run_analysis
        self.similarity = similarity
        self.similarity_threshold = similarity_threshold
        self.offline = offline

    def emit(  # noqa: C901, PLR0912, PLR0915
        self,
        workspace: WorkspaceAST,
        *,
        iac_graph: CodeGraph | None = None,
    ) -> list[Path]:
        """Serialize the workspace AST to the configured output format(s).

        The JSON formatter auto-populates ``workspace.meta`` with statistics
        and timestamps on first call, so markdown also sees the populated meta.

        Args:
            workspace: A fully indexed ``WorkspaceAST``.
            iac_graph: Optional IaC-only ``CodeGraph`` to merge into
                the code graph before emission.

        Returns:
            List of file paths that were written.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []

        if self.output_format in ("json", "both", "all"):
            json_path = self.output_dir / AST_JSON_FILENAME
            json_fmt = JsonFormatter()
            json_fmt.write(workspace, json_path, iac_graph=iac_graph)
            written.append(json_path)

        if self.output_format in ("md", "both", "all"):
            md_path = self.output_dir / SUMMARY_MD_FILENAME
            md_fmt = MarkdownFormatter()
            md_fmt.write(workspace, md_path)
            written.append(md_path)

        # --- Graph formats ---
        graph = None
        needs_graph = (
            self.output_format in _GRAPH_FORMATS
            or self.output_format in _COMBO_FORMATS
            or self.run_analysis
        )
        if needs_graph:
            builder = GraphBuilder(
                similarity=self.similarity,
                similarity_threshold=self.similarity_threshold,
            )
            graph = builder.build(workspace)

        # Merge IaC nodes/edges into the code graph
        if graph is not None and iac_graph is not None:
            graph.nodes.extend(iac_graph.nodes)
            graph.edges.extend(iac_graph.edges)
            logger.info(
                "Merged %d IaC nodes, %d IaC edges into code graph",
                len(iac_graph.nodes),
                len(iac_graph.edges),
            )

            # Cross-domain resolution (IaC ↔ code)
            from ast_intel.core._iac_resolver import IaCResolver

            _iac_prefixes = (
                "k8s://", "helm://", "docker://", "ansible://",
                "cicd://", "tf://", "cloud://",
            )
            code_only = CodeGraph(
                nodes=[
                    n for n in graph.nodes
                    if not n.id.startswith(_iac_prefixes)
                ],
            )
            resolver = IaCResolver()
            cross_edges = resolver.resolve(code_only, iac_graph)
            graph.edges.extend(cross_edges)
            if cross_edges:
                logger.info(
                    "Resolver added %d cross-domain edges",
                    len(cross_edges),
                )

        # --- Hyperedge detection ---
        if graph is not None:
            from ast_intel.core._hyperedge import detect_hyperedges

            graph.hyperedges = detect_hyperedges(graph)
            if graph.hyperedges:
                logger.info(
                    "Detected %d hyperedges", len(graph.hyperedges),
                )

        if self.output_format in _GRAPH_FORMATS and graph is not None:
            if self.output_format == "graph-json":
                gj_path = self.output_dir / GRAPH_JSON_FILENAME
                GraphJsonFormatter().write(graph, gj_path)
                written.append(gj_path)

            elif self.output_format == "dot":
                dot_path = self.output_dir / GRAPH_DOT_FILENAME
                GraphDotFormatter().write(graph, dot_path)
                written.append(dot_path)

            elif self.output_format == "mermaid":
                mm_path = self.output_dir / GRAPH_MERMAID_FILENAME
                GraphMermaidFormatter().write(graph, mm_path)
                written.append(mm_path)

            elif self.output_format == "html":
                html_path = self.output_dir / GRAPH_HTML_FILENAME
                GraphHtmlFormatter().write(
                    graph, html_path, offline=self.offline,
                )
                written.append(html_path)

            elif self.output_format == "arch":
                arch_path = self.output_dir / ARCH_HTML_FILENAME
                ArchHtmlFormatter().write(
                    graph, arch_path, offline=self.offline,
                )
                written.append(arch_path)

        # "both"/"all": also emit graph.json alongside ast.json + summary.md
        if self.output_format in _COMBO_FORMATS and graph is not None:
            gj_path = self.output_dir / GRAPH_JSON_FILENAME
            GraphJsonFormatter().write(graph, gj_path)
            written.append(gj_path)

        # "all": also emit interactive HTML visualization
        if self.output_format == "all" and graph is not None:
            html_path = self.output_dir / GRAPH_HTML_FILENAME
            GraphHtmlFormatter().write(
                graph, html_path, offline=self.offline,
            )
            written.append(html_path)

            arch_path = self.output_dir / ARCH_HTML_FILENAME
            ArchHtmlFormatter().write(
                graph, arch_path, offline=self.offline,
            )
            written.append(arch_path)

        # --- Analysis ---
        if self.run_analysis and graph is not None:
            analyzer = GraphAnalyzer()
            analysis = analyzer.analyze(graph)
            report_fmt = ReportFormatter()
            md_path, json_path = report_fmt.write(analysis, self.output_dir)
            written.extend([md_path, json_path])

        return written
