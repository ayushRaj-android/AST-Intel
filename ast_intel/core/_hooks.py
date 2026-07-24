"""Git hooks — auto-rebuild the code graph on commit / branch switch.

Installs post-commit and post-checkout hooks that run ``ast-intel scan``
after every commit or branch switch.  Hook sections are delimited with
markers so they can be surgically installed and removed without affecting
existing hook content.

Usage::

    from ast_intel.core._hooks import install_hooks, uninstall_hooks, hook_status

    installed = install_hooks(Path("."))
    status    = hook_status(Path("."))
    removed   = uninstall_hooks(Path("."))
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__: list[str] = [
    "HookStatus",
    "hook_status",
    "install_hooks",
    "install_merge_driver",
    "uninstall_hooks",
    "uninstall_merge_driver",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

HOOK_START_MARKER = "# >>> ast-intel-hook-start"
HOOK_END_MARKER = "# <<< ast-intel-hook-end"

_GITATTR_START_MARKER = "# >>> ast-intel-merge-driver-start"
_GITATTR_END_MARKER = "# <<< ast-intel-merge-driver-end"

_SHEBANG = "#!/bin/sh"

_MANAGED_HOOKS: tuple[str, ...] = ("post-commit", "post-checkout")

_POST_COMMIT_TEMPLATE = """\
{start}
# Auto-rebuild AST_INTEL graph after commit.
# Installed by: ast-intel hook install
# Safe: errors do not block git operations.
AST_INTEL_BIN="{binary}"
if [ -x "$AST_INTEL_BIN" ]; then
    "$AST_INTEL_BIN" scan . --format graph-json --quiet 2>/dev/null || true
fi
{end}
"""

_POST_CHECKOUT_TEMPLATE = """\
{start}
# Auto-rebuild AST_INTEL graph on branch switch.
# Installed by: ast-intel hook install
# Safe: errors do not block git operations.
# $3 = 1 means branch switch, 0 means file checkout.
if [ "$3" = "1" ]; then
    AST_INTEL_BIN="{binary}"
    if [ -x "$AST_INTEL_BIN" ]; then
        "$AST_INTEL_BIN" scan . --format graph-json --quiet 2>/dev/null || true
    fi
