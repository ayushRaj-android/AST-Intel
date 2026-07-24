"""AST Intel CLI — main entry point.

This module defines the ``ast-intel`` command-line interface using Typer.
Subcommands:

- ``scan`` — full pipeline: discover → extract → index → emit
- ``query`` — search the code graph for symbols matching a pattern
- ``path`` — find the shortest path between two symbols
- ``explain`` — structural explanation of a symbol's role
- ``impact`` — blast-radius / impact analysis for a symbol
- ``deps`` — forward dependencies of a symbol
- ``dependents`` — reverse dependents of a symbol
- ``context`` — unified single-shot context for a symbol
- ``usages`` — all incoming references to a symbol
- ``files`` — list file nodes with symbol counts
- ``implementors`` — find all implementations of a trait
- ``similar`` — find structurally similar symbols
- ``community`` — explore community cluster members
- ``serve`` — start an MCP server exposing graph tools for AI agents
- ``merge`` — combine multiple graph.json files into a unified graph
- ``install`` — configure an AI assistant to use ast-intel's MCP server
- ``uninstall`` — remove ast-intel configuration from an AI assistant

Usage::

    ast-intel scan /path/to/repo --include src/ --output ./analysis
    ast-intel query "UserService" /path/to/repo
    ast-intel path "AuthController" "Database" /path/to/repo
    ast-intel explain "UserService" /path/to/repo
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ast_intel import SCHEMA_VERSION, TOOL_VERSION
from ast_intel.core.cache import IncrementalCache
from ast_intel.core.dispatcher import Dispatcher
from ast_intel.core.emitter import Emitter
from ast_intel.core.indexer import Indexer
from ast_intel.core.security import (
    PathSecurityError,
    check_grammar_availability,
    sanitize_output_path,
    validate_repo_path,
)
from ast_intel.core.workspace import WorkspaceDiscovery, _load_ast_intel_scan
from ast_intel.models.graph_model import CodeGraph, NodeKind

if TYPE_CHECKING:
    from ast_intel.core._query_engine import DependencyResult
    from ast_intel.models.graph_model import GraphNode

__all__: list[str] = ["app"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants & Types
# ---------------------------------------------------------------------------

_console = Console(stderr=True)
_err_console = Console(stderr=True, style="bold red")


class OutputFormat(StrEnum):
    """Output format choices for the ``--format`` flag."""

    JSON = "json"
    MD = "md"
    BOTH = "both"
    GRAPH_JSON = "graph-json"
    DOT = "dot"
    MERMAID = "mermaid"
    HTML = "html"
    ARCH = "arch"
    ALL = "all"


class SupportedLanguage(StrEnum):
    """Languages recognized by the ``--lang`` flag."""

    RUST = "rust"
    GO = "go"
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    CSHARP = "csharp"
    CPP = "cpp"
    JAVA = "java"
    RUBY = "ruby"
    KOTLIN = "kotlin"
    SCALA = "scala"
    SWIFT = "swift"
    PHP = "php"


# endregion: --- Constants & Types


# ---------------------------------------------------------------------------
# region:    --- Version callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:  # noqa: FBT001
    """Print version and exit when ``--version`` is passed."""
    if value:
        _console.print(f"ast-intel {TOOL_VERSION} (schema {SCHEMA_VERSION})")
        raise typer.Exit


def _version_callback_app(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            help="Print tool version and exit.",
            is_eager=True,
        ),
    ] = None,
) -> None:
    """App-level callback — handles ``--version`` before subcommands."""
    if version:
        _console.print(f"ast-intel {TOOL_VERSION} (schema {SCHEMA_VERSION})")
        raise typer.Exit


# endregion: --- Version callback


# ---------------------------------------------------------------------------
# region:    --- Typer Application
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="ast-intel",
    help=(
        "Language-agnostic CLI that parses a codebase into a structured "
        "semantic AST index — ast.json + summary.md — for coding agents, "
        "security analysis, and impact assessment."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
    invoke_without_command=True,
    callback=_version_callback_app,
)


def _run_iac_pipeline(  # noqa: C901
    repo_root: Path,
    include: list[str] | None,
    exclude: list[str] | None,
    *,
    quiet: bool,
) -> CodeGraph | None:
    """Run the IaC discovery + extraction + graph build pipeline.

    Returns a CodeGraph with IaC-only nodes/edges, or None if no IaC
    files found.
    """
    try:
        from ast_intel.core.iac_discovery import IaCDiscovery
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder
        from ast_intel.extractors.iac.ansible import AnsibleExtractor
        from ast_intel.extractors.iac.bicep import BicepExtractor
        from ast_intel.extractors.iac.cicd import CICDExtractor
        from ast_intel.extractors.iac.docker import DockerExtractor
        from ast_intel.extractors.iac.helm import HelmExtractor
        from ast_intel.extractors.iac.kubernetes import KubernetesExtractor
        from ast_intel.extractors.iac.terraform import TerraformExtractor
        from ast_intel.models.iac_model import IaCContext, IaCGraph
    except ImportError:
        logger.debug("IaC dependencies not available (pyyaml missing?)")
        return None

    discovery = IaCDiscovery(
        repo_root=repo_root,
        include_paths=include or [],
        exclude_paths=exclude or [],
    )
    iac_files = discovery.discover()
    if not iac_files:
        return None

    if not quiet:
        _console.print(f"  IaC: {len(iac_files)} candidate file(s)")

    # Extractor registry: order matters (Helm before K8s to claim Chart.yaml,
    # K8s before Ansible since K8s wins for apiVersion files)
    helm_extractor = HelmExtractor()
    extractors = [
        helm_extractor,
        KubernetesExtractor(),
        TerraformExtractor(),
        BicepExtractor(),
        CICDExtractor(),
        AnsibleExtractor(),
        DockerExtractor(),
    ]
    iac_graphs: list[IaCGraph] = []
    seen_chart_dirs: set[str] = set()

    for iac_file in iac_files:
        try:
            source = iac_file.abs_path.read_bytes()
        except OSError as exc:
            logger.warning(
                "Cannot read IaC file %s: %s", iac_file.rel_path, exc,
            )
            continue

        peek = source[:1024]
        context = IaCContext(
            workspace_root=str(repo_root),
            rel_path=iac_file.rel_path,
        )

        for extractor in extractors:
            if extractor.can_handle(iac_file.abs_path, peek):
                # Helm: deduplicate by chart directory
                if isinstance(extractor, HelmExtractor):
                    chart_dir = str(iac_file.abs_path.parent)
                    if chart_dir in seen_chart_dirs:
                        break
                    seen_chart_dirs.add(chart_dir)
                result = extractor.extract(
                    iac_file.abs_path, source, context,
                )
                if result.resources:
                    iac_graphs.append(result)
                break  # first matching extractor wins

    if not iac_graphs:
        return None

    builder = IaCGraphBuilder()
    code_graph = builder.build(iac_graphs)

    if not quiet:
        _console.print(
            f"  IaC: {len(code_graph.nodes)} nodes, "
            f"{len(code_graph.edges)} edges",
        )

    return code_graph


@app.command(name="scan")
def scan(  # noqa: PLR0913, C901, PLR0915, PLR0912
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root to analyze.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            "-i",
            help="Include specific paths (repeatable). Relative to repo root.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            "-e",
            help="Exclude specific paths (repeatable). Supports glob patterns.",
        ),
    ] = None,
    lang: Annotated[
        list[SupportedLanguage] | None,
        typer.Option(
            "--lang",
            "-l",
            help="Languages to parse. Default: auto-detect from manifests.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory for generated files. Default: <repo>/ast_output/.",
        ),
    ] = Path(),
    fmt: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help=(
                "Output format: json, md, both, "
                "graph-json, dot, mermaid, html, arch, or all. "
                "'arch' emits architecture.html (service architecture viewer). "
                "'all' emits json + md + graph-json + html + arch."
            ),
        ),
    ] = OutputFormat.BOTH,
    analyze: Annotated[
        bool,
        typer.Option(
            "--analyze",
            help=(
                "Run graph analysis after extraction. Produces "
                "GRAPH_REPORT.md and analysis.json with god nodes, "
                "communities, surprising connections, and hyperedges."
            ),
        ),
    ] = False,
    similarity: Annotated[
        bool,
        typer.Option(
            "--similarity",
            help=(
                "Emit SIMILAR_TO edges between structurally similar "
                "symbols using Jaccard similarity on feature sets. "
                "Only effective with graph or analysis outputs."
            ),
        ),
    ] = False,
    similarity_threshold: Annotated[
        float,
        typer.Option(
            "--similarity-threshold",
            help=(
                "Minimum Jaccard similarity (0.0\u20131.0) to emit a "
                "SIMILAR_TO edge. Default: 0.4."
            ),
            min=0.0,
            max=1.0,
        ),
    ] = 0.4,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help=(
                "For HTML format: inline vis.js in the output instead "
                "of loading from CDN. Produces a larger but self-contained file."
            ),
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            "-w",
            help="Parallel workers for file extraction. 0 = CPU count.",
            min=0,
        ),
    ] = 0,
    no_methods: Annotated[
        bool,
        typer.Option(
            "--no-methods",
            help="Skip package method call extraction (faster).",
        ),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached results, force full re-parse.",
        ),
    ] = False,
    no_iac: Annotated[
        bool,
        typer.Option(
            "--no-iac",
            help="Skip Infrastructure-as-Code scanning (K8s, Helm, Docker, etc.).",
        ),
    ] = False,
    infra_scan: Annotated[
        bool,
        typer.Option(
            "--infra-scan",
            help="Emit resources.json with the cloud infrastructure inventory.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress progress output (for CI pipelines).",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Enable verbose debug logging to stderr.",
        ),
    ] = False,
) -> None:
    """Analyze a codebase and produce a structured semantic AST index."""
    # --- Setup logging ---
    _configure_logging(debug=debug)
    if debug and not quiet:
        _console.print(
            "[yellow]Debug mode: verbose logs may include file paths "
            "and system information[/yellow]"
        )

    start_time = time.monotonic()

    # --- Security: validate paths ---
    try:
        validated_repo = validate_repo_path(repo_path)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    try:
        output_dir = sanitize_output_path(output, validated_repo)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    # --- Grammar availability check ---
    languages = [lg.value for lg in lang] if lang else None
    grammar_warnings = check_grammar_availability(languages)
    if grammar_warnings:
        for warning in grammar_warnings:
            _err_console.print(f"Warning: {warning}")
        # If ALL requested languages are missing, abort
        if languages and len(grammar_warnings) == len(languages):
            _err_console.print(
                "Error: No grammars available for requested languages. "
                "Install with: pip install ast-intel[all]"
            )
            raise typer.Exit(code=2)

    # Resolve worker count (0 means auto-detect, cap at 32)
    worker_count = min(workers if workers > 0 else (os.cpu_count() or 4), 32)

    if not quiet:
        _console.print(
            f"[bold blue]AST Intel v{TOOL_VERSION}[/bold blue] "
            f"analyzing [green]{validated_repo}[/green]"
        )
        _console.print(
            f"  Workers: {worker_count} | "
            f"Format: {fmt.value} | "
            f"Methods: {'skip' if no_methods else 'extract'}"
        )

    # --- Stage 1: Discover ---
    try:
        discovery = WorkspaceDiscovery(
            repo_root=validated_repo,
            include_paths=include or [],
            exclude_paths=exclude or [],
            languages=languages,
        )
        workspace = discovery.discover()
    except Exception as exc:  # noqa: BLE001
        _err_console.print(f"Error during discovery: {exc}")
        raise typer.Exit(code=2) from None

    file_count = sum(len(c.files) for c in workspace.crates.values())
    if file_count == 0:
        if not quiet:
            _console.print("[yellow]0 files found. Nothing to process.[/yellow]")
        raise typer.Exit(code=0)

    if not quiet:
        _console.print(
            f"  Discovered: {len(workspace.crates)} crate(s), {file_count} file(s)"
        )

    # --- Stage 1.5: Cache diff ---
    cache = IncrementalCache(output_dir / ".ast-intel-cache.json")
    skip_files: frozenset[str] = frozenset()
    file_hashes: dict[str, str] = {}

    if not no_cache:
        cached_entries = cache.load()
        if cached_entries:
            cache_diff, file_hashes = cache.diff(
                workspace, cached_entries, validated_repo,
            )
            cache.restore_cached_asts(
                workspace, cached_entries, cache_diff.unchanged,
            )
            skip_files = cache_diff.unchanged
            if not quiet:
                _console.print(
                    f"  Cache: {len(cache_diff.unchanged)} unchanged, "
                    f"{len(cache_diff.changed)} changed, "
                    f"{len(cache_diff.added)} new, "
                    f"{len(cache_diff.deleted)} deleted"
                )

    # --- Stage 2: Extract ---
    dispatcher = Dispatcher(
        workers=worker_count,
        skip_methods=no_methods,
        skip_files=skip_files,
        quiet=quiet,
        debug=debug,
    )
    workspace = dispatcher.dispatch(workspace)

    # --- Stage 3: Index ---
    indexer = Indexer()
    workspace = indexer.build_cross_references(workspace)

    # --- Stage 3.5: IaC Discovery + Extraction ---
    iac_graph: CodeGraph | None = None
    if not no_iac:
        # Merge CLI --include with .ast-intel-scan so IaC discovery
        # respects the same path constraints as code discovery.
        scan_paths = _load_ast_intel_scan(validated_repo)
        iac_include = list(dict.fromkeys(
            (include or []) + scan_paths,
        ))
        try:
            iac_graph = _run_iac_pipeline(
                validated_repo, iac_include or None, exclude, quiet=quiet,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("IaC scanning failed (non-fatal): %s", exc)
            if not quiet:
                _console.print(f"[yellow]IaC scanning failed: {exc}[/yellow]")

    # --- Stage 4: Emit ---
    try:
        emitter = Emitter(
            output_dir=output_dir,
            output_format=fmt.value,
            run_analysis=analyze,
            similarity=similarity,
            similarity_threshold=similarity_threshold,
            offline=offline,
        )
        emitter.emit(workspace, iac_graph=iac_graph)
    except Exception as exc:  # noqa: BLE001
        _err_console.print(f"Error writing output: {exc}")
        raise typer.Exit(code=2) from None

    # --- Stage 4.1: Infra scan (resources.json) ---
    if infra_scan:
        try:
            from ast_intel.core.graph_builder import GraphBuilder
            from ast_intel.models.graph_model import NodeKind

            builder = GraphBuilder(
                similarity=similarity,
                similarity_threshold=similarity_threshold,
            )
            graph = builder.build(workspace)
            if iac_graph is not None:
                graph.nodes.extend(iac_graph.nodes)
                graph.edges.extend(iac_graph.edges)
            cr_nodes = [n for n in graph.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
            if cr_nodes:
                resources_data = [
                    {
                        "provider": n.properties.get("provider", ""),
                        "service": n.properties.get("service", ""),
                        "category": n.properties.get("category", ""),
                        "client": n.properties.get("client", ""),
                        "caller": n.properties.get("caller", ""),
                        "name": n.properties.get("name", ""),
                        "source": n.properties.get("source", ""),
                        "file": n.file,
                    }
                    for n in cr_nodes
                ]
                resources_path = output_dir / "resources.json"
                resources_path.write_text(
                    json.dumps(resources_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                if not quiet:
                    _console.print(
                        f"  Infra: {len(cr_nodes)} cloud resources → resources.json",
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Infra scan failed (non-fatal): %s", exc)

    # --- Stage 4.5: Save cache ---
    if not no_cache:
        if not file_hashes:
            # Full parse (no prior cache) — compute hashes now
            _, file_hashes = cache.diff(workspace, {}, validated_repo)
        cache.save(workspace, file_hashes)

    elapsed = time.monotonic() - start_time

    # --- Summary ---
    all_files = [f for c in workspace.crates.values() for f in c.files]
    total_files = len(all_files)
    total_structs = sum(len(f.structs) for f in all_files)
    total_functions = sum(len(f.functions) for f in all_files)
    error_count = sum(1 for f in all_files if f.errors)
    if not quiet:
        _console.print(
            f"\n[bold green]Done[/bold green] in {elapsed:.2f}s — "
            f"{total_files} files, "
            f"{total_structs} structs, "
            f"{total_functions} functions"
        )
        if error_count > 0:
            _err_console.print(f"  ⚠ {error_count} file(s) had parse errors")

    # Exit code: 1 if partial failures, 0 if clean
    if error_count > 0:
        raise typer.Exit(code=1)

    raise typer.Exit(code=0)


# endregion: --- Typer Application


# ---------------------------------------------------------------------------
# region:    --- Query Subcommands
# ---------------------------------------------------------------------------


class QueryOutputFormat(StrEnum):
    """Output format for the ``query`` subcommand."""

    TABLE = "table"
    JSON = "json"


def _load_graph_for_query(
    repo_path: Path,
    output: Path,
    *,
    no_cache: bool,
) -> CodeGraph:
    """Validate paths and load (or build) a :class:`CodeGraph`.

    Shared pre-flight for ``query``, ``path``, and ``explain``.
    """
    from ast_intel.core._graph_loader import load_or_build_graph

    try:
        validated_repo = validate_repo_path(repo_path)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    try:
        output_dir = sanitize_output_path(output, validated_repo)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    return load_or_build_graph(
        validated_repo,
        output_dir=output_dir,
        no_cache=no_cache,
    )


@app.command(name="egress")
def egress_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (used for cached graph.json).",
        ),
    ] = Path(),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached graph.json, rebuild from scratch.",
        ),
    ] = False,
    include_internal: Annotated[
        bool,
        typer.Option(
            "--include-internal",
            help="Include internal/first-party HTTP calls, not just third-party.",
        ),
    ] = False,
    no_heuristic: Annotated[
        bool,
        typer.Option(
            "--no-heuristic",
            help="Exclude low-confidence heuristic 'unknown vendor' SDK detections.",
        ),
    ] = False,
    fmt: Annotated[
        QueryOutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: json or table.",
        ),
    ] = QueryOutputFormat.JSON,
) -> None:
    """Report outbound third-party API calls and the data sent to them."""
    from ast_intel.core.egress_report import build_egress_report

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    report = build_egress_report(
        graph,
        third_party_only=not include_internal,
        include_heuristic=not no_heuristic,
    )

    if fmt == QueryOutputFormat.JSON:
        _console.print(json.dumps(report, indent=2, ensure_ascii=False))
        raise typer.Exit(code=0)

    entries = report["egress"]
    if not entries:
        _console.print("[yellow]No third-party egress calls found.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title="Third-party data egress", show_lines=False)
    table.add_column("Vendor", style="bold cyan")
    table.add_column("Cat")
    table.add_column("Method", style="green")
    table.add_column("Caller")
    table.add_column("Location", style="dim")
    table.add_column("Fields")
    table.add_column("Secrets")
    for e in entries:
        loc = f"{e['file']}:{e['line']}" if e["line"] else e["file"]
        fields = ", ".join(f["name"] or "<pos>" for f in e["payload_fields"])
        table.add_row(
            e.get("vendor") or "-",
            e.get("category") or "-",
            e.get("method") or "-",
            e.get("caller") or "-",
            loc,
            fields or "-",
            "yes" if e["has_secrets"] else "",
        )
    _console.print(table)


@app.command(name="watch")
def watch_cmd(  # noqa: PLR0913
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root to watch.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            "-i",
            help="Include specific paths (repeatable). Relative to repo root.",
        ),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            "-e",
            help="Exclude specific paths (repeatable). Supports glob patterns.",
        ),
    ] = None,
    lang: Annotated[
        list[SupportedLanguage] | None,
        typer.Option(
            "--lang",
            "-l",
            help="Languages to parse. Default: auto-detect.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory. Default: <repo>/ast_output/.",
        ),
    ] = Path(),
    debounce: Annotated[
        int,
        typer.Option(
            "--debounce",
            help="Debounce delay in milliseconds.",
            min=100,
            max=10000,
        ),
    ] = 500,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            "-w",
            help="Parallel workers for extraction. 0 = CPU count.",
            min=0,
        ),
    ] = 0,
    no_methods: Annotated[
        bool,
        typer.Option("--no-methods", help="Skip method call extraction."),
    ] = False,
    similarity: Annotated[
        bool,
        typer.Option("--similarity", help="Emit SIMILAR_TO edges."),
    ] = False,
    similarity_threshold: Annotated[
        float,
        typer.Option(
            "--similarity-threshold",
            min=0.0,
            max=1.0,
        ),
    ] = 0.4,
    no_iac: Annotated[
        bool,
        typer.Option("--no-iac", help="Skip IaC scanning."),
    ] = False,
    no_watch: Annotated[
        bool,
        typer.Option(
            "--no-watch",
            help="Disable filesystem watching. Performs a single graph "
            "rebuild and exits.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress output."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable verbose debug logging."),
    ] = False,
) -> None:
    """Watch a repository for changes and auto-rebuild the code graph."""
    from ast_intel.core._watcher import FileWatcher, WatchConfig
    from ast_intel.core.security import (
        PathSecurityError,
        sanitize_output_path,
        validate_repo_path,
    )

    _configure_logging(debug=debug)

    try:
        validated_repo = validate_repo_path(repo_path)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    try:
        output_dir = sanitize_output_path(output, validated_repo)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    languages = [lg.value for lg in lang] if lang else None

    config = WatchConfig(
        repo_root=validated_repo,
        output_dir=output_dir,
        debounce_ms=debounce,
        languages=languages,
        include=include,
        exclude=exclude,
        workers=workers,
        no_methods=no_methods,
        similarity=similarity,
        similarity_threshold=similarity_threshold,
        no_iac=no_iac,
        enabled=not no_watch,
    )

    if not quiet:
        _console.print(
            f"[bold blue]AST Intel v{TOOL_VERSION}[/bold blue] "
            f"watching [green]{validated_repo}[/green]"
        )
        _console.print(f"  Debounce: {debounce}ms | Output: {output_dir}")
        _console.print("  Press Ctrl+C to stop.")

    watcher = FileWatcher(config)
    watcher.run(quiet=quiet)


@app.command(name="query")
def query_cmd(  # noqa: PLR0913
    pattern: Annotated[
        str,
        typer.Argument(help="Search pattern (substring, or regex with -r)."),
    ],
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    kind: Annotated[
        NodeKind | None,
        typer.Option(
            "--kind",
            "-k",
            help="Filter results by node kind (e.g. struct, function).",
        ),
    ] = None,
    regex: Annotated[
        bool,
        typer.Option(
            "--regex",
            "-r",
            help="Treat PATTERN as a Python regex.",
        ),
    ] = False,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (used for cached graph.json).",
        ),
    ] = Path(),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached graph.json, rebuild from scratch.",
        ),
    ] = False,
    fmt: Annotated[
        QueryOutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: table (Rich) or json.",
        ),
    ] = QueryOutputFormat.TABLE,
) -> None:
    """Search the code graph for symbols matching PATTERN."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)
    hits = engine.search(pattern, kind=kind, regex=regex)

    if not hits:
        _console.print("[yellow]No matching symbols found.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "id": h.id,
                "label": h.label,
                "kind": h.kind.value,
                "file": h.file,
                "span": (
                    f"L{h.span.start_line}-L{h.span.end_line}"
                    if h.span
                    else ""
                ),
            }
            for h in hits
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(title=f"Search: {pattern!r}", show_lines=False)
        table.add_column("Label", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        table.add_column("Span")
        for h in hits:
            span_text = (
                f"L{h.span.start_line}-L{h.span.end_line}"
                if h.span
                else ""
            )
            table.add_row(h.label, h.kind.value, h.file, span_text)
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="path")
def path_cmd(
    source: Annotated[
        str,
        typer.Argument(help="Source symbol name or node ID."),
    ],
    target: Annotated[
        str,
        typer.Argument(help="Target symbol name or node ID."),
    ],
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (used for cached graph.json).",
        ),
    ] = Path(),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached graph.json, rebuild from scratch.",
        ),
    ] = False,
) -> None:
    """Find the shortest path between two symbols in the code graph."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)
    result = engine.shortest_path(source, target)

    if result is None:
        _console.print(
            f"[yellow]No path found between "
            f"{source!r} and {target!r}.[/yellow]",
        )
        raise typer.Exit(code=0)

    # Format: A →(calls)→ B →(imports)→ C
    parts: list[str] = []
    for node, relation in result:
        parts.append(f"[bold cyan]{node.label}[/bold cyan]")
        if relation is not None:
            parts.append(f" [dim]→({relation.value})→[/dim] ")

    _console.print("".join(parts))
    raise typer.Exit(code=0)


@app.command(name="explain")
def explain_cmd(
    symbol: Annotated[
        str,
        typer.Argument(help="Symbol name or node ID to explain."),
    ],
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (used for cached graph.json).",
        ),
    ] = Path(),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached graph.json, rebuild from scratch.",
        ),
    ] = False,
) -> None:
    """Explain a symbol's structural role in the code graph."""
    from ast_intel.core._query_engine import QueryEngine
    from ast_intel.core.analyzer import GraphAnalyzer

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)

    # Run analysis for community info (fast enough for interactive use).
    try:
        analyzer = GraphAnalyzer()
        analysis = analyzer.analyze(graph)
    except Exception:  # noqa: BLE001
        analysis = None

    engine = QueryEngine(graph, analysis=analysis)

    try:
        explanation = engine.explain(symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    # Build a Rich panel with the explanation.
    node = explanation.node
    span_text = (
        f"L{node.span.start_line}-L{node.span.end_line}"
        if node.span
        else "—"
    )

    lines = [
        f"[bold]{node.label}[/bold]  ({node.kind.value})",
        f"File: [green]{node.file}[/green]  Span: {span_text}",
        "",
        f"Degree: {explanation.degree} "
        f"(in: {explanation.in_degree}, out: {explanation.out_degree})",
        f"Role: [bold]{explanation.role}[/bold]",
    ]
    if explanation.community is not None:
        lines.append(f"Community: {explanation.community}")
    if explanation.callers:
        lines.append(f"Called by: {', '.join(explanation.callers[:5])}")
    if explanation.callees:
        lines.append(f"Calls: {', '.join(explanation.callees[:5])}")
    lines.append("")
    lines.append(explanation.summary)

    _console.print(Panel("\n".join(lines), title="Symbol Explanation"))
    raise typer.Exit(code=0)


@app.command(name="impact")
def impact_cmd(  # noqa: PLR0913
    symbol: Annotated[
        str,
        typer.Argument(help="Symbol name or node ID to analyse."),
    ],
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    depth: Annotated[
        int,
        typer.Option(
            "--depth",
            "-d",
            help="Max traversal depth (0 = unlimited).",
        ),
    ] = 0,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output directory (used for cached graph.json).",
        ),
    ] = Path(),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Ignore cached graph.json, rebuild from scratch.",
        ),
    ] = False,
    fmt: Annotated[
        QueryOutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format.",
        ),
    ] = QueryOutputFormat.TABLE,
) -> None:
    """Show the blast radius (reverse dependents) of a symbol."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        result = engine.impact(symbol, depth=depth)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    if not result.affected:
        _console.print(
            f"[yellow]No dependents found for {symbol!r}.[/yellow]",
        )
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "label": n.label,
                "kind": n.kind.value,
                "file": n.file,
                "distance": result.distances[n.id],
            }
            for n in result.affected
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(
            title=f"Blast radius: {result.root.label!r}",
            show_lines=False,
        )
        table.add_column("Dist", justify="right", style="bold")
        table.add_column("Label", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        for n in result.affected:
            table.add_row(
                str(result.distances[n.id]),
                n.label,
                n.kind.value,
                n.file,
            )
        _console.print(table)

    raise typer.Exit(code=0)


# --- Shared argument/option factories for the new subcommands -----------

_SYMBOL_ARG = Annotated[
    str,
    typer.Argument(help="Symbol name or node ID."),
]
_REPO_ARG = Annotated[
    Path,
    typer.Argument(
        help="Path to the repository root.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
]
_OUTPUT_OPT = Annotated[
    Path,
    typer.Option(
        "--output", "-o",
        help="Output directory (used for cached graph.json). Default: <repo>/ast_output/.",
    ),
]
_NO_CACHE_OPT = Annotated[
    bool,
    typer.Option(
        "--no-cache",
        help="Ignore cached graph.json, rebuild from scratch.",
    ),
]
_FMT_OPT = Annotated[
    QueryOutputFormat,
    typer.Option("--format", "-f", help="Output format."),
]


@app.command(name="deps")
def deps_cmd(
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Show forward dependencies of a symbol (what it depends on)."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        result = engine.get_dependencies(symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    _print_dependency_result(result, f"Dependencies of {result.node.label!r}", fmt)
    raise typer.Exit(code=0)


@app.command(name="dependents")
def dependents_cmd(
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Show reverse dependents of a symbol (what depends on it)."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        result = engine.get_dependents(symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    _print_dependency_result(result, f"Dependents of {result.node.label!r}", fmt)
    raise typer.Exit(code=0)


@app.command(name="context")
def context_cmd(
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Unified single-shot context for a symbol (explanation + deps + usages)."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        ctx = engine.get_context(symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    if fmt == QueryOutputFormat.JSON:
        blob = {
            "node": _node_to_dict(ctx.node),
            "role": ctx.explanation.role,
            "degree": ctx.explanation.degree,
            "in_degree": ctx.explanation.in_degree,
            "out_degree": ctx.explanation.out_degree,
            "community": ctx.explanation.community,
            "summary": ctx.explanation.summary,
            "dependencies": _dep_result_to_dict(ctx.dependencies),
            "dependents": _dep_result_to_dict(ctx.dependents),
            "siblings": [_node_to_dict(n) for n in ctx.siblings[:10]],
            "community_peers": [_node_to_dict(n) for n in ctx.community_peers],
            "similar": [_node_to_dict(n) for n in ctx.similar],
        }
        _console.print(json.dumps(blob, indent=2))
    else:
        exp = ctx.explanation
        node = ctx.node
        span_text = (
            f"L{node.span.start_line}-L{node.span.end_line}"
            if node.span else "—"
        )
        lines = [
            f"[bold]{node.label}[/bold]  ({node.kind.value})",
            f"File: [green]{node.file}[/green]  Span: {span_text}",
            f"Role: [bold]{exp.role}[/bold]  "
            f"Degree: {exp.degree} (in: {exp.in_degree}, out: {exp.out_degree})",
        ]
        if exp.community is not None:
            lines.append(f"Community: {exp.community}")
        if node.properties:
            props = ", ".join(
                f"{k}={v}" for k, v in sorted(node.properties.items())
            )
            lines.append(f"Properties: {props}")
        lines.append("")
        lines.append(exp.summary)

        # Dependencies summary.
        dep_labels = _dep_labels(ctx.dependencies)
        if dep_labels:
            lines.append(f"\nDependencies: {', '.join(dep_labels[:8])}")
        dpt_labels = _dep_labels(ctx.dependents)
        if dpt_labels:
            lines.append(f"Dependents: {', '.join(dpt_labels[:8])}")
        if ctx.siblings:
            sib = ", ".join(s.label for s in ctx.siblings[:5])
            lines.append(f"Siblings: {sib}")
        if ctx.similar:
            sim = ", ".join(n.label for n in ctx.similar[:5])
            lines.append(f"Similar: {sim}")

        _console.print(Panel("\n".join(lines), title="Symbol Context"))

    raise typer.Exit(code=0)


@app.command(name="usages")
def usages_cmd(
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Find all incoming references to a symbol."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        entries = engine.find_usages(symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    if not entries:
        _console.print(f"[yellow]No usages found for {symbol!r}.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "label": e.node.label,
                "kind": e.node.kind.value,
                "relation": e.relation.value,
                "file": e.file,
            }
            for e in entries
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(title=f"Usages of {symbol!r}", show_lines=False)
        table.add_column("Referencing Symbol", style="bold cyan")
        table.add_column("Kind")
        table.add_column("Relation")
        table.add_column("File", style="green")
        for e in entries:
            table.add_row(e.node.label, e.node.kind.value, e.relation.value, e.file)
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="files")
def files_cmd(
    repo_path: _REPO_ARG,
    pattern: Annotated[
        str,
        typer.Option(
            "--pattern", "-p",
            help="Glob pattern to filter file paths (e.g. '*redis*').",
        ),
    ] = "",
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """List file nodes with symbol counts and key symbols."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)
    infos = engine.list_files(pattern)

    if not infos:
        _console.print("[yellow]No files found.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "file": f.node.file,
                "symbols": f.symbol_count,
                "structs": f.structs,
                "functions": f.functions,
                "methods": f.methods,
                "traits": f.traits,
                "key_symbols": list(f.key_symbols),
            }
            for f in infos
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(title="Files", show_lines=False)
        table.add_column("File", style="green")
        table.add_column("Symbols", justify="right")
        table.add_column("S/F/M/T", justify="right")
        table.add_column("Key Symbols")
        for f in infos:
            table.add_row(
                f.node.file,
                str(f.symbol_count),
                f"{f.structs}/{f.functions}/{f.methods}/{f.traits}",
                ", ".join(f.key_symbols[:3]),
            )
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="routes")
def routes_cmd(
    repo_path: _REPO_ARG,
    service: Annotated[
        str | None,
        typer.Option(
            "--service", "-s",
            help=(
                "Filter to routes owned by this service "
                "(set by 'ast-intel merge' on multi-repo graphs)."
            ),
        ),
    ] = None,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """List HTTP routes/endpoints, optionally filtered by service."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)
    routes = engine.list_routes(service=service)

    if not routes:
        _console.print("[yellow]No routes found.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "method": r.method,
                "path": r.path,
                "handler": r.handler,
                "framework": r.framework,
                "file": r.file,
                "service": r.service,
                "span": (
                    f"L{r.node.span.start_line}-L{r.node.span.end_line}"
                    if r.node.span
                    else ""
                ),
            }
            for r in routes
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        has_service = any(r.service for r in routes)
        title = f"Routes ({len(routes)})"
        if service:
            title += f" — service={service}"
        table = Table(title=title, show_lines=False)
        table.add_column("Method", style="bold magenta")
        table.add_column("Path", style="bold cyan")
        table.add_column("Handler", style="green")
        table.add_column("Framework")
        if has_service:
            table.add_column("Service")
        table.add_column("File", style="dim")
        for r in routes:
            cells = [r.method, r.path, r.handler, r.framework]
            if has_service:
                cells.append(r.service)
            cells.append(r.file)
            table.add_row(*cells)
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="resources")
def resources_cmd(
    repo_path: _REPO_ARG,
    service: Annotated[
        str | None,
        typer.Option(
            "--service", "-s",
            help="Filter to resources used by this service/module.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category", "-c",
            help="Filter by category: database, cache, queue, topic, stream, storage, secret.",
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider", "-p",
            help="Filter by cloud provider: azure, aws, gcp, generic.",
        ),
    ] = None,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """List cloud infrastructure resources (databases, queues, caches, etc.) used by the repo."""
    from collections import defaultdict

    from ast_intel.models.graph_model import NodeKind

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    cr_nodes = [n for n in graph.nodes if n.kind == NodeKind.CLOUD_RESOURCE]

    # Apply filters
    if service:
        svc_lower = service.lower()
        cr_nodes = [
            n for n in cr_nodes
            if svc_lower in n.file.lower()
            or svc_lower in n.properties.get("caller", "").lower()
        ]
    if category:
        cr_nodes = [n for n in cr_nodes if n.properties.get("category") == category]
    if provider:
        cr_nodes = [n for n in cr_nodes if n.properties.get("provider") == provider]

    if not cr_nodes:
        _console.print("[yellow]No cloud resources found.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {
                "provider": n.properties.get("provider", ""),
                "service": n.properties.get("service", ""),
                "category": n.properties.get("category", ""),
                "client": n.properties.get("client", ""),
                "caller": n.properties.get("caller", ""),
                "name": n.properties.get("name", ""),
                "source": n.properties.get("source", ""),
                "file": n.file,
            }
            for n in cr_nodes
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        by_cat: dict[str, list] = defaultdict(list)
        for n in cr_nodes:
            by_cat[n.properties.get("category", "other")].append(n)

        title = f"Cloud Resources ({len(cr_nodes)})"
        if service:
            title += f" — service={service}"
        _console.print(f"\n[bold]{title}[/bold]\n")
        for cat in sorted(by_cat):
            nodes = by_cat[cat]
            _console.print(f"  [bold cyan]{cat}[/bold cyan] ({len(nodes)})")
            for n in nodes:
                p = n.properties
                src_tag = "[dim](iac)[/dim]" if p.get("source") == "iac" else ""
                client = p.get("client", "")
                caller = p.get("caller", "")
                svc = p.get("service", "")
                via = f"via {client}" if client else ""
                attr = f"caller: {caller}" if caller else ""
                parts = [f"[green]{svc}[/green]", via, attr, src_tag]
                _console.print(f"    {'  '.join(p for p in parts if p)}")
            _console.print()

        _console.print(
            f"  [dim]Total: {len(cr_nodes)} resources "
            f"({len(by_cat)} categories)[/dim]",
        )

    raise typer.Exit(code=0)


@app.command(name="implementors")
def implementors_cmd(
    trait_symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Find all implementations of a trait / interface."""
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        impls = engine.get_implementors(trait_symbol)
    except KeyError:
        _err_console.print(f"Symbol not found: {trait_symbol!r}")
        raise typer.Exit(code=1) from None

    if not impls:
        _console.print(
            f"[yellow]No implementors found for {trait_symbol!r}.[/yellow]",
        )
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {"label": n.label, "kind": n.kind.value, "file": f}
            for n, f in impls
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(
            title=f"Implementors of {trait_symbol!r}", show_lines=False,
        )
        table.add_column("Implementor", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        for n, f in impls:
            table.add_row(n.label, n.kind.value, f)
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="similar")
def similar_cmd(  # noqa: PLR0913
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max results."),
    ] = 5,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Find structurally similar symbols (ranked candidates).

    Uses precomputed SIMILAR_TO edges when present, otherwise computes
    Jaccard similarity over structural features on demand.
    """
    from ast_intel.core._query_engine import QueryEngine

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)
    engine = QueryEngine(graph)

    try:
        hits = engine.find_similar(symbol, limit=limit)
        if not hits:
            hits = engine.rank_similar(symbol, limit=limit)
    except KeyError:
        _err_console.print(f"Symbol not found: {symbol!r}")
        raise typer.Exit(code=1) from None

    if not hits:
        _console.print(f"[yellow]No similar symbols for {symbol!r}.[/yellow]")
        raise typer.Exit(code=0)

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {"label": n.label, "kind": n.kind.value, "file": n.file, "score": s}
            for n, s in hits
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(
            title=f"Similar to {symbol!r}", show_lines=False,
        )
        table.add_column("Score", justify="right", style="bold")
        table.add_column("Label", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        for n, s in hits:
            table.add_row(f"{s:.2f}", n.label, n.kind.value, n.file)
        _console.print(table)

    raise typer.Exit(code=0)


@app.command(name="community")
def community_cmd(
    symbol: _SYMBOL_ARG,
    repo_path: _REPO_ARG,
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    fmt: _FMT_OPT = QueryOutputFormat.TABLE,
) -> None:
    """Show all symbols in the same community cluster."""
    from ast_intel.core._query_engine import QueryEngine
    from ast_intel.core.analyzer import GraphAnalyzer

    graph = _load_graph_for_query(repo_path, output, no_cache=no_cache)

    try:
        analyzer = GraphAnalyzer()
        analysis = analyzer.analyze(graph)
    except Exception:  # noqa: BLE001
        _err_console.print("Analysis failed. Cannot detect communities.")
        raise typer.Exit(code=1) from None

    engine = QueryEngine(graph, analysis=analysis)

    try:
        result = engine.get_community(symbol)
    except KeyError as exc:
        _err_console.print(str(exc))
        raise typer.Exit(code=1) from None

    if fmt == QueryOutputFormat.JSON:
        blob = {
            "community_id": result.community_id,
            "member_count": result.member_count,
            "hub_nodes": [_node_to_dict(n) for n in result.hub_nodes],
            "members": [_node_to_dict(n) for n in result.members],
        }
        _console.print(json.dumps(blob, indent=2))
    else:
        table = Table(
            title=f"Community {result.community_id} ({result.member_count} members)",
            show_lines=False,
        )
        table.add_column("Label", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        for n in result.members:
            style = "bold" if n in result.hub_nodes else ""
            table.add_row(n.label, n.kind.value, n.file, style=style)
        _console.print(table)

    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# region:    --- Serve Subcommand (MCP Server)
# ---------------------------------------------------------------------------


@app.command(name="serve")
def serve_cmd(  # noqa: PLR0913
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    output: _OUTPUT_OPT = Path(),
    no_cache: _NO_CACHE_OPT = False,
    analyze: Annotated[
        bool,
        typer.Option(
            "--analyze",
            help="Run community detection (enables get_community tool).",
        ),
    ] = False,
    similarity: Annotated[
        bool,
        typer.Option(
            "--similarity/--no-similarity",
            help=(
                "Auto-compute SIMILAR_TO edges at startup if the "
                "graph has none. Enables the find_similar tool."
            ),
        ),
    ] = True,
    similarity_threshold: Annotated[
        float,
        typer.Option(
            "--similarity-threshold",
            help=(
                "Minimum Jaccard similarity (0.0\u20131.0) for "
                "SIMILAR_TO edges. Default: 0.4."
            ),
            min=0.0,
            max=1.0,
        ),
    ] = 0.4,
    no_watch: Annotated[
        bool,
        typer.Option(
            "--no-watch",
            help="Disable background file watcher (no auto-rebuild).",
        ),
    ] = False,
) -> None:
    """Start an MCP server exposing the code graph as tools.

    The server uses stdio transport by default, suitable for editor
    integration (VS Code, Cursor, etc.).  Configure in your editor::

        {
          "servers": {
            "ast-intel": {
              "command": "ast-intel",
              "args": ["serve", "/path/to/repo"]
            }
          }
        }
    """
    import asyncio

    from ast_intel.core.security import PathSecurityError, sanitize_output_path, validate_repo_path

    try:
        validated_repo = validate_repo_path(repo_path)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    try:
        output_dir = sanitize_output_path(output, validated_repo)
    except PathSecurityError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None

    try:
        from ast_intel.mcp_server import run_stdio
    except ImportError:
        _err_console.print(
            "MCP support requires the [bold]mcp[/bold] package.\n"
            "Install with: [cyan]pip install ast-intel\\[mcp][/cyan]"
        )
        raise typer.Exit(code=1) from None

    asyncio.run(
        run_stdio(
            validated_repo,
            output_dir=output_dir,
            no_cache=no_cache,
            analyze=analyze,
            similarity=similarity,
            similarity_threshold=similarity_threshold,
            watch=not no_watch,
        ),
    )


# endregion: --- Serve Subcommand (MCP Server)


# --- Helper formatters for new subcommands --------------------------------


def _node_to_dict(n: GraphNode) -> dict[str, object]:
    """Serialize a GraphNode to a plain dict."""
    d: dict[str, object] = {
        "label": n.label,
        "kind": n.kind.value,
        "file": n.file,
    }
    if n.span:
        d["span"] = f"L{n.span.start_line}-L{n.span.end_line}"
    if n.properties:
        d["properties"] = n.properties
    return d


def _dep_result_to_dict(dep: DependencyResult) -> dict[str, list[dict[str, object]]]:
    """Serialize a DependencyResult to a plain dict."""
    out: dict[str, list[dict[str, object]]] = {}
    for field_name in (
        "calls", "imports", "uses_methods", "inherits",
        "implements", "contains", "other",
    ):
        nodes: tuple[GraphNode, ...] = getattr(dep, field_name)
        if nodes:
            out[field_name] = [_node_to_dict(n) for n in nodes]
    return out


def _dep_labels(dep: DependencyResult) -> list[str]:
    """Flatten dependency result to a label list."""
    labels: list[str] = []
    for field_name in (
        "calls", "imports", "uses_methods", "inherits",
        "implements", "contains", "other",
    ):
        nodes: tuple[GraphNode, ...] = getattr(dep, field_name)
        labels.extend(n.label for n in nodes)
    return labels


def _print_dependency_result(
    result: DependencyResult,
    title: str,
    fmt: QueryOutputFormat,
) -> None:
    """Print a DependencyResult in table or JSON format."""
    all_nodes: list[tuple[str, GraphNode]] = []
    for field_name in (
        "calls", "imports", "uses_methods", "inherits",
        "implements", "contains", "other",
    ):
        nodes: tuple[GraphNode, ...] = getattr(result, field_name)
        all_nodes.extend((field_name, n) for n in nodes)

    if not all_nodes:
        _console.print(f"[yellow]No results for {result.node.label!r}.[/yellow]")
        return

    if fmt == QueryOutputFormat.JSON:
        rows = [
            {"label": n.label, "kind": n.kind.value, "relation": rel, "file": n.file}
            for rel, n in all_nodes
        ]
        _console.print(json.dumps(rows, indent=2))
    else:
        table = Table(title=title, show_lines=False)
        table.add_column("Relation", style="bold")
        table.add_column("Label", style="bold cyan")
        table.add_column("Kind")
        table.add_column("File", style="green")
        for rel, n in all_nodes:
            table.add_row(rel, n.label, n.kind.value, n.file)
        _console.print(table)


# endregion: --- Query Subcommands


# ---------------------------------------------------------------------------
# region:    --- Merge Subcommand
# ---------------------------------------------------------------------------


@app.command(name="merge")
def merge_cmd(  # noqa: PLR0913
    graphs: Annotated[
        list[Path],
        typer.Argument(
            help="Paths to graph.json files to merge.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ],
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Output path for the merged graph JSON file.",
            resolve_path=True,
        ),
    ] = Path("merged-graph.json"),
    names: Annotated[
        list[str] | None,
        typer.Option(
            "--name",
            "-n",
            help=(
                "Service names for each input graph (repeatable). "
                "Order must match the graph arguments. "
                "If omitted, names are inferred from graph metadata."
            ),
        ),
    ] = None,
    html: Annotated[
        bool,
        typer.Option(
            "--html",
            help="Also generate an interactive HTML visualization.",
        ),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Inline vis.js in HTML output (no CDN).",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable verbose debug logging."),
    ] = False,
    no_resolve: Annotated[
        bool,
        typer.Option(
            "--no-resolve",
            help="Skip cross-service edge resolution (HTTP_CALL → ROUTE matching).",
        ),
    ] = False,
) -> None:
    """Merge multiple graph.json files into a single unified graph.

    Node IDs are prefixed with the service name to avoid collisions.
    A SERVICE node is created for each input, and BELONGS_TO edges
    link every FILE node to its service.  Cross-service CALLS_SERVICE
    edges are resolved automatically unless --no-resolve is given.

    Examples::

        ast-intel merge svc-a/graph.json svc-b/graph.json -o merged.json
        ast-intel merge --name api api/graph.json --name web web/graph.json
        ast-intel merge a.json b.json --html
    """
    _configure_logging(debug=debug)

    from ast_intel.core._merge import merge_graphs
    from ast_intel.formatters.graph_json_formatter import GraphJsonFormatter

    if len(graphs) < 2:  # noqa: PLR2004
        _console.print(
            "[red]At least 2 graph files are required for merge.[/red]",
        )
        raise typer.Exit(code=1)

    formatter = GraphJsonFormatter()

    # Load all input graphs
    loaded: list[tuple[str, CodeGraph]] = []
    for i, path in enumerate(graphs):
        _console.print(f"  Loading [cyan]{path.name}[/cyan]…")
        graph = formatter.read(path)

        if names and i < len(names):
            name = names[i]
        else:
            from ast_intel.core._merge import _infer_service_name

            name = _infer_service_name(graph)

        loaded.append((name, graph))

    # Merge
    _console.print(
        f"\nMerging [bold]{len(loaded)}[/bold] graphs: "
        + ", ".join(f"[cyan]{n}[/cyan]" for n, _ in loaded),
    )
    merged = merge_graphs(loaded, resolve=not no_resolve)

    # Write merged JSON
    out.parent.mkdir(parents=True, exist_ok=True)
    formatter.write(merged, out)
    _console.print(
        f"\n[green]✓[/green] Merged graph written to [bold]{out}[/bold]"
        f"  ({len(merged.nodes)} nodes, {len(merged.edges)} edges)",
    )

    # Optionally write HTML
    if html:
        from ast_intel.formatters.graph_html_formatter import (
            GraphHtmlFormatter,
        )

        html_path = out.with_suffix(".html")
        GraphHtmlFormatter().write(merged, html_path, offline=offline)
        _console.print(
            f"[green]✓[/green] HTML visualization written to "
            f"[bold]{html_path}[/bold]",
        )

    raise typer.Exit(code=0)


# endregion: --- Merge Subcommand


# ---------------------------------------------------------------------------
# region:    --- Install / Uninstall Subcommands
# ---------------------------------------------------------------------------


@app.command(name="install")
def install_cmd(
    platform: Annotated[
        str | None,
        typer.Option(
            "--platform",
            "-p",
            help=(
                "Target platform: vscode, cursor, windsurf, "
                "claude-desktop, claude-code, codex. "
                "If omitted, auto-detect from project."
            ),
        ),
    ] = None,
    project_root: Annotated[
        Path,
        typer.Option(
            "--root",
            help="Project root directory. Default: current directory.",
            resolve_path=True,
        ),
    ] = Path(),
) -> None:
    """Configure an AI assistant to use ast-intel's MCP server.

    Auto-detects the platform from the project directory, or specify
    explicitly with --platform.

    Examples::

        ast-intel install
        ast-intel install --platform vscode
        ast-intel install --platform cursor --root /path/to/project
    """
    from ast_intel.core._installer import (
        InstallResult,
        Platform,
        detect_platforms,
        install,
    )

    abs_root = project_root.resolve()

    if platform:
        try:
            platforms = [Platform(platform)]
        except ValueError:
            valid = ", ".join(p.value for p in Platform)
            _err_console.print(
                f"Unknown platform: [bold]{platform}[/bold]\n"
                f"Valid platforms: {valid}",
            )
            raise typer.Exit(code=1) from None
    else:
        platforms = detect_platforms(abs_root)
        if not platforms:
            valid = ", ".join(p.value for p in Platform)
            _console.print(
                "[yellow]No AI assistant platform detected.[/yellow]\n"
                f"Use [bold]--platform[/bold] to specify one: {valid}",
            )
            raise typer.Exit(code=1)

    results: list[InstallResult] = []
    for plat in platforms:
        try:
            result = install(abs_root, plat)
            results.append(result)
        except OSError as exc:
            _err_console.print(
                f"Failed to install for {plat.value}: {exc}",
            )
            continue

    if not results:
        raise typer.Exit(code=1)

    for r in results:
        action = "Created" if r.created else "Updated"
        _console.print(
            f"  [green]✓[/green] {r.platform.value}: {action} "
            f"[cyan]{r.config_path}[/cyan]",
        )

    _console.print(
        f"\n[green]✓[/green] ast-intel configured for "
        f"{len(results)} platform(s)",
    )
    raise typer.Exit(code=0)


@app.command(name="uninstall")
def uninstall_cmd(
    platform: Annotated[
        str | None,
        typer.Option(
            "--platform",
            "-p",
            help=(
                "Target platform to remove config from. "
                "If omitted, auto-detect from project."
            ),
        ),
    ] = None,
    project_root: Annotated[
        Path,
        typer.Option(
            "--root",
            help="Project root directory. Default: current directory.",
            resolve_path=True,
        ),
    ] = Path(),
) -> None:
    """Remove ast-intel configuration from an AI assistant.

    Examples::

        ast-intel uninstall
        ast-intel uninstall --platform vscode
    """
    from ast_intel.core._installer import (
        Platform,
        detect_platforms,
        uninstall,
    )

    abs_root = project_root.resolve()

    if platform:
        try:
            platforms = [Platform(platform)]
        except ValueError:
            valid = ", ".join(p.value for p in Platform)
            _err_console.print(
                f"Unknown platform: [bold]{platform}[/bold]\n"
                f"Valid platforms: {valid}",
            )
            raise typer.Exit(code=1) from None
    else:
        platforms = detect_platforms(abs_root)
        if not platforms:
            _console.print("[dim]Nothing to uninstall.[/dim]")
            raise typer.Exit(code=0)

    removed = 0
    for plat in platforms:
        try:
            result = uninstall(abs_root, plat)
            if result:
                _console.print(
                    f"  [green]✓[/green] {plat.value}: Removed from "
                    f"[cyan]{result}[/cyan]",
                )
                removed += 1
        except OSError as exc:
            _err_console.print(
                f"Failed to uninstall for {plat.value}: {exc}",
            )

    if removed:
        _console.print(
            f"\n[green]✓[/green] Removed ast-intel from "
            f"{removed} platform(s)",
        )
    else:
        _console.print("[dim]No ast-intel configuration found.[/dim]")

    raise typer.Exit(code=0)


# endregion: --- Install / Uninstall Subcommands


# ---------------------------------------------------------------------------
# region:    --- Hook Subcommand
# ---------------------------------------------------------------------------


@app.command(name="hook")
def hook_cmd(
    action: Annotated[
        str,
        typer.Argument(
            help="Action to perform: install, uninstall, or status.",
        ),
    ],
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            "-r",
            help="Repository root. Default: current directory.",
            resolve_path=True,
        ),
    ] = Path(),
    merge_driver: Annotated[
        bool,
        typer.Option(
            "--merge-driver",
            help="Also install/uninstall the graph.json merge driver.",
        ),
    ] = False,
) -> None:
    """Manage git hooks for automatic graph rebuild.

    Examples::

        ast-intel hook install
        ast-intel hook install --merge-driver
        ast-intel hook uninstall
        ast-intel hook status
    """
    valid_actions = {"install", "uninstall", "status"}
    if action not in valid_actions:
        _err_console.print(
            f"Unknown action: [bold]{action}[/bold]\n"
            f"Valid actions: {', '.join(sorted(valid_actions))}",
        )
        raise typer.Exit(code=1)

    try:
        _hook_dispatch(action, repo, merge_driver=merge_driver)
    except FileNotFoundError as exc:
        _err_console.print(f"[red]Error:[/red] {exc}")
        _err_console.print(
            "Initialize a git repository with: [cyan]git init[/cyan]",
        )
        raise typer.Exit(code=1) from None
    except OSError as exc:
        _err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None


def _hook_dispatch(  # noqa: C901, PLR0912
    action: str, repo: Path, *, merge_driver: bool,
) -> None:
    """Dispatch hook action to the appropriate handler."""
    from ast_intel.core._hooks import (
        hook_status,
        install_hooks,
        install_merge_driver,
        uninstall_hooks,
        uninstall_merge_driver,
    )

    if action == "install":
        installed = install_hooks(repo)
        for name in installed:
            _console.print(f"  [green]✓[/green] {name} hook installed")
        if merge_driver:
            if install_merge_driver(repo):
                _console.print(
                    "  [green]✓[/green] merge driver configured "
                    "(graph.json)",
                )
            else:
                _console.print(
                    "  [dim]merge driver already installed[/dim]",
                )
        _console.print(
            f"\n[green]✓[/green] {len(installed)} git hook(s) installed",
        )

    elif action == "uninstall":
        removed = uninstall_hooks(repo)
        for name in removed:
            _console.print(f"  [green]✓[/green] {name} hook removed")
        if merge_driver:
            if uninstall_merge_driver(repo):
                _console.print(
                    "  [green]✓[/green] merge driver removed",
                )
            else:
                _console.print(
                    "  [dim]merge driver was not installed[/dim]",
                )
        if removed:
            _console.print(
                f"\n[green]✓[/green] {len(removed)} git hook(s) removed",
            )
        else:
            _console.print("[dim]No ast-intel hooks found.[/dim]")

    else:  # status
        st = hook_status(repo)
        from rich.table import Table

        table = Table(title="AST_INTEL Git Hooks")
        table.add_column("Hook", style="bold")
        table.add_column("Installed")
        for name, is_installed in st.as_dict().items():
            icon = "[green]✓[/green]" if is_installed else "[red]✗[/red]"
            table.add_row(name, icon)
        _console.print(table)


# endregion: --- Hook Subcommand


# ---------------------------------------------------------------------------
# region:    --- Logging Configuration
# ---------------------------------------------------------------------------


def _configure_logging(*, debug: bool) -> None:
    """Configure root logging based on --debug flag.

    - ``--debug``: DEBUG level to stderr with verbose format
    - Default: WARNING level (errors only, no noise in normal runs)
    """
    level = logging.DEBUG if debug else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ),
    )
    root = logging.getLogger("ast_intel")
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(handler)


# endregion: --- Logging Configuration


# ---------------------------------------------------------------------------
# region:    --- History subcommands (Phase 3)
# ---------------------------------------------------------------------------


def _history_repo_arg() -> Path:
    """Sentinel for the per-command repo argument annotation."""
    return Path()


@app.command(name="history")
def history_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    file: Annotated[
        str,
        typer.Option("--file", "-f", help="Workspace-relative file path."),
    ],
    start: Annotated[
        int,
        typer.Option("--start", "-s", help="Start line (1-based, inclusive)."),
    ],
    end: Annotated[
        int,
        typer.Option("--end", "-e", help="End line (1-based, inclusive)."),
    ],
    max_commits: Annotated[
        int,
        typer.Option("--max-commits", help="Cap on returned commits."),
    ] = 50,
) -> None:
    """Print the git commit history of a file:line-range as JSON."""
    from ast_intel.history.history_builder import HistoryBuilder
    from ast_intel.history.models import SymbolKind, SymbolRef

    ref = SymbolRef(
        id=f"{file}::L{start}-L{end}",
        label=Path(file).name,
        kind=SymbolKind.OTHER,
        file=file,
        start_line=start,
        end_line=end,
    )
    builder = HistoryBuilder(repo_root=repo_path, max_commits=max_commits)
    record = builder.build(ref)
    payload = {
        "head_sha": record.head_sha,
        "symbol": {
            "file": record.symbol.file,
            "start_line": record.symbol.start_line,
            "end_line": record.symbol.end_line,
        },
        "total_lines": record.total_lines,
        "commits": [
            {
                "sha": c.sha,
                "author": {"name": c.author_name, "email": c.author_email},
                "authored_at": c.authored_at,
                "committed_at": c.committed_at,
                "subject": c.subject,
                "body": c.body,
                "lines_added": c.lines_added,
                "lines_removed": c.lines_removed,
                "files_changed": list(c.files_changed),
            }
            for c in record.commits
        ],
        "blame": [
            {
                "sha": h.sha,
                "author": {"name": h.author_name, "email": h.author_email},
                "authored_at": h.authored_at,
                "start_line": h.start_line,
                "end_line": h.end_line,
            }
            for h in record.blame
        ],
    }
    typer.echo(json.dumps(payload, indent=2))


@app.command(name="ownership")
def ownership_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    file: Annotated[
        str,
        typer.Option("--file", "-f", help="Workspace-relative file path."),
    ],
    start: Annotated[
        int,
        typer.Option("--start", "-s", help="Start line (1-based)."),
    ],
    end: Annotated[
        int,
        typer.Option("--end", "-e", help="End line (1-based)."),
    ],
) -> None:
    """Print per-author ownership scores for a file:line-range as JSON."""
    from ast_intel.history.history_builder import HistoryBuilder
    from ast_intel.history.models import SymbolKind, SymbolRef
    from ast_intel.history.ownership_scorer import OwnershipScorer

    ref = SymbolRef(
        id=f"{file}::L{start}-L{end}",
        label=Path(file).name,
        kind=SymbolKind.OTHER,
        file=file,
        start_line=start,
        end_line=end,
    )
    builder = HistoryBuilder(repo_root=repo_path)
    record = builder.build(ref)
    own = OwnershipScorer().score(
        symbol=record.symbol,
        head_sha=record.head_sha,
        commits=list(record.commits),
        blame=list(record.blame),
    )
    payload = {
        "head_sha": own.head_sha,
        "total_commits": own.total_commits,
        "total_lines": own.total_lines,
        "scores": [
            {
                "author": {"name": s.author_name, "email": s.author_email},
                "commit_count": s.commit_count,
                "last_commit_at": s.last_commit_at,
                "blame_lines": s.blame_lines,
                "commit_frequency": round(s.commit_frequency, 4),
                "recency_factor": round(s.recency_factor, 4),
                "blame_share": round(s.blame_share, 4),
                "score": round(s.score, 4),
            }
            for s in own.scores
        ],
    }
    typer.echo(json.dumps(payload, indent=2))


@app.command(name="decision-context")
def decision_context_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    file: Annotated[
        str,
        typer.Option("--file", "-f", help="Workspace-relative file path."),
    ],
    start: Annotated[
        int,
        typer.Option("--start", "-s", help="Start line (1-based)."),
    ],
    end: Annotated[
        int,
        typer.Option("--end", "-e", help="End line (1-based)."),
    ],
    max_signals: Annotated[
        int,
        typer.Option(
            "--max-signals", help="Cap on returned decision signals.",
        ),
    ] = 10,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Skip provider API calls (offline message linker only).",
        ),
    ] = False,
) -> None:
    """Print PR + review evidence for a file:line-range as JSON."""
    from ast_intel.history.enrichment import EnrichmentPipeline
    from ast_intel.history.history_builder import HistoryBuilder
    from ast_intel.history.models import SymbolKind, SymbolRef
    from ast_intel.history.providers.registry import build_provider

    ref = SymbolRef(
        id=f"{file}::L{start}-L{end}",
        label=Path(file).name,
        kind=SymbolKind.OTHER,
        file=file,
        start_line=start,
        end_line=end,
    )
    builder = HistoryBuilder(repo_root=repo_path)
    record = builder.build(ref)
    provider = None if offline else build_provider(repo_path)
    pipeline = EnrichmentPipeline(provider)
    enriched = pipeline.enrich(record)

    signals = list(enriched.signals)[:max_signals]
    payload = {
        "head_sha": record.head_sha,
        "total_commits": len(record.commits),
        "pull_requests": [
            {
                "id": ec.pr.id,
                "title": ec.pr.title,
                "url": ec.pr.url,
                "author": ec.pr.author_name,
                "merged_at": ec.pr.merged_at,
                "state": ec.pr.state,
            }
            for ec in enriched.enriched_commits
            if ec.pr is not None
        ],
        "signals": [
            {
                "kind": s.kind.value,
                "summary": s.summary,
                "detail": s.detail,
                "confidence": round(s.confidence, 4),
                "citation": {
                    "type": s.citation.type,
                    "url": s.citation.url,
                    "author": s.citation.author,
                    "date": s.citation.date,
                    "sha": s.citation.sha,
                    "pr_id": s.citation.pr_id,
                },
            }
            for s in signals
        ],
        "warnings": list(enriched.warnings),
    }
    typer.echo(json.dumps(payload, indent=2))


# endregion: --- History subcommands


# ---------------------------------------------------------------------------
# region:    --- Phase 4 subcommands (rich signals + feedback)
# ---------------------------------------------------------------------------


def _build_phase4_stack(repo_path: Path):  # type: ignore[no-untyped-def]
    """Return (builder, pipeline, aggregator) — local helper for CLI."""
    from ast_intel.history.enrichment import EnrichmentPipeline
    from ast_intel.history.history_builder import HistoryBuilder
    from ast_intel.history.providers.registry import build_provider
    from ast_intel.history.signals.signal_aggregator import SignalAggregator

    builder = HistoryBuilder(repo_root=repo_path)
    provider = build_provider(repo_path)
    pipeline = EnrichmentPipeline(provider)
    aggregator = SignalAggregator(
        repo_path,
        history_builder=builder,
        enrichment=pipeline,
    )
    return builder, pipeline, aggregator


@app.command(name="rfc-index")
def rfc_index_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
) -> None:
    """Scan the repo for markdown RFCs and print their symbol links."""
    from ast_intel.history.signals.rfc_indexer import RFCIndexer

    result = RFCIndexer(repo_path).index()
    payload = {
        "files_scanned": result.files_scanned,
        "files_with_symbols": result.files_with_symbols,
        "links": [
            {
                "rfc_id": link.rfc_id,
                "rfc_title": link.rfc_title,
                "rfc_url": link.rfc_url,
                "section": link.section,
                "symbol_label": link.symbol_label,
                "status": link.status,
                "excerpt": link.excerpt[:240],
            }
            for link in result.links
        ],
    }
    typer.echo(json.dumps(payload, indent=2))


@app.command(name="add-tribal-knowledge")
def add_tribal_knowledge_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    symbol_label: Annotated[
        str,
        typer.Option("--symbol", help="Symbol label this note applies to."),
    ],
    author: Annotated[str, typer.Option("--author", help="Note author.")],
    text: Annotated[str, typer.Option("--text", help="The note body.")],
    tags: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help="Optional tag, repeatable (e.g. --tag perf-sensitive).",
        ),
    ] = None,
    symbol_id: Annotated[
        str,
        typer.Option(
            "--symbol-id",
            help="Optional stable symbol id (defaults to label).",
        ),
    ] = "",
) -> None:
    """Persist a tribal-knowledge note to ``.ast-intel/tribal-knowledge.jsonl``."""
    from ast_intel.history.signals.tribal_knowledge import (
        TribalKnowledgeStore,
    )

    store = TribalKnowledgeStore(repo_path)
    note = store.add(
        symbol_id=symbol_id or symbol_label,
        symbol_label=symbol_label,
        author=author,
        text=text,
        tags=tuple(tags or ()),
    )
    typer.echo(
        json.dumps(
            {
                "id": note.id,
                "symbol_label": note.symbol_label,
                "author": note.author,
                "tags": list(note.tags),
                "created_at": note.created_at,
                "path": str(store.path),
            },
            indent=2,
        ),
    )


@app.command(name="add-incident")
def add_incident_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    title: Annotated[str, typer.Option("--title")],
    symbol_label: Annotated[
        str, typer.Option("--symbol", help="Symbol label implicated."),
    ],
    severity: Annotated[
        str, typer.Option("--severity", help="sev1|sev2|sev3|sev4."),
    ] = "sev3",
    status: Annotated[str, typer.Option("--status")] = "investigating",
    url: Annotated[str, typer.Option("--url")] = "",
    postmortem_url: Annotated[str, typer.Option("--postmortem-url")] = "",
    fix_commit: Annotated[str, typer.Option("--fix-commit")] = "",
    fix_pr: Annotated[str, typer.Option("--fix-pr")] = "",
    symbol_id: Annotated[str, typer.Option("--symbol-id")] = "",
) -> None:
    """Persist an incident record to ``.ast-intel/incidents.jsonl``."""
    from ast_intel.history.signals.incident_linker import IncidentStore

    store = IncidentStore(repo_path)
    inc = store.add(
        title=title,
        symbol_id=symbol_id or symbol_label,
        symbol_label=symbol_label,
        severity=severity,
        status=status,
        url=url,
        postmortem_url=postmortem_url,
        fix_commit=fix_commit,
        fix_pr=fix_pr,
    )
    typer.echo(
        json.dumps(
            {
                "incident_id": inc.incident_id,
                "title": inc.title,
                "severity": inc.severity,
                "status": inc.status,
                "symbol_label": inc.symbol_label,
                "occurred_at": inc.occurred_at,
                "path": str(store.path),
            },
            indent=2,
        ),
    )


@app.command(name="submit-feedback")
def submit_feedback_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    signal_id: Annotated[str, typer.Option("--signal-id")],
    verdict: Annotated[
        str,
        typer.Option(
            "--verdict",
            help="One of accept|reject|edit|outdated.",
        ),
    ],
    author: Annotated[str, typer.Option("--author")],
    comment: Annotated[str, typer.Option("--comment")] = "",
    replacement_text: Annotated[
        str, typer.Option("--replacement-text"),
    ] = "",
) -> None:
    """Submit feedback on a signal — adjusts future confidence scoring."""
    from ast_intel.history.feedback import FeedbackStore

    store = FeedbackStore(repo_path)
    entry = store.submit(
        signal_id=signal_id,
        verdict=verdict,
        author=author,
        comment=comment,
        replacement_text=replacement_text,
    )
    typer.echo(
        json.dumps(
            {
                "signal_id": entry.signal_id,
                "verdict": entry.verdict.value,
                "created_at": entry.created_at,
                "path": str(store.path),
            },
            indent=2,
        ),
    )


@app.command(name="rich-context")
def rich_context_cmd(
    repo_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the repository root.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ],
    file: Annotated[
        str, typer.Option("--file", "-f", help="Workspace-relative file."),
    ],
    start: Annotated[int, typer.Option("--start", "-s")],
    end: Annotated[int, typer.Option("--end", "-e")],
    symbol_label: Annotated[
        str,
        typer.Option(
            "--symbol",
            help="Symbol label to join with RFC/tribal/incident stores.",
        ),
    ] = "",
    max_signals: Annotated[
        int, typer.Option("--max-signals"),
    ] = 20,
    include_hidden: Annotated[
        bool,
        typer.Option(
            "--include-hidden",
            help="Show signals below the confidence floor.",
        ),
    ] = False,
) -> None:
    """Print fully-fused Phase 4 context for a file:line-range as JSON."""
    from ast_intel.history.models import SymbolKind, SymbolRef

    _builder, _pipeline, aggregator = _build_phase4_stack(repo_path)
    ref = SymbolRef(
        id=f"{file}::L{start}-L{end}",
        label=symbol_label or Path(file).name,
        kind=SymbolKind.OTHER,
        file=file,
        start_line=start,
        end_line=end,
    )
    ctx = aggregator.aggregate(
        ref,
        symbol_id=symbol_label or ref.id,
        symbol_label=symbol_label or ref.label,
        include_hidden=include_hidden,
    )
    signals = list(ctx.signals)[:max_signals]
    payload = {
        "head_sha": ctx.enriched.record.head_sha,
        "ownership_top": [
            {
                "author": s.author_name,
                "score": round(s.score, 4),
            }
            for s in list(ctx.ownership.scores)[:5]
        ],
        "rfcs": [
            {
                "rfc_id": r.rfc_id,
                "rfc_title": r.rfc_title,
                "section": r.section,
                "status": r.status,
                "symbol_label": r.symbol_label,
            }
            for r in ctx.rfcs
        ],
        "incidents": [
            {
                "incident_id": i.incident_id,
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
            }
            for i in ctx.incidents
        ],
        "tribal_knowledge": [
            {
                "id": n.id,
                "author": n.author,
                "text": n.text,
                "upvotes": n.upvotes,
                "downvotes": n.downvotes,
            }
            for n in ctx.tribal
        ],
        "signals": [
            {
                "id": s.id,
                "type": s.signal_type.value,
                "summary": s.summary,
                "confidence": round(s.confidence, 4),
                "conflicts_with": list(s.conflicts_with),
                "citation": {
                    "type": s.citation.type,
                    "url": s.citation.url,
                    "date": s.citation.date,
                    "sha": s.citation.sha,
                    "pr_id": s.citation.pr_id,
                },
            }
            for s in signals
        ],
        "conflicts": [
            {
                "type": c.conflict_type,
                "description": c.description,
                "signal_ids": list(c.signal_ids),
            }
            for c in ctx.conflicts
        ],
        "hidden_signal_count": ctx.hidden_signal_count,
    }
    typer.echo(json.dumps(payload, indent=2))


# endregion: --- Phase 4 subcommands



# ---------------------------------------------------------------------------
# region:    --- Direct execution guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()

# endregion: --- Direct execution guard
