"""Heuristic conflict detection across scored signals.

Phase 4 spec defines four conflict types:

- ``ownership_dispute``    — two authors within 0.05 of each other
- ``intent_mismatch``      — RFC text contradicts a later PR description
- ``stale_rfc``            — RFC linked but status "superseded" / > 2y old
- ``incident_recurrence``  — same symbol appears in 2+ incidents within 6mo

The detector returns :class:`ConflictReport` objects suitable for
side-by-side display.  Full semantic conflict detection (NLP) is
deferred per the roadmap.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ast_intel.history.models import (
    ConflictReport,
    IncidentLink,
    OwnershipRecord,
    RFCLink,
    ScoredSignal,
    SignalKind,
)

__all__: list[str] = ["ConflictDetector"]


_OWNERSHIP_DELTA: float = 0.05
_STALE_RFC_YEARS: float = 2.0
_RECURRENCE_WINDOW = timedelta(days=183)  # ~6 months
_MIN_RECURRENCES = 2


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ConflictDetector:
    """Stateless conflict-detection helper."""

    def __init__(self, *, now: datetime | None = None) -> None:
        self._now = now or datetime.now(tz=UTC)

    # ------------------------------------------------------------------
    # Public — symbol-scoped batch detection
    # ------------------------------------------------------------------

    def detect(
        self,
        symbol_id: str,
        *,
        signals: list[ScoredSignal],
        ownership: OwnershipRecord | None = None,
        rfcs: list[RFCLink] | None = None,
        incidents: list[IncidentLink] | None = None,
    ) -> list[ConflictReport]:
        """Return every conflict detected for *symbol_id*."""
        reports: list[ConflictReport] = []
        if ownership is not None:
            reports.extend(
                self._ownership_disputes(symbol_id, ownership),
            )
        if rfcs is not None:
            reports.extend(self._stale_rfcs(symbol_id, rfcs))
            reports.extend(
                self._intent_mismatches(symbol_id, rfcs, signals),
            )
        if incidents is not None:
            reports.extend(self._recurring_incidents(symbol_id, incidents))
        return reports

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------

    def _ownership_disputes(
        self,
        symbol_id: str,
        ownership: OwnershipRecord,
    ) -> list[ConflictReport]:
        scores = sorted(ownership.scores, key=lambda s: s.score, reverse=True)
        if len(scores) < 2:  # noqa: PLR2004
            return []
        top, runner = scores[0], scores[1]
        if abs(top.score - runner.score) > _OWNERSHIP_DELTA:
            return []
        return [
            ConflictReport(
                symbol_id=symbol_id,
                signal_ids=(
                    f"ownership::{top.author_email}",
                    f"ownership::{runner.author_email}",
                ),
                conflict_type="ownership_dispute",
                description=(
                    f"{top.author_name} ({top.score:.2f}) and "
                    f"{runner.author_name} ({runner.score:.2f}) have "
                    "ownership scores within 0.05 — co-ownership likely."
                ),
                recommended_resolution=(
                    "Consult both authors before changing this symbol."
                ),
            ),
        ]

    def _stale_rfcs(
        self,
        symbol_id: str,
        rfcs: list[RFCLink],
    ) -> list[ConflictReport]:
        reports: list[ConflictReport] = []
        for rfc in rfcs:
            status = (rfc.status or "").lower()
            age_years = 0.0
            dt = _parse_iso(rfc.created_at)
            if dt is not None:
                age_years = (self._now - dt).total_seconds() / (
                    365.25 * 86400.0
                )
            is_stale_status = status == "superseded"
            is_stale_age = age_years > _STALE_RFC_YEARS and status != "accepted"
            if not (is_stale_status or is_stale_age):
                continue
            reports.append(
                ConflictReport(
                    symbol_id=symbol_id,
                    signal_ids=(f"rfc::{rfc.rfc_id}",),
                    conflict_type="stale_rfc",
                    description=(
                        f"RFC {rfc.rfc_id!r} (status={status or 'unknown'}, "
                        f"age={age_years:.1f}y) may no longer reflect "
                        f"current behaviour of {rfc.symbol_label}."
                    ),
                    recommended_resolution=(
                        "Verify against recent PRs before citing this RFC."
                    ),
                ),
            )
        return reports

    def _intent_mismatches(
        self,
        symbol_id: str,
        rfcs: list[RFCLink],
        signals: list[ScoredSignal],
    ) -> list[ConflictReport]:
        """Flag RFCs whose excerpt is contradicted by a newer PR."""
        reports: list[ConflictReport] = []
        prs_by_date: list[ScoredSignal] = [
            s
            for s in signals
            if s.signal_type == SignalKind.PR_DESCRIPTION
        ]
        prs_by_date.sort(
            key=lambda s: _parse_iso(s.citation.date) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        if not prs_by_date or not rfcs:
            return reports

        latest_pr = prs_by_date[0]
        for rfc in rfcs:
            rfc_dt = _parse_iso(rfc.created_at)
            pr_dt = _parse_iso(latest_pr.citation.date)
            if rfc_dt is None or pr_dt is None or pr_dt <= rfc_dt:
                continue
            if self._contradicts(rfc.excerpt, latest_pr.detail):
                reports.append(
                    ConflictReport(
                        symbol_id=symbol_id,
                        signal_ids=(f"rfc::{rfc.rfc_id}", latest_pr.id),
                        conflict_type="intent_mismatch",
                        description=(
                            f"RFC {rfc.rfc_id!r} describes one design "
                            f"but a newer PR ({latest_pr.citation.pr_id}) "
                            "appears to describe a different one."
                        ),
                        recommended_resolution=(
                            "Update the RFC or reconcile the change."
                        ),
                    ),
                )
        return reports

    def _recurring_incidents(
        self,
        symbol_id: str,
        incidents: list[IncidentLink],
    ) -> list[ConflictReport]:
        if len(incidents) < _MIN_RECURRENCES:
            return []
        dated = [(inc, _parse_iso(inc.occurred_at)) for inc in incidents]
        dated = [(i, d) for i, d in dated if d is not None]
        if len(dated) < _MIN_RECURRENCES:
            return []
        dated.sort(key=lambda t: t[1])  # type: ignore[arg-type]
        reports: list[ConflictReport] = []
        for i in range(len(dated) - 1):
            inc_a, date_a = dated[i]
            inc_b, date_b = dated[i + 1]
            if date_b is None or date_a is None:
                continue
            if date_b - date_a > _RECURRENCE_WINDOW:
                continue
            reports.append(
                ConflictReport(
                    symbol_id=symbol_id,
                    signal_ids=(
                        f"incident::{inc_a.incident_id}",
                        f"incident::{inc_b.incident_id}",
                    ),
                    conflict_type="incident_recurrence",
                    description=(
                        f"{inc_a.incident_id} and {inc_b.incident_id} hit "
                        f"this symbol within {_RECURRENCE_WINDOW.days} days."
                    ),
                    recommended_resolution=(
                        "Investigate root cause — likely needs structural fix."
                    ),
                ),
            )
        return reports

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _NEGATION_CUES = re.compile(
        r"\b(now|migrate|replac|deprecat|remov|switch)\w*\b",
        re.IGNORECASE,
    )

    def _contradicts(self, rfc_text: str, pr_text: str) -> bool:
        """Cheap heuristic: PR mentions migration/replacement keywords."""
        if not rfc_text or not pr_text:
            return False
        return bool(self._NEGATION_CUES.search(pr_text))
