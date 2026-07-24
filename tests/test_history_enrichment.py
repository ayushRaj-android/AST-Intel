"""Tests for the enrichment pipeline using a fake provider.

We avoid hitting any network: a small in-memory ``FakeProvider``
satisfies the :class:`HistoryProvider` interface and returns canned
PRs and review threads.  This locks in the hydration + signal-folding
logic that the live Safeguard verification covers end-to-end.
"""

from __future__ import annotations

from ast_intel.history.enrichment import EnrichmentPipeline
from ast_intel.history.models import (
    DecisionSignal,
    GitCommitInfo,
    HistoryRecord,
    PullRequestInfo,
    ReviewComment,
    ReviewThread,
    SignalKind,
    SymbolKind,
    SymbolRef,
)
from ast_intel.history.provider_registry import (
    ProviderIdentity,
    ProviderKind,
)
from ast_intel.history.providers.base import (
    HistoryProvider,
    ProviderCapabilities,
)


_IDENT = ProviderIdentity(
    kind=ProviderKind.AZURE_DEVOPS,
    host="dev.azure.com",
    owner="org",
    repo="repo",
    project="proj",
    web_base_url="https://dev.azure.com/org/proj/_git/repo",
    api_base_url="https://dev.azure.com/org/proj/_apis",
)


class _FakeProvider(HistoryProvider):
    """Minimal stand-in for AdoProvider in unit tests."""

    capabilities: ProviderCapabilities = ProviderCapabilities(
        commit_to_pr_mapping=True,
        pr_metadata=True,
        inline_threads=True,
        pr_hydration=True,
    )

    def __init__(self) -> None:
        self.identity = _IDENT
        self.hydrate_calls: list[str] = []

    def initialize(self) -> None:
        return

    def get_pull_request(self, pr_id: str) -> PullRequestInfo | None:
        self.hydrate_calls.append(pr_id)
        return PullRequestInfo(
            id=pr_id,
            title=f"PR {pr_id} title",
            description=f"why-we-did-it {pr_id}",
            state="completed",
            url=f"{_IDENT.web_base_url}/pullrequest/{pr_id}",
            author_name="Author",
            author_email="a@x",
            created_at="2024-01-01T00:00:00+00:00",
            merged_at="2024-01-02T00:00:00+00:00",
        )

    def get_review_threads(
        self,
        pr_id: str,
        *,
        file: str = "",
        start_line: int = 0,
        end_line: int = 0,
    ) -> list[ReviewThread]:
        _ = (file, start_line, end_line)
        return [
            ReviewThread(
                id=f"{pr_id}-t1",
                file="x.py",
                line=1,
                resolved=False,
                comments=(
                    ReviewComment(
                        id="c1",
                        author_name="Reviewer",
                        author_email="r@x",
                        body="please rename",
                        created_at="2024-01-01T01:00:00+00:00",
                        file="x.py",
                        line=1,
                    ),
                ),
            ),
        ]

    def decision_signals(
        self,
        pr: PullRequestInfo,
        threads: list[ReviewThread],
    ) -> list[DecisionSignal]:
        from ast_intel.history.providers.ado_provider import AdoProvider

        return AdoProvider(_IDENT).decision_signals(pr, threads)


def _record_with_merged_pr(pr_subject: str) -> HistoryRecord:
    ref = SymbolRef(
        id="x.py::f",
        label="f",
        kind=SymbolKind.FUNCTION,
        file="x.py",
        start_line=1,
        end_line=5,
    )
    return HistoryRecord(
        symbol=ref,
        head_sha="h",
        commits=(
            GitCommitInfo(
                sha="a" * 40,
                author_name="A",
                author_email="a@x",
                authored_at="2024-01-02T00:00:00+00:00",
                committed_at="2024-01-02T00:00:00+00:00",
                subject=pr_subject,
            ),
        ),
        blame=(),
        total_lines=5,
    )


def test_enrichment_hydrates_stub_and_emits_signals() -> None:
    provider = _FakeProvider()
    pipeline = EnrichmentPipeline(provider, max_pr_threads=5)
    record = _record_with_merged_pr("Merged PR 99: Fix things")
    out = pipeline.enrich(record)

    # Stub upgraded → full description present.
    assert len(out.enriched_commits) == 1
    pr = out.enriched_commits[0].pr
    assert pr is not None
    assert pr.description.startswith("why-we-did-it")
    # Hydration was called exactly once for the deduped PR id.
    assert provider.hydrate_calls == ["99"]

    # Two signals: pr_description + review thread.
    kinds = [s.kind for s in out.signals]
    assert SignalKind.PR_DESCRIPTION in kinds
    assert SignalKind.REVIEW_THREAD in kinds
    # Every signal carries a citation URL.
    assert all(s.citation.url for s in out.signals)
    assert out.warnings == ()


def test_enrichment_without_provider_yields_no_signals() -> None:
    pipeline = EnrichmentPipeline(None)
    out = pipeline.enrich(_record_with_merged_pr("Merged PR 1: x"))
    assert out.signals == ()
    # Without a provider we have no identity to build PR URLs from,
    # so the offline linker is skipped entirely.
    assert out.enriched_commits[0].pr is None