fi
{end}
"""

_GITATTR_MERGE_DRIVER = """\
{start}
ast_output/graph.json merge=ast-intel-graph
{end}
"""

# endregion: --- Constants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Data Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookStatus:
    """Status of managed hooks in a repository."""

    post_commit: bool = False
    post_checkout: bool = False
    merge_driver: bool = False

    def as_dict(self) -> dict[str, bool]:
        """Return status as a plain dict."""
        return {
            "post-commit": self.post_commit,
            "post-checkout": self.post_checkout,
            "merge-driver": self.merge_driver,
        }


# endregion: --- Data Types
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def install_hooks(repo: Path) -> list[str]:
    """Install post-commit and post-checkout git hooks.

    For each managed hook:
    - If the file doesn't exist, creates it with a shebang + marker block.
    - If it exists with markers, replaces the marker block (idempotent).
    - If it exists without markers, appends the marker block.

    Sets executable permission on each hook file.

    Args:
        repo: Repository root (must contain ``.git``).

    Returns:
        List of hook names that were installed.

    Raises:
        FileNotFoundError: If *repo* has no ``.git`` directory.
        OSError: On permission errors writing hook files.
    """
    binary = _resolve_binary()
    hooks_dir = _hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    templates: dict[str, str] = {
        "post-commit": _POST_COMMIT_TEMPLATE.format(
            start=HOOK_START_MARKER,
            end=HOOK_END_MARKER,
            binary=binary,
        ),
        "post-checkout": _POST_CHECKOUT_TEMPLATE.format(
            start=HOOK_START_MARKER,
            end=HOOK_END_MARKER,
            binary=binary,
        ),
    }

    installed: list[str] = []
    for hook_name, section in templates.items():
        hook_path = hooks_dir / hook_name
        content = _read_hook(hook_path)

        if content is None:
            # New file: shebang + section
            new_content = f"{_SHEBANG}\n{section}"
        else:
            new_content = _inject_section(content, section)

        _atomic_write(hook_path, new_content)
        _make_executable(hook_path)
        installed.append(hook_name)
        logger.info("Installed %s hook at %s", hook_name, hook_path)

    return installed


def uninstall_hooks(repo: Path) -> list[str]:
    """Remove ast-intel sections from git hooks.

    For each managed hook with markers, removes the marker block.
    If the file becomes empty (or only a shebang), deletes it.

    Args:
        repo: Repository root.

    Returns:
        List of hook names that were cleaned.

    Raises:
        FileNotFoundError: If *repo* has no ``.git`` directory.
    """
    hooks_dir = _hooks_dir(repo)
    cleaned: list[str] = []

    for hook_name in _MANAGED_HOOKS:
        hook_path = hooks_dir / hook_name
        content = _read_hook(hook_path)
        if content is None or not _has_section(content):
            continue

        new_content = _remove_section(content)

        # If only whitespace / shebang remains, delete the file
        stripped = new_content.strip()
        if not stripped or stripped == _SHEBANG:
            hook_path.unlink(missing_ok=True)
        else:
            _atomic_write(hook_path, new_content)

        cleaned.append(hook_name)
        logger.info("Removed %s hook section from %s", hook_name, hook_path)

    return cleaned


def hook_status(repo: Path) -> HookStatus:
    """Report which managed hooks are currently installed.

    Args:
        repo: Repository root.

    Returns:
        A :class:`HookStatus` with per-hook installation state.

    Raises:
        FileNotFoundError: If *repo* has no ``.git`` directory.
    """
    hooks_dir = _hooks_dir(repo)

    def _check(name: str) -> bool:
        content = _read_hook(hooks_dir / name)
        return content is not None and _has_section(content)

    merge = _has_merge_driver(repo)

    return HookStatus(
        post_commit=_check("post-commit"),
        post_checkout=_check("post-checkout"),
        merge_driver=merge,
    )


def install_merge_driver(repo: Path) -> bool:
    """Configure a git merge driver for ``graph.json``.

    Adds a marker-delimited line to ``.gitattributes`` and configures
    the merge driver command in ``.git/config``.

    Args:
        repo: Repository root.

    Returns:
        ``True`` if newly installed, ``False`` if already present.

    Raises:
        FileNotFoundError: If *repo* has no ``.git`` directory.
    """
    abs_repo = repo.resolve()
    _find_git_dir(abs_repo)  # validate git repo exists

    # -- .gitattributes --
    gitattr_path = abs_repo / ".gitattributes"
    existing = ""
    if gitattr_path.is_file():
        existing = gitattr_path.read_text(encoding="utf-8")

    if _GITATTR_START_MARKER in existing:
        return False  # already installed

    section = _GITATTR_MERGE_DRIVER.format(
        start=_GITATTR_START_MARKER,
        end=_GITATTR_END_MARKER,
    )
    new_content = _inject_section_raw(existing, section, _GITATTR_START_MARKER)
    _atomic_write(gitattr_path, new_content)

    # -- .git/config merge driver --
    binary = _resolve_binary()
    driver_cmd = (
        f'"{binary}" scan . --format graph-json --quiet 2>/dev/null || true'
    )
    subprocess.run(
        [
            "git", "-C", str(abs_repo),
            "config", "merge.ast-intel-graph.name",
            "AST_INTEL graph merge driver",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", "-C", str(abs_repo),
            "config", "merge.ast-intel-graph.driver",
            driver_cmd,
        ],
        check=True,
        capture_output=True,
    )

    logger.info("Merge driver installed for %s", abs_repo)
    return True


def uninstall_merge_driver(repo: Path) -> bool:
    """Remove the git merge driver for ``graph.json``.

    Removes the marker block from ``.gitattributes`` and the merge
    section from ``.git/config``.

    Args:
        repo: Repository root.

    Returns:
        ``True`` if removed, ``False`` if not present.
    """
    abs_repo = repo.resolve()

    # -- .gitattributes --
    gitattr_path = abs_repo / ".gitattributes"
    removed = False
    if gitattr_path.is_file():
        content = gitattr_path.read_text(encoding="utf-8")
        if _GITATTR_START_MARKER in content:
            new_content = _remove_section_raw(
                content, _GITATTR_START_MARKER, _GITATTR_END_MARKER,
            )
            stripped = new_content.strip()
            if not stripped:
                gitattr_path.unlink(missing_ok=True)
            else:
                _atomic_write(gitattr_path, new_content)
            removed = True

    # -- .git/config --
    try:
        subprocess.run(
            [
                "git", "-C", str(abs_repo),
                "config", "--remove-section", "merge.ast-intel-graph",
            ],
            check=True,
            capture_output=True,
        )
        removed = True
    except subprocess.CalledProcessError:
        pass  # section didn't exist

    if removed:
        logger.info("Merge driver removed for %s", abs_repo)
    return removed


# endregion: --- Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _resolve_binary() -> str:
    """Resolve the ast-intel binary path.

    Tries ``shutil.which`` first, falls back to ``sys.executable -m ast_intel``.
    """
    import shutil

    found = shutil.which("ast-intel")
    if found:
        return found
    return f"{sys.executable} -m ast_intel"


def _find_git_dir(repo: Path) -> Path:
    """Locate the ``.git`` directory for a repository.

    Handles standard repos, worktrees (where ``.git`` is a file with a
    ``gitdir:`` pointer), and the ``$GIT_DIR`` environment variable.

    Raises:
        FileNotFoundError: If no ``.git`` directory is found.
    """
    # Environment override
    env_git_dir = os.environ.get("GIT_DIR")
    if env_git_dir:
        gd = Path(env_git_dir).resolve()
        if gd.is_dir():
            return gd

    dot_git = repo.resolve() / ".git"

    if dot_git.is_dir():
        return dot_git

    # Worktree: .git is a file with "gitdir: /path/to/..."
    if dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            gitdir = text.split(":", 1)[1].strip()
            resolved = Path(gitdir)
            if not resolved.is_absolute():
                resolved = (dot_git.parent / resolved).resolve()
            if resolved.is_dir():
                return resolved

    msg = f"No git repository found at {repo}"
    raise FileNotFoundError(msg)


def _hooks_dir(repo: Path) -> Path:
    """Return the hooks directory, respecting ``core.hooksPath``.

    Falls back to ``<git_dir>/hooks``.
    """
    abs_repo = repo.resolve()

    # Check core.hooksPath
    try:
        result = subprocess.run(
            ["git", "-C", str(abs_repo), "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            check=True,
        )
        custom_path = result.stdout.strip()
        if custom_path:
            p = Path(custom_path)
            if not p.is_absolute():
                p = abs_repo / p
            return p.resolve()
    except subprocess.CalledProcessError:
        pass  # not set, use default

    git_dir = _find_git_dir(abs_repo)
    return git_dir / "hooks"


def _read_hook(path: Path) -> str | None:
    """Read a hook file. Returns ``None`` if it doesn't exist."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _has_section(content: str) -> bool:
    """Check if content contains an ast-intel hook marker block."""
    return HOOK_START_MARKER in content and HOOK_END_MARKER in content


