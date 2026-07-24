"""Azure DevOps history provider.

Uses the REST 7.1 API:

- ``POST /_apis/git/pullrequestquery``  → commits → PR ids
- ``GET  /_apis/git/repositories/{repo}/pullrequests/{id}`` → metadata
- ``GET  /_apis/git/repositories/{repo}/pullRequests/{id}/threads`` → reviews

Authentication is HTTP Basic with an empty user and the user's PAT as
the password. The PAT is read from the ``AZURE_DEVOPS_PAT`` env var or
provided explicitly to the constructor.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any
from urllib.parse import quote

from ast_intel.history.models import (
    Citation,
    DecisionSignal,
    PullRequestInfo,
    ReviewComment,
    ReviewThread,
    SignalKind,
)
from ast_intel.history.provider_registry import ProviderIdentity
from ast_intel.history.providers._http import HttpError, http_get, http_post
from ast_intel.history.providers.base import (
    HistoryProvider,
    ProviderCapabilities,
)

__all__: list[str] = ["AdoProvider"]

logger = logging.getLogger(__name__)

_API_VERSION: str = "7.1-preview.1"


class AdoProvider(HistoryProvider):
    """Azure DevOps history adapter."""

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
        pat: str | None = None,
    ) -> None:
        self.identity = identity
        self._pat = pat or os.environ.get("AZURE_DEVOPS_PAT", "")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(f":{self._pat}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _repo_root(self) -> str:
        """Return ``<api_base>/git/repositories/<repo>``."""
        repo = quote(self.identity.repo, safe="")
        return f"{self.identity.api_base_url}/git/repositories/{repo}"

    def initialize(self) -> None:
        if not self._pat:
            msg = (
                "Azure DevOps requires a Personal Access Token. "
                "Set AZURE_DEVOPS_PAT or pass `pat=...`."
            )
            raise RuntimeError(msg)

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def get_pull_requests_for_commit(self, sha: str) -> list[PullRequestInfo]:
        url = (
            f"{self._repo_root()}/pullrequestquery?api-version={_API_VERSION}"
        )
        payload = {"queries": [{"items": [sha], "type": "lastMergeCommit"}]}
        try:
            data = http_post(url, payload, headers=self._auth_header)
        except HttpError as exc:
            logger.warning("ADO pullrequestquery failed: %s", exc)
            return []

        out: list[PullRequestInfo] = []
        for query in (data or {}).get("results", []):
            for pr_list in query.values():
                for pr in pr_list:
                    out.append(self._pr_from_payload(pr))
        return out

    def get_pull_request(self, pr_id: str) -> PullRequestInfo | None:
        url = (
            f"{self._repo_root()}/pullrequests/{pr_id}"
            f"?api-version={_API_VERSION}"
        )
        try:
            data = http_get(url, headers=self._auth_header)
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
        url = (
            f"{self._repo_root()}/pullRequests/{pr_id}/threads"
            f"?api-version={_API_VERSION}"
        )
        try:
            data = http_get(url, headers=self._auth_header)
        except HttpError:
            return []

        threads: list[ReviewThread] = []
        for t in (data or {}).get("value", []):
            ctx = t.get("threadContext") or {}
            t_file = (ctx.get("filePath") or "").lstrip("/")
            r_left = ctx.get("rightFileStart") or {}
            t_line = int(r_left.get("line", 0))
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
            comments: list[ReviewComment] = []
            for c in t.get("comments") or []:
                if c.get("commentType") == "system":
                    continue
                author = (c.get("author") or {}).get("displayName") or ""
                email = (c.get("author") or {}).get("uniqueName") or ""
                comments.append(
                    ReviewComment(
                        id=str(c.get("id", "")),
                        author_name=author,
                        author_email=email,
                        body=c.get("content", "") or "",
                        created_at=c.get("publishedDate", "") or "",
                        file=t_file,
                        line=t_line,
                        url="",
                    ),
                )
            threads.append(
                ReviewThread(
                    id=str(t.get("id", "")),
                    file=t_file,
                    line=t_line,
                    resolved=(t.get("status") or "").lower() == "closed",
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
                        url=pr.url,
                        author=head.author_name,
                        date=head.created_at,
                        pr_id=pr.id,
                    ),
                    confidence=0.7,
                ),
            )
        return signals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pr_from_payload(self, raw: dict[str, Any]) -> PullRequestInfo:
        author = raw.get("createdBy") or {}
        pr_id = str(raw.get("pullRequestId", ""))
        web = f"{self.identity.web_base_url}/pullrequest/{pr_id}"
        return PullRequestInfo(
            id=pr_id,
            title=raw.get("title", "") or "",
            description=raw.get("description", "") or "",
            state=(raw.get("status") or "").lower(),
            url=web,
            author_name=author.get("displayName", "") or "",
            author_email=author.get("uniqueName", "") or "",
            created_at=raw.get("creationDate", "") or "",
            merged_at=raw.get("closedDate", "") or "",
            reviewers=tuple(
                (r.get("displayName") or "")
                for r in (raw.get("reviewers") or [])
            ),
        )
