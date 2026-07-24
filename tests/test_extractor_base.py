"""Tests for the ExtractorBase abstract class."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.base import ExtractorBase
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import CrateModel


class TestExtractorBase:
    """Verify the ExtractorBase contract."""

    def test_cannot_instantiate_directly(self) -> None:
        """ExtractorBase is abstract and cannot be instantiated."""
        with pytest.raises(TypeError, match="abstract"):
            ExtractorBase()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        """A properly implemented subclass can be instantiated."""

        class DummyExtractor(ExtractorBase):
            language_id = "dummy"
            file_extensions = [".dum"]

            def extract(self, file_path: Path, source: bytes) -> FileAST:
                return FileAST(file=str(file_path))

            def parse_manifest(self, manifest_path: Path) -> CrateModel:
                return CrateModel(name="dummy")

        ext = DummyExtractor()
        assert ext.language_id == "dummy"
        assert ext.file_extensions == [".dum"]

        result = ext.extract(Path("/tmp/test.dum"), b"")
        assert isinstance(result, FileAST)

    def test_incomplete_subclass_fails(self) -> None:
        """A subclass missing abstract methods cannot be instantiated."""

        class IncompleteExtractor(ExtractorBase):
            language_id = "bad"
            file_extensions = [".bad"]

            def extract(self, file_path: Path, source: bytes) -> FileAST:
                return FileAST()

            # Missing parse_manifest

        with pytest.raises(TypeError, match="abstract"):
            IncompleteExtractor()  # type: ignore[abstract]
