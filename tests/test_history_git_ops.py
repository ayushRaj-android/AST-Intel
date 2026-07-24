"""Tests for :mod:`ast_intel.history.git_ops`.

We spin up an ephemeral throw-away git repo via :mod:`subprocess` to
exercise the real CLI rather than mocking it — the value of these
tests is in catching format drift, not in achieving rock-bottom
runtime.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ast_intel.history.git_ops import (
    blame_range,
    find_repo_root,
    get_head_sha,
    log_line_range,
)


def _has_git() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _has_git(),
    reason="git CLI not available on PATH",
)


def _run(cwd: Path, *cmd: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2024-01-01T00:00:00",
           "GIT_COMMITTER_DATE": "2024-01-01T00:00:00"}
    subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.email", "alice@example.com")
    _run(repo, "git", "config", "user.name", "Alice")

    f = repo / "x.txt"
    f.write_text("a\nb\nc\nd\n")
    _run(repo, "git", "add", "x.txt")
    _run(repo, "git", "commit", "-q", "-m", "initial")

    # Second commit by a different author.
    _run(repo, "git", "config", "user.email", "bob@example.com")
    _run(repo, "git", "config", "user.name", "Bob")
    time.sleep(1.1)  # ensure distinct committer time
    f.write_text("a\nBB\nc\nd\n")
    _run(repo, "git", "add", "x.txt")
    _run(repo, "git", "commit", "-q", "-m", "edit line 2")
    return repo


def test_find_repo_root_walks_upward(tiny_repo: Path) -> None:
    nested = tiny_repo / "sub" / "deep"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tiny_repo


def test_get_head_sha_is_40_chars(tiny_repo: Path) -> None:
    sha = get_head_sha(tiny_repo)
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_log_line_range_finds_edits(tiny_repo: Path) -> None:
    commits = log_line_range(tiny_repo, "x.txt", 1, 4)
    assert len(commits) == 2
    # Newest first; second commit was "edit line 2" by Bob.
    assert commits[0].author_email == "bob@example.com"
    assert commits[0].subject == "edit line 2"
    assert commits[-1].subject == "initial"


def test_log_line_range_emits_numstat(tiny_repo: Path) -> None:
    commits = log_line_range(tiny_repo, "x.txt", 1, 4)
    head = commits[0]
    # "edit line 2" changed exactly one line (one removed + one added).
    assert head.lines_added == 1
    assert head.lines_removed == 1
    assert "x.txt" in head.files_changed


def test_blame_range_attributes_lines(tiny_repo: Path) -> None:
    hunks = blame_range(tiny_repo, "x.txt", 1, 4)
    by_author: dict[str, int] = {}
    for h in hunks:
        by_author[h.author_email] = by_author.get(h.author_email, 0) + h.line_count
    # Bob owns the modified line; Alice owns the rest.
    assert by_author.get("bob@example.com") == 1
    assert by_author.get("alice@example.com") == 3
