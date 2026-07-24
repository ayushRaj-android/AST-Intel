"""Shared payload / value extraction helpers for egress detectors.

These utilities are used by both the HTTP client detector
(:mod:`ast_intel.extractors._http_calls`) and the SDK egress detector
(:mod:`ast_intel.extractors._sdk_calls`) to turn tree-sitter argument nodes
into structured :class:`~ast_intel.models.ast_node.PayloadField` records —
capturing *what data* is sent while redacting secret-looking values.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ast_intel.models.ast_node import Confidence, PayloadField

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = [
    "describe_value_node",
    "dict_fields",
    "find_enclosing_function",
    "make_payload_field",
    "node_text",
    "object_fields",
    "payload_confidence",
]

# Field/header names whose values are secrets and must never be emitted.
_SECRET_KEY_RE: re.Pattern[str] = re.compile(
    r"(?i)(authorization|api[_-]?key|secret|passw(or)?d|pwd|token|"
    r"access[_-]?key|client[_-]?secret|private[_-]?key|credential|"
    r"session|cookie|bearer|\bauth\b)",
)

# Literal values that are obviously secrets regardless of the field name.
_SECRET_VALUE_RE: re.Pattern[str] = re.compile(
    r"(sk_(live|test)_[A-Za-z0-9]+|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"AIza[0-9A-Za-z_-]{20,}|Bearer\s+[A-Za-z0-9._-]+)",
)

# Value-node types treated as literal constants.
_LITERAL_NODE_TYPES: frozenset[str] = frozenset({
    "string", "string_literal", "interpreted_string_literal",
    "raw_string_literal", "template_string", "integer", "float",
    "number", "true", "false", "none", "null",
})

# Value-node types treated as bare variable references.
_VARIABLE_NODE_TYPES: frozenset[str] = frozenset({
    "identifier", "shorthand_property_identifier", "field_identifier",
})

_MAX_VALUE_LEN: int = 60

# Function/method node types across languages — for caller attribution.
_FUNC_TYPES: frozenset[str] = frozenset({
    "function_definition",     # Python
    "function_item",           # Rust
    "function_declaration",    # TypeScript, Go, C
    "method_declaration",      # Java
    "method_definition",       # TypeScript class method
    "arrow_function",          # TypeScript arrow
})


def node_text(node: Node, src: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
    return src[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace",
    ).strip()


def find_enclosing_function(node: Node, src: bytes) -> str:
    """Walk the parent chain to find the enclosing function name.

    Returns ``"<module>"`` when no enclosing function is found.
    """
    current = node.parent
    while current is not None:
        if current.type in _FUNC_TYPES:
            for child in current.children:
                if child.type == "identifier":
                    return node_text(child, src)
            return "<anonymous>"
        current = current.parent
    return "<module>"


def make_payload_field(
    location: str, name: str, value: str, value_kind: str,
) -> PayloadField:
    """Build a :class:`PayloadField`, redacting secret-looking data.

    A value is redacted when the field name matches a secret pattern, or
    when a literal value itself looks like a credential.
    """
    redacted = bool(name and _SECRET_KEY_RE.search(name))
    if not redacted and value_kind == "literal" and _SECRET_VALUE_RE.search(value):
        redacted = True
    if value_kind == "literal":
        source_kind = "literal"
    elif value_kind == "expression":
        source_kind = "computed"
    else:
        source_kind = "unknown"
    return PayloadField(
        location=location,
        name=name,
        value="<redacted>" if redacted else value,
        value_kind=value_kind,
        redacted=redacted,
        source_kind=source_kind,
    )


def describe_value_node(node: Node, src: bytes) -> tuple[str, str]:
    """Return ``(value, value_kind)`` for a value node.

    ``value_kind`` is ``"literal"`` for constants, ``"variable"`` for bare
    identifiers (resolved later by intra-function data-flow), or
    ``"expression"`` for computed values (calls, attribute access, ...).
    """
    if node.type in _LITERAL_NODE_TYPES:
        return node_text(node, src).strip("\"'`"), "literal"
    if node.type in _VARIABLE_NODE_TYPES:
        return node_text(node, src), "variable"
    text = " ".join(node_text(node, src).split())
    if len(text) > _MAX_VALUE_LEN:
        text = text[: _MAX_VALUE_LEN - 3] + "..."
    return text, "expression"


def dict_fields(
    dict_node: Node, src: bytes, location: str,
) -> list[PayloadField]:
    """Extract payload fields from a Python ``dictionary`` node."""
    fields: list[PayloadField] = []
    for pair in dict_node.named_children:
        if pair.type != "pair":
            # Splats / comprehensions — capture as an opaque expression.
            text, kind = describe_value_node(pair, src)
            fields.append(make_payload_field(location, "", text, kind))
            continue
        key_node = pair.child_by_field_name("key")
        val_node = pair.child_by_field_name("value")
        if key_node is None:
            continue
        key = node_text(key_node, src).strip("\"'`")
        if val_node is not None:
            value, kind = describe_value_node(val_node, src)
        else:
            value, kind = "", "expression"
        fields.append(make_payload_field(location, key, value, kind))
    return fields


def object_fields(
    object_node: Node, src: bytes, location: str,
) -> list[PayloadField]:
    """Extract payload fields from a JS/TS ``object`` literal node."""
    fields: list[PayloadField] = []
    for pair in object_node.named_children:
        if pair.type != "pair":
            text, kind = describe_value_node(pair, src)
            fields.append(make_payload_field(location, "", text, kind))
            continue
        key_node = pair.child_by_field_name("key")
        val_node = pair.child_by_field_name("value")
        if key_node is None:
            continue
        key = node_text(key_node, src).strip("\"'`")
        if val_node is not None:
            value, kind = describe_value_node(val_node, src)
        else:
            value, kind = "", "expression"
        fields.append(make_payload_field(location, key, value, kind))
    return fields


def payload_confidence(fields: list[PayloadField]) -> Confidence:
    """Grade how completely a payload was captured."""
    if not fields:
        return Confidence.EXTRACTED
    if any(f.value_kind == "expression" and not f.name for f in fields):
        return Confidence.AMBIGUOUS
    if any(f.value_kind != "literal" for f in fields):
        return Confidence.INFERRED
    return Confidence.EXTRACTED
