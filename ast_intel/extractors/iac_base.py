"""Abstract base class for Infrastructure-as-Code extractors.

IaC extractors do NOT reuse ``ExtractorBase`` because:
- ``ExtractorBase.extract()`` returns ``FileAST`` (code-centric).
- IaC extractors don't use tree-sitter; they use YAML/HCL parsers.
- IaC files need content-based classification (``can_handle``).

Each IaC extractor parses one format (K8s YAML, Helm, Dockerfile, etc.)
and returns an ``IaCGraph`` with resources and intra-file edges.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

    from ast_intel.models.iac_model import IaCContext, IaCGraph

__all__: list[str] = ["IaCExtractorBase"]

# Number of bytes to peek for content-based detection.
PEEK_SIZE: int = 1024


class IaCExtractorBase(ABC):
    """Base class for Infrastructure-as-Code extractors.

    Class Attributes:
        format_id: Short identifier (e.g. ``"kubernetes"``, ``"helm"``).
        file_patterns: Glob patterns for file matching.
        file_extensions: Simple extension match (e.g. ``[".yaml", ".yml"]``).
    """

    format_id: ClassVar[str]
    file_patterns: ClassVar[list[str]]
    file_extensions: ClassVar[list[str]]

    @abstractmethod
    def extract(
        self,
        file_path: Path,
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse an IaC file and return resources + intra-file edges.

        Args:
            file_path: Absolute path to the file.
            source: Raw file contents as bytes (UTF-8).
            context: Workspace context (root path, relative path).

        Returns:
            An ``IaCGraph`` populated with resources and edges.
            Errors are recorded in ``IaCGraph.errors``, never raised.
        """

    @abstractmethod
    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:
        """Heuristic check: can this extractor parse this file?

        Called with the first ``PEEK_SIZE`` bytes of the file.
        Needed because many IaC formats share ``.yaml``/``.yml``
        extensions.

        Args:
            file_path: Absolute path (for filename-based heuristics).
            source_peek: First ``PEEK_SIZE`` bytes of the file.

        Returns:
            ``True`` if this extractor should handle the file.
        """

    def extract_directory(
        self,
        directory: Path,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse an IaC directory (e.g. a Helm chart) and return resources.

        Override this in extractors that operate on directories rather
        than individual files (e.g. HelmExtractor).

        Args:
            directory: Absolute path to the directory root.
            context: Workspace context (root path, relative path).

        Returns:
            An ``IaCGraph`` populated with resources and edges.

        Raises:
            NotImplementedError: If the extractor does not support
                directory-level extraction.
        """
        msg = f"{type(self).__name__} does not support directory extraction"
        raise NotImplementedError(msg)
