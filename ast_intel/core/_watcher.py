"""Watch mode — auto-rebuild graph on file changes.

Uses ``watchdog`` to monitor the workspace for file changes, debounces
rapid events, and triggers incremental rebuilds via the existing pipeline.

Requires the ``[watch]`` optional dependency::

    pip install ast-intel[watch]
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__: list[str] = ["FileWatcher", "WatchConfig"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Configuration
# ---------------------------------------------------------------------------


class WatchConfig:
    """Configuration for the file watcher."""

    __slots__ = (
        "debounce_ms",
        "enabled",
        "exclude",
        "include",
        "languages",
        "no_iac",
        "no_methods",
        "output_dir",
        "repo_root",
        "similarity",
        "similarity_threshold",
        "workers",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        repo_root: Path,
        output_dir: Path,
        debounce_ms: int = 500,
        languages: list[str] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        workers: int = 0,
        no_methods: bool = False,
        similarity: bool = False,
        similarity_threshold: float = 0.4,
        no_iac: bool = False,
        enabled: bool = True,
    ) -> None:
        self.repo_root = repo_root
        self.output_dir = output_dir
        self.debounce_ms = debounce_ms
        self.languages = languages
        self.include = include
        self.exclude = exclude
        self.workers = workers
        self.no_methods = no_methods
        self.similarity = similarity
        self.similarity_threshold = similarity_threshold
        self.no_iac = no_iac
        self.enabled = enabled


# endregion: --- Configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Ignore Filter
# ---------------------------------------------------------------------------


class _IgnoreFilter:
    """Filters filesystem events against ignore rules.

    Reuses the same pathspec logic as WorkspaceDiscovery:
    - .gitignore
    - .ast-intel-ignore
    - Default excludes (node_modules, .git, __pycache__, etc.)
    - The output directory itself (ast_output/)
    """

    def __init__(self, repo_root: Path, output_dir: Path) -> None:
        from ast_intel.core.workspace import (
            _DEFAULT_EXCLUDES,
            _load_ast_intel_ignore,
            _load_gitignore,
        )

        self._repo_root = repo_root.resolve()
        self._output_dir = output_dir.resolve()
        self._gitignore = _load_gitignore(repo_root)
        self._ast_ignore = _load_ast_intel_ignore(repo_root)
        self._default_excludes = _DEFAULT_EXCLUDES

    def should_ignore(self, path: Path) -> bool:
        """Return True if this path should be ignored."""
        resolved = path.resolve()

        # Skip output directory (feedback loop prevention)
        try:
            resolved.relative_to(self._output_dir)
        except ValueError:
            pass
        else:
            return True

        # Compute relative path for pattern matching
        try:
            rel = resolved.relative_to(self._repo_root)
        except ValueError:
            return True  # Outside repo

        rel_str = str(rel)

        # Check default excludes (any path component)
        parts = rel.parts
        if any(part in self._default_excludes for part in parts):
            return True

        # Check .gitignore
        if self._gitignore and self._gitignore.match_file(rel_str):
            return True

        # Check .ast-intel-ignore
        return bool(
            self._ast_ignore and self._ast_ignore.match_file(rel_str)
        )


# endregion: --- Ignore Filter
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Debouncer
# ---------------------------------------------------------------------------


class _Debouncer:
    """Coalesces rapid filesystem events into batched rebuilds.

    Thread-safe. Accumulates changed paths and fires the callback
    after ``delay_s`` seconds of silence (no new events).
    """

    def __init__(
        self,
        delay_s: float,
        callback: Callable[[set[str]], None],
    ) -> None:
        self._delay = delay_s
        self._callback = callback
        self._lock = threading.Lock()
        self._pending: dict[str, float] = {}
        self._timer: threading.Timer | None = None

    def push(self, rel_path: str) -> None:
        """Record a file change event."""
        with self._lock:
            self._pending[rel_path] = time.monotonic()
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        """Flush pending events to the callback."""
        with self._lock:
            if not self._pending:
                return
            batch = set(self._pending.keys())
            self._pending.clear()
            self._timer = None

        self._callback(batch)

    def cancel(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# endregion: --- Debouncer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Event Handler
# ---------------------------------------------------------------------------


class _WatchEventHandler:
    """Watchdog event handler that feeds the debouncer."""

    def __init__(
        self,
        repo_root: Path,
        ignore_filter: _IgnoreFilter,
        debouncer: _Debouncer,
        source_extensions: frozenset[str],
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._filter = ignore_filter
        self._debouncer = debouncer
        self._extensions = source_extensions

    def _handle(self, event: Any) -> None:  # noqa: ANN401
        """Common handler for all event types."""
        if event.is_directory:
            return

        src_path = Path(event.src_path)

        # Check extension
        if src_path.suffix not in self._extensions:
            return

        # Check ignore rules
        if self._filter.should_ignore(src_path):
            return

        # Compute relative path
        try:
            rel = str(src_path.resolve().relative_to(self._repo_root))
        except ValueError:
            return

        self._debouncer.push(rel)

    def on_created(self, event: Any) -> None:  # noqa: ANN401
        """Handle file creation events."""
        self._handle(event)

    def on_modified(self, event: Any) -> None:  # noqa: ANN401
        """Handle file modification events."""
        self._handle(event)

    def on_deleted(self, event: Any) -> None:  # noqa: ANN401
        """Handle file deletion events."""
        self._handle(event)

    def on_moved(self, event: Any) -> None:  # noqa: ANN401
        """Handle file move/rename events."""
        self._handle(event)
        if hasattr(event, "dest_path"):

            class _FakeEvent:
                def __init__(self, path: str) -> None:
                    self.src_path = path
                    self.is_directory = False

            self._handle(_FakeEvent(event.dest_path))


# endregion: --- Event Handler
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- FileWatcher
# ---------------------------------------------------------------------------


class FileWatcher:
    """Main watcher orchestrator.

    Starts a watchdog Observer, filters + debounces events, and triggers
    incremental rebuilds.
    """

    def __init__(self, config: WatchConfig) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._rebuild_lock = threading.Lock()

    def run(self, *, quiet: bool = False) -> None:
        """Start watching. Blocks until SIGINT/SIGTERM.

        If ``config.enabled`` is ``False``, performs a single rebuild
        and exits immediately without starting the filesystem observer.

        Args:
            quiet: Suppress progress output.
        """
        config = self._config

        if not config.enabled:
            if not quiet:
                logger.info("Watch mode disabled — running single rebuild")
            # Perform one rebuild with all files marked as changed
            self._do_rebuild(set(), quiet=quiet)
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            msg = (
                "Watch mode requires the 'watchdog' package.\n"
                "Install it with: pip install ast-intel[watch]"
            )
            raise SystemExit(msg) from exc

        from ast_intel.core.workspace import _ALL_SOURCE_EXTENSIONS

        config = self._config
        ignore_filter = _IgnoreFilter(config.repo_root, config.output_dir)

        debouncer = _Debouncer(
            delay_s=config.debounce_ms / 1000.0,
            callback=lambda batch: self._on_rebuild(batch, quiet=quiet),
        )

        handler = _WatchEventHandler(
            repo_root=config.repo_root,
            ignore_filter=ignore_filter,
            debouncer=debouncer,
            source_extensions=_ALL_SOURCE_EXTENSIONS,
        )

        # Create a proper watchdog handler class at runtime
        class _Handler(FileSystemEventHandler):
            on_created = handler.on_created  # type: ignore[assignment]
            on_modified = handler.on_modified  # type: ignore[assignment]
            on_deleted = handler.on_deleted  # type: ignore[assignment]
            on_moved = handler.on_moved  # type: ignore[assignment]

        observer = Observer()
        observer.schedule(_Handler(), str(config.repo_root), recursive=True)  # type: ignore[no-untyped-call]
        observer.start()  # type: ignore[no-untyped-call]

        # Register signal handlers for graceful shutdown (main thread only)
        import threading as _threading

        _is_main = _threading.current_thread() is _threading.main_thread()

        if _is_main:
            original_sigint = signal.getsignal(signal.SIGINT)
            original_sigterm = signal.getsignal(signal.SIGTERM)

            def _signal_handler(signum: int, frame: Any) -> None:  # noqa: ANN401
                del signum, frame  # unused
                self._stop_event.set()

            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)

        if not quiet:
            logger.info(
                "Watching %s (debounce=%dms)",
                config.repo_root,
                config.debounce_ms,
            )

        # Block until stop signal
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        finally:
            debouncer.cancel()
            observer.stop()  # type: ignore[no-untyped-call]
            observer.join(timeout=5.0)
            # Restore original signal handlers
            if _is_main:
                signal.signal(signal.SIGINT, original_sigint)
                signal.signal(signal.SIGTERM, original_sigterm)

    def stop(self) -> None:
        """Signal the watcher to stop (thread-safe)."""
        self._stop_event.set()

    def _on_rebuild(self, changed_files: set[str], *, quiet: bool) -> None:
        """Perform an incremental rebuild for the changed files."""
        if not self._rebuild_lock.acquire(blocking=False):
            logger.debug("Rebuild already in progress, skipping")
            return
        try:
            self._do_rebuild(changed_files, quiet=quiet)
        finally:
            self._rebuild_lock.release()

    def _do_rebuild(self, changed_files: set[str], *, quiet: bool) -> None:
        """Execute the incremental rebuild pipeline."""
        import os

        from ast_intel.core.cache import IncrementalCache
        from ast_intel.core.dispatcher import Dispatcher
        from ast_intel.core.graph_builder import GraphBuilder
        from ast_intel.core.indexer import Indexer
        from ast_intel.core.workspace import WorkspaceDiscovery
        from ast_intel.formatters.graph_json_formatter import (
            GRAPH_JSON_FILENAME,
            GraphJsonFormatter,
        )

        config = self._config
        start = time.monotonic()

        if not quiet:
            logger.info("Rebuilding: %d file(s) changed", len(changed_files))

        try:
            # 1. Full discovery (needed for correct crate structure)
            discovery = WorkspaceDiscovery(
                repo_root=config.repo_root,
                include_paths=config.include or [],
                exclude_paths=config.exclude or [],
                languages=config.languages,
            )
            workspace = discovery.discover()

            # 2. Cache diff — identify what actually needs re-parsing
            cache_path = config.output_dir / ".ast-intel-cache.json"
            cache = IncrementalCache(cache_path)
            cached_entries = cache.load()
            cache_diff, file_hashes = cache.diff(
                workspace, cached_entries, config.repo_root,
            )

            # Restore unchanged ASTs from cache
            cache.restore_cached_asts(
                workspace, cached_entries, cache_diff.unchanged,
            )
            skip_files = cache_diff.unchanged

            # 3. Extract only changed/added files
            worker_count = min(
                config.workers if config.workers > 0 else (os.cpu_count() or 4),
                32,
            )
            dispatcher = Dispatcher(
                workers=worker_count,
                skip_methods=config.no_methods,
                skip_files=skip_files,
                quiet=True,
                debug=False,
            )
            workspace = dispatcher.dispatch(workspace)

            # 4. Rebuild cross-references
            indexer = Indexer()
            workspace = indexer.build_cross_references(workspace)

            # 5. Rebuild graph
            builder = GraphBuilder(
                similarity=config.similarity,
                similarity_threshold=config.similarity_threshold,
            )
            graph = builder.build(workspace)

            # 6. Detect hyperedges
            from ast_intel.core._hyperedge import detect_hyperedges

            graph.hyperedges = detect_hyperedges(graph)

            # 7. Emit graph.json
            config.output_dir.mkdir(parents=True, exist_ok=True)
            graph_path = config.output_dir / GRAPH_JSON_FILENAME
            GraphJsonFormatter().write(graph, graph_path)

            # 8. Save cache
            cache.save(workspace, file_hashes)

            # 9. Touch sentinel for MCP server notification
            sentinel = config.output_dir / ".graph-updated"
            sentinel.write_text(
                f"{time.time()}\n", encoding="utf-8",
            )

            elapsed = time.monotonic() - start
            if not quiet:
                logger.info(
                    "Rebuild complete in %.2fs (%d nodes, %d edges)",
                    elapsed,
                    len(graph.nodes),
                    len(graph.edges),
                )

        except Exception:
            logger.exception("Rebuild failed")


# endregion: --- FileWatcher
# ---------------------------------------------------------------------------
