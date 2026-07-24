"""Tests for the history dataclasses and enum mappings."""

from __future__ import annotations

import math

import pytest

from ast_intel.history.history_builder import (
    node_kind_to_symbol_kind,
    symbol_ref_from_node,
)
from ast_intel.history.models import (
    BlameHunk,
    Citation,
    DecisionSignal,
    GitCommitInfo,
    HistoryRecord,
    SignalKind,
    SymbolKind,
    SymbolRef,
)
from ast_intel.history.ownership_scorer import OwnershipScorer
from ast_intel.models.ast_node import Span
from ast_intel.models.graph_model import GraphNode, NodeKind


def _commit(sha: str, email: str, when: str) -> GitCommitInfo:
    return GitCommitInfo(
        sha=sha,
        author_name=email.split("@")[0],
        author_email=email,
        authored_at=when,
        committed_at=when,
        subject="x",
    )


def test_dataclasses_are_frozen_and_hashable() -> None:
    ref = SymbolRef(
        id="src/a.py::f",
        label="f",
        kind=SymbolKind.FUNCTION,
        file="src/a.py",
        start_line=1,
        end_line=10,
    )
    with pytest.raises(AttributeError):
        ref.label = "g"  # type: ignore[misc]
    assert hash(ref) == hash(ref)


def test_blame_hunk_line_count() -> None:
    h = BlameHunk(
        sha="a" * 40,
        author_name="A",
        author_email="a@x",
        authored_at="2024-01-01T00:00:00+00:00",
        start_line=5,
        end_line=12,
    )
    assert h.line_count == 8


def test_node_kind_mapping() -> None:
    assert node_kind_to_symbol_kind(NodeKind.FUNCTION) is SymbolKind.FUNCTION
    assert node_kind_to_symbol_kind(NodeKind.METHOD) is SymbolKind.METHOD
    assert node_kind_to_symbol_kind(NodeKind.STRUCT) is SymbolKind.STRUCT
    # Unmapped kinds fall through to OTHER.
    assert node_kind_to_symbol_kind(NodeKind.CRATE) is SymbolKind.OTHER


def test_symbol_ref_from_node_requires_span() -> None:
    n = GraphNode(
        id="src/a.py::f",
        label="f",
        kind=NodeKind.FUNCTION,
        file="src/a.py",
        span=None,
    )
    assert symbol_ref_from_node(n) is None
    n2 = GraphNode(
        id="src/a.py::f",
        label="f",
        kind=NodeKind.FUNCTION,
        file="src/a.py",
        span=Span(start_line=10, start_col=1, end_line=20, end_col=1),
    )
    ref = symbol_ref_from_node(n2)
    assert ref is not None
    assert ref.start_line == 10
    assert ref.end_line == 20
    assert ref.kind is SymbolKind.FUNCTION


def test_ownership_scorer_components_sum_to_score() -> None:
    """For a single author, score must equal the weighted formula exactly."""
    ref = SymbolRef(
        id="x::y",
        label="y",
        kind=SymbolKind.FUNCTION,
        file="x",
        start_line=1,
        end_line=10,
    )
    commits = [_commit("a" * 40, "a@x", "2024-01-01T00:00:00+00:00")]
    blame = [
        BlameHunk(
            sha="a" * 40,
            author_name="a",
            author_email="a@x",
            authored_at="2024-01-01T00:00:00+00:00",
            start_line=1,
            end_line=10,
        ),
    ]
    record = HistoryRecord(
        symbol=ref,
        head_sha="h",
        commits=tuple(commits),
        blame=tuple(blame),
        total_lines=10,
    )
    own = OwnershipScorer().score(
        symbol=record.symbol,
        head_sha=record.head_sha,
        commits=commits,
        blame=blame,
    )
    assert len(own.scores) == 1
    s = own.scores[0]
    assert s.commit_frequency == pytest.approx(1.0)
    assert s.blame_share == pytest.approx(1.0)
    # Per-author recency factor is in (0, 1].
    assert 0.0 < s.recency_factor <= 1.0
    expected = (
        0.40 * s.commit_frequency
        + 0.35 * s.recency_factor
        + 0.25 * s.blame_share
    )
    assert math.isclose(s.score, expected)


def test_ownership_scorer_orders_by_score() -> None:
    """Recent + larger-blame author ranks above a dormant top committer."""
    ref = SymbolRef(
        id="x::y",
        label="y",
        kind=SymbolKind.FUNCTION,
        file="x",
        start_line=1,
        end_line=10,
    )
    old = "2010-01-01T00:00:00+00:00"
    new = "2025-12-31T00:00:00+00:00"
    # Old author dominates commit count, but they last committed ~15
    # years ago — their recency factor is essentially zero. New author
    # owns the majority of the blame and committed recently.
    commits = (
        [_commit(f"{i:040x}", "old@x", old) for i in range(8)]
        + [_commit("a" * 40, "new@x", new), _commit("b" * 40, "new@x", new)]
    )
    blame = [
        BlameHunk(
            sha="1" * 40,
            author_name="old",
            author_email="old@x",
            authored_at=old,
            start_line=1,
            end_line=1,
        ),
        BlameHunk(
            sha="a" * 40,
            author_name="new",
            author_email="new@x",
            authored_at=new,
            start_line=2,
            end_line=10,
        ),
    ]
    own = OwnershipScorer().score(
        symbol=ref,
        head_sha="h",
        commits=commits,
        blame=blame,
    )
    assert own.scores[0].author_email == "new@x"


def test_decision_signal_requires_citation_struct() -> None:
    """A DecisionSignal is only well-formed with a non-empty citation URL."""
    sig = DecisionSignal(
        kind=SignalKind.PR_DESCRIPTION,
        summary="PR 1",
        detail="body",
        citation=Citation(
            type="pull_request",
            url="https://example/1",
            author="A",
            date="2024-01-01T00:00:00+00:00",
            pr_id="1",
        ),
    )
    assert sig.citation.url.startswith("https://")
