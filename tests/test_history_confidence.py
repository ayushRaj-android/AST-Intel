"""Unit tests for Phase 4 confidence scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ast_intel.history.models import (
    FeedbackEntry,
    FeedbackVerdict,
    SignalKind,
)
from ast_intel.history.scoring.confidence import (
    DEFAULT_CONFIG,
    ConfidenceScorer,
)


_NOW = datetime(2025, 12, 31, tzinfo=UTC)


class TestConfidenceScorer:
    def test_git_blame_is_high_and_no_decay(self) -> None:
        s = ConfidenceScorer()
        recent = (_NOW - timedelta(days=5)).isoformat()
        very_old = (_NOW - timedelta(days=5 * 365)).isoformat()
        assert s.score(SignalKind.GIT_BLAME, cited_at=recent, now=_NOW) == 0.95
        assert s.score(SignalKind.GIT_BLAME, cited_at=very_old, now=_NOW) == 0.95

    def test_pr_description_merged_boost(self) -> None:
        s = ConfidenceScorer()
        date = (_NOW - timedelta(days=30)).isoformat()
        base = s.score(SignalKind.PR_DESCRIPTION, cited_at=date, now=_NOW)
        merged = s.score(
            SignalKind.PR_DESCRIPTION,
            cited_at=date,
            now=_NOW,
            pr_state="merged",
        )
        assert merged - base > 0.05

    def test_rfc_accepted_boost(self) -> None:
        s = ConfidenceScorer()
        date = (_NOW - timedelta(days=180)).isoformat()
        draft = s.score(
            SignalKind.RFC, cited_at=date, now=_NOW, rfc_status="draft",
        )
        accepted = s.score(
            SignalKind.RFC, cited_at=date, now=_NOW, rfc_status="accepted",
        )
        assert accepted > draft

    def test_conventional_commit_boost(self) -> None:
        s = ConfidenceScorer()
        date = (_NOW - timedelta(days=10)).isoformat()
        plain = s.score(
            SignalKind.GIT_LOG,
            cited_at=date,
            now=_NOW,
            commit_subject="fix bug",
        )
        conv = s.score(
            SignalKind.GIT_LOG,
            cited_at=date,
            now=_NOW,
            commit_subject="fix: bug",
        )
        assert conv > plain

    def test_tribal_upvotes_capped(self) -> None:
        s = ConfidenceScorer()
        date = _NOW.isoformat()
        net100 = s.score(
            SignalKind.TRIBAL_KNOWLEDGE,
            cited_at=date,
            now=_NOW,
            upvotes=100,
        )
        # Cap at 0.30 boost over the base 0.50.
        assert net100 <= 0.50 + DEFAULT_CONFIG.tribal_upvote_cap + 1e-9

    def test_feedback_reject_drops_below_floor(self) -> None:
        s = ConfidenceScorer()
        date = (_NOW - timedelta(days=10)).isoformat()
        fb = [
            FeedbackEntry(
                signal_id="x",
                verdict=FeedbackVerdict.REJECT,
                author="a",
            ),
            FeedbackEntry(
                signal_id="x",
                verdict=FeedbackVerdict.REJECT,
                author="a",
            ),
            FeedbackEntry(
                signal_id="x",
                verdict=FeedbackVerdict.REJECT,
                author="a",
            ),
        ]
        score = s.score(
            SignalKind.PR_DESCRIPTION,
            cited_at=date,
            now=_NOW,
            feedback=fb,
        )
        assert score < s.visibility_floor()

    def test_feedback_accept_boosts(self) -> None:
        s = ConfidenceScorer()
        date = (_NOW - timedelta(days=10)).isoformat()
        base = s.score(SignalKind.PR_DESCRIPTION, cited_at=date, now=_NOW)
        with_accept = s.score(
            SignalKind.PR_DESCRIPTION,
            cited_at=date,
            now=_NOW,
            feedback=[
                FeedbackEntry(
                    signal_id="x",
                    verdict=FeedbackVerdict.ACCEPT,
                    author="a",
                ),
            ],
        )
        assert with_accept > base

    def test_score_is_clamped_to_unit_interval(self) -> None:
        s = ConfidenceScorer()
        very_old = (_NOW - timedelta(days=20 * 365)).isoformat()
        score = s.score(SignalKind.TRIBAL_KNOWLEDGE, cited_at=very_old, now=_NOW)
        assert 0.0 <= score <= 1.0
