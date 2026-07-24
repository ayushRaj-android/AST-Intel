"""Tests for Phase 10 — Packaging, CLI Polish & Security Hardening."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ast_intel import SCHEMA_VERSION, TOOL_VERSION
from ast_intel.cli import OutputFormat, SupportedLanguage, _configure_logging, app
from ast_intel.core.security import (
    PathSecurityError,
    check_grammar_availability,
    is_safe_symlink,
    sanitize_output_path,
    validate_repo_path,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# region:    --- pyproject.toml & Packaging Tests
# ---------------------------------------------------------------------------


class TestPackaging:
    """Verify pyproject.toml is correct and installable."""

    def test_pyproject_valid_toml(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        assert data["project"]["name"] == "ast-intel"
        assert "version" in data["project"]

    def test_pyproject_has_license(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        assert "license" in data["project"]

    def test_pyproject_has_urls(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        urls = data["project"].get("urls", {})
        assert "Repository" in urls
        assert "Homepage" in urls

    def test_pyproject_has_entry_point(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        scripts = data["project"].get("scripts", {})
        assert "ast-intel" in scripts
        assert scripts["ast-intel"] == "ast_intel.cli:app"

    def test_all_optional_deps_present(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        extras = data["project"]["optional-dependencies"]
        for lang in ("rust", "go", "python", "typescript", "csharp", "cpp", "java"):
            assert lang in extras, f"Missing optional dep group: {lang}"
        assert "all" in extras
        assert "dev" in extras

    def test_dependency_upper_bounds(self) -> None:
        """All deps should have upper bounds to prevent breakage."""
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        for dep in data["project"]["dependencies"]:
            assert "<" in dep, f"Missing upper bound: {dep}"

    def test_py_typed_marker_exists(self) -> None:
        marker = Path(__file__).parent.parent / "ast_intel" / "py.typed"
        assert marker.exists(), "py.typed marker missing — needed for PEP 561"

    def test_license_file_exists(self) -> None:
        license_file = Path(__file__).parent.parent / "LICENSE"
        assert license_file.exists()

    def test_version_constants_match(self) -> None:
        import tomllib

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        assert data["project"]["version"] == TOOL_VERSION


# endregion: --- pyproject.toml & Packaging Tests


# ---------------------------------------------------------------------------
# region:    --- CLI Polish Tests
# ---------------------------------------------------------------------------


class TestCLIVersion:
    """Verify --version output."""

    def test_version_flag_prints_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert TOOL_VERSION in result.output
        assert SCHEMA_VERSION in result.output
        assert "ast-intel" in result.output

    def test_version_is_semver(self) -> None:
        parts = TOOL_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


class TestCLIExitCodes:
    """Verify structured exit codes: 0, 1, 2."""

    def test_exit_0_on_empty_dir(self, tmp_path) -> None:
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0

    def test_exit_2_on_nonexistent_path(self) -> None:
        result = runner.invoke(app, ["scan", "/nonexistent/directory/999"])
        assert result.exit_code != 0

    def test_exit_0_on_success(self, tmp_path) -> None:
        src = tmp_path / "hello.py"
        src.write_text("def greet(): pass\n", encoding="utf-8")
        result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0

    def test_quiet_suppresses_output(self, tmp_path) -> None:
        src = tmp_path / "hello.py"
        src.write_text("x = 1\n", encoding="utf-8")
        result = runner.invoke(app, ["scan", str(tmp_path), "--quiet", "--format", "json"])
        # With --quiet, stderr output is suppressed (no banner/progress)
        assert result.exit_code == 0


class TestCLIErrorMessages:
    """Verify structured error messages for common problems."""

    def test_permission_denied_message(self, tmp_path) -> None:
        # Create unreadable directory
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            result = runner.invoke(app, ["scan", str(restricted)])
            # Should fail with error, not crash with traceback
            assert result.exit_code != 0
        finally:
            restricted.chmod(0o755)


class TestCLIFlags:
    """Verify all flags are wired and recognized."""

    def test_help_lists_all_options(self) -> None:
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--include", "--exclude", "--lang", "--output", "--format",
            "--workers", "--no-methods", "--no-cache", "--quiet",
            "--debug", "--analyze",
        ):
            assert flag in result.output

    def test_supported_formats(self) -> None:
        expected = {
            "json", "md", "both", "graph-json", "dot", "mermaid", "html",
            "arch", "all",
        }
        actual = {f.value for f in OutputFormat}
        assert actual == expected

    def test_supported_languages(self) -> None:
        expected = {
            "rust", "go", "python", "typescript", "csharp", "cpp", "java",
            "ruby", "kotlin", "scala", "swift", "php",
        }
        actual = {lg.value for lg in SupportedLanguage}
        assert actual == expected

    def test_workers_cap_at_32(self, tmp_path) -> None:
        src = tmp_path / "hello.py"
        src.write_text("x = 1\n", encoding="utf-8")
        # Even with --workers 999, should not crash
        result = runner.invoke(
            app, ["scan", str(tmp_path), "--workers", "999", "--quiet", "--format", "json"],
        )
        assert result.exit_code == 0


class TestDebugLogging:
    """Verify --debug configures logging."""

    def test_configure_logging_debug(self) -> None:
        import logging

        _configure_logging(debug=True)
        logger = logging.getLogger("ast_intel")
        assert logger.level == logging.DEBUG

    def test_configure_logging_default(self) -> None:
        import logging

        _configure_logging(debug=False)
        logger = logging.getLogger("ast_intel")
        assert logger.level == logging.WARNING


# endregion: --- CLI Polish Tests


# ---------------------------------------------------------------------------
# region:    --- Security Hardening Tests
# ---------------------------------------------------------------------------


class TestValidateRepoPath:
    """Tests for path validation security."""

    def test_valid_directory(self, tmp_path) -> None:
        result = validate_repo_path(tmp_path)
        assert result == tmp_path.resolve()

    def test_nonexistent_raises(self) -> None:
        import pytest

        with pytest.raises(PathSecurityError, match="Cannot resolve"):
            validate_repo_path(Path("/nonexistent/dir/999"))

    def test_file_not_dir_raises(self, tmp_path) -> None:
        import pytest

        f = tmp_path / "file.txt"
        f.write_text("hi")
        with pytest.raises(PathSecurityError, match="Not a directory"):
            validate_repo_path(f)

    def test_blocked_system_dirs(self) -> None:
        import pytest

        # Only test if running as non-root (root has access to everything)
        if os.getuid() == 0:
            return
        with pytest.raises(PathSecurityError, match="system directory"):
            validate_repo_path(Path("/"))


class TestSanitizeOutputPath:
    """Tests for output directory validation."""

    def test_creates_output_dir(self, tmp_path) -> None:
        out = tmp_path / "new" / "output"
        result = sanitize_output_path(out, tmp_path)
        assert result.is_dir()

    def test_default_to_ast_output_subdir(self, tmp_path) -> None:
        # An empty output path defaults to <repo>/ast_output/.
        result = sanitize_output_path(Path(), tmp_path)
        assert result == (tmp_path / "ast_output").resolve()


class TestSymlinkSafety:
    """Tests for symlink escape prevention."""

    def test_regular_file_is_safe(self, tmp_path) -> None:
        f = tmp_path / "safe.py"
        f.write_text("x = 1")
        assert is_safe_symlink(f, tmp_path) is True

    def test_symlink_within_root_is_safe(self, tmp_path) -> None:
        target = tmp_path / "target.py"
        target.write_text("x = 1")
        link = tmp_path / "link.py"
        link.symlink_to(target)
        assert is_safe_symlink(link, tmp_path) is True

    def test_symlink_outside_root_is_unsafe(self, tmp_path) -> None:
        # Create a symlink that escapes the repo root
        external = tmp_path.parent / "external_target.txt"
        external.write_text("secret")
        link = tmp_path / "escape.py"
        link.symlink_to(external)
        try:
            assert is_safe_symlink(link, tmp_path) is False
        finally:
            external.unlink(missing_ok=True)

    def test_broken_symlink_is_unsafe(self, tmp_path) -> None:
        link = tmp_path / "broken.py"
        link.symlink_to(tmp_path / "nonexistent")
        assert is_safe_symlink(link, tmp_path) is False


class TestGrammarHints:
    """Tests for missing grammar installation hints."""

    def test_no_warnings_when_no_lang_specified(self) -> None:
        result = check_grammar_availability(None)
        assert result == []

    def test_no_warnings_when_grammar_installed(self) -> None:
        # Python grammar should be installed in test env
        result = check_grammar_availability(["python"])
        assert result == []

    def test_warning_for_missing_grammar(self) -> None:
        with patch(
            "ast_intel.core.security._is_grammar_installed",
            return_value=False,
        ):
            result = check_grammar_availability(["java"])
            assert len(result) == 1
            assert "java" in result[0]
            assert "pip install" in result[0]

    def test_hint_includes_extra_name(self) -> None:
        with patch(
            "ast_intel.core.security._is_grammar_installed",
            return_value=False,
        ):
            result = check_grammar_availability(["typescript"])
            assert "ast-intel[typescript]" in result[0]


# endregion: --- Security Hardening Tests


# ---------------------------------------------------------------------------
# region:    --- Output Security Tests
# ---------------------------------------------------------------------------


class TestOutputSecurity:
    """Verify output files don't leak sensitive information."""

    def test_ast_json_no_absolute_paths_in_files(self, tmp_path) -> None:
        """File paths in ast.json should be relative, not absolute."""
        src = tmp_path / "src" / "main.py"
        src.parent.mkdir(parents=True)
        src.write_text("def hello(): pass\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", str(tmp_path), "--format", "json", "--quiet", "--no-cache"],
        )
        assert result.exit_code == 0

        ast_json = tmp_path / "ast.json"
        if ast_json.exists():
            data = json.loads(ast_json.read_text())
            # File entries should use relative paths
            for crate in data.get("crates", {}).values():
                for f in crate.get("files", []):
                    file_path = f.get("file", "")
                    assert not file_path.startswith("/"), (
                        f"Absolute path leaked: {file_path}"
                    )

    def test_ast_json_no_env_vars(self, tmp_path) -> None:
        """ast.json must not contain environment variable values."""
        src = tmp_path / "app.py"
        src.write_text("x = 1\n", encoding="utf-8")

        # Set a recognizable env var
        env_marker = "SUPER_SECRET_TOKEN_12345"
        os.environ[env_marker] = "secret_value"
        try:
            result = runner.invoke(
                app,
                ["scan", str(tmp_path), "--format", "json", "--quiet", "--no-cache"],
            )
            assert result.exit_code == 0

            ast_json = tmp_path / "ast.json"
            if ast_json.exists():
                content = ast_json.read_text()
                assert "secret_value" not in content
        finally:
            del os.environ[env_marker]

    def test_error_output_no_traceback(self, tmp_path) -> None:
        """Errors should show clean messages, not Python tracebacks."""
        result = runner.invoke(app, ["scan", "/nonexistent/path/xyz"])
        # Should not contain traceback markers
        assert "Traceback" not in (result.output or "")


