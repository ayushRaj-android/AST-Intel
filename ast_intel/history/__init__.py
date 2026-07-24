"""Code History Intelligence — shared Python engine.

This package extracts, scores, and enriches the version-control history
of a symbol so AI agents can answer *who*, *when*, and *why* questions
with verifiable citations.

It is provider-agnostic: a thin :class:`HistoryProvider` interface lets
the same builder work against git alone, GitHub, Azure DevOps,
Bitbucket, or AWS CodeCommit.

Public API:

- :class:`HistoryBuilder` — top-level entry point used by
  :class:`~ast_intel.core._query_engine.QueryEngine`.
- :class:`OwnershipScorer` — pure-function ownership ranking.
- :class:`EnrichmentPipeline` — offline-first commit → PR → review
  signal expansion.
- Dataclass models exported from :mod:`ast_intel.history.models`.
"""

from __future__ import annotations

from ast_intel.history.enrichment import EnrichmentPipeline
from ast_intel.history.feedback import FeedbackStore
from ast_intel.history.history_builder import HistoryBuilder
from ast_intel.history.models import (
    BlameHunk,
    ConflictReport,
    DecisionSignal,
    EnrichedCommit,
    EnrichedHistoryRecord,
    FeedbackEntry,
    FeedbackVerdict,
    GitCommitInfo,
    HistoryRecord,
    IncidentLink,
    OwnershipRecord,
    OwnershipScore,
    PullRequestInfo,
    RFCLink,
    ReviewComment,
    ReviewThread,
    ScoredSignal,
    SignalKind,
    SymbolRef,
    TribalNote,
)
from ast_intel.history.ownership_scorer import OwnershipScorer
from ast_intel.history.scoring.confidence import (
    DEFAULT_CONFIG,
    ConfidenceConfig,
    ConfidenceScorer,
)
from ast_intel.history.scoring.conflict_detector import ConflictDetector
from ast_intel.history.signals import (
    IncidentStore,
    RFCIndexer,
    RFCIndexResult,
    TribalKnowledgeStore,
)
from ast_intel.history.signals.signal_aggregator import (
    AggregatedContext,
    SignalAggregator,
)

__all__: list[str] = [
    "DEFAULT_CONFIG",
    "AggregatedContext",
    "BlameHunk",
    "ConfidenceConfig",
    "ConfidenceScorer",
    "ConflictDetector",
    "ConflictReport",
    "DecisionSignal",
    "EnrichedCommit",
    "EnrichedHistoryRecord",
    "EnrichmentPipeline",
    "FeedbackEntry",
    "FeedbackStore",
    "FeedbackVerdict",
    "GitCommitInfo",
    "HistoryBuilder",
    "HistoryRecord",
    "IncidentLink",
    "IncidentStore",
    "OwnershipRecord",
    "OwnershipScore",
    "OwnershipScorer",
    "PullRequestInfo",
    "RFCIndexResult",
    "RFCIndexer",
    "RFCLink",
    "ReviewComment",
    "ReviewThread",
    "ScoredSignal",
    "SignalAggregator",
    "SignalKind",
    "SymbolRef",
    "TribalKnowledgeStore",
    "TribalNote",
]
