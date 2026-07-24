"""Abstract base class for all language extractors.

Every language extractor (Rust, Go, Python, TypeScript, C#, C/C++, Java)
must inherit from :class:`ExtractorBase` and implement its abstract methods.

The :class:`~ast_intel.core.dispatcher.Dispatcher` routes files to the correct
extractor by matching file extensions. The extractor never needs to know what
language a *different* file is — it only handles its own language.

Usage::

    class RustExtractor(ExtractorBase):
        language_id = "rust"
        file_extensions = [".rs"]

        def extract(self, file_path: Path, source: bytes) -> FileAST:
            ...

        def parse_manifest(self, manifest_path: Path) -> CrateModel:
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ast_intel.models.ast_node import Span

if TYPE_CHECKING:
    from pathlib import Path

    from tree_sitter import Node

    from ast_intel.models.ast_node import FileAST
    from ast_intel.models.workspace_model import CrateModel

__all__: list[str] = ["ExtractorBase", "span_from_node"]


def span_from_node(node: Node) -> Span:
    """Create a ``Span`` from a tree-sitter ``Node``.

    Tree-sitter uses 0-based (row, column) tuples. This helper
    converts to 1-based line/column numbers to match editor
    conventions.

    Args:
        node: A tree-sitter syntax node.

    Returns:
        A ``Span`` with 1-based positions.
    """
    sr, sc = node.start_point
    er, ec = node.end_point
    return Span(
        start_line=sr + 1,
        start_col=sc + 1,
        end_line=er + 1,
        end_col=ec + 1,
    )


class ExtractorBase(ABC):
    """Contract that every language extractor must fulfil.

    Class Attributes:
        language_id: Short identifier for the language (e.g., ``"rust"``).
            Used in logs, CLI ``--lang`` flag matching, and ``CrateModel.language``.
        file_extensions: File extensions handled by this extractor.
            Must include the leading dot (e.g., ``[".rs"]``, ``[".ts", ".tsx"]``).
    """

    language_id: str
    file_extensions: list[str]

    @abstractmethod
    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a single source file and return its AST representation.

        The caller provides both the path (for metadata) and the raw bytes
        (already read from disk). The extractor should handle parse errors
        gracefully — logging them and populating ``FileAST.errors`` rather
        than raising exceptions.

        Args:
            file_path: Absolute path to the source file.
            source: Raw file contents as bytes (UTF-8 encoded).

        Returns:
            A :class:`~ast_intel.models.ast_node.FileAST` populated with all
            symbols, imports, methods, and package method references found
            in the file.
        """

    @abstractmethod
    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a language-specific manifest file.

        Extracts crate / package metadata and dependency information.

        Args:
            manifest_path: Absolute path to the manifest file
                (e.g., ``Cargo.toml``, ``package.json``, ``go.mod``).

        Returns:
            A :class:`~ast_intel.models.workspace_model.CrateModel` populated
            with name, version, language, dependencies, and manifest path.
        """
