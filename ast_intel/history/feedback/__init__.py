"""Feedback loop — per-signal accept/reject/edit/outdated store.

Storage: ``<repo>/.ast-intel/feedback.jsonl`` (ADR-006).  The store is
append-only; updates are applied by replaying the log in order.  The
:class:`FeedbackLoop` exposes ``adjust_confidence`` for the scoring
pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ast_intel.history._jsonl_store import JSONLStore
from ast_intel.history.models import FeedbackEntry, FeedbackVerdict

__all__: list[str] = ["FEEDBACK_FILE", "FeedbackStore"]


FEEDBACK_FILE: str = "feedback.jsonl"


class FeedbackStore:
    """Append-only feedback log keyed by ``signal_id``."""

    def __init__(self, repo_root: Path) -> None:
        self._store = JSONLStore(repo_root, FEEDBACK_FILE)

    @property
    def path(self) -> Path:
        return self._store.path

    def submit(
        self,
        *,
        signal_id: str,
        verdict: FeedbackVerdict | str,
        author: str,
        comment: str = "",
        replacement_text: str = "",
    ) -> FeedbackEntry:
        kind = (
            verdict
            if isinstance(verdict, FeedbackVerdict)
            else FeedbackVerdict(verdict)
        )
        entry = FeedbackEntry(
            signal_id=signal_id,
            verdict=kind,
            author=author,
            comment=comment,
            replacement_text=replacement_text,
            created_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        )
        self._store.append(
            {
                "signal_id": entry.signal_id,
                "verdict": entry.verdict.value,
                "author": entry.author,
                "comment": entry.comment,
                "replacement_text": entry.replacement_text,
                "created_at": entry.created_at,
            },
        )
        return entry

    def list_all(self) -> list[FeedbackEntry]:
        out: list[FeedbackEntry] = []
        for raw in self._store.iter_records():
            try:
                out.append(
                    FeedbackEntry(
                        signal_id=str(raw["signal_id"]),
                        verdict=FeedbackVerdict(raw["verdict"]),
                        author=str(raw.get("author", "")),
                        comment=str(raw.get("comment", "")),
                        replacement_text=str(raw.get("replacement_text", "")),
                        created_at=str(raw.get("created_at", "")),
                    ),
                )
            except (KeyError, ValueError):
                continue
        return out

    def by_signal(self) -> dict[str, list[FeedbackEntry]]:
        grouped: dict[str, list[FeedbackEntry]] = {}
        for entry in self.list_all():
            grouped.setdefault(entry.signal_id, []).append(entry)
        return grouped
