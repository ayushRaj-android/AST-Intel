"""Offline-first commit → PR → review enrichment.

The pipeline takes a raw :class:`HistoryRecord`, links each commit to
its pull-request via the message-linker (free, deterministic, offline),
upgrades stub PRs into fully hydrated objects via the provider API,
fetches inline review threads for the top-N PRs, and finally folds the
results into a list of :class:`DecisionSignal` instances suitable for
display to an AI agent.

Concurrency: PR hydrations and thread fetches happen in a tiny
:class:`concurrent.futures.ThreadPoolExecutor` capped by
*max_workers*.  Each provider HTTP call is itself synchronous; we use
threads (not asyncio) because the surrounding ``QueryEngine`` and CLI
are entirely synchronous.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ast_intel.history.message_linker import LinkResult, link_from_messages
from ast_intel.history.models import (
    DecisionSignal,
    EnrichedCommit,
    EnrichedHistoryRecord,
    HistoryRecord,
    PullRequestInfo,
    ReviewThread,
)
from ast_intel.history.providers.base import HistoryProvider

__all__: list[str] = ["EnrichmentPipeline"]

logger = logging.getLogger(__name__)


class EnrichmentPipeline:
    """Compose offline + online enrichment.

    Args:
        provider: The provider adapter; ``None`` disables online steps.
        max_pr_threads: Maximum number of PRs to fetch threads for.
            Larger values give more signals at the cost of latency.
        max_workers: Thread-pool size for parallel API calls.
    """

    def __init__(
        self,
        provider: HistoryProvider | None,
        *,
        max_pr_threads: int = 5,
        max_workers: int = 4,
    ) -> None:
        self._provider = provider
        self._max_pr_threads = max_pr_threads
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def enrich(self, record: HistoryRecord) -> EnrichedHistoryRecord:
        """Return an enriched view of *record*."""
        warnings: list[str] = []
        commits = list(record.commits)

        # Step 1 — offline message linking (always runs).
        identity = (
            self._provider.identity if self._provider is not None else None
        )
        if identity is not None:
            linked: list[LinkResult] = link_from_messages(commits, identity)
        else:
            linked = [LinkResult(commit=c, pr=None) for c in commits]

        # Step 2 — provider API fallback for un-linked commits.
        if (
            self._provider is not None
            and self._provider.capabilities.commit_to_pr_mapping
        ):
            linked = self._api_link_missing(linked)
        elif self._provider is not None and not any(lr.pr for lr in linked):
            if not self._provider.capabilities.any():
                # Stub provider; suppress noisy warning.
                pass
            else:
                warnings.append(
                    "Provider lacks commit→PR mapping; using message linker only.",
                )

        # Step 3 — hydrate stub PRs.
        if (
            self._provider is not None
            and self._provider.capabilities.pr_hydration
        ):
            linked = self._hydrate_stubs(linked)

        # Step 4 — pick top-N PRs and fetch review threads.
        threads_by_pr: dict[str, list[ReviewThread]] = {}
        if (
            self._provider is not None
            and self._provider.capabilities.inline_threads
        ):
            top_pr_ids = self._top_pr_ids(linked, self._max_pr_threads)
            threads_by_pr = self._fetch_threads(top_pr_ids, record)

        # Step 5 — assemble enriched commits.
        enriched_commits: list[EnrichedCommit] = []
        seen_prs: dict[str, PullRequestInfo] = {}
        for lr in linked:
            pr = lr.pr
            if pr is not None:
                seen_prs.setdefault(pr.id, pr)
            enriched_commits.append(
                EnrichedCommit(
                    commit=lr.commit,
                    pr=pr,
                    review_threads=tuple(
                        threads_by_pr.get(pr.id, []) if pr else [],
                    ),
                ),
            )

        # Step 6 — build decision signals.
        signals: list[DecisionSignal] = []
        if self._provider is not None:
            for pr_id, pr in seen_prs.items():
                signals.extend(
                    self._provider.decision_signals(
                        pr,
                        threads_by_pr.get(pr_id, []),
                    ),
                )

        return EnrichedHistoryRecord(
            record=record,
            enriched_commits=tuple(enriched_commits),
            signals=tuple(signals),
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _api_link_missing(
        self,
        linked: list[LinkResult],
    ) -> list[LinkResult]:
        """Fill in PRs for commits the offline linker missed."""
        if self._provider is None:
            return linked

        provider = self._provider
        missing_idx = [i for i, lr in enumerate(linked) if lr.pr is None]
        if not missing_idx:
            return linked

        def _fetch(idx: int) -> tuple[int, PullRequestInfo | None]:
            sha = linked[idx].commit.sha
            prs = provider.get_pull_requests_for_commit(sha)
            return idx, (prs[0] if prs else None)

        results = self._map_pool(_fetch, missing_idx)
        new_linked = list(linked)
        for idx, pr in results:
            if pr is not None:
                new_linked[idx] = LinkResult(commit=linked[idx].commit, pr=pr)
        return new_linked

    def _hydrate_stubs(
        self,
        linked: list[LinkResult],
    ) -> list[LinkResult]:
        """Upgrade stub PRs (``description == ""``) via the API."""
        if self._provider is None:
            return linked

        provider = self._provider
        # Deduplicate by pr_id — multiple commits share a PR.
        stub_ids: dict[str, int] = {}
        for i, lr in enumerate(linked):
            if lr.pr is not None and not lr.pr.description:
                stub_ids.setdefault(lr.pr.id, i)
        if not stub_ids:
            return linked

        def _fetch(pr_id: str) -> tuple[str, PullRequestInfo | None]:
            return pr_id, provider.get_pull_request(pr_id)

        hydrated: dict[str, PullRequestInfo] = {}
        for pr_id, pr in self._map_pool(_fetch, list(stub_ids)):
            if pr is not None:
                hydrated[pr_id] = pr

        if not hydrated:
            return linked

        out: list[LinkResult] = []
        for lr in linked:
            if lr.pr is not None and lr.pr.id in hydrated:
                out.append(LinkResult(commit=lr.commit, pr=hydrated[lr.pr.id]))
            else:
                out.append(lr)
        return out

    def _fetch_threads(
        self,
        pr_ids: list[str],
        record: HistoryRecord,
    ) -> dict[str, list[ReviewThread]]:
        if self._provider is None or not pr_ids:
            return {}
        provider = self._provider
        symbol = record.symbol

        def _fetch(pr_id: str) -> tuple[str, list[ReviewThread]]:
            threads = provider.get_review_threads(
                pr_id,
                file=symbol.file,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            )
            return pr_id, threads

        return dict(self._map_pool(_fetch, pr_ids))

    @staticmethod
    def _top_pr_ids(linked: list[LinkResult], limit: int) -> list[str]:
        """Return distinct PR ids, preserving commit order, capped at *limit*."""
        seen: list[str] = []
        for lr in linked:
            if lr.pr is None:
                continue
            if lr.pr.id in seen:
                continue
            seen.append(lr.pr.id)
            if len(seen) >= limit:
                break
        return seen

    def _map_pool(self, fn, items):  # type: ignore[no-untyped-def]
        """Parallelize *fn* over *items* with a small thread pool."""
        if not items:
            return []
        workers = min(self._max_workers, len(items))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(fn, items))
