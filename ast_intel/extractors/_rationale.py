"""Shared rationale comment extractor — language-agnostic.

Walks a tree-sitter AST and extracts specially-prefixed comments
(``NOTE:``, ``HACK:``, ``TODO:``, ``FIXME:``, etc.) as
:class:`~ast_intel.models.ast_node.RationaleNode` instances.

The recognized prefixes are intentionally broad — covering design
decisions (``WHY:``), known workarounds (``HACK:``), unsafe-code
justifications (``SAFETY:``), and performance notes (``PERF:``).
Each language extractor calls :func:`extract_rationale_comments`
once at the end of its ``extract()`` method.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import RationaleNode, Span

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = ["extract_rationale_comments"]


# ---------------------------------------------------------------------------
# region:    --- Prefix Patterns
# ---------------------------------------------------------------------------

# Matches line comments: `// PREFIX:` or `# PREFIX:` (stripped text)
# and block comments: `/* PREFIX:` (first line only).
# Group 1 = prefix, Group 2 = body text after the colon.
_LINE_PATTERN: re.Pattern[str] = re.compile(
    r"^(?://|#)\s*"
    r"(NOTE|HACK|WHY|TODO|FIXME|IMPORTANT|SAFETY|RATIONALE|PERF)"
    r":\s*(.+)",
    re.IGNORECASE,
)

_BLOCK_PATTERN: re.Pattern[str] = re.compile(
    r"^/\*+\s*"
    r"(NOTE|HACK|WHY|TODO|FIXME|IMPORTANT|SAFETY|RATIONALE|PERF)"
    r":\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Tree-sitter node types that represent comments across all supported
# grammars.  Rust uses ``line_comment`` / ``block_comment``; all others
# use ``comment``.
_COMMENT_NODE_TYPES: frozenset[str] = frozenset({
    "comment",
    "line_comment",
    "block_comment",
})

# Tree-sitter node types that define a named scope (function, class, etc.).
# When we walk *up* from a comment node, the first ancestor whose type is
# in this set becomes the ``parent`` field of the ``RationaleNode``.
_SCOPE_NODE_TYPES: frozenset[str] = frozenset({
    # Rust
    "function_item",
    "impl_item",
    "trait_item",
    "struct_item",
    "enum_item",
    "mod_item",
    # Python
    "function_definition",
    "class_definition",
    # TypeScript / JavaScript
    "function_declaration",
    "method_definition",
    "class_declaration",
    "arrow_function",
    # Go
    "method_declaration",
    "type_declaration",
    # C#
    "struct_declaration",
    "interface_declaration",
    "constructor_declaration",
    "enum_declaration",
    "namespace_declaration",
    "record_declaration",
    # Kotlin
    "object_declaration",
    # Scala
    "object_definition",
    "trait_definition",
    # Swift
    "protocol_declaration",
    # PHP
    "trait_declaration",
})

# endregion: --- Prefix Patterns


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def extract_rationale_comments(
    root: Node,
    source: bytes,
) -> list[RationaleNode]:
    """Walk the AST and extract rationale comments.

    This function is language-agnostic — it looks for ``comment``,
    ``line_comment``, and ``block_comment`` nodes in the tree-sitter
    AST, checks whether the comment text matches a recognized prefix,
    and resolves the enclosing scope to populate ``parent``.

    Consecutive comments with the same prefix are **merged** into a
    single ``RationaleNode`` (e.g., two ``# NOTE:`` lines in a row).

    Args:
        root: The tree-sitter root node of the parsed file.
        source: Raw file bytes (needed to extract comment text).

    Returns:
        A list of ``RationaleNode`` instances sorted by source line.
    """
    raw = _collect_raw_rationale(root, source)
    return _merge_consecutive(raw)


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _collect_raw_rationale(
    root: Node,
    source: bytes,
) -> list[RationaleNode]:
    """Walk the full AST tree and yield one RationaleNode per match."""
    results: list[RationaleNode] = []
    _walk(root, source, results)
    return results


def _walk(node: Node, source: bytes, results: list[RationaleNode]) -> None:
    """Depth-first traversal that checks every comment node."""
    if node.type in _COMMENT_NODE_TYPES:
        text = node.text.decode("utf-8", errors="replace") if node.text else ""
        parsed = _try_parse(text)
        if parsed is not None:
            kind, body = parsed
            results.append(
                RationaleNode(
                    kind=kind.upper(),
                    text=body.strip(),
                    span=span_from_node(node),
                    parent=_resolve_parent(node),
                )
            )
    for child in node.children:
        _walk(child, source, results)


def _try_parse(text: str) -> tuple[str, str] | None:
    """Try to extract a rationale prefix + body from comment text.

    Returns:
        ``(prefix, body)`` if matched, else ``None``.
    """
    for line in text.splitlines():
        stripped = line.strip()
        m = _LINE_PATTERN.match(stripped) or _BLOCK_PATTERN.match(stripped)
        if m:
            body = m.group(2).rstrip().removesuffix("*/").rstrip()
            return m.group(1), body
    return None


def _resolve_parent(node: Node) -> str:
    """Walk up the AST to find the enclosing named scope.

    Returns the *name identifier* of the nearest ancestor whose type is
    in ``_SCOPE_NODE_TYPES``.  If no scope ancestor is found, returns
    ``"<file>"`` to indicate a file-level comment.
    """
    current = node.parent
    while current is not None:
        if current.type in _SCOPE_NODE_TYPES:
            name = _extract_scope_name(current)
            if name:
                return name
        current = current.parent
    return "<file>"


def _extract_scope_name(node: Node) -> str:
    """Extract the name identifier from a scope node.

    Looks for the first ``identifier``, ``name``, or
    ``type_identifier`` child.
    """
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier"):
            return (
                child.text.decode("utf-8", errors="replace")
                if child.text
                else ""
            )
    return ""


def _merge_consecutive(
    nodes: list[RationaleNode],
) -> list[RationaleNode]:
    """Merge consecutive rationale comments that share the same prefix.

    When two or more adjacent lines have the same ``kind`` and
    ``parent``, with no gap in line numbers, they are combined into
    a single ``RationaleNode`` whose ``text`` is the joined body and
    whose ``span`` covers the full range.
    """
    if not nodes:
        return []

    merged: list[RationaleNode] = []
    current = nodes[0]

    for nxt in nodes[1:]:
        same_kind = nxt.kind == current.kind
        same_parent = nxt.parent == current.parent
        adjacent = nxt.span.start_line == current.span.end_line + 1

        if same_kind and same_parent and adjacent:
            # Extend current by merging text & expanding span
            current = RationaleNode(
                kind=current.kind,
                text=current.text + " " + nxt.text,
                span=Span(
                    start_line=current.span.start_line,
                    start_col=current.span.start_col,
                    end_line=nxt.span.end_line,
                    end_col=nxt.span.end_col,
                ),
                parent=current.parent,
            )
        else:
            merged.append(current)
            current = nxt

    merged.append(current)
    return merged


# endregion: --- Internal Helpers
