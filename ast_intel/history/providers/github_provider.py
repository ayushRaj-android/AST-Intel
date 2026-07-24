"""GitHub history provider (REST v3)."""

from __future__ import annotations

import logging
import os
from typing import Any

from ast_intel.history.models import (
    Citation,
    DecisionSignal,
    PullRequestInfo,
    ReviewComment,
    ReviewThread,
    SignalKind,
)
from ast_intel.history.provider_registry import ProviderIdentity
from ast_intel.history.providers._http import HttpError, http_get
from ast_intel.history.providers.base import (
    HistoryProvider,
    ProviderCapabilities,
)

__all__: list[str] = ["GitHubProvider"]

logger = logging.getLogger(__name__)


class GitHubProvider(HistoryProvider):
    """GitHub.com history adapter (also works for GitHub Enterprise
    when ``identity.api_base_url`` is overridden)."""

    capabilities: ProviderCapabilities = ProviderCapabilities(
        commit_to_pr_mapping=True,
        pr_metadata=True,
        review_comments=True,
        approvals=True,
        rejections=True,
        inline_threads=True,
        pr_hydration=True,
    )

    def __init__(
        self,
        identity: ProviderIdentity,
        *,
        token: str | None = None,
    ) -> None:
        self.identity = identity
        self._token = token or os.environ.get("GITHUB_TOKEN", "")

    @property
    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _repo_root(self) -> str:
        return (
            f"{self.identity.api_base_url}/repos/"
            f"{self.identity.owner}/{self.identity.repo}"
        )

    def initialize(self) -> None:
        # Token is optional — public repos work unauthenticated, just
        # with a tighter rate limit. We do not raise if missing.
        return

    def get_pull_requests_for_commit(self, sha: str) -> list[PullRequestInfo]:
        url = f"{self._repo_root()}/commits/{sha}/pulls"
        try:
            data = http_get(url, headers=self._headers)
        except HttpError as exc:
            logger.warning("GitHub pulls-for-commit failed: %s", exc)
            return []
        return [self._pr_from_payload(p) for p in (data or [])]

    def get_pull_request(self, pr_id: str) -> PullRequestInfo | None:
        url = f"{self._repo_root()}/pulls/{pr_id}"
        try:
            data = http_get(url, headers=self._headers)
        except HttpError:
            return None
        if not isinstance(data, dict):
            return None
        return self._pr_from_payload(data)

    def get_review_threads(
        self,
        pr_id: str,
        *,
        file: str = "",
        start_line: int = 0,
        end_line: int = 0,
    ) -> list[ReviewThread]:
        url = f"{self._repo_root()}/pulls/{pr_id}/comments"
        try:
            data = http_get(url, headers=self._headers)
        except HttpError:
            return []

        # GitHub returns flat comments; group them by ``in_reply_to_id``.
        roots: dict[int, list[dict[str, Any]]] = {}
        for c in data or []:
            parent = c.get("in_reply_to_id") or c.get("id")
            roots.setdefault(int(parent), []).append(c)

        threads: list[ReviewThread] = []
        for root_id, group in roots.items():
            head = group[0]
            t_file = head.get("path") or ""
            t_line = int(head.get("line") or head.get("original_line") or 0)
            if file:
                if t_file != file:
                    continue
                if (
                    start_line
                    and end_line
                    and t_line
                    and not (start_line <= t_line <= end_line)
                ):
                    continue
            comments = [
                ReviewComment(
                    id=str(c.get("id", "")),
                    author_name=(c.get("user") or {}).get("login", "") or "",
                    author_email="",
                    body=c.get("body", "") or "",
                    created_at=c.get("created_at", "") or "",
                    file=t_file,
                    line=t_line,
                    url=c.get("html_url", "") or "",
                )
                for c in group
            ]
            threads.append(
                ReviewThread(
                    id=str(root_id),
                    file=t_file,
                    line=t_line,
                    resolved=False,
                    comments=tuple(comments),
                ),
            )
        return threads

    def decision_signals(
        self,
        pr: PullRequestInfo,
        threads: list[ReviewThread],
    ) -> list[DecisionSignal]:
        signals: list[DecisionSignal] = []
        if pr.description.strip():
            signals.append(
                DecisionSignal(
                    kind=SignalKind.PR_DESCRIPTION,
                    summary=f"PR #{pr.id}: {pr.title}",
                    detail=pr.description,
                    citation=Citation(
                        type="pull_request",
                        url=pr.url,
                        author=pr.author_name,
                        date=pr.merged_at or pr.created_at,
                        pr_id=pr.id,
                    ),
                    confidence=0.85,
                ),
            )
        for t in threads:
            if not t.comments:
                continue
            head = t.comments[0]
            signals.append(
                DecisionSignal(
                    kind=SignalKind.REVIEW_THREAD,
                    summary=(
                        f"Review on {t.file}:L{t.line}"
                        if t.file
                        else "Review thread"
                    ),
                    detail=head.body,
                    citation=Citation(
                        type="review_thread",
                        url=head.url or pr.url,
                        author=head.author_name,
                        date=head.created_at,
                        pr_id=pr.id,
                    ),
                    confidence=0.7,
                ),
            )
        return signals

    def _pr_from_payload(self, raw: dict[str, Any]) -> PullRequestInfo:
        user = raw.get("user") or {}
        reviewers = tuple(
            (r.get("login") or "")
            for r in (raw.get("requested_reviewers") or [])
        )
        return PullRequestInfo(
            id=str(raw.get("number", "")),
            title=raw.get("title", "") or "",
            description=raw.get("body", "") or "",
            state=raw.get("state", "") or "",
            url=raw.get("html_url", "") or "",
            author_name=user.get("login", "") or "",
            author_email="",
            created_at=raw.get("created_at", "") or "",
            merged_at=raw.get("merged_at", "") or "",
            closed_at=raw.get("closed_at", "") or "",
            reviewers=reviewers,
        )