def _inject_section(content: str, section: str) -> str:
    """Insert or replace an ast-intel marker block in hook content.

    If markers already exist, replaces the block between them.
    Otherwise appends the block.
    """
    if _has_section(content):
        return _replace_section(content, section)

    # Append
    if content.endswith("\n"):
        return content + section
    return content + "\n" + section


def _replace_section(content: str, section: str) -> str:
    """Replace the existing marker block with a new one."""
    start_idx = content.index(HOOK_START_MARKER)
    end_idx = content.index(HOOK_END_MARKER) + len(HOOK_END_MARKER)

    # Include trailing newline if present
    if end_idx < len(content) and content[end_idx] == "\n":
        end_idx += 1

    return content[:start_idx] + section + content[end_idx:]


def _remove_section(content: str) -> str:
    """Remove the ast-intel marker block from hook content."""
    return _remove_section_raw(content, HOOK_START_MARKER, HOOK_END_MARKER)


def _remove_section_raw(
    content: str, start_marker: str, end_marker: str,
) -> str:
    """Remove a marker-delimited block from text."""
    if start_marker not in content or end_marker not in content:
        return content

    start_idx = content.index(start_marker)
    end_idx = content.index(end_marker) + len(end_marker)

    # Include trailing newline if present
    if end_idx < len(content) and content[end_idx] == "\n":
        end_idx += 1

    result = content[:start_idx] + content[end_idx:]
    # Clean up double blank lines left behind
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


def _inject_section_raw(
    content: str, section: str, start_marker: str,
) -> str:
    """Append a marker section to content (no replacement)."""
    if start_marker in content:
        return content  # already present

    if content and not content.endswith("\n"):
        content += "\n"
    return content + section


def _has_merge_driver(repo: Path) -> bool:
    """Check if the merge driver is configured."""
    abs_repo = repo.resolve()
    gitattr_path = abs_repo / ".gitattributes"
    if not gitattr_path.is_file():
        return False
    content = gitattr_path.read_text(encoding="utf-8")
    return _GITATTR_START_MARKER in content


def _make_executable(path: Path) -> None:
    """Set the executable bit on a file (no-op on Windows)."""
    if sys.platform == "win32":
        return
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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


# endregion: --- Internal Helpers
# ---------------------------------------------------------------------------
