"""Graph loader — cache-aware graph construction.

Provides a single entry point that either loads a previously serialized
``graph.json`` from disk or runs the full extraction pipeline and returns
a :class:`~ast_intel.models.graph_model.CodeGraph`.  Used by the
``query``, ``path``, and ``explain`` CLI subcommands.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ast_intel.core.dispatcher import Dispatcher
from ast_intel.core.graph_builder import GraphBuilder
from ast_intel.core.indexer import Indexer
from ast_intel.core.workspace import WorkspaceDiscovery
from ast_intel.formatters.graph_json_formatter import (
    GRAPH_JSON_FILENAME,
    GraphJsonFormatter,
)
from ast_intel.models.graph_model import CodeGraph

__all__: list[str] = ["load_or_build_graph"]

logger = logging.getLogger(__name__)


def load_or_build_graph(  # noqa: PLR0913
    repo_path: Path,
    *,
    output_dir: Path | None = None,
    no_cache: bool = False,
    similarity: bool = False,
    similarity_threshold: float = 0.4,
    languages: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    workers: int = 0,
    no_methods: bool = False,
) -> CodeGraph:
    """Load a cached graph or run the full pipeline.

    Fast path
        If *no_cache* is ``False`` and ``graph.json`` exists in
        *output_dir* the graph is deserialized from disk — no parsing
        or extraction is performed.

    Slow path
        Otherwise the full pipeline (discover → extract → index →
        build graph) is executed.  The resulting graph is written to
        ``graph.json`` for future reuse.

    Args:
        repo_path: Resolved repository root.
        output_dir: Directory for ``graph.json``.  Defaults to *repo_path*.
        no_cache: Force a fresh build even when ``graph.json`` exists.
        similarity: Enable ``SIMILAR_TO`` edge emission.
        similarity_threshold: Minimum Jaccard score for similarity edges.
        languages: Restrict to these language IDs (``None`` = auto-detect).
        include: Include only these relative paths.
        exclude: Exclude these glob patterns.
        workers: Parallel worker count (0 = auto).
        no_methods: Skip package-method call extraction.

    Returns:
        A fully built :class:`CodeGraph`.
    """
    resolved_output = output_dir if output_dir is not None else repo_path
    graph_path = resolved_output / GRAPH_JSON_FILENAME

    # --- Fast path: load cached graph ---
    if not no_cache and graph_path.is_file():
        logger.info("Loading cached graph from %s", graph_path)
        return GraphJsonFormatter.read(graph_path)

    # --- Slow path: run full pipeline ---
    logger.info("Building graph from scratch for %s", repo_path)

    discovery = WorkspaceDiscovery(
        repo_root=repo_path,
        include_paths=include or [],
        exclude_paths=exclude or [],
        languages=languages,
    )
    workspace = discovery.discover()

    import os

    worker_count = min(workers if workers > 0 else (os.cpu_count() or 4), 32)

    dispatcher = Dispatcher(
        workers=worker_count,
        skip_methods=no_methods,
        quiet=True,
        debug=False,
    )
    workspace = dispatcher.dispatch(workspace)

    indexer = Indexer()
    workspace = indexer.build_cross_references(workspace)

    builder = GraphBuilder(
        similarity=similarity,
        similarity_threshold=similarity_threshold,
    )
    graph = builder.build(workspace)

    # Persist for next time
    resolved_output.mkdir(parents=True, exist_ok=True)
    GraphJsonFormatter().write(graph, graph_path)

    return graph
