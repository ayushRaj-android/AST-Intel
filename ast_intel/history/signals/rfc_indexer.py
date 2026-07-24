"""Markdown RFC / design-doc indexer.

Discovers markdown files matching configured globs, extracts symbol
references via backtick-quoted names (`` `processPayment` ``), and emits
:class:`~ast_intel.history.models.RFCLink` records that can be joined
with the graph by ``symbol_label``.

The indexer keeps an ``excerpt`` of ±3 paragraphs around each mention so
the agent can cite verbatim text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ast_intel.history.models import RFCLink

__all__: list[str] = [
    "DEFAULT_RFC_GLOBS",
    "RFCIndexResult",
    "RFCIndexer",
]


DEFAULT_RFC_GLOBS: tuple[str, ...] = (
    "docs/rfcs/**/*.md",
    "docs/rfc/**/*.md",
    "design/**/*.md",
    "rfcs/**/*.md",
    "RFCS/**/*.md",
)

_BACKTICK_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_./:\-]{1,128})`")
_FRONTMATTER = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*\n(?P<rest>.*)\Z",
    re.DOTALL,
)
_FM_FIELD = re.compile(r"^(?P<key>[A-Za-z0-9_-]+)\s*:\s*(?P<val>.+?)\s*$")
_HEADING = re.compile(r"^#+\s+(.+?)\s*$", re.MULTILINE)
_EXCERPT_PARAGRAPHS: int = 3


@dataclass(frozen=True, slots=True)
class RFCIndexResult:
    """Result of an indexing pass over a repository."""

    links: tuple[RFCLink, ...]
    files_scanned: int
    files_with_symbols: int


# ---------------------------------------------------------------------------
# region:    --- Indexer
# ---------------------------------------------------------------------------


class RFCIndexer:
    """Scan markdown RFCs and emit :class:`RFCLink` records.

    The indexer is graph-agnostic — it emits symbol *labels* and lets
    the caller resolve them to ``symbol_id`` via the :class:`CodeGraph`.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        globs: tuple[str, ...] = DEFAULT_RFC_GLOBS,
    ) -> None:
        self._root = repo_root.resolve()
        self._globs = globs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index(
        self,
        *,
        known_labels: frozenset[str] | None = None,
    ) -> RFCIndexResult:
        """Return all RFC links discovered under :attr:`_root`.

        If *known_labels* is provided the indexer keeps only mentions
        whose backtick label is in the set — drastically reducing
        false-positive symbol links.
        """
        links: list[RFCLink] = []
        files_scanned = 0
        files_with_symbols = 0
        seen_paths: set[Path] = set()
        for pattern in self._globs:
            for path in sorted(self._root.glob(pattern)):
                if path in seen_paths or not path.is_file():
                    continue
                seen_paths.add(path)
                files_scanned += 1
                file_links = self._index_file(path, known_labels)
                if file_links:
                    files_with_symbols += 1
                    links.extend(file_links)
        return RFCIndexResult(
            links=tuple(links),
            files_scanned=files_scanned,
            files_with_symbols=files_with_symbols,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _index_file(
        self,
        path: Path,
        known_labels: frozenset[str] | None,
    ) -> list[RFCLink]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        meta, body = self._split_frontmatter(text)
        rfc_id = meta.get("id") or path.stem
        rfc_title = meta.get("title") or self._first_heading(body) or path.stem
        status = (meta.get("status") or "draft").lower()
        author = meta.get("author") or ""
        created_at = meta.get("date") or self._mtime_iso(path)
        rel = path.relative_to(self._root)
        rfc_url = f"file://{path}"

        out: list[RFCLink] = []
        seen_for_file: set[str] = set()
        for label, section, excerpt in self._iter_mentions(body):
            if known_labels is not None and label not in known_labels:
                continue
            key = f"{label}::{section}"
            if key in seen_for_file:
                continue
            seen_for_file.add(key)
            out.append(
                RFCLink(
                    rfc_id=str(rfc_id),
                    rfc_title=str(rfc_title),
                    rfc_url=rfc_url,
                    section=section,
                    excerpt=excerpt,
                    symbol_id="",  # resolved later via graph
                    symbol_label=label,
                    author=author,
                    created_at=created_at,
                    status=status,
                ),
            )
        # We don't use *rel* but exposing it via the path's relative form
        # would help downstream callers; keep it implicit via rfc_url.
        del rel
        return out

    def _split_frontmatter(self, text: str) -> tuple[dict[str, str], str]:
        match = _FRONTMATTER.match(text)
        if match is None:
            return {}, text
        body = match.group("rest")
        meta: dict[str, str] = {}
        for raw in match.group("body").splitlines():
            field = _FM_FIELD.match(raw)
            if field:
                meta[field.group("key").lower()] = field.group("val").strip(
                    "'\" ",
                )
        return meta, body

    def _first_heading(self, body: str) -> str:
        match = _HEADING.search(body)
        return match.group(1).strip() if match else ""

    def _mtime_iso(self, path: Path) -> str:
        try:
            ts = path.stat().st_mtime
        except OSError:
            return ""
        return (
            datetime.fromtimestamp(ts, tz=UTC)
            .isoformat(timespec="seconds")
        )

    def _iter_mentions(
        self,
        body: str,
    ) -> list[tuple[str, str, str]]:
        """Yield ``(symbol_label, section_title, excerpt)`` triples."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        results: list[tuple[str, str, str]] = []
        current_section = ""
        for idx, para in enumerate(paragraphs):
            heading = _HEADING.match(para)
            if heading:
                current_section = heading.group(1).strip()
                continue
            for match in _BACKTICK_SYMBOL.finditer(para):
                label = match.group(1)
                excerpt = self._build_excerpt(paragraphs, idx)
                results.append((label, current_section, excerpt))
        return results

    def _build_excerpt(self, paragraphs: list[str], idx: int) -> str:
        start = max(0, idx - _EXCERPT_PARAGRAPHS)
        end = min(len(paragraphs), idx + _EXCERPT_PARAGRAPHS + 1)
        return "\n\n".join(paragraphs[start:end])


# endregion: --- Indexer
