"""Tests for the CLI entry point."""

from __future__ import annotations

from typer.testing import CliRunner

from ast_intel import TOOL_VERSION
from ast_intel.cli import app

runner = CliRunner()


class TestCLIBasics:
    """Verify CLI wiring and basic behavior."""

    def test_help_shows_subcommands(self) -> None:
        """--help lists all subcommands."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "query" in result.output
        assert "path" in result.output
        assert "explain" in result.output
        assert "--version" in result.output

    def test_scan_help_shows_all_options(self) -> None:
        """scan --help prints usage with all registered options."""
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "repo_path" in result.output.lower() or "REPO_PATH" in result.output
        assert "--include" in result.output
        assert "--exclude" in result.output
        assert "--lang" in result.output
        assert "--output" in result.output
        assert "--format" in result.output
        assert "--workers" in result.output
        assert "--no-methods" in result.output
        assert "--no-cache" in result.output
        assert "--quiet" in result.output
        assert "--debug" in result.output

    def test_version_flag(self) -> None:
        """--version prints version and exits cleanly."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "ast-intel" in result.output
        assert TOOL_VERSION in result.output

    def test_empty_directory(self, tmp_path: object) -> None:
        """Running scan on an empty directory exits with 0."""
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "0 file" in result.output.lower() or "nothing" in result.output.lower()

    def test_nonexistent_path(self) -> None:
        """Running scan on a nonexistent path exits with error."""
        result = runner.invoke(app, ["scan", "/nonexistent/path/that/does/not/exist"])
        assert result.exit_code != 0
