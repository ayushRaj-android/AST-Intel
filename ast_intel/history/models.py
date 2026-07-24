"""Code-history dataclasses.

All models are :func:`dataclasses.dataclass` instances declared with
``frozen=True, slots=True`` so they are hashable, immutable, and
memory-cheap. They mirror the TypeScript types used by the
``code-history-intel`` VS Code extension (Phase 1 & 2 of the roadmap)
so the Python engine can be a drop-in replacement.

Conventions:

- Timestamps are ISO-8601 strings (``2024-09-15T13:24:01Z``) to keep
  responses deterministic and trivially JSON-encodable.
- Confidence scores live in the inclusive range ``[0.0, 1.0]``.
- Author identities use the e-mail field for canonical equality; the
  display ``name`` is for presentation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


# ---------------------------------------------------------------------------
# region:    --- Symbol & Git primitives
# ---------------------------------------------------------------------------


class SymbolKind(StrEnum):
    """Coarse category of a code symbol.

    These map onto :class:`~ast_intel.models.graph_model.NodeKind`
    via :func:`ast_intel.history.history_builder.node_kind_to_symbol_kind`.
    """

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    INTERFACE = "interface"
    MODULE = "module"
    FILE = "file"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """Pointer to a symbol inside a repository.

    Combined with ``start_line``/``end_line`` (1-based, inclusive) this
    fully identifies the byte-range used by :command:`git log -L` and
    :command:`git blame`.
    """

    id: str
    label: str
    kind: SymbolKind
    file: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class GitCommitInfo:
    """A single commit derived from ``git log``.

    Attributes:
        sha: Full 40-char commit SHA.
        author_name: Display name from ``git config user.name``.
        author_email: Canonical author identity.
        authored_at: Author date, ISO-8601 UTC.
        committed_at: Committer date, ISO-8601 UTC.
        subject: First line of the commit message.
        body: Remainder of the commit message (may be empty).
        files_changed: Workspace-relative paths touched by the commit.
        lines_added: Total lines added across *files_changed*.
        lines_removed: Total lines removed.
    """

    sha: str
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    subject: str
    body: str = ""
    files_changed: tuple[str, ...] = ()
    lines_added: int = 0
    lines_removed: int = 0


@dataclass(frozen=True, slots=True)
class BlameHunk:
    """A contiguous range of lines attributed to a single commit.

    The ranges use 1-based inclusive endpoints — consistent with
    :class:`~ast_intel.models.ast_node.Span`.
    """

    sha: str
    author_name: str
    author_email: str
    authored_at: str
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        """Number of lines (inclusive)."""
        return self.end_line - self.start_line + 1


# endregion: --- Symbol & Git primitives


# ---------------------------------------------------------------------------
# region:    --- Aggregate history records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    """All raw history evidence for one :class:`SymbolRef`.

    Built by :class:`~ast_intel.history.history_builder.HistoryBuilder`
    before any provider enrichment.
    """

    symbol: SymbolRef
    head_sha: str
    commits: tuple[GitCommitInfo, ...]
    blame: tuple[BlameHunk, ...]
    total_lines: int


@dataclass(frozen=True, slots=True)
class OwnershipScore:
    """Per-author ownership components.

    The final ``score`` is::

        0.40 * commit_frequency
        + 0.35 * recency_factor
        + 0.25 * blame_share

    See :mod:`ast_intel.history.ownership_scorer` for definitions.
    """

    author_name: str
    author_email: str
    commit_count: int
    last_commit_at: str
    blame_lines: int
    commit_frequency: float
    recency_factor: float
    blame_share: float
    score: float


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    """Sorted ownership ranking for a :class:`SymbolRef`."""

    symbol: SymbolRef
    head_sha: str
    total_commits: int
    total_lines: int
    scores: tuple[OwnershipScore, ...]


# endregion: --- Aggregate history records


# ---------------------------------------------------------------------------
# region:    --- Pull request & review evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """Provider-neutral pull-request metadata.

    Stub instances (``description == ""``) are produced by the offline
    message-linker and later upgraded by
    :func:`ast_intel.history.enrichment.hydrate_prs`.
    """

    id: str
    title: str
    description: str
    state: str
    url: str
    author_name: str
    author_email: str
    created_at: str
    merged_at: str = ""
    closed_at: str = ""
    reviewers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """A single in-line review comment."""

    id: str
    author_name: str
    author_email: str
    body: str
    created_at: str
    file: str = ""
    line: int = 0
    url: str = ""


@dataclass(frozen=True, slots=True)
class ReviewThread:
    """A thread of inline review comments on a single line."""

    id: str
    file: str
    line: int
    resolved: bool
    comments: tuple[ReviewComment, ...] = ()


# endregion: --- Pull request & review evidence


# ---------------------------------------------------------------------------
# region:    --- Decision-context signals
# ---------------------------------------------------------------------------


class SignalKind(StrEnum):
    """Categorical kind of an evidence card surfaced to the agent."""

    PR_DESCRIPTION = "pr_description"
    REVIEW_THREAD = "review_thread"
    COMMIT_MESSAGE = "commit_message"
    APPROVAL = "approval"
    REJECTION = "rejection"
    # Phase 4 — richer signal sources.
    RFC = "rfc"
    INCIDENT = "incident"
    TRIBAL_KNOWLEDGE = "tribal_knowledge"
    GIT_BLAME = "git_blame"
    GIT_LOG = "git_log"


@dataclass(frozen=True, slots=True)
class Citation:
    """A verifiable pointer to the source of a :class:`DecisionSignal`.

    Every signal *must* carry a citation — the engine never asserts
    a claim it cannot back with a URL or SHA. This is enforced by
    construction (see :class:`DecisionSignal`).
    """

    type: str
    url: str
    author: str
    date: str
    sha: str = ""
    pr_id: str = ""


@dataclass(frozen=True, slots=True)
class DecisionSignal:
    """A single piece of evidence explaining *why* code looks the way it does."""

    kind: SignalKind
    summary: str
    detail: str
    citation: Citation
    confidence: float = 0.5


# endregion: --- Decision-context signals


# ---------------------------------------------------------------------------
# region:    --- Enriched outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnrichedCommit:
    """A commit annotated with its associated pull-request, if any."""

    commit: GitCommitInfo
    pr: PullRequestInfo | None = None
    review_threads: tuple[ReviewThread, ...] = ()


@dataclass(frozen=True, slots=True)
class EnrichedHistoryRecord:
    """A :class:`HistoryRecord` plus PR + signal enrichment."""

    record: HistoryRecord
    enriched_commits: tuple[EnrichedCommit, ...]
    signals: tuple[DecisionSignal, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


# endregion: --- Enriched outputs


# ---------------------------------------------------------------------------
# region:    --- Phase 4: Rich signal sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RFCLink:
    """A discovered link between a design / RFC document and a symbol.

    Excerpt text is stored verbatim (±3 paragraphs around the mention)
    so the agent can ground its answer in the source — never paraphrase
    or fabricate.
    """

    rfc_id: str
    rfc_title: str
    rfc_url: str
    section: str
    excerpt: str
    symbol_id: str
    symbol_label: str
    author: str = ""
    created_at: str = ""
    status: str = "draft"


@dataclass(frozen=True, slots=True)
class IncidentLink:
    """A manually- or API-recorded incident touching a symbol."""

    incident_id: str
    title: str
    url: str
    severity: str
    status: str
    symbol_id: str
    symbol_label: str
    occurred_at: str
    resolved_at: str = ""
    fix_commit: str = ""
    fix_pr: str = ""
    postmortem_url: str = ""


@dataclass(frozen=True, slots=True)
class TribalNote:
    """Developer-annotated knowledge about a symbol.

    Upvotes/downvotes feed into the confidence scorer — see
    :mod:`ast_intel.history.scoring.confidence`.
    """

    id: str
    symbol_id: str
    symbol_label: str
    author: str
    text: str
    created_at: str
    tags: tuple[str, ...] = ()
    upvotes: int = 0
    downvotes: int = 0


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    """A confidence-scored signal ready for ranking and conflict detection.

    Unlike :class:`DecisionSignal` this carries a stable ``id`` (so
    feedback can target it) and a normalised ``signal_type`` covering
    the full Phase 4 taxonomy.
    """

    id: str
    signal_type: SignalKind
    summary: str
    detail: str
    confidence: float
    citation: Citation
    symbol_id: str = ""
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConflictReport:
    """A heuristic-flagged contradiction between two or more signals."""

    symbol_id: str
    signal_ids: tuple[str, ...]
    conflict_type: str
    description: str
    recommended_resolution: str = ""


class FeedbackVerdict(StrEnum):
    """User verdict on a signal."""

    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    OUTDATED = "outdated"


@dataclass(frozen=True, slots=True)
class FeedbackEntry:
    """One row in ``.ast-intel/feedback.jsonl``."""

    signal_id: str
    verdict: FeedbackVerdict
    author: str
    comment: str = ""
    replacement_text: str = ""
    created_at: str = ""


# endregion: --- Phase 4: Rich signal sources


__all__: list[str] = [
    "BlameHunk",
    "Citation",
    "DecisionSignal",
    "EnrichedCommit",
    "EnrichedHistoryRecord",
    "GitCommitInfo",
    "HistoryRecord",
    "OwnershipRecord",
    "OwnershipScore",
    "PullRequestInfo",
    "ReviewComment",
    "ReviewThread",
    "SignalKind",
    "SymbolKind",
    "SymbolRef",
    # Phase 4
    "ConflictReport",
    "FeedbackEntry",
    "FeedbackVerdict",
    "IncidentLink",
    "RFCLink",
    "ScoredSignal",
    "TribalNote",
]
