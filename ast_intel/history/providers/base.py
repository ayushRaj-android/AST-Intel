"""Abstract provider interface + capability descriptors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ast_intel.history.models import (
    DecisionSignal,
    PullRequestInfo,
    ReviewThread,
)
from ast_intel.history.provider_registry import ProviderIdentity

__all__: list[str] = [
    "EMPTY_CAPABILITIES",
    "HistoryProvider",
    "ProviderCapabilities",
]


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Static description of what a provider adapter can do.

    Capability gating lets the enrichment pipeline skip steps the
    provider cannot serve — e.g. avoid printing a "no commit→PR
    mapping" warning when the provider never claimed that ability.
    """

    commit_to_pr_mapping: bool = False
    pr_metadata: bool = False
    review_comments: bool = False
    approvals: bool = False
    rejections: bool = False
    inline_threads: bool = False
    issue_links: bool = False
    pr_hydration: bool = False

    def any(self) -> bool:
        """Return ``True`` if at least one capability is enabled."""
        return any(
            (
                self.commit_to_pr_mapping,
                self.pr_metadata,
                self.review_comments,
                self.approvals,
                self.rejections,
                self.inline_threads,
                self.issue_links,
                self.pr_hydration,
            ),
        )


EMPTY_CAPABILITIES: ProviderCapabilities = ProviderCapabilities()


@dataclass(frozen=True, slots=True)
class _Unused:
    """Placeholder to make :func:`dataclass`-decorated abstract classes lint-clean."""

    _: int = field(default=0)


class HistoryProvider(ABC):
    """Abstract base for code-hosting adapters.

    Implementations need *not* override every method; default
    implementations return empty results and capability flags advertise
    actual functionality.
    """

    identity: ProviderIdentity
    capabilities: ProviderCapabilities = EMPTY_CAPABILITIES

    @abstractmethod
    def initialize(self) -> None:
        """Eagerly validate credentials. Raise on misconfiguration."""

    def get_pull_requests_for_commit(self, sha: str) -> list[PullRequestInfo]:
        """Return PRs containing *sha* (sorted newest-first)."""
        _ = sha
        return []

    def get_pull_request(self, pr_id: str) -> PullRequestInfo | None:
        """Return the full PR metadata, or ``None`` if not found."""
        _ = pr_id
        return None

    def get_review_threads(
        self,
        pr_id: str,
        *,
        file: str = "",
        start_line: int = 0,
        end_line: int = 0,
    ) -> list[ReviewThread]:
        """Return inline review threads, optionally line-range filtered."""
        _ = (pr_id, file, start_line, end_line)
        return []

    def decision_signals(
        self,
        pr: PullRequestInfo,
        threads: list[ReviewThread],
    ) -> list[DecisionSignal]:
        """Convert provider-native data into normalised signals."""
        _ = (pr, threads)
        return []
