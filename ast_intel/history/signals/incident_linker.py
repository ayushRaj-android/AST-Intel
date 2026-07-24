"""Incident linker — manual entries today, API adapters tomorrow.

Phase 4.0 supports manual incident records (PagerDuty/Jira UI URLs +
metadata) persisted as JSON lines in ``.ast-intel/incidents.jsonl``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from ast_intel.history._jsonl_store import JSONLStore
from ast_intel.history.models import IncidentLink

__all__: list[str] = ["INCIDENTS_FILE", "IncidentStore"]


INCIDENTS_FILE: str = "incidents.jsonl"


class IncidentStore:
    """Append-only store of :class:`IncidentLink` records."""

    def __init__(self, repo_root: Path) -> None:
        self._store = JSONLStore(repo_root, INCIDENTS_FILE)

    @property
    def path(self) -> Path:
        return self._store.path

    def add(
        self,
        *,
        title: str,
        symbol_id: str,
        symbol_label: str,
        severity: str = "sev3",
        status: str = "investigating",
        url: str = "",
        incident_id: str = "",
        occurred_at: str = "",
        resolved_at: str = "",
        fix_commit: str = "",
        fix_pr: str = "",
        postmortem_url: str = "",
    ) -> IncidentLink:
        record = IncidentLink(
            incident_id=incident_id or f"incident-{uuid.uuid4().hex[:8]}",
            title=title,
            url=url,
            severity=severity,
            status=status,
            symbol_id=symbol_id,
            symbol_label=symbol_label,
            occurred_at=occurred_at
            or datetime.now(tz=UTC).isoformat(timespec="seconds"),
            resolved_at=resolved_at,
            fix_commit=fix_commit,
            fix_pr=fix_pr,
            postmortem_url=postmortem_url,
        )
        self._store.append(_to_dict(record))
        return record

    def list_all(self) -> list[IncidentLink]:
        out: list[IncidentLink] = []
        for raw in self._store.iter_records():
            try:
                out.append(IncidentLink(**raw))
            except TypeError:
                continue
        return out

    def for_symbol(self, symbol_id: str) -> list[IncidentLink]:
        return [
            i
            for i in self.list_all()
            if i.symbol_id == symbol_id or i.symbol_label == symbol_id
        ]


def _to_dict(link: IncidentLink) -> dict[str, str]:
    return {
        "incident_id": link.incident_id,
        "title": link.title,
        "url": link.url,
        "severity": link.severity,
        "status": link.status,
        "symbol_id": link.symbol_id,
        "symbol_label": link.symbol_label,
        "occurred_at": link.occurred_at,
        "resolved_at": link.resolved_at,
        "fix_commit": link.fix_commit,
        "fix_pr": link.fix_pr,
        "postmortem_url": link.postmortem_url,
    }
