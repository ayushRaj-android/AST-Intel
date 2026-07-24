"""Dispatcher — routes files to the correct language extractor in parallel.

The dispatcher is the orchestrator of Stage 2 (Extract). For each source file
discovered by workspace discovery, it:

1. Detects the language from the file extension.
2. Picks the appropriate :class:`~ast_intel.extractors.base.ExtractorBase`.
3. Calls ``extractor.extract(file_path, source_bytes)``.
4. Collects the resulting :class:`~ast_intel.models.ast_node.FileAST`.

Extraction runs in parallel using ``concurrent.futures.ThreadPoolExecutor``.
Parse errors on individual files are caught, logged, and recorded in
``FileAST.errors`` — they never abort the entire run.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from ast_intel.extractors.base import ExtractorBase
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import WorkspaceAST

__all__: list[str] = ["Dispatcher"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Extension → Extractor Registry
# ---------------------------------------------------------------------------


def _build_extractor_registry() -> dict[str, type[ExtractorBase]]:  # noqa: C901, PLR0912, PLR0915
    """Build a map of file extension → extractor **class**.

    Returns classes (not instances) so each thread can create its own
    extractor, avoiding shared mutable state such as tree-sitter parsers.

    Only extractors whose runtime dependencies are importable are registered.
    """
    registry: dict[str, type[ExtractorBase]] = {}

    # Rust
    try:
        from ast_intel.extractors.rust import RustExtractor

        for suffix in RustExtractor.file_extensions:
            registry[suffix] = RustExtractor
    except ImportError:
        logger.debug("Rust extractor not available (missing tree-sitter-rust)")

    # Python
    try:
        from ast_intel.extractors.python import PythonExtractor

        for suffix in PythonExtractor.file_extensions:
            registry[suffix] = PythonExtractor
    except ImportError:
        logger.debug("Python extractor not available (missing tree-sitter-python)")

    # C#
    try:
        from ast_intel.extractors.csharp import CSharpExtractor

        for suffix in CSharpExtractor.file_extensions:
            registry[suffix] = CSharpExtractor
    except ImportError:
        logger.debug("C# extractor not available (missing tree-sitter-c-sharp)")

    # TypeScript / JavaScript
    try:
        from ast_intel.extractors.typescript import TypeScriptExtractor

        for suffix in TypeScriptExtractor.file_extensions:
            registry[suffix] = TypeScriptExtractor
    except ImportError:
        logger.debug(
            "TypeScript extractor not available "
            "(missing tree-sitter-typescript/javascript)",
        )

    # Go
    try:
        from ast_intel.extractors.go import GoExtractor

        for suffix in GoExtractor.file_extensions:
            registry[suffix] = GoExtractor
    except ImportError:
        logger.debug("Go extractor not available (missing tree-sitter-go)")

    # C / C++
    try:
        from ast_intel.extractors.cpp import CppExtractor

        for suffix in CppExtractor.file_extensions:
            registry[suffix] = CppExtractor
    except ImportError:
        logger.debug("C/C++ extractor not available (missing tree-sitter-c/cpp)")

    # Java
    try:
        from ast_intel.extractors.java import JavaExtractor

        for suffix in JavaExtractor.file_extensions:
            registry[suffix] = JavaExtractor
    except ImportError:
        logger.debug("Java extractor not available (missing tree-sitter-java)")

    # Ruby
    try:
        from ast_intel.extractors.ruby import RubyExtractor

        for suffix in RubyExtractor.file_extensions:
            registry[suffix] = RubyExtractor
    except ImportError:
        logger.debug("Ruby extractor not available (missing tree-sitter-ruby)")

    # Kotlin
    try:
        from ast_intel.extractors.kotlin import KotlinExtractor

        for suffix in KotlinExtractor.file_extensions:
            registry[suffix] = KotlinExtractor
    except ImportError:
        logger.debug("Kotlin extractor not available (missing tree-sitter-kotlin)")

    # Scala
    try:
        from ast_intel.extractors.scala import ScalaExtractor

        for suffix in ScalaExtractor.file_extensions:
            registry[suffix] = ScalaExtractor
    except ImportError:
        logger.debug("Scala extractor not available (missing tree-sitter-scala)")

    # Swift
    try:
        from ast_intel.extractors.swift import SwiftExtractor

        for suffix in SwiftExtractor.file_extensions:
            registry[suffix] = SwiftExtractor
    except ImportError:
        logger.debug("Swift extractor not available (missing tree-sitter-swift)")

    # PHP
    try:
        from ast_intel.extractors.php import PhpExtractor

        for suffix in PhpExtractor.file_extensions:
            registry[suffix] = PhpExtractor
    except ImportError:
        logger.debug("PHP extractor not available (missing tree-sitter-php)")

    # Contract files (OpenAPI, Protobuf, GraphQL)
    try:
        from ast_intel.extractors.contract import ContractExtractor

        for suffix in ContractExtractor.file_extensions:
            registry[suffix] = ContractExtractor
    except ImportError:
        logger.debug("Contract extractor not available")

    return registry


# endregion: --- Extension → Extractor Registry


# ---------------------------------------------------------------------------
# region:    --- File Extraction Task
# ---------------------------------------------------------------------------


def _extract_one(
    repo_root: Path,
    stub: FileAST,
    extractor_cls: type[ExtractorBase],
    *,
    skip_methods: bool,
) -> FileAST:
    """Extract a single file, returning a populated ``FileAST``.

    On error, returns a ``FileAST`` with the ``errors`` field populated.
    This function **never** raises.
    """
    abs_path = repo_root / stub.file

    try:
        source = abs_path.read_bytes()
    except OSError as exc:
        logger.warning("Cannot read %s: %s", stub.file, exc)
        return FileAST(
            file=stub.file,
            module_path=stub.module_path,
            is_test=stub.is_test,
            errors=[f"Cannot read file: {exc}"],
        )

    extractor = extractor_cls()
    try:
        result = extractor.extract(abs_path, source)
    except Exception as exc:  # noqa: BLE001  — intentional broad catch
        logger.warning("Extraction failed for %s: %s", stub.file, exc)
        return FileAST(
            file=stub.file,
            module_path=stub.module_path,
            is_test=stub.is_test,
            errors=[f"Extraction error: {exc}"],
        )

    # Carry forward the module path and is_test computed during discovery
    result.file = stub.file
    result.module_path = stub.module_path
    result.is_test = _detect_is_test(stub.file, extractor_flag=result.is_test)

    # Optionally strip imported_package_methods
    if skip_methods:
        result.imported_package_methods = {}

    return result


def _detect_is_test(rel_path: str, *, extractor_flag: bool) -> bool:
    """Determine whether a file is a test file.

    Heuristic: already flagged by extractor, or path contains ``/tests/``
    or ``/test/``, or filename starts with ``test_`` or ends with
    ``_test.`` / ``_tests.``.
    """
    if extractor_flag:
        return True

    parts = rel_path.replace("\\", "/").split("/")
    if any(p in ("tests", "test") for p in parts):
        return True

    filename = parts[-1] if parts else ""
    stem = filename.rsplit(".", maxsplit=1)[0] if "." in filename else filename
    return stem.startswith("test_") or stem.endswith(("_test", "_tests"))


# endregion: --- File Extraction Task


# ---------------------------------------------------------------------------
# region:    --- Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Route files to extractors and run extraction in parallel.

    Args:
        workers: Number of parallel threads. Default: 4.
        skip_methods: If True, skip ``imported_package_methods`` extraction.
        quiet: Suppress progress bar output.
        debug: Enable verbose per-file debug logging.
    """

    def __init__(
        self,
        workers: int = 4,
        *,
        skip_methods: bool = False,
        skip_files: frozenset[str] = frozenset(),
        quiet: bool = False,
        debug: bool = False,
    ) -> None:
        self.workers = workers
        self.skip_methods = skip_methods
        self.skip_files = skip_files
        self.quiet = quiet
        self.debug = debug

    def dispatch(self, workspace: WorkspaceAST) -> WorkspaceAST:
        """Run extraction on all files in the workspace.

        Iterates over every ``CrateModel`` in ``workspace.crates``,
        reads each file listed in ``crate.files``, dispatches to the
        correct extractor, and replaces the stub ``FileAST`` with the
        fully parsed version.

        Args:
            workspace: A ``WorkspaceAST`` with discovered files (stubs).

        Returns:
            The same ``WorkspaceAST`` with ``FileAST`` entries fully populated.
        """
        registry = _build_extractor_registry()
        repo_root = self._resolve_repo_root(workspace)

        # Build the list of (crate_name, file_index, stub, extractor)
        tasks = self._prepare_tasks(workspace, registry)

        total_files = sum(len(c.files) for c in workspace.crates.values())
        logger.info(
            "Dispatching extraction for %d files "
            "(%d extractable) across %d crate(s) with %d workers",
            total_files,
            len(tasks),
            len(workspace.crates),
            self.workers,
        )

        if not tasks:
            logger.info("No extractable files found")
            return workspace

        # Run extraction in parallel
        results = self._run_parallel(tasks, repo_root)

        # Write results back into workspace crates
        for crate_name, file_idx, populated in results:
            workspace.crates[crate_name].files[file_idx] = populated

        error_count = sum(
            1
            for c in workspace.crates.values()
            for f in c.files
            if f.errors
        )

        logger.info(
            "Extraction complete: %d file(s) processed, %d with errors",
            len(tasks),
            error_count,
        )

        return workspace

    # --- Internal helpers ---

    @staticmethod
    def _resolve_repo_root(workspace: WorkspaceAST) -> Path:
        """Resolve the repository root from workspace metadata.

        Falls back to CWD if ``repo_root`` is not set.
        """
        if workspace.meta.workspace_root:
            return Path(workspace.meta.workspace_root)
        return Path.cwd()

    def _prepare_tasks(
        self,
        workspace: WorkspaceAST,
        registry: dict[str, type[ExtractorBase]],
    ) -> list[tuple[str, int, FileAST, type[ExtractorBase]]]:
        """Build extraction task list from workspace stubs.

        Returns list of (crate_name, file_index, stub, extractor_cls) tuples.
        Files with no matching extractor or in ``self.skip_files`` are skipped.
        """
        tasks: list[tuple[str, int, FileAST, type[ExtractorBase]]] = []

        for crate_name, crate in workspace.crates.items():
            for idx, stub in enumerate(crate.files):
                if stub.file in self.skip_files:
                    logger.debug("Skipping cached file: %s", stub.file)
                    continue
                suffix = Path(stub.file).suffix
                ext_cls = registry.get(suffix)
                if ext_cls is None:
                    logger.debug(
                        "No extractor for %s (ext=%s), skipping",
                        stub.file,
                        suffix,
                    )
                    continue
                tasks.append((crate_name, idx, stub, ext_cls))

        return tasks

    def _run_parallel(
        self,
        tasks: list[tuple[str, int, FileAST, type[ExtractorBase]]],
        repo_root: Path,
    ) -> list[tuple[str, int, FileAST]]:
        """Execute extraction tasks in parallel with a progress bar."""
        results: list[tuple[str, int, FileAST]] = []

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            disable=self.quiet,
            transient=True,
        )

        with progress:
            task_id = progress.add_task("Extracting…", total=len(tasks))

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                future_map = {
                    pool.submit(
                        _extract_one,
                        repo_root,
                        stub,
                        ext_cls,
                        skip_methods=self.skip_methods,
                    ): (crate_name, file_idx, stub)
                    for crate_name, file_idx, stub, ext_cls in tasks
                }

                for future in as_completed(future_map):
                    crate_name, file_idx, stub = future_map[future]

                    try:
                        populated = future.result()
                    except Exception:
                        logger.exception(
                            "Unexpected error extracting %s", stub.file,
                        )
                        populated = FileAST(
                            file=stub.file,
                            module_path=stub.module_path,
                            is_test=stub.is_test,
                            errors=[f"Internal error extracting {stub.file}"],
                        )

                    if self.debug:
                        logger.debug(
                            "Extracted %s (%d errors)",
                            populated.file,
                            len(populated.errors),
                        )

                    results.append((crate_name, file_idx, populated))
                    progress.advance(task_id)

        return results


# endregion: --- Dispatcher
