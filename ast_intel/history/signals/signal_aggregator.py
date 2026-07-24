"""Signal aggregator — fuse Phase 1-4 evidence into ranked ScoredSignals.

The aggregator takes the raw outputs of:

- :class:`~ast_intel.history.history_builder.HistoryBuilder` (commits)
- :class:`~ast_intel.history.enrichment.EnrichmentPipeline` (PR + threads)
- :class:`~ast_intel.history.signals.rfc_indexer.RFCIndexer`
- :class:`~ast_intel.history.signals.incident_linker.IncidentStore`
- :class:`~ast_intel.history.signals.tribal_knowledge.TribalKnowledgeStore`
- :class:`~ast_intel.history.feedback.FeedbackStore`

…and produces a deterministic, confidence-scored list of
:class:`~ast_intel.history.models.ScoredSignal` records ready for the
agent to cite verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ast_intel.history.enrichment import EnrichmentPipeline
from ast_intel.history.feedback import FeedbackStore
from ast_intel.history.history_builder import HistoryBuilder
from ast_intel.history.models import (
    Citation,
    ConflictReport,
    DecisionSignal,
    EnrichedHistoryRecord,
    IncidentLink,
    OwnershipRecord,
    RFCLink,
    ScoredSignal,
    SignalKind,
    SymbolRef,
    TribalNote,
)
from ast_intel.history.ownership_scorer import OwnershipScorer
from ast_intel.history.scoring.confidence import ConfidenceScorer
from ast_intel.history.scoring.conflict_detector import ConflictDetector
from ast_intel.history.signals.incident_linker import IncidentStore
from ast_intel.history.signals.rfc_indexer import RFCIndexer
from ast_intel.history.signals.tribal_knowledge import TribalKnowledgeStore

__all__: list[str] = [
    "AggregatedContext",
    "SignalAggregator",
]


@dataclass(frozen=True, slots=True)
class AggregatedContext:
    """Everything an agent needs to answer "why does this code exist?"."""

    symbol_id: str
    enriched: EnrichedHistoryRecord
    ownership: OwnershipRecord
    rfcs: tuple[RFCLink, ...]
    incidents: tuple[IncidentLink, ...]
    tribal: tuple[TribalNote, ...]
    signals: tuple[ScoredSignal, ...]
    conflicts: tuple[ConflictReport, ...]
    hidden_signal_count: int = 0


# ---------------------------------------------------------------------------
# region:    --- Aggregator
# ---------------------------------------------------------------------------


class SignalAggregator:
    """Top-level Phase-4 fusion engine.

    The aggregator is *stateless* — repos in, ranked context out — so it
    is safe to instantiate per request inside the MCP server.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        history_builder: HistoryBuilder,
        enrichment: EnrichmentPipeline | None = None,
        rfc_indexer: RFCIndexer | None = None,
        incident_store: IncidentStore | None = None,
        tribal_store: TribalKnowledgeStore | None = None,
        feedback_store: FeedbackStore | None = None,
        confidence: ConfidenceScorer | None = None,
        conflict_detector: ConflictDetector | None = None,
        ownership_scorer: OwnershipScorer | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._history = history_builder
        self._enrichment = enrichment
        self._rfc_indexer = rfc_indexer or RFCIndexer(repo_root)
        self._incidents = incident_store or IncidentStore(repo_root)
        self._tribal = tribal_store or TribalKnowledgeStore(repo_root)
        self._feedback = feedback_store or FeedbackStore(repo_root)
        self._scorer = confidence or ConfidenceScorer()
        self._conflicts = conflict_detector or ConflictDetector()
        self._ownership_scorer = ownership_scorer or OwnershipScorer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(
        self,
        symbol: SymbolRef,
        symbol_id: str,
        *,
        symbol_label: str,
        include_hidden: bool = False,
    ) -> AggregatedContext:
        """Build a fully-scored context for one symbol."""
        record = self._history.build(symbol)
        ownership = self._ownership_scorer.score(
            symbol=record.symbol,
            head_sha=record.head_sha,
            commits=list(record.commits),
            blame=list(record.blame),
        )
        if self._enrichment is not None:
            enriched = self._enrichment.enrich(record)
        else:
            enriched = EnrichedHistoryRecord(
                record=record,
                enriched_commits=(),
                signals=(),
            )

        rfcs = self._rfcs_for_symbol(symbol_label)
        incidents = self._incidents.for_symbol(symbol_id or symbol_label)
        tribal = self._tribal.for_symbol(symbol_id or symbol_label)
        feedback_by_signal = self._feedback.by_signal()

        scored = self._score_all(
            symbol_id=symbol_id,
            enriched=enriched,
            rfcs=rfcs,
            incidents=incidents,
            tribal=tribal,
            feedback_by_signal=feedback_by_signal,
        )

        floor = self._scorer.visibility_floor()
        visible = [s for s in scored if s.confidence >= floor or include_hidden]
        hidden = len(scored) - len(visible)
        visible.sort(key=lambda s: s.confidence, reverse=True)

        conflicts = self._conflicts.detect(
            symbol_id,
            signals=visible,
            ownership=ownership,
            rfcs=list(rfcs),
            incidents=list(incidents),
        )
        # Annotate signals that participate in conflicts.
        if conflicts:
            visible = _attach_conflicts(visible, conflicts)

        return AggregatedContext(
            symbol_id=symbol_id,
            enriched=enriched,
            ownership=ownership,
            rfcs=tuple(rfcs),
            incidents=tuple(incidents),
            tribal=tuple(tribal),
            signals=tuple(visible),
            conflicts=tuple(conflicts),
            hidden_signal_count=hidden,
        )

    # ------------------------------------------------------------------
    # Source-by-source scoring
    # ------------------------------------------------------------------

    def _rfcs_for_symbol(self, symbol_label: str) -> list[RFCLink]:
        # Re-index on every call so newly-added RFCs are picked up.
        # The indexer is cheap (glob + regex) so this is fine for v1.
        index = self._rfc_indexer.index()
        return [link for link in index.links if link.symbol_label == symbol_label]

    def _score_all(
        self,
        *,
        symbol_id: str,
        enriched: EnrichedHistoryRecord,
        rfcs: list[RFCLink],
        incidents: list[IncidentLink],
        tribal: list[TribalNote],
        feedback_by_signal: dict[str, list],
    ) -> list[ScoredSignal]:
        out: list[ScoredSignal] = []

        # 1. Enriched PR / review / commit signals from Phase 2-3.
        for sig in enriched.signals:
            sig_id = _enriched_signal_id(sig)
            out.append(
                ScoredSignal(
                    id=sig_id,
                    signal_type=sig.kind,
                    summary=sig.summary,
                    detail=sig.detail,
                    confidence=self._scorer.score(
                        sig.kind,
                        cited_at=sig.citation.date,
                        feedback=feedback_by_signal.get(sig_id, []),
                    ),
                    citation=sig.citation,
                    symbol_id=symbol_id,
                ),
            )

        # 2. Raw commit log fallback (always present if there are commits).
        for commit in enriched.record.commits:
            sig_id = f"commit::{commit.sha}"
            out.append(
                ScoredSignal(
                    id=sig_id,
                    signal_type=SignalKind.GIT_LOG,
                    summary=commit.subject,
                    detail=commit.body,
                    confidence=self._scorer.score(
                        SignalKind.GIT_LOG,
                        cited_at=commit.committed_at,
                        commit_subject=commit.subject,
                        feedback=feedback_by_signal.get(sig_id, []),
                    ),
                    citation=Citation(
                        type="commit",
                        url="",
                        author=commit.author_name,
                        date=commit.committed_at,
                        sha=commit.sha,
                    ),
                    symbol_id=symbol_id,
                ),
            )

        # 3. RFCs.
        for rfc in rfcs:
            sig_id = f"rfc::{rfc.rfc_id}"
            out.append(
                ScoredSignal(
                    id=sig_id,
                    signal_type=SignalKind.RFC,
                    summary=f"{rfc.rfc_title} (§{rfc.section})"
                    if rfc.section
                    else rfc.rfc_title,
                    detail=rfc.excerpt,
                    confidence=self._scorer.score(
                        SignalKind.RFC,
                        cited_at=rfc.created_at,
                        rfc_status=rfc.status,
                        feedback=feedback_by_signal.get(sig_id, []),
                    ),
                    citation=Citation(
                        type="rfc",
                        url=rfc.rfc_url,
                        author=rfc.author,
                        date=rfc.created_at,
                    ),
                    symbol_id=symbol_id,
                ),
            )

        # 4. Incidents.
        for inc in incidents:
            sig_id = f"incident::{inc.incident_id}"
            out.append(
                ScoredSignal(
                    id=sig_id,
                    signal_type=SignalKind.INCIDENT,
                    summary=f"[{inc.severity}] {inc.title}",
                    detail=(
                        f"status={inc.status} "
                        f"fix_commit={inc.fix_commit or '-'} "
                        f"postmortem={inc.postmortem_url or '-'}"
                    ),
                    confidence=self._scorer.score(
                        SignalKind.INCIDENT,
                        cited_at=inc.occurred_at,
                        has_postmortem=bool(inc.postmortem_url),
                        feedback=feedback_by_signal.get(sig_id, []),
                    ),
                    citation=Citation(
                        type="incident",
                        url=inc.url,
                        author="",
                        date=inc.occurred_at,
                    ),
                    symbol_id=symbol_id,
                ),
            )

        # 5. Tribal notes.
        for note in tribal:
            sig_id = note.id
            out.append(
                ScoredSignal(
                    id=sig_id,
                    signal_type=SignalKind.TRIBAL_KNOWLEDGE,
                    summary=f"Note by {note.author}",
                    detail=note.text,
                    confidence=self._scorer.score(
                        SignalKind.TRIBAL_KNOWLEDGE,
                        cited_at=note.created_at,
                        upvotes=note.upvotes,
                        downvotes=note.downvotes,
                        feedback=feedback_by_signal.get(sig_id, []),
                    ),
                    citation=Citation(
                        type="tribal",
                        url="",
                        author=note.author,
                        date=note.created_at,
                    ),
                    symbol_id=symbol_id,
                ),
            )

        # Apply feedback "edit" verdicts last: they rewrite signal text
        # without touching confidence.
        out = [_apply_edit_feedback(s, feedback_by_signal) for s in out]
        return out


