"""Unified confidence model — Phase 4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ast_intel.history.models import (
    FeedbackEntry,
    FeedbackVerdict,
    SignalKind,
)

__all__: list[str] = [
    "ConfidenceConfig",
    "ConfidenceScorer",
    "DEFAULT_CONFIG",
]


# ---------------------------------------------------------------------------
# region:    --- Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Tuning:
    """Per-signal base confidence + linear decay per year."""

    base: float
    decay_per_year: float


@dataclass(frozen=True, slots=True)
class ConfidenceConfig:
    """Tunable confidence parameters.

    Mirrors the table in the Phase 4 roadmap.  All numbers live in the
    inclusive range ``[0.0, 1.0]``.
    """

    by_kind: dict[SignalKind, _Tuning]
    rfc_accepted_boost: float = 0.20
    rfc_non_accepted_decay: float = 0.10
    pr_merged_boost: float = 0.10
    conventional_commit_boost: float = 0.10
    incident_with_postmortem_boost: float = 0.10
    tribal_upvote_step: float = 0.05
    tribal_upvote_cap: float = 0.30
    feedback_accept: float = 0.10
    feedback_reject: float = -0.30
    feedback_outdated: float = -0.20
    visibility_floor: float = 0.10


DEFAULT_CONFIG: ConfidenceConfig = ConfidenceConfig(
    by_kind={
        SignalKind.GIT_BLAME: _Tuning(base=0.95, decay_per_year=0.0),
        SignalKind.GIT_LOG: _Tuning(base=0.70, decay_per_year=0.05),
        SignalKind.COMMIT_MESSAGE: _Tuning(base=0.70, decay_per_year=0.05),
        SignalKind.PR_DESCRIPTION: _Tuning(base=0.85, decay_per_year=0.03),
        SignalKind.REVIEW_THREAD: _Tuning(base=0.80, decay_per_year=0.05),
        SignalKind.APPROVAL: _Tuning(base=0.85, decay_per_year=0.05),
        SignalKind.REJECTION: _Tuning(base=0.80, decay_per_year=0.05),
        SignalKind.RFC: _Tuning(base=0.75, decay_per_year=0.00),
        SignalKind.INCIDENT: _Tuning(base=0.90, decay_per_year=0.02),
        SignalKind.TRIBAL_KNOWLEDGE: _Tuning(base=0.50, decay_per_year=0.15),
    },
)


# endregion: --- Configuration


# ---------------------------------------------------------------------------
# region:    --- Scorer
# ---------------------------------------------------------------------------


_CONVENTIONAL_PREFIXES: tuple[str, ...] = (
    "feat:", "fix:", "docs:", "style:", "refactor:",
    "perf:", "test:", "build:", "ci:", "chore:", "revert:",
)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _years_since(iso: str, now: datetime) -> float:
    parsed = _parse_iso(iso)
    if parsed is None:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (now - parsed).total_seconds() / (365.25 * 86400.0))


class ConfidenceScorer:
    """Compute confidence in ``[0.0, 1.0]`` for any Phase 4 signal."""

    def __init__(self, config: ConfidenceConfig | None = None) -> None:
        self._config = config or DEFAULT_CONFIG

    def score(
        self,
        kind: SignalKind,
        *,
        cited_at: str = "",
        now: datetime | None = None,
        # Per-source boosts (optional).
        pr_state: str = "",
        commit_subject: str = "",
        rfc_status: str = "",
        has_postmortem: bool = False,
        upvotes: int = 0,
        downvotes: int = 0,
        feedback: list[FeedbackEntry] | None = None,
    ) -> float:
        """Return the final confidence score."""
        ref_now = now or datetime.now(tz=UTC)
        tuning = self._config.by_kind.get(kind)
        if tuning is None:
            return 0.5  # safe default for unmapped kinds

        score = tuning.base
        years = _years_since(cited_at, ref_now)
        score -= tuning.decay_per_year * years

        cfg = self._config
        if kind == SignalKind.RFC:
            status = (rfc_status or "").lower()
            if status == "accepted":
                score += cfg.rfc_accepted_boost
            else:
                score -= cfg.rfc_non_accepted_decay * years

        if kind == SignalKind.PR_DESCRIPTION and pr_state:
            # Both "merged" (GitHub) and "completed" (Azure DevOps)
            # indicate the change actually shipped, not just an open
            # proposal.  Boost only those terminal states.
            if pr_state.lower() in {"merged", "completed"}:
                score += cfg.pr_merged_boost

        if kind in {SignalKind.GIT_LOG, SignalKind.COMMIT_MESSAGE}:
            subject = (commit_subject or "").lower().strip()
            if any(subject.startswith(p) for p in _CONVENTIONAL_PREFIXES):
                score += cfg.conventional_commit_boost

        if kind == SignalKind.INCIDENT and has_postmortem:
            score += cfg.incident_with_postmortem_boost

        if kind == SignalKind.TRIBAL_KNOWLEDGE:
            net = max(0, upvotes - downvotes)
            score += min(net * cfg.tribal_upvote_step, cfg.tribal_upvote_cap)

        # Feedback adjustments.
        for fb in feedback or []:
            if fb.verdict == FeedbackVerdict.ACCEPT:
                score += cfg.feedback_accept
            elif fb.verdict == FeedbackVerdict.REJECT:
                score += cfg.feedback_reject
            elif fb.verdict == FeedbackVerdict.OUTDATED:
                score += cfg.feedback_outdated
            # EDIT: neutral on confidence; the edit replaces text downstream.

        return max(0.0, min(1.0, score))

    def visibility_floor(self) -> float:
        """Return the threshold below which a signal is hidden."""
        return self._config.visibility_floor


# endregion: --- Scorer
