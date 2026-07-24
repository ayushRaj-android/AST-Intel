"""Tests for the platform installer (Feature 22)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ast_intel.core._installer import (
    Platform,
    _atomic_write,
    _deep_merge,
    detect_platforms,
    install,
    resolve_binary,
    uninstall,
)

# ---------------------------------------------------------------------------
# region:    --- Detection Tests
# ---------------------------------------------------------------------------


class TestDetectPlatforms:
    """Test platform detection from project directory."""

    def test_detect_vscode(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        result = detect_platforms(tmp_path)
        assert Platform.VSCODE in result

    def test_detect_cursor(self, tmp_path: Path) -> None:
        (tmp_path / ".cursor").mkdir()
        result = detect_platforms(tmp_path)
        assert Platform.CURSOR in result

    def test_detect_windsurf(self, tmp_path: Path) -> None:
        (tmp_path / ".windsurf").mkdir()
        result = detect_platforms(tmp_path)
        assert Platform.WINDSURF in result

    def test_detect_claude_code(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        result = detect_platforms(tmp_path)
        assert Platform.CLAUDE_CODE in result

    def test_detect_codex(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        result = detect_platforms(tmp_path)
        assert Platform.CODEX in result

    def test_detect_nothing(self, tmp_path: Path) -> None:
        result = detect_platforms(tmp_path)
        # May include CLAUDE_DESKTOP if ~/.config/claude exists
        assert Platform.VSCODE not in result
        assert Platform.CURSOR not in result
        assert Platform.WINDSURF not in result
        assert Platform.CLAUDE_CODE not in result
        assert Platform.CODEX not in result

    def test_detect_multiple(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        (tmp_path / ".cursor").mkdir()
        result = detect_platforms(tmp_path)
        assert Platform.VSCODE in result
        assert Platform.CURSOR in result


# endregion: --- Detection Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Resolve Binary Tests
# ---------------------------------------------------------------------------


class TestResolveBinary:
    """Test binary resolution."""

    def test_resolve_found(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            assert resolve_binary() == "/usr/bin/ast-intel"

    def test_resolve_fallback(self) -> None:
        with patch("shutil.which", return_value=None):
            result = resolve_binary()
            assert sys.executable in result
            assert "ast_intel" in result


# endregion: --- Resolve Binary Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Install Tests (VS Code / Cursor / Windsurf)
# ---------------------------------------------------------------------------


class TestInstallEditorMCP:
    """Test editor MCP config installation."""

    def test_install_vscode_new(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.VSCODE)

        assert result.platform == Platform.VSCODE
        assert result.created is True
        assert result.config_path.exists()

        config = json.loads(result.config_path.read_text())
        assert "ast-intel" in config["servers"]
        assert config["servers"]["ast-intel"]["command"] == "/usr/bin/ast-intel"
        assert "${workspaceFolder}" in config["servers"]["ast-intel"]["args"]

    def test_install_vscode_merge(self, tmp_path: Path) -> None:
        """Existing servers are preserved."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        existing = {
            "servers": {
                "other-tool": {
                    "command": "other",
                    "args": ["run"],
                },
            },
        }
        (vscode_dir / "mcp.json").write_text(json.dumps(existing))

        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.VSCODE)

        assert result.created is False
        config = json.loads(result.config_path.read_text())
        assert "other-tool" in config["servers"]
        assert "ast-intel" in config["servers"]

    def test_install_vscode_idempotent(self, tmp_path: Path) -> None:
        """Running install twice produces same result."""
        (tmp_path / ".vscode").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.VSCODE)
            result = install(tmp_path, Platform.VSCODE)

        config = json.loads(result.config_path.read_text())
        # Should have exactly one ast-intel entry.
        assert len(config["servers"]) == 1

    def test_install_cursor(self, tmp_path: Path) -> None:
        (tmp_path / ".cursor").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.CURSOR)

        assert result.config_path == tmp_path / ".cursor" / "mcp.json"
        config = json.loads(result.config_path.read_text())
        assert "ast-intel" in config["servers"]

    def test_install_windsurf(self, tmp_path: Path) -> None:
        (tmp_path / ".windsurf").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.WINDSURF)

        assert result.config_path == tmp_path / ".windsurf" / "mcp.json"
        config = json.loads(result.config_path.read_text())
        assert "ast-intel" in config["servers"]


# endregion: --- Install Tests (VS Code / Cursor / Windsurf)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Install Tests (Claude Desktop)
# ---------------------------------------------------------------------------


