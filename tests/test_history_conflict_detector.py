"""Unit tests for Phase 4 conflict detector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ast_intel.history.models import (
    Citation,
    IncidentLink,
    OwnershipRecord,
    OwnershipScore,
    RFCLink,
    ScoredSignal,
    SignalKind,
    SymbolRef,
)
from ast_intel.history.scoring.conflict_detector import ConflictDetector

_NOW = datetime(2025, 12, 31, tzinfo=UTC)


def _symbol() -> SymbolRef:
    from ast_intel.history.models import SymbolKind

    return SymbolRef(
        id="sym",
        label="foo",
        kind=SymbolKind.FUNCTION,
        file="a.py",
        start_line=1,
        end_line=10,
    )


def _ownership(scores: list[tuple[str, float]]) -> OwnershipRecord:
    return OwnershipRecord(
        symbol=_symbol(),
        head_sha="deadbeef" * 5,
        total_commits=10,
        total_lines=10,
        scores=tuple(
            OwnershipScore(
                author_name=name,
                author_email=f"{name}@x",
                commit_count=1,
                last_commit_at=_NOW.isoformat(),
                blame_lines=1,
                commit_frequency=0.0,
                recency_factor=0.0,
                blame_share=0.0,
                score=score,
            )
            for name, score in scores
        ),
    )


class TestConflictDetector:
    def test_ownership_dispute_within_threshold(self) -> None:
        det = ConflictDetector(now=_NOW)
        ownership = _ownership([("alice", 0.41), ("bob", 0.39)])
        reports = det.detect("sym", signals=[], ownership=ownership)
        assert any(r.conflict_type == "ownership_dispute" for r in reports)

    def test_no_dispute_when_gap_exceeds_threshold(self) -> None:
        det = ConflictDetector(now=_NOW)
        ownership = _ownership([("alice", 0.60), ("bob", 0.20)])
        reports = det.detect("sym", signals=[], ownership=ownership)
        assert not any(
            r.conflict_type == "ownership_dispute" for r in reports
        )

    def test_stale_rfc_by_age(self) -> None:
        det = ConflictDetector(now=_NOW)
        old = (_NOW - timedelta(days=365 * 3)).isoformat()
        rfc = RFCLink(
            rfc_id="R1",
            rfc_title="t",
            rfc_url="file://x",
            section="",
            excerpt="",
            symbol_id="sym",
            symbol_label="foo",
            created_at=old,
            status="draft",
        )
        reports = det.detect("sym", signals=[], rfcs=[rfc])
        assert any(r.conflict_type == "stale_rfc" for r in reports)

    def test_stale_rfc_by_status(self) -> None:
        det = ConflictDetector(now=_NOW)
        rfc = RFCLink(
            rfc_id="R1",
            rfc_title="t",
            rfc_url="file://x",
            section="",
            excerpt="",
            symbol_id="sym",
            symbol_label="foo",
            created_at=_NOW.isoformat(),
            status="superseded",
        )
        reports = det.detect("sym", signals=[], rfcs=[rfc])
        assert any(r.conflict_type == "stale_rfc" for r in reports)

    def test_intent_mismatch_detected(self) -> None:
        det = ConflictDetector(now=_NOW)
        rfc_date = (_NOW - timedelta(days=400)).isoformat()
        pr_date = (_NOW - timedelta(days=30)).isoformat()
        rfc = RFCLink(
            rfc_id="R1",
            rfc_title="t",
            rfc_url="file://x",
            section="",
            excerpt="processPayment uses Stripe",
            symbol_id="sym",
            symbol_label="foo",
            created_at=rfc_date,
            status="accepted",
        )
        pr_signal = ScoredSignal(
            id="pr::99",
            signal_type=SignalKind.PR_DESCRIPTION,
            summary="migrate to Adyen",
            detail="Replace Stripe with Adyen.",
            confidence=0.9,
            citation=Citation(
                type="pr", url="https://x/pr/99", author="a", date=pr_date,
            ),
        )
        reports = det.detect("sym", signals=[pr_signal], rfcs=[rfc])
        assert any(r.conflict_type == "intent_mismatch" for r in reports)

    def test_incident_recurrence(self) -> None:
        det = ConflictDetector(now=_NOW)
        d1 = (_NOW - timedelta(days=120)).isoformat()
        d2 = (_NOW - timedelta(days=60)).isoformat()
        incs = [
            IncidentLink(
                incident_id="I1",
                title="t",
                url="",
                severity="sev2",
                status="resolved",
                symbol_id="sym",
                symbol_label="foo",
                occurred_at=d1,
            ),
            IncidentLink(
                incident_id="I2",
                title="t",
                url="",
                severity="sev2",
                status="resolved",
                symbol_id="sym",
                symbol_label="foo",
                occurred_at=d2,
            ),
        ]
        reports = det.detect("sym", signals=[], incidents=incs)
        assert any(r.conflict_type == "incident_recurrence" for r in reports)
