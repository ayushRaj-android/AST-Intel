"""Tests for the offline commit-message linker."""

from __future__ import annotations

from ast_intel.history.message_linker import (
    link_from_messages,
    stub_pr_from_commit,
)
from ast_intel.history.models import GitCommitInfo
from ast_intel.history.provider_registry import (
    ProviderIdentity,
    ProviderKind,
)


def _commit(subject: str) -> GitCommitInfo:
    return GitCommitInfo(
        sha="a" * 40,
        author_name="A",
        author_email="a@x",
        authored_at="2024-01-01T00:00:00+00:00",
        committed_at="2024-01-01T00:00:00+00:00",
        subject=subject,
    )


_ADO_IDENTITY = ProviderIdentity(
    kind=ProviderKind.AZURE_DEVOPS,
    host="dev.azure.com",
    owner="org",
    repo="repo",
    project="proj",
    web_base_url="https://dev.azure.com/org/proj/_git/repo",
    api_base_url="https://dev.azure.com/org/proj/_apis",
)
_GH_IDENTITY = ProviderIdentity(
    kind=ProviderKind.GITHUB,
    host="github.com",
    owner="o",
    repo="r",
    web_base_url="https://github.com/o/r",
    api_base_url="https://api.github.com",
)


def test_ado_merged_pr_subject() -> None:
    pr = stub_pr_from_commit(
        _commit("Merged PR 12345: Add new endpoint"),
        _ADO_IDENTITY,
    )
    assert pr is not None
    assert pr.id == "12345"
    assert pr.title == "Add new endpoint"
    assert pr.url == "https://dev.azure.com/org/proj/_git/repo/pullrequest/12345"
    assert pr.description == ""  # stub — hydrate later


def test_github_web_merge_subject() -> None:
    pr = stub_pr_from_commit(
        _commit("Merge pull request #42 from feature/foo"),
        _GH_IDENTITY,
    )
    assert pr is not None
    assert pr.id == "42"
    assert pr.url == "https://github.com/o/r/pull/42"


def test_github_squash_tail() -> None:
    pr = stub_pr_from_commit(
        _commit("Improve caching (#101)"),
        _GH_IDENTITY,
    )
    assert pr is not None
    assert pr.id == "101"
    assert pr.title == "Improve caching"


def test_no_match_returns_none() -> None:
    pr = stub_pr_from_commit(
        _commit("WIP: refactor parser"),
        _GH_IDENTITY,
    )
    assert pr is None


def test_link_from_messages_preserves_order() -> None:
    commits = [
        _commit("Merged PR 1: a"),
        _commit("WIP"),
        _commit("Squash (#2)"),
    ]
    results = link_from_messages(commits, _ADO_IDENTITY)
    assert [lr.pr.id if lr.pr else None for lr in results] == ["1", None, "2"]