class TestInstallClaudeDesktop:
    """Test Claude Desktop config installation."""

    def test_install_creates_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "claude_desktop_config.json"
        with (
            patch("shutil.which", return_value="/usr/bin/ast-intel"),
            patch(
                "ast_intel.core._installer._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            result = install(tmp_path, Platform.CLAUDE_DESKTOP)

        assert result.created is True
        config = json.loads(config_path.read_text())
        assert "ast-intel" in config["mcpServers"]
        # Claude Desktop uses absolute path, not ${workspaceFolder}
        assert str(tmp_path) in config["mcpServers"]["ast-intel"]["args"]

    def test_install_merges_existing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "claude_desktop_config.json"
        existing = {
            "mcpServers": {"other-mcp": {"command": "other"}},
            "theme": "dark",
        }
        config_path.write_text(json.dumps(existing))

        with (
            patch("shutil.which", return_value="/usr/bin/ast-intel"),
            patch(
                "ast_intel.core._installer._claude_desktop_config_path",
                return_value=config_path,
            ),
        ):
            install(tmp_path, Platform.CLAUDE_DESKTOP)

        config = json.loads(config_path.read_text())
        assert "other-mcp" in config["mcpServers"]
        assert "ast-intel" in config["mcpServers"]
        assert config["theme"] == "dark"


# endregion: --- Install Tests (Claude Desktop)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Install Tests (Claude Code)
# ---------------------------------------------------------------------------


class TestInstallClaudeCode:
    """Test Claude Code config installation."""

    def test_install_creates_settings(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.CLAUDE_CODE)

        settings = json.loads(result.config_path.read_text())
        assert "ast-intel" in settings["mcpServers"]

    def test_install_creates_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.CLAUDE_CODE)

        md_path = tmp_path / "CLAUDE.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "ast-intel" in content.lower() or "AST Intel" in content

    def test_install_appends_to_existing_claude_md(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / ".claude").mkdir()
        md_path = tmp_path / "CLAUDE.md"
        md_path.write_text("# My Project\n\nExisting content.\n")

        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.CLAUDE_CODE)

        content = md_path.read_text()
        assert "# My Project" in content
        assert "Existing content." in content
        assert "AST Intel" in content

    def test_install_idempotent_md(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.CLAUDE_CODE)
            install(tmp_path, Platform.CLAUDE_CODE)

        content = (tmp_path / "CLAUDE.md").read_text()
        # Marker should appear exactly once.
        assert content.count("<!-- ast-intel:start -->") == 1


# endregion: --- Install Tests (Claude Code)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Install Tests (Codex)
# ---------------------------------------------------------------------------


class TestInstallCodex:
    """Test Codex AGENTS.md installation."""

    def test_install_creates_agents_md(self, tmp_path: Path) -> None:
        result = install(tmp_path, Platform.CODEX)

        assert result.created is True
        assert result.config_path == tmp_path / "AGENTS.md"
        content = result.config_path.read_text()
        assert "ast-intel" in content

    def test_install_appends_to_existing(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents\n\nExisting agent info.\n")

        install(tmp_path, Platform.CODEX)

        content = agents_md.read_text()
        assert "# Agents" in content
        assert "Existing agent info." in content
        assert "AST Intel" in content

    def test_install_idempotent(self, tmp_path: Path) -> None:
        install(tmp_path, Platform.CODEX)
        install(tmp_path, Platform.CODEX)

        content = (tmp_path / "AGENTS.md").read_text()
        assert content.count("<!-- ast-intel:start -->") == 1


# endregion: --- Install Tests (Codex)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Uninstall Tests
# ---------------------------------------------------------------------------


class TestUninstall:
    """Test configuration removal."""

    def test_uninstall_vscode(self, tmp_path: Path) -> None:
        """Remove ast-intel key, keep others."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        config = {
            "servers": {
                "ast-intel": {"command": "ast-intel", "args": ["serve", "."]},
                "other": {"command": "other"},
            },
        }
        (vscode_dir / "mcp.json").write_text(json.dumps(config))

        result = uninstall(tmp_path, Platform.VSCODE)

        assert result is not None
        remaining = json.loads((vscode_dir / "mcp.json").read_text())
        assert "ast-intel" not in remaining["servers"]
        assert "other" in remaining["servers"]

    def test_uninstall_vscode_only_entry(self, tmp_path: Path) -> None:
        """If ast-intel was only server, remove entire file."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        config = {
            "servers": {
                "ast-intel": {"command": "ast-intel", "args": ["serve", "."]},
            },
        }
        (vscode_dir / "mcp.json").write_text(json.dumps(config))

        uninstall(tmp_path, Platform.VSCODE)

        assert not (vscode_dir / "mcp.json").exists()

    def test_uninstall_nothing_installed(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        result = uninstall(tmp_path, Platform.VSCODE)
        assert result is None

    def test_uninstall_codex(self, tmp_path: Path) -> None:
        """Strips marker block from AGENTS.md."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# Agents\n\n"
            "<!-- ast-intel:start -->\n## AST Intel\nContent.\n"
            "<!-- ast-intel:end -->\n",
        )

        result = uninstall(tmp_path, Platform.CODEX)
        assert result is not None

        content = agents_md.read_text()
        assert "ast-intel" not in content
        assert "# Agents" in content

    def test_uninstall_claude_code(self, tmp_path: Path) -> None:
        """Removes from settings.json and CLAUDE.md."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "mcpServers": {
                "ast-intel": {"command": "ast-intel", "args": ["serve", "."]},
            },
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        md_path = tmp_path / "CLAUDE.md"
        md_path.write_text(
            "# Docs\n\n"
            "<!-- ast-intel:start -->\n## AST Intel\n"
            "<!-- ast-intel:end -->\n",
        )

        uninstall(tmp_path, Platform.CLAUDE_CODE)

        new_settings = json.loads(
            (claude_dir / "settings.json").read_text(),
        )
        assert "ast-intel" not in new_settings["mcpServers"]

        md_content = md_path.read_text()
        assert "ast-intel" not in md_content


