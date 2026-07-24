"""Tests for ast_intel.core._hooks — git hook lifecycle."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ast_intel.core._hooks import (
    HOOK_END_MARKER,
    HOOK_START_MARKER,
    hook_status,
    install_hooks,
    install_merge_driver,
    uninstall_hooks,
    uninstall_merge_driver,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    subprocess.run(
        ["git", "init", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    # Set required git config for commits
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    return tmp_path


# endregion: --- Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Installation Tests
# ---------------------------------------------------------------------------


class TestInstallHooks:
    """Tests for install_hooks()."""

    def test_install_creates_post_commit(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        hook = git_repo / ".git" / "hooks" / "post-commit"
        assert hook.is_file()
        content = hook.read_text()
        assert HOOK_START_MARKER in content
        assert HOOK_END_MARKER in content
        assert "scan" in content

    def test_install_creates_post_checkout(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        assert hook.is_file()
        content = hook.read_text()
        assert HOOK_START_MARKER in content
        assert '"$3" = "1"' in content

    def test_install_sets_executable(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        for name in ("post-commit", "post-checkout"):
            hook = git_repo / ".git" / "hooks" / name
            assert os.access(hook, os.X_OK)

    def test_install_returns_hook_names(self, git_repo: Path) -> None:
        result = install_hooks(git_repo)
        assert "post-commit" in result
        assert "post-checkout" in result

    def test_install_appends_to_existing_hook(self, git_repo: Path) -> None:
        hooks_dir = git_repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/sh\necho 'existing hook'\n")
        hook_path.chmod(0o755)

        install_hooks(git_repo)

        content = hook_path.read_text()
        assert "echo 'existing hook'" in content
        assert HOOK_START_MARKER in content

    def test_install_preserves_existing_shebang(self, git_repo: Path) -> None:
        hooks_dir = git_repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/bash\necho hi\n")

        install_hooks(git_repo)

        content = hook_path.read_text()
        assert content.startswith("#!/bin/bash\n")

    def test_install_idempotent(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        install_hooks(git_repo)

        hook = git_repo / ".git" / "hooks" / "post-commit"
        content = hook.read_text()
        assert content.count(HOOK_START_MARKER) == 1
        assert content.count(HOOK_END_MARKER) == 1

    def test_install_creates_hooks_dir(self, git_repo: Path) -> None:
        hooks_dir = git_repo / ".git" / "hooks"
        if hooks_dir.exists():
            import shutil

            shutil.rmtree(hooks_dir)

        install_hooks(git_repo)
        assert hooks_dir.is_dir()
        assert (hooks_dir / "post-commit").is_file()

    def test_install_has_shebang_for_new_files(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        hook = git_repo / ".git" / "hooks" / "post-commit"
        content = hook.read_text()
        assert content.startswith("#!/bin/sh\n")

    def test_hook_content_uses_or_true(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        for name in ("post-commit", "post-checkout"):
            hook = git_repo / ".git" / "hooks" / name
            content = hook.read_text()
            assert "|| true" in content

    def test_hook_content_redirects_stderr(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        for name in ("post-commit", "post-checkout"):
            hook = git_repo / ".git" / "hooks" / name
            content = hook.read_text()
            assert "2>/dev/null" in content

    def test_hook_content_checks_executable(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        for name in ("post-commit", "post-checkout"):
            hook = git_repo / ".git" / "hooks" / name
            content = hook.read_text()
            assert '[ -x "$AST_INTEL_BIN" ]' in content


# endregion: --- Installation Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Uninstall Tests
# ---------------------------------------------------------------------------


class TestUninstallHooks:
    """Tests for uninstall_hooks()."""

    def test_uninstall_removes_marker_block(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        uninstall_hooks(git_repo)

        hook = git_repo / ".git" / "hooks" / "post-commit"
        # File should be deleted since it only had our content
        assert not hook.exists()

    def test_uninstall_preserves_other_content(self, git_repo: Path) -> None:
        hooks_dir = git_repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/sh\necho 'my custom hook'\n")

        install_hooks(git_repo)
        uninstall_hooks(git_repo)

        assert hook_path.is_file()
        content = hook_path.read_text()
        assert "my custom hook" in content
        assert HOOK_START_MARKER not in content

    def test_uninstall_deletes_empty_hook(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        uninstall_hooks(git_repo)

        for name in ("post-commit", "post-checkout"):
            hook = git_repo / ".git" / "hooks" / name
            assert not hook.exists()

    def test_uninstall_returns_cleaned_names(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        removed = uninstall_hooks(git_repo)
        assert "post-commit" in removed
        assert "post-checkout" in removed

    def test_uninstall_noop_when_not_installed(self, git_repo: Path) -> None:
        removed = uninstall_hooks(git_repo)
        assert removed == []


# endregion: --- Uninstall Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Status Tests
# ---------------------------------------------------------------------------


class TestHookStatus:
    """Tests for hook_status()."""

    def test_status_all_installed(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        st = hook_status(git_repo)
        assert st.post_commit is True
        assert st.post_checkout is True

    def test_status_none_installed(self, git_repo: Path) -> None:
        st = hook_status(git_repo)
        assert st.post_commit is False
        assert st.post_checkout is False

    def test_status_partial(self, git_repo: Path) -> None:
        """Only post-commit installed manually."""
        install_hooks(git_repo)
        # Remove post-checkout manually
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        hook.unlink()

        st = hook_status(git_repo)
        assert st.post_commit is True
        assert st.post_checkout is False

    def test_status_as_dict(self, git_repo: Path) -> None:
        install_hooks(git_repo)
        d = hook_status(git_repo).as_dict()
        assert d["post-commit"] is True
        assert d["post-checkout"] is True
        assert "merge-driver" in d


# endregion: --- Status Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Merge Driver Tests
# ---------------------------------------------------------------------------


class TestMergeDriver:
    """Tests for install_merge_driver() and uninstall_merge_driver()."""

    def test_merge_driver_install_gitattributes(
        self, git_repo: Path,
    ) -> None:
        install_merge_driver(git_repo)
        gitattr = git_repo / ".gitattributes"
        assert gitattr.is_file()
        content = gitattr.read_text()
        assert "ast_output/graph.json merge=ast-intel-graph" in content

    def test_merge_driver_install_git_config(self, git_repo: Path) -> None:
        install_merge_driver(git_repo)
        result = subprocess.run(
            [
                "git", "-C", str(git_repo),
                "config", "--get", "merge.ast-intel-graph.name",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "AST_INTEL" in result.stdout

    def test_merge_driver_uninstall(self, git_repo: Path) -> None:
        install_merge_driver(git_repo)
        uninstall_merge_driver(git_repo)

        gitattr = git_repo / ".gitattributes"
        assert not gitattr.exists()  # was empty after removal

        result = subprocess.run(
            [
                "git", "-C", str(git_repo),
                "config", "--get", "merge.ast-intel-graph.name",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0  # section removed

    def test_merge_driver_idempotent(self, git_repo: Path) -> None:
        install_merge_driver(git_repo)
        result = install_merge_driver(git_repo)
        assert result is False  # already installed

        gitattr = git_repo / ".gitattributes"
        content = gitattr.read_text()
        assert content.count("ast_output/graph.json") == 1

    def test_merge_driver_preserves_existing_gitattributes(
        self, git_repo: Path,
    ) -> None:
        gitattr = git_repo / ".gitattributes"
        gitattr.write_text("*.md linguist-documentation\n")

        install_merge_driver(git_repo)

        content = gitattr.read_text()
        assert "*.md linguist-documentation" in content
        assert "ast_output/graph.json merge=ast-intel-graph" in content

    def test_merge_driver_status(self, git_repo: Path) -> None:
        st = hook_status(git_repo)
        assert st.merge_driver is False

        install_merge_driver(git_repo)
        st = hook_status(git_repo)
        assert st.merge_driver is True


# endregion: --- Merge Driver Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Edge Case Tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and error handling tests."""

    def test_no_git_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No git repository"):
            install_hooks(tmp_path)

    def test_no_git_dir_status_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No git repository"):
            hook_status(tmp_path)

    def test_no_git_dir_uninstall_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No git repository"):
            uninstall_hooks(tmp_path)

    def test_core_hooks_path(self, git_repo: Path) -> None:
        """Hooks installed in custom directory when core.hooksPath is set."""
        custom_dir = git_repo / ".custom-hooks"
        custom_dir.mkdir()
        subprocess.run(
            [
                "git", "-C", str(git_repo),
                "config", "core.hooksPath", str(custom_dir),
            ],
            check=True,
            capture_output=True,
        )

        install_hooks(git_repo)

        assert (custom_dir / "post-commit").is_file()
        assert not (git_repo / ".git" / "hooks" / "post-commit").is_file()

    def test_install_updates_binary_path(self, git_repo: Path) -> None:
        """Re-install updates the binary path in the hook."""
        install_hooks(git_repo)

        with patch(
            "ast_intel.core._hooks._resolve_binary",
            return_value="/new/path/ast-intel",
        ):
            install_hooks(git_repo)

        hook = git_repo / ".git" / "hooks" / "post-commit"
        content = hook.read_text()
        assert "/new/path/ast-intel" in content

    def test_worktree_support(self, git_repo: Path) -> None:
        """Hooks can be installed from a worktree."""
        # Create an initial commit so worktree can be created
        dummy = git_repo / "dummy.txt"
        dummy.write_text("hello")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )

        # Create worktree
        wt_path = git_repo.parent / "worktree"
        subprocess.run(
            [
                "git", "-C", str(git_repo),
                "worktree", "add", str(wt_path), "-b", "wt-branch",
            ],
            check=True,
            capture_output=True,
        )

        # Install hooks from worktree — should resolve to main repo
        installed = install_hooks(wt_path)
        assert len(installed) == 2


# endregion: --- Edge Case Tests
# ---------------------------------------------------------------------------
