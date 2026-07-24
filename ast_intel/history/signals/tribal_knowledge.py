"""Tribal-knowledge note store.

Developers add free-form annotations about symbols ("this is
perf-sensitive", "do not refactor without consulting Alice").  Notes
support tags + upvotes/downvotes which feed into the confidence model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from ast_intel.history._jsonl_store import JSONLStore
from ast_intel.history.models import TribalNote

__all__: list[str] = ["TRIBAL_FILE", "TribalKnowledgeStore"]


TRIBAL_FILE: str = "tribal-knowledge.jsonl"


class TribalKnowledgeStore:
    """Append-only store of :class:`TribalNote` plus vote actions."""

    def __init__(self, repo_root: Path) -> None:
        self._store = JSONLStore(repo_root, TRIBAL_FILE)

    @property
    def path(self) -> Path:
        return self._store.path

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        symbol_id: str,
        symbol_label: str,
        author: str,
        text: str,
        tags: tuple[str, ...] = (),
    ) -> TribalNote:
        note = TribalNote(
            id=f"tribal-{uuid.uuid4().hex[:8]}",
            symbol_id=symbol_id,
            symbol_label=symbol_label,
            author=author,
            text=text,
            created_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
            tags=tags,
        )
        self._store.append(
            {"kind": "note", **_note_to_dict(note)},
        )
        return note

    def upvote(self, note_id: str) -> None:
        self._store.append({"kind": "vote", "note_id": note_id, "delta": 1})

    def downvote(self, note_id: str) -> None:
        self._store.append({"kind": "vote", "note_id": note_id, "delta": -1})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_all(self) -> list[TribalNote]:
        """Replay the log and return the current materialised view."""
        notes: dict[str, TribalNote] = {}
        for raw in self._store.iter_records():
            kind = raw.get("kind")
            if kind == "note":
                try:
                    payload = {k: v for k, v in raw.items() if k != "kind"}
                    if "tags" in payload and isinstance(payload["tags"], list):
                        payload["tags"] = tuple(payload["tags"])
                    note = TribalNote(**payload)
                except TypeError:
                    continue
                notes[note.id] = note
            elif kind == "vote":
                note = notes.get(raw.get("note_id", ""))
                if note is None:
                    continue
                delta = int(raw.get("delta", 0))
                if delta > 0:
                    notes[note.id] = _replace_votes(note, up=note.upvotes + 1)
                elif delta < 0:
                    notes[note.id] = _replace_votes(
                        note, down=note.downvotes + 1,
                    )
        return list(notes.values())

    def for_symbol(self, symbol_id: str) -> list[TribalNote]:
        return [
            n
            for n in self.list_all()
            if n.symbol_id == symbol_id or n.symbol_label == symbol_id
        ]


def _note_to_dict(note: TribalNote) -> dict[str, object]:
    return {
        "id": note.id,
        "symbol_id": note.symbol_id,
        "symbol_label": note.symbol_label,
        "author": note.author,
        "text": note.text,
        "created_at": note.created_at,
        "tags": list(note.tags),
        "upvotes": note.upvotes,
        "downvotes": note.downvotes,
    }


def _replace_votes(
    note: TribalNote,
    *,
    up: int | None = None,
    down: int | None = None,
) -> TribalNote:
    return TribalNote(
        id=note.id,
        symbol_id=note.symbol_id,
        symbol_label=note.symbol_label,
        author=note.author,
        text=note.text,
        created_at=note.created_at,
        tags=note.tags,
        upvotes=note.upvotes if up is None else up,
        downvotes=note.downvotes if down is None else down,
    )