# endregion: --- Aggregator


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _enriched_signal_id(sig: DecisionSignal) -> str:
    """Stable id derived from kind + citation so feedback survives reruns."""
    cit = sig.citation
    parts = [sig.kind.value, cit.type, cit.pr_id, cit.sha, cit.url]
    return "::".join(p for p in parts if p)


def _attach_conflicts(
    signals: list[ScoredSignal],
    conflicts: list[ConflictReport],
) -> list[ScoredSignal]:
    by_id: dict[str, set[str]] = {}
    for c in conflicts:
        for sid in c.signal_ids:
            by_id.setdefault(sid, set()).update(
                other for other in c.signal_ids if other != sid
            )
    if not by_id:
        return signals
    out: list[ScoredSignal] = []
    for s in signals:
        peers = by_id.get(s.id)
        if peers:
            out.append(
                ScoredSignal(
                    id=s.id,
                    signal_type=s.signal_type,
                    summary=s.summary,
                    detail=s.detail,
                    confidence=s.confidence,
                    citation=s.citation,
                    symbol_id=s.symbol_id,
                    conflicts_with=tuple(sorted(peers)),
                ),
            )
        else:
            out.append(s)
    return out


def _apply_edit_feedback(
    signal: ScoredSignal,
    feedback_by_signal: dict[str, list],
) -> ScoredSignal:
    entries = feedback_by_signal.get(signal.id, [])
    if not entries:
        return signal
    # Latest EDIT wins.
    latest_edit = None
    for entry in entries:
        if (
            getattr(entry, "verdict", None) is not None
            and entry.verdict.value == "edit"
            and entry.replacement_text
        ):
            latest_edit = entry
    if latest_edit is None:
        return signal
    return ScoredSignal(
        id=signal.id,
        signal_type=signal.signal_type,
        summary=signal.summary,
        detail=latest_edit.replacement_text,
        confidence=signal.confidence,
        citation=signal.citation,
        symbol_id=signal.symbol_id,
        conflicts_with=signal.conflicts_with,
    )


# Silence linter warning about unused datetime/UTC — kept for future use.
_ = (UTC, datetime)


# endregion: --- Helpers
