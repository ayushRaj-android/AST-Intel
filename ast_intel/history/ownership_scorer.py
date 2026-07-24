"""Per-author ownership scoring.

Implements the formula specified by the Code History Intelligence
roadmap::

    score = 0.40 * commit_frequency
          + 0.35 * recency_factor
          + 0.25 * blame_share

Where:

- ``commit_frequency`` = author_commit_count / total_commits
- ``recency_factor``   = exp(-LAMBDA * days_since_authors_last_commit)
- ``blame_share``      = author_blame_lines / total_blame_lines

The recency factor is **per-author** — *not* a global symbol recency —
so a dormant top-committer slips below a recently-active contributor
even with fewer commits, matching the V1 TypeScript implementation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime

from ast_intel.history.models import (
    BlameHunk,
    GitCommitInfo,
    OwnershipRecord,
    OwnershipScore,
    SymbolRef,
)

__all__: list[str] = ["OwnershipScorer", "LAMBDA"]

# Half-life ~140 days (ln(2)/0.005 ≈ 138.6).  Pinned in lock-step with
# the TypeScript engine so the extension and the Python MCP server
# produce identical rankings.
LAMBDA: float = 0.005


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp.

    The git CLI emits ``2024-01-02T03:04:05+00:00``; :func:`fromisoformat`
    in Python 3.11+ accepts that.  We fall back to the epoch when the
    value is empty so the scorer never raises on malformed input.
    """
    if not value:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)


class OwnershipScorer:
    """Compute :class:`OwnershipRecord` from raw commits + blame.

    The scorer is stateless — all parameters are passed at score time
    so it can be reused across symbols without reinitialization.
    """

    def __init__(self, *, recency_lambda: float = LAMBDA) -> None:
        self._lambda = recency_lambda

    def score(
        self,
        symbol: SymbolRef,
        head_sha: str,
        commits: list[GitCommitInfo],
        blame: list[BlameHunk],
        *,
        now: datetime | None = None,
    ) -> OwnershipRecord:
        """Return per-author scores sorted descending by ``score``.

        Authors are keyed by lowercased e-mail. Display name is taken
        from the most recent commit by that author.
        """
        ref_now = now or datetime.now(tz=UTC)

        total_commits = len(commits)
        total_blame_lines = sum(h.line_count for h in blame)

        # Aggregate per author.
        commit_count: dict[str, int] = defaultdict(int)
        last_commit_at: dict[str, datetime] = {}
        display_name: dict[str, str] = {}
        for c in commits:
            key = c.author_email.lower()
            commit_count[key] += 1
            ts = _parse_iso(c.authored_at)
            prev = last_commit_at.get(key)
            if prev is None or ts > prev:
                last_commit_at[key] = ts
                display_name[key] = c.author_name

        blame_lines: dict[str, int] = defaultdict(int)
        for h in blame:
            key = h.author_email.lower()
            blame_lines[key] += h.line_count
            # Backfill display_name if author only appears in blame.
            display_name.setdefault(key, h.author_name)
            ts = _parse_iso(h.authored_at)
            prev = last_commit_at.get(key)
            if prev is None or ts > prev:
                last_commit_at[key] = ts

        keys = set(commit_count) | set(blame_lines)
        scores: list[OwnershipScore] = []
        for key in keys:
            cf = (
                commit_count[key] / total_commits
                if total_commits > 0
                else 0.0
            )
            bs = (
                blame_lines[key] / total_blame_lines
                if total_blame_lines > 0
                else 0.0
            )
            last = last_commit_at.get(key, ref_now)
            days = max(0.0, (ref_now - last).total_seconds() / 86400.0)
            rf = math.exp(-self._lambda * days)
            score_val = 0.40 * cf + 0.35 * rf + 0.25 * bs
            scores.append(
                OwnershipScore(
                    author_name=display_name.get(key, key),
                    author_email=key,
                    commit_count=commit_count[key],
                    last_commit_at=last.isoformat(),
                    blame_lines=blame_lines[key],
                    commit_frequency=cf,
                    recency_factor=rf,
                    blame_share=bs,
                    score=score_val,
                ),
            )

        scores.sort(key=lambda s: s.score, reverse=True)
        return OwnershipRecord(
            symbol=symbol,
            head_sha=head_sha,
            total_commits=total_commits,
            total_lines=total_blame_lines,
            scores=tuple(scores),
        )
