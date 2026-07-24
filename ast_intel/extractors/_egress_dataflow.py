"""Intra-function data-flow (L2) — resolve where egress payload data comes from.

For each payload field captured as a bare *variable*, this module looks at the
enclosing function body and traces the variable to its nearest prior assignment,
labelling the origin (``from-input`` / ``from-db`` / ``from-env`` / ``literal``
/ ``computed`` / ``unknown``).

This is deliberately **intra-procedural and single-pass**: it never crosses
function boundaries (that would be full taint analysis).  It follows simple
identifier aliases up to a small depth and classifies the resolved expression
with lightweight, heuristic pattern matching.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ast_intel.extractors._payload import describe_value_node, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

    from ast_intel.models.ast_node import PayloadField

__all__: list[str] = [
    "SOURCE_KINDS",
    "resolve_payload_sources",
]

# Valid ``PayloadField.source_kind`` values.
SOURCE_KINDS: frozenset[str] = frozenset({
    "literal",
    "from-input",
    "from-db",
    "from-env",
    "computed",
    "unknown",
})

# Max identifier-alias hops to follow (``a = b; b = request.json``).
_MAX_HOPS: int = 5

_PY_FUNC_TYPES: frozenset[str] = frozenset({"function_definition"})

# Heuristic expression signatures (substring match on the RHS source text).
_ENV_PATTERNS: tuple[str, ...] = (
    "os.environ", "os.getenv", "getenv(", "environ[",
    "settings.", "config.", "config[", "dotenv",
)
_INPUT_PATTERNS: tuple[str, ...] = (
    "request.", "flask.request", "self.request",
    "req.body", "req.query", "req.params", ".get_json(",
    "input(", "sys.argv",
)
_DB_PATTERNS: tuple[str, ...] = (
    ".query(", ".execute(", ".fetchone(", ".fetchall(", ".fetchmany(",
    ".first(", ".all(", ".objects.", "cursor.",
    ".find(", ".find_one(", ".aggregate(",
    "session.query", "session.execute",
)


def resolve_payload_sources(
    fields: tuple[PayloadField, ...], call_node: Node, src: bytes,
) -> tuple[PayloadField, ...]:
    """Return *fields* with ``source_kind`` resolved for variable payloads.

    Only bare, non-redacted variable fields are resolved (their ``value`` still
    holds the original identifier).  Literal/expression fields keep the
    ``source_kind`` assigned at capture time.
    """
    if not fields:
        return fields
    func_node = _enclosing_function(call_node)
    if func_node is None:
        return fields

    resolved: list[PayloadField] = []
    for field in fields:
        if field.value_kind == "variable" and not field.redacted:
            kind = _resolve_variable(
                field.value, call_node.start_byte, func_node, src, 0,
            )
            resolved.append(replace(field, source_kind=kind))
        else:
            resolved.append(field)
    return tuple(resolved)


def _enclosing_function(node: Node) -> Node | None:
    """Return the nearest enclosing ``function_definition`` node, or ``None``."""
    current = node.parent
    while current is not None:
        if current.type in _PY_FUNC_TYPES:
            return current
        current = current.parent
    return None


def _resolve_variable(
    var: str, before_byte: int, func_node: Node, src: bytes, depth: int,
) -> str:
    """Classify the origin of *var* by its nearest prior assignment."""
    right = _nearest_prior_assignment(var, before_byte, func_node, src)
    if right is None:
        # No assignment found — a parameter or an outer-scope name.
        return "unknown"
    _, kind = describe_value_node(right, src)
    if kind == "literal":
        return "literal"
    if kind == "variable" and depth < _MAX_HOPS:
        return _resolve_variable(
            node_text(right, src), right.start_byte, func_node, src, depth + 1,
        )
    return _classify_expr(right, src)


def _nearest_prior_assignment(
    var: str, before_byte: int, func_node: Node, src: bytes,
) -> Node | None:
    """Find the RHS of the last ``var = <expr>`` before *before_byte*.

    Does not descend into nested functions, so only same-scope assignments
    are considered.
    """
    best: Node | None = None
    best_start = -1
    stack: list[Node] = list(func_node.children)
    while stack:
        node = stack.pop()
        if node.type in _PY_FUNC_TYPES:
            continue  # skip nested function scopes
        if node.type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if (
                left is not None
                and right is not None
                and left.type == "identifier"
                and node_text(left, src) == var
                and node.start_byte < before_byte
                and node.start_byte > best_start
            ):
                best = right
                best_start = node.start_byte
        stack.extend(node.children)
    return best


def _classify_expr(node: Node, src: bytes) -> str:
    """Heuristically classify a resolved RHS expression."""
    text = node_text(node, src)
    if any(pattern in text for pattern in _ENV_PATTERNS):
        return "from-env"
    if any(pattern in text for pattern in _INPUT_PATTERNS):
        return "from-input"
    if any(pattern in text for pattern in _DB_PATTERNS) or "select " in text.lower():
        return "from-db"
    return "computed"
