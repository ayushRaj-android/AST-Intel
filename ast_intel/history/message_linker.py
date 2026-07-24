"""Extract pull-request IDs from commit subjects offline.

Mirrors ``src/providers/messageLinker.ts``.  Three message conventions
cover the vast majority of real-world repositories:

- Azure DevOps merge commits: ``Merged PR 12345: <title>``
- GitHub web-UI merges:        ``Merge pull request #123 from ...``
- GitHub squash merges:        ``<title> (#123)`` at end-of-subject

When a match is found we synthesise a *stub* :class:`PullRequestInfo`
with an empty description and no reviewers.  The enrichment pipeline
hydrates stubs into full PRs via the provider's REST API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ast_intel.history.models import GitCommitInfo, PullRequestInfo
from ast_intel.history.provider_registry import (
    ProviderIdentity,
    pull_request_url,
)

__all__: list[str] = ["LinkResult", "link_from_messages", "stub_pr_from_commit"]


_ADO_MERGED_PR = re.compile(r"^Merged PR (\d+)\s*:\s*(.*)$")
_GH_MERGE = re.compile(r"^Merge pull request #(\d+) from .*$")
_GH_SQUASH_TAIL = re.compile(r"\(#(\d+)\)\s*$")


def _extract_pr_id(subject: str) -> tuple[str, str] | None:
    """Return ``(pr_id, title)`` if *subject* contains a PR reference."""
    m = _ADO_MERGED_PR.match(subject)
    if m:
        return m.group(1), m.group(2).strip()
    m = _GH_MERGE.match(subject)
    if m:
        return m.group(1), subject
    m = _GH_SQUASH_TAIL.search(subject)
    if m:
        return m.group(1), subject[: m.start()].rstrip()
    return None


def stub_pr_from_commit(
    commit: GitCommitInfo,
    identity: ProviderIdentity,
) -> PullRequestInfo | None:
    """Build a stub PR from a merge-commit message, or return ``None``."""
    parsed = _extract_pr_id(commit.subject)
    if parsed is None:
        return None
    pr_id, title = parsed
    return PullRequestInfo(
        id=pr_id,
        title=title or commit.subject,
        description="",
        state="merged",
        url=pull_request_url(identity, pr_id),
        author_name=commit.author_name,
        author_email=commit.author_email,
        created_at=commit.authored_at,
        merged_at=commit.committed_at,
    )


@dataclass(frozen=True, slots=True)
class LinkResult:
    """The (commit, pr) pairing produced by the offline linker.

    A ``None`` ``pr`` field means the commit message contained no
    recognisable PR reference; callers may still query the provider
    API for an authoritative link.
    """

    commit: GitCommitInfo
    pr: PullRequestInfo | None


def link_from_messages(
    commits: list[GitCommitInfo],
    identity: ProviderIdentity,
) -> list[LinkResult]:
    """Apply :func:`stub_pr_from_commit` over *commits*."""
    return [
        LinkResult(commit=c, pr=stub_pr_from_commit(c, identity))
        for c in commits
    ]
