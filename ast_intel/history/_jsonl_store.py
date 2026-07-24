"""Append-only JSONL store helpers used by Phase 4 signal stores.

All Phase 4 user-data stores follow the same convention as the
feedback loop (ADR-006): one JSON object per line in a file under
``<repo>/.ast-intel/``.  This module centralises the I/O so the
incident, tribal-knowledge and feedback stores stay tiny.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__: list[str] = [
    "AST_INTEL_DIR",
    "JSONLStore",
    "append_jsonl",
]


AST_INTEL_DIR: str = ".ast-intel"


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a JSON record as one line to a JSONL file, creating dirs if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write("\n")


class JSONLStore:
    """Append-only JSON Lines store, scoped to a repository."""

    def __init__(self, repo_root: Path, filename: str) -> None:
        self._dir = repo_root / AST_INTEL_DIR
        self._path = self._dir / filename

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: dict[str, Any]) -> None:
        append_jsonl(self._path, record)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    # Skip corrupt lines so a single bad write can't break
                    # the whole store; the next compaction can drop them.
                    continue

    def rewrite(self, records: list[dict[str, Any]]) -> None:
        """Atomically rewrite the store (used for compaction).

        Writes to a temp file in the same directory and renames it,
        which is atomic on POSIX filesystems.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=self._dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    )
                    fh.write("\n")
            os.replace(tmp_path, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