# endregion: --- Uninstall Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Backup Tests
# ---------------------------------------------------------------------------


class TestBackup:
    """Test backup creation."""

    def test_backup_created(self, tmp_path: Path) -> None:
        (tmp_path / ".vscode").mkdir()
        mcp_json = tmp_path / ".vscode" / "mcp.json"
        mcp_json.write_text('{"servers": {}}')

        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            result = install(tmp_path, Platform.VSCODE)

        assert result.backup_path is not None
        assert result.backup_path.exists()
        assert result.backup_path.suffix == ".bak"

    def test_backup_not_overwritten(self, tmp_path: Path) -> None:
        """Second install doesn't overwrite first backup."""
        (tmp_path / ".vscode").mkdir()
        mcp_json = tmp_path / ".vscode" / "mcp.json"
        mcp_json.write_text('{"servers": {"original": {}}}')

        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.VSCODE)

        bak_content = (
            tmp_path / ".vscode" / "mcp.json.bak"
        ).read_text()
        assert "original" in bak_content

        # Modify and reinstall — backup should still be original.
        with patch("shutil.which", return_value="/usr/bin/ast-intel"):
            install(tmp_path, Platform.VSCODE)

        bak_content2 = (
            tmp_path / ".vscode" / "mcp.json.bak"
        ).read_text()
        assert bak_content2 == bak_content


# endregion: --- Backup Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helper Tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test utility functions."""

    def test_deep_merge_preserves_keys(self) -> None:
        base = {"servers": {"other": {"cmd": "x"}}, "theme": "dark"}
        overlay = {"servers": {"ast-intel": {"cmd": "y"}}}
        result = _deep_merge(base, overlay, ["servers"])
        assert result["theme"] == "dark"
        assert "other" in result["servers"]
        assert "ast-intel" in result["servers"]

    def test_deep_merge_overwrites_non_merge_keys(self) -> None:
        base = {"version": 1}
        overlay = {"version": 2}
        result = _deep_merge(base, overlay, ["servers"])
        assert result["version"] == 2

    def test_atomic_write(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        _atomic_write(target, '{"ok": true}\n')
        assert target.exists()
        assert json.loads(target.read_text()) == {"ok": True}

    def test_atomic_write_creates_parent(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "dir" / "file.json"
        _atomic_write(target, '{"nested": true}\n')
        assert target.exists()

    def test_json_parse_error_raises(self, tmp_path: Path) -> None:
        """Invalid JSON in existing config raises OSError."""
        (tmp_path / ".vscode").mkdir()
        (tmp_path / ".vscode" / "mcp.json").write_text("not json {{{")

        with (
            patch("shutil.which", return_value="/usr/bin/ast-intel"),
            pytest.raises(OSError, match="Cannot parse"),
        ):
            install(tmp_path, Platform.VSCODE)


# endregion: --- Helper Tests
# ---------------------------------------------------------------------------
