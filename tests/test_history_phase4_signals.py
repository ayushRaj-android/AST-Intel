"""Unit tests for RFC indexer + tribal-knowledge + incident + feedback stores."""

from __future__ import annotations

from pathlib import Path

from ast_intel.history.feedback import FeedbackStore
from ast_intel.history.models import FeedbackVerdict
from ast_intel.history.signals.incident_linker import IncidentStore
from ast_intel.history.signals.rfc_indexer import RFCIndexer
from ast_intel.history.signals.tribal_knowledge import TribalKnowledgeStore


# ---------------------------------------------------------------------------
# RFC indexer
# ---------------------------------------------------------------------------


class TestRFCIndexer:
    def test_extracts_backtick_symbols(self, tmp_path: Path) -> None:
        rfc_dir = tmp_path / "docs" / "rfcs"
        rfc_dir.mkdir(parents=True)
        (rfc_dir / "rfc-0001.md").write_text(
            """---
id: RFC-0001
title: Payments redesign
status: accepted
author: alice
date: 2024-01-15
---

# Overview

The `processPayment` function should use Stripe under the hood.

## Details

We also touch `validateOrder` for idempotency.
""",
            encoding="utf-8",
        )

        result = RFCIndexer(tmp_path).index()
        assert result.files_scanned == 1
        assert result.files_with_symbols == 1
        labels = {link.symbol_label for link in result.links}
        assert "processPayment" in labels
        assert "validateOrder" in labels

        process_link = next(
            link
            for link in result.links
            if link.symbol_label == "processPayment"
        )
        assert process_link.rfc_id == "RFC-0001"
        assert process_link.status == "accepted"
        assert "Stripe" in process_link.excerpt

    def test_known_labels_filter(self, tmp_path: Path) -> None:
        rfc_dir = tmp_path / "docs" / "rfcs"
        rfc_dir.mkdir(parents=True)
        (rfc_dir / "rfc.md").write_text(
            "`foo` and `bar` are mentioned.\n",
            encoding="utf-8",
        )
        result = RFCIndexer(tmp_path).index(known_labels=frozenset({"foo"}))
        labels = {link.symbol_label for link in result.links}
        assert labels == {"foo"}

    def test_no_matching_files_returns_empty(self, tmp_path: Path) -> None:
        result = RFCIndexer(tmp_path).index()
        assert result.files_scanned == 0
        assert result.links == ()


# ---------------------------------------------------------------------------
# Tribal-knowledge store
# ---------------------------------------------------------------------------


class TestTribalKnowledgeStore:
    def test_add_then_list_roundtrip(self, tmp_path: Path) -> None:
        store = TribalKnowledgeStore(tmp_path)
        store.add(
            symbol_id="sym-1",
            symbol_label="foo",
            author="alice",
            text="be careful: perf-sensitive",
            tags=("perf-sensitive",),
        )
        notes = store.list_all()
        assert len(notes) == 1
        assert notes[0].author == "alice"
        assert "perf-sensitive" in notes[0].tags

    def test_votes_aggregate(self, tmp_path: Path) -> None:
        store = TribalKnowledgeStore(tmp_path)
        note = store.add(
            symbol_id="sym-1",
            symbol_label="foo",
            author="alice",
            text="t",
        )
        store.upvote(note.id)
        store.upvote(note.id)
        store.downvote(note.id)
        updated = store.list_all()[0]
        assert updated.upvotes == 2
        assert updated.downvotes == 1

    def test_for_symbol_filters(self, tmp_path: Path) -> None:
        store = TribalKnowledgeStore(tmp_path)
        store.add(symbol_id="a", symbol_label="A", author="x", text="t")
        store.add(symbol_id="b", symbol_label="B", author="x", text="t")
        assert len(store.for_symbol("a")) == 1
        assert len(store.for_symbol("B")) == 1
        assert len(store.for_symbol("missing")) == 0


# ---------------------------------------------------------------------------
# Incident store
# ---------------------------------------------------------------------------


class TestIncidentStore:
    def test_add_and_for_symbol(self, tmp_path: Path) -> None:
        store = IncidentStore(tmp_path)
        store.add(
            title="latency spike",
            symbol_id="sym-1",
            symbol_label="processPayment",
            severity="sev2",
            url="https://example.invalid/incidents/42",
            postmortem_url="https://example.invalid/pm/42",
        )
        incs = store.for_symbol("sym-1")
        assert len(incs) == 1
        assert incs[0].severity == "sev2"
        assert incs[0].postmortem_url.endswith("/42")

    def test_incident_ids_unique(self, tmp_path: Path) -> None:
        store = IncidentStore(tmp_path)
        a = store.add(
            title="a", symbol_id="x", symbol_label="X",
        )
        b = store.add(
            title="b", symbol_id="x", symbol_label="X",
        )
        assert a.incident_id != b.incident_id


# ---------------------------------------------------------------------------
# Feedback store
# ---------------------------------------------------------------------------


class TestFeedbackStore:
    def test_submit_and_replay(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path)
        store.submit(
            signal_id="pr_description::pr::1234",
            verdict=FeedbackVerdict.REJECT,
            author="alice",
            comment="this PR is unrelated",
        )
        store.submit(
            signal_id="pr_description::pr::1234",
            verdict="accept",  # string accepted too
            author="bob",
        )
        grouped = store.by_signal()
        assert len(grouped["pr_description::pr::1234"]) == 2
        verdicts = [
            e.verdict for e in grouped["pr_description::pr::1234"]
        ]
        assert FeedbackVerdict.REJECT in verdicts
        assert FeedbackVerdict.ACCEPT in verdicts

    def test_corrupt_line_is_skipped(self, tmp_path: Path) -> None:
        store = FeedbackStore(tmp_path)
        store.submit(
            signal_id="x",
            verdict=FeedbackVerdict.ACCEPT,
            author="a",
        )
        # Corrupt the file: append a garbage line.
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        # Reader must still surface the valid entry.
        entries = store.list_all()
        assert len(entries) == 1
        assert entries[0].author == "a"
