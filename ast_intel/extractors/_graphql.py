"""GraphQL schema parser.

Parses ``.graphql`` / ``.gql`` schema files using regex-based extraction
and returns :class:`~ast_intel.models.ast_node.RouteNode` for Query /
Mutation / Subscription fields and
:class:`~ast_intel.models.ast_node.StructNode` for object types and
input types.

No external dependencies — uses only the Python standard library.
"""

from __future__ import annotations

import logging
import re

from ast_intel.models.ast_node import (
    FieldNode,
    RouteNode,
    StructNode,
    Visibility,
)

__all__: list[str] = [
    "parse_graphql",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Regex Patterns
# ---------------------------------------------------------------------------

# Matches top-level type/input/interface blocks:
#   type User { ... }
#   input CreateUserInput { ... }
#   interface Node { ... }
_TYPE_BLOCK_RE = re.compile(
    r"(type|input|interface)\s+(\w+)"
    r"(?:\s+implements\s+[\w\s&]+)?"
    r"\s*\{([^}]*)\}",
    re.DOTALL,
)

# Matches a field inside a type block:
#   name: String!
#   posts(first: Int): [Post!]!
#   user(id: ID!): User
_FIELD_RE = re.compile(
    r"^\s*(\w+)"               # field name
    r"(?:\([^)]*\))?"          # optional arguments
    r"\s*:\s*"                 # colon separator
    r"(\[?\w+!?\]?!?)",       # return type
    re.MULTILINE,
)

# Operation type names that map to routes.
_OPERATION_TYPES: frozenset[str] = frozenset({
    "Query",
    "Mutation",
    "Subscription",
})

# ---------------------------------------------------------------------------
# endregion: --- Regex Patterns
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def parse_graphql(
    source: bytes,
    file_path: str,
) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse a GraphQL schema and extract operations and types.

    Args:
        source: Raw file contents (UTF-8).
        file_path: Path for logging context.

    Returns:
        A tuple of ``(routes, structs)`` where:
        - *routes* are Query/Mutation/Subscription fields as ``RouteNode``.
        - *structs* are object types and input types as ``StructNode``.
    """
    text = source.decode("utf-8", errors="replace")

    # Strip comments.
    text = _strip_comments(text)

    routes: list[RouteNode] = []
    structs: list[StructNode] = []

    for m in _TYPE_BLOCK_RE.finditer(text):
        kind = m.group(1)       # "type", "input", or "interface"
        name = m.group(2)       # e.g. "Query", "User", "CreateUserInput"
        body = m.group(3)

        if kind == "type" and name in _OPERATION_TYPES:
            # Operation root type → extract as routes.
            routes.extend(_extract_operations(body, name))
        else:
            # Object / input / interface → extract as struct.
            fields = _extract_fields(body)
            structs.append(
                StructNode(
                    name=name,
                    visibility=Visibility.PUBLIC,
                    fields=tuple(fields),
                ),
            )

    return routes, structs


# ---------------------------------------------------------------------------
# endregion: --- Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    """Remove ``#`` single-line comments from GraphQL source."""
    return re.sub(r"#[^\n]*", "", text)


def _extract_operations(
    body: str,
    operation_type: str,
) -> list[RouteNode]:
    """Extract Query/Mutation/Subscription fields as routes.

    The ``method`` is derived from the operation type name:
    ``Query`` → ``"QUERY"``, ``Mutation`` → ``"MUTATION"``,
    ``Subscription`` → ``"SUBSCRIPTION"``.
    """
    method = operation_type.upper()
    routes: list[RouteNode] = []
    for m in _FIELD_RE.finditer(body):
        field_name = m.group(1)
        routes.append(
            RouteNode(
                path=field_name,
                method=method,
                handler=field_name,
                framework="graphql",
            ),
        )
    return routes


def _extract_fields(body: str) -> list[FieldNode]:
    """Extract fields from a type / input body."""
    fields: list[FieldNode] = []
    for m in _FIELD_RE.finditer(body):
        name = m.group(1)
        raw_type = m.group(2)
        type_str = _normalize_type(raw_type)
        fields.append(
            FieldNode(
                name=name,
                type=type_str,
                visibility=Visibility.PUBLIC,
            ),
        )
    return fields


def _normalize_type(raw: str) -> str:
    """Normalise a GraphQL type string.

    Strips ``!`` (non-null markers) and converts ``[Foo!]`` to
    ``array<Foo>`` for consistency with the OpenAPI parser.
    """
    s = raw.replace("!", "")
    # List type: [Foo] → array<Foo>
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1]
        return f"array<{inner}>"
    return s


# ---------------------------------------------------------------------------
# endregion: --- Internal Helpers
# ---------------------------------------------------------------------------