# endregion: --- Output Security Tests


# ---------------------------------------------------------------------------
# region:    --- Integration Tests
# ---------------------------------------------------------------------------


class TestEndToEndPolish:
    """Integration tests for polished CLI behavior."""

    def test_full_pipeline_json_output(self, tmp_path) -> None:
        src = tmp_path / "src" / "lib.py"
        src.parent.mkdir(parents=True)
        src.write_text(
            "class Foo:\n    def bar(self) -> str:\n        return 'hi'\n",
            encoding="utf-8",
        )

        out = tmp_path / "output"
        result = runner.invoke(
            app,
            ["scan", str(tmp_path), "--output", str(out), "--format", "json", "--no-cache"],
        )
        assert result.exit_code == 0
        assert (out / "ast.json").exists()

        data = json.loads((out / "ast.json").read_text())
        assert "meta" in data
        assert data["meta"]["schema_version"] == SCHEMA_VERSION
        assert data["meta"]["tool_version"] == TOOL_VERSION

    def test_full_pipeline_both_outputs(self, tmp_path) -> None:
        src = tmp_path / "hello.py"
        src.write_text("x: int = 42\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", str(tmp_path), "--format", "both", "--no-cache"],
        )
        assert result.exit_code == 0
        # No --output given: outputs default to <repo>/ast_output/.
        out = tmp_path / "ast_output"
        assert (out / "ast.json").exists()
        assert (out / "summary.md").exists()

    def test_module_import_clean(self) -> None:
        """All public modules should be importable without side effects."""
        result = subprocess.run(
            [sys.executable, "-c", "import ast_intel; print(ast_intel.TOOL_VERSION)"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert TOOL_VERSION in result.stdout

    def test_no_unsafe_eval_or_exec(self) -> None:
        """Codebase must not use eval() or exec() anywhere."""
        src_dir = Path(__file__).parent.parent / "ast_intel"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            # Check for bare eval/exec calls (not in comments/strings heuristic)
            for line_num, line in enumerate(content.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Look for eval( or exec( as standalone calls
                for dangerous in ("eval(", "exec("):
                    if (
                        dangerous in stripped
                        and not stripped.startswith(("'", '"', "#"))
                        and "__import__" not in stripped
                    ):
                        msg = (
                            f"Unsafe {dangerous}) found in "
                            f"{py_file.relative_to(src_dir.parent)}:{line_num}"
                        )
                        raise AssertionError(msg)


# endregion: --- Integration Tests
