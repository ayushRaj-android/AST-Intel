"""Phase 0 — Smoke tests for project scaffold.

These tests verify that the project structure, imports, and CLI wiring
are correctly set up. They do NOT test any extraction logic.
"""

from __future__ import annotations

from ast_intel import SCHEMA_VERSION, TOOL_VERSION


class TestPackageMetadata:
    """Verify package-level constants and imports."""

    def test_schema_version_is_semver(self) -> None:
        """SCHEMA_VERSION follows semver format."""
        parts = SCHEMA_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_tool_version_is_semver(self) -> None:
        """TOOL_VERSION follows semver format."""
        parts = TOOL_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_schema_version_value(self) -> None:
        assert SCHEMA_VERSION == "1.0.0"

    def test_tool_version_value(self) -> None:
        assert TOOL_VERSION == "0.1.8"
