"""Top-level orchestrator that assembles a :class:`HistoryRecord`.

The builder is the seam between AST-Intel's graph layer and the rest
of the history engine. Given a :class:`SymbolRef` it:

1. Resolves the repository root (caller may pass an explicit override).
2. Reads the HEAD SHA for cache keying.
3. Calls :mod:`ast_intel.history.git_ops` for raw commits + blame.
4. Wraps the result in a :class:`HistoryRecord`.

Caching is *(file, start_line, end_line, head_sha)*-keyed and bounded
to a small LRU so back-to-back MCP calls within one conversation are
near-instant.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from ast_intel.history.git_ops import (
    blame_range,
    find_repo_root,
    get_head_sha,
)
from ast_intel.history.git_ops import log_line_range as _log_range
from ast_intel.history.models import (
    HistoryRecord,
    SymbolKind,
    SymbolRef,
)
from ast_intel.models.graph_model import GraphNode, NodeKind

__all__: list[str] = [
    "HistoryBuilder",
    "node_kind_to_symbol_kind",
    "symbol_ref_from_node",
]


# ---------------------------------------------------------------------------
# region:    --- NodeKind ↔ SymbolKind mapping
# ---------------------------------------------------------------------------


_KIND_MAP: dict[NodeKind, SymbolKind] = {
    NodeKind.FUNCTION: SymbolKind.FUNCTION,
    NodeKind.METHOD: SymbolKind.METHOD,
    NodeKind.STRUCT: SymbolKind.STRUCT,
    NodeKind.ENUM: SymbolKind.ENUM,
    NodeKind.TRAIT: SymbolKind.TRAIT,
    NodeKind.MODULE: SymbolKind.MODULE,
    NodeKind.FILE: SymbolKind.FILE,
}


def node_kind_to_symbol_kind(kind: NodeKind) -> SymbolKind:
    """Map an AST-Intel :class:`NodeKind` to the coarser :class:`SymbolKind`."""
    return _KIND_MAP.get(kind, SymbolKind.OTHER)


def symbol_ref_from_node(node: GraphNode) -> SymbolRef | None:
    """Build a :class:`SymbolRef` from a :class:`GraphNode`.

    Returns ``None`` when the node lacks either a file path or a span —
    history queries require both to delimit ``git log -L``.
    """
    if not node.file or node.span is None:
        return None
    return SymbolRef(
        id=node.id,
        label=node.label,
        kind=node_kind_to_symbol_kind(node.kind),
        file=node.file,
        start_line=node.span.start_line,
        end_line=node.span.end_line,
    )


# endregion: --- NodeKind ↔ SymbolKind mapping


# ---------------------------------------------------------------------------
# region:    --- Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CacheKey:
    file: str
    start: int
    end: int
    head: str


class HistoryBuilder:
    """Construct :class:`HistoryRecord` instances with a small LRU cache.

    Args:
        repo_root: Repository root. When ``None``, every call resolves
            from the symbol's file path on demand.
        max_commits: Upper bound forwarded to :func:`git log -L`.
        cache_size: Maximum number of distinct symbols to memoize.
    """

    def __init__(
        self,
        repo_root: Path | None = None,
        *,
        max_commits: int = 200,
        cache_size: int = 64,
    ) -> None:
        self._repo_root = repo_root
        self._max_commits = max_commits
        self._cache_size = cache_size
        self._cache: OrderedDict[_CacheKey, HistoryRecord] = OrderedDict()

    def resolve_repo(self, sample_file: str | Path) -> Path:
        """Return the repository root, resolving lazily if not pinned."""
        if self._repo_root is not None:
            return self._repo_root
        path = Path(sample_file)
        if not path.is_absolute():
            msg = (
                "HistoryBuilder.resolve_repo requires an absolute path "
                "or an explicit repo_root."
            )
            raise ValueError(msg)
        return find_repo_root(path)

    def build(self, symbol: SymbolRef) -> HistoryRecord:
        """Return the (possibly cached) :class:`HistoryRecord` for *symbol*."""
        repo = self._repo_root or find_repo_root(
            Path(symbol.file)
            if Path(symbol.file).is_absolute()
            else Path.cwd(),
        )
        head = get_head_sha(repo)
        key = _CacheKey(symbol.file, symbol.start_line, symbol.end_line, head)
        cached = self._cache.get(key)
        if cached is not None:
            # LRU touch.
            self._cache.move_to_end(key)
            return cached

        commits = _log_range(
            repo,
            symbol.file,
            symbol.start_line,
            symbol.end_line,
            max_commits=self._max_commits,
        )
        try:
            blame = blame_range(
                repo,
                symbol.file,
                symbol.start_line,
                symbol.end_line,
            )
        except Exception:  # noqa: BLE001 — blame failures are non-fatal.
            blame = []

        total_lines = symbol.end_line - symbol.start_line + 1
        record = HistoryRecord(
            symbol=symbol,
            head_sha=head,
            commits=tuple(commits),
            blame=tuple(blame),
            total_lines=total_lines,
        )

        self._cache[key] = record
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return record


# endregion: --- Builder
