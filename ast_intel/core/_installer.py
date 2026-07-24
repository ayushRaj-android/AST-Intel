"""Platform installer — auto-configure AI assistants for MCP server.

Detects the current project's AI assistant tooling and writes/removes
the configuration needed to connect to ``ast-intel serve``.

Public API
----------
- ``install(project_root, platform)`` — write platform config
- ``uninstall(project_root, platform)`` — remove ast-intel config
- ``detect_platforms(project_root)`` — find which platforms are present
- ``resolve_binary()`` — find the ast-intel CLI binary path
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__: list[str] = [
    "InstallResult",
    "Platform",
    "detect_platforms",
    "install",
    "resolve_binary",
    "uninstall",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants & Types
# ---------------------------------------------------------------------------

_MARKER_START = "<!-- ast-intel:start -->"
_MARKER_END = "<!-- ast-intel:end -->"


class Platform(StrEnum):
    """Supported AI assistant platforms."""

    VSCODE = "vscode"
    CURSOR = "cursor"
    WINDSURF = "windsurf"
    CLAUDE_DESKTOP = "claude-desktop"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


@dataclass(frozen=True)
class InstallResult:
    """Result of an install operation."""

    platform: Platform
    config_path: Path
    backup_path: Path | None
    created: bool  # True = new file, False = merged into existing


# endregion: --- Constants & Types
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Detection
# ---------------------------------------------------------------------------


def detect_platforms(project_root: Path) -> list[Platform]:
    """Detect which AI assistant platforms are present in the project."""
    detected: list[Platform] = []
    if (project_root / ".vscode").is_dir():
        detected.append(Platform.VSCODE)
    if (project_root / ".cursor").is_dir():
        detected.append(Platform.CURSOR)
    if (project_root / ".windsurf").is_dir():
        detected.append(Platform.WINDSURF)
    if (project_root / ".claude").is_dir():
        detected.append(Platform.CLAUDE_CODE)
    if _claude_desktop_config_path().parent.is_dir():
        detected.append(Platform.CLAUDE_DESKTOP)
    # Codex: only if AGENTS.md already exists (don't assume Codex)
    if (project_root / "AGENTS.md").exists():
        detected.append(Platform.CODEX)
    return detected


def resolve_binary() -> str:
    """Resolve the absolute path to the ast-intel CLI binary."""
    found = shutil.which("ast-intel")
    if found:
        return found
    # Fallback: invoke via current Python interpreter
    return f"{sys.executable} -m ast_intel"


# endregion: --- Detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Install
# ---------------------------------------------------------------------------


def install(project_root: Path, platform: Platform) -> InstallResult:
    """Install ast-intel config for the given platform.

    Returns an ``InstallResult`` with details about what was written.
    Raises ``OSError`` on permission/write failures.
    """
    binary = resolve_binary()
    abs_root = project_root.resolve()

    if platform == Platform.VSCODE:
        return _install_editor_mcp(abs_root, binary, ".vscode", platform)
    if platform == Platform.CURSOR:
        return _install_editor_mcp(abs_root, binary, ".cursor", platform)
    if platform == Platform.WINDSURF:
        return _install_editor_mcp(abs_root, binary, ".windsurf", platform)
    if platform == Platform.CLAUDE_DESKTOP:
        return _install_claude_desktop(abs_root, binary)
    if platform == Platform.CLAUDE_CODE:
        return _install_claude_code(abs_root, binary)
    # Platform.CODEX
    return _install_codex(abs_root)


def _install_editor_mcp(
    project_root: Path,
    binary: str,
    config_dir: str,
    platform: Platform,
) -> InstallResult:
    """Install MCP config for VS Code / Cursor / Windsurf."""
    dir_path = project_root / config_dir
    dir_path.mkdir(parents=True, exist_ok=True)
    config_path = dir_path / "mcp.json"

    our_block = {
        "servers": {
            "ast-intel": {
                "command": binary,
                "args": ["serve", "${workspaceFolder}"],
            },
        },
    }

    backup = _write_json_merged(config_path, our_block, ["servers"])
    return InstallResult(
        platform=platform,
        config_path=config_path,
        backup_path=backup,
        created=backup is None,
    )


def _install_claude_desktop(
    project_root: Path,
    binary: str,
) -> InstallResult:
    """Install MCP config for Claude Desktop (global config)."""
    config_path = _claude_desktop_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    our_block = {
        "mcpServers": {
            "ast-intel": {
                "command": binary,
                "args": ["serve", str(project_root)],
            },
        },
    }

    backup = _write_json_merged(config_path, our_block, ["mcpServers"])
    return InstallResult(
        platform=Platform.CLAUDE_DESKTOP,
        config_path=config_path,
        backup_path=backup,
        created=backup is None,
    )


def _install_claude_code(
    project_root: Path,
    binary: str,
) -> InstallResult:
    """Install MCP config for Claude Code (.claude/settings.json + CLAUDE.md)."""
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    our_block = {
        "mcpServers": {
            "ast-intel": {
                "command": binary,
                "args": ["serve", "."],
            },
        },
    }

    backup = _write_json_merged(settings_path, our_block, ["mcpServers"])

    # Also write/append CLAUDE.md
    md_path = project_root / "CLAUDE.md"
    _write_markdown_section(md_path, _CLAUDE_MD_SECTION)

    return InstallResult(
        platform=Platform.CLAUDE_CODE,
        config_path=settings_path,
        backup_path=backup,
        created=backup is None,
    )


def _install_codex(project_root: Path) -> InstallResult:
    """Install instructions for Codex (AGENTS.md)."""
    md_path = project_root / "AGENTS.md"
    created = not md_path.exists()
    _write_markdown_section(md_path, _CODEX_MD_SECTION)
    return InstallResult(
        platform=Platform.CODEX,
        config_path=md_path,
        backup_path=None,
        created=created,
    )


# endregion: --- Install
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Uninstall
# ---------------------------------------------------------------------------


def uninstall(project_root: Path, platform: Platform) -> Path | None:
    """Remove ast-intel configuration for the given platform.

    Returns the config path that was modified, or None if nothing was found.
    """
    abs_root = project_root.resolve()

    if platform in (Platform.VSCODE, Platform.CURSOR, Platform.WINDSURF):
        dir_name = {
            Platform.VSCODE: ".vscode",
            Platform.CURSOR: ".cursor",
            Platform.WINDSURF: ".windsurf",
        }[platform]
        return _uninstall_editor_mcp(abs_root / dir_name / "mcp.json")
    if platform == Platform.CLAUDE_DESKTOP:
        return _uninstall_claude_desktop()
    if platform == Platform.CLAUDE_CODE:
        return _uninstall_claude_code(abs_root)
    # Platform.CODEX
    return _uninstall_markdown(abs_root / "AGENTS.md")


def _uninstall_editor_mcp(config_path: Path) -> Path | None:
    """Remove ast-intel from an editor MCP JSON config."""
    if not config_path.exists():
        return None

    try:
        content = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    servers = content.get("servers", {})
    if "ast-intel" not in servers:
        return None

    del servers["ast-intel"]

    if not servers:
        # File only had our entry — remove it entirely.
        config_path.unlink()
    else:
        content["servers"] = servers
        _atomic_write(config_path, json.dumps(content, indent=2) + "\n")

    return config_path


def _uninstall_claude_desktop() -> Path | None:
    """Remove ast-intel from Claude Desktop config."""
    config_path = _claude_desktop_config_path()
    if not config_path.exists():
        return None

    try:
        content = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    servers = content.get("mcpServers", {})
    if "ast-intel" not in servers:
        return None

    del servers["ast-intel"]
    content["mcpServers"] = servers
    _atomic_write(config_path, json.dumps(content, indent=2) + "\n")
    return config_path


def _uninstall_claude_code(project_root: Path) -> Path | None:
    """Remove ast-intel from Claude Code settings + CLAUDE.md."""
    settings_path = project_root / ".claude" / "settings.json"
    result: Path | None = None

    if settings_path.exists():
        try:
            content = json.loads(settings_path.read_text(encoding="utf-8"))
            servers = content.get("mcpServers", {})
            if "ast-intel" in servers:
                del servers["ast-intel"]
                content["mcpServers"] = servers
                _atomic_write(
                    settings_path,
                    json.dumps(content, indent=2) + "\n",
                )
                result = settings_path
        except (json.JSONDecodeError, OSError):
            pass

    # Also remove markdown section
    _uninstall_markdown(project_root / "CLAUDE.md")
    return result or (project_root / "CLAUDE.md")


def _uninstall_markdown(md_path: Path) -> Path | None:
    """Remove the ast-intel marker block from a markdown file."""
    if not md_path.exists():
        return None

    text = md_path.read_text(encoding="utf-8")
    if _MARKER_START not in text:
        return None

    start_idx = text.index(_MARKER_START)
    end_idx = text.index(_MARKER_END) + len(_MARKER_END)

    # Remove the block and any trailing newline.
    before = text[:start_idx].rstrip("\n")
    after = text[end_idx:].lstrip("\n")

    new_text = before + ("\n" + after if after else "")

    if new_text.strip():
        _atomic_write(md_path, new_text.rstrip("\n") + "\n")
    else:
        md_path.unlink()

    return md_path


# endregion: --- Uninstall
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _claude_desktop_config_path() -> Path:
    """Return the Claude Desktop config file path for this OS."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    # Linux (and fallback)
    return Path.home() / ".config" / "claude" / "claude_desktop_config.json"


