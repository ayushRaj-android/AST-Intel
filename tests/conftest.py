"""Shared test fixtures and utilities for AST Intel tests.

This module provides:
- Temporary directory management for test outputs
- Fixture file loading helpers
- Common assertion utilities
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Root of the test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Provide a temporary output directory for test-generated files."""
    output = tmp_path / "output"
    output.mkdir()
    return output


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal synthetic repository for integration testing.

    The repo has a basic structure with a placeholder file.
    Language-specific fixtures are added in each extractor's phase.
    """
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Sample Repo\n", encoding="utf-8")
    return repo