def _write_json_merged(
    path: Path,
    our_block: Mapping[str, object],
    merge_keys: list[str],
) -> Path | None:
    """Write JSON config, merging into existing file if present.

    Returns the backup path if an existing file was backed up, None otherwise.
    """
    backup: Path | None = None

    if path.exists():
        backup = _backup(path)
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            msg = f"Cannot parse existing {path}: {exc}"
            raise OSError(msg) from exc
    else:
        existing = {}

    merged = _deep_merge(existing, our_block, merge_keys)
    _atomic_write(path, json.dumps(merged, indent=2) + "\n")
    return backup


def _deep_merge(
    base: dict[str, object],
    overlay: Mapping[str, object],
    merge_keys: list[str],
) -> dict[str, object]:
    """Deep-merge overlay into base at the specified key paths.

    Only keys listed in ``merge_keys`` are deep-merged (dict-level).
    Other keys from overlay overwrite base.
    """
    result = dict(base)
    for key, value in overlay.items():
        if key in merge_keys and isinstance(value, dict):
            existing_val = result.get(key)
            if isinstance(existing_val, dict):
                result[key] = {**existing_val, **value}
            else:
                result[key] = value
        else:
            result[key] = value
    return result


def _write_markdown_section(path: Path, section: str) -> None:
    """Append a marker-delimited section to a markdown file.

    If the marker block already exists, replace it (idempotent).
    """
    block = f"{_MARKER_START}\n{section}\n{_MARKER_END}\n"

    if path.exists():
        text = path.read_text(encoding="utf-8")
        if _MARKER_START in text:
            # Replace existing block.
            start_idx = text.index(_MARKER_START)
            end_idx = text.index(_MARKER_END) + len(_MARKER_END)
            new_text = text[:start_idx] + block + text[end_idx:].lstrip("\n")
            _atomic_write(path, new_text)
            return
        # Append to existing file (ensure newline separation).
        if not text.endswith("\n"):
            text += "\n"
        _atomic_write(path, text + "\n" + block)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, block)


def _backup(path: Path) -> Path:
    """Create a .bak backup of the file (skip if .bak already exists)."""
    bak_path = path.with_suffix(path.suffix + ".bak")
    if not bak_path.exists():
        shutil.copy2(path, bak_path)
    return bak_path


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".ast-intel-",
            suffix=".tmp",
            delete=False,
        ) as fd:
            fd.write(content)
            tmp_path = Path(fd.name)
        tmp_path.replace(path)
    except BaseException:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# endregion: --- Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Markdown Sections
# ---------------------------------------------------------------------------

_CLAUDE_MD_SECTION = """\
## AST Intel

This project has a code knowledge graph available via the `ast-intel` MCP server.
Use the ast-intel tools (search_symbols, find_path, explain_symbol, get_impact,
get_dependencies, get_context, find_usages) to query symbol relationships,
find dependencies, trace impact, and explore the codebase structure.
"""

_CODEX_MD_SECTION = """\
## AST Intel

This project has a code knowledge graph. The `ast-intel` MCP server provides
tools for querying symbols, dependencies, impact analysis, and code structure.

Start server: `ast-intel serve .`
"""

# endregion: --- Markdown Sections
