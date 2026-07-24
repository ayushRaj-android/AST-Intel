"""Protobuf / gRPC contract parser.

Parses ``.proto`` files using regex-based extraction and returns
:class:`~ast_intel.models.ast_node.RouteNode` for RPC methods,
:class:`~ast_intel.models.ast_node.TraitNode` for services, and
:class:`~ast_intel.models.ast_node.StructNode` for messages.

No external dependencies — uses only the Python standard library.
"""

from __future__ import annotations

import logging
import re

from ast_intel.models.ast_node import (
    FieldNode,
    RouteNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    Visibility,
)

__all__: list[str] = [
    "parse_proto",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Regex Patterns
# ---------------------------------------------------------------------------

# Matches:  service UserService { ... }
_SERVICE_RE = re.compile(
    r"service\s+(\w+)\s*\{([^}]*)\}",
    re.DOTALL,
)

# Matches:  rpc GetUser (GetUserRequest) returns (User);
_RPC_RE = re.compile(
    r"rpc\s+(\w+)\s*\(\s*([\w.]+)\s*\)\s*returns\s*\(\s*([\w.]+)\s*\)\s*;",
)

# Matches:  message User { ... }
_MESSAGE_RE = re.compile(
    r"message\s+(\w+)\s*\{([^}]*)\}",
    re.DOTALL,
)

# Matches a field line inside a message:
#   string name = 1;
#   repeated User users = 2;
#   map<string, int32> counts = 3;
_FIELD_RE = re.compile(
    r"^\s*(repeated\s+)?(map<[\w\s,]+>|[\w.]+)\s+(\w+)\s*=\s*\d+\s*;",
    re.MULTILINE,
)

# Matches:  package users;
_PACKAGE_RE = re.compile(r"package\s+([\w.]+)\s*;")

# ---------------------------------------------------------------------------
# endregion: --- Regex Patterns
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def parse_proto(
    source: bytes,
    file_path: str,
) -> tuple[list[RouteNode], list[StructNode], list[TraitNode]]:
    """Parse a ``.proto`` file and extract services, RPCs, and messages.

    Args:
        source: Raw file contents (UTF-8).
        file_path: Path for logging context.

    Returns:
        A tuple of ``(routes, structs, traits)`` where:
        - *routes* are RPC methods as ``RouteNode(method="GRPC")``.
        - *structs* are message definitions as ``StructNode``.
        - *traits* are service definitions as ``TraitNode``.
    """
    text = source.decode("utf-8", errors="replace")

    # Strip single-line comments to avoid false matches.
    text = _strip_comments(text)

    package = _extract_package(text)
    routes = _extract_rpcs(text, package)
    structs = _extract_messages(text)
    traits = _extract_services(text)

    return routes, structs, traits


# ---------------------------------------------------------------------------
# endregion: --- Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    """Remove ``//`` single-line comments from proto source."""
    return re.sub(r"//[^\n]*", "", text)


def _extract_package(text: str) -> str:
    """Extract the ``package`` declaration, or return ``""``."""
    m = _PACKAGE_RE.search(text)
    return m.group(1) if m else ""


def _extract_rpcs(text: str, package: str) -> list[RouteNode]:
    """Extract RPC definitions as ``RouteNode`` instances.

    Each RPC becomes a route with ``method="GRPC"`` and
    ``framework="grpc"``.  The ``path`` is formatted as
    ``"/{package}.{ServiceName}/{RpcName}"`` following the standard
    gRPC path convention.
    """
    routes: list[RouteNode] = []
    for svc_match in _SERVICE_RE.finditer(text):
        service_name = svc_match.group(1)
        body = svc_match.group(2)
        prefix = f"/{package}.{service_name}" if package else f"/{service_name}"
        for rpc_match in _RPC_RE.finditer(body):
            rpc_name = rpc_match.group(1)
            routes.append(
                RouteNode(
                    path=f"{prefix}/{rpc_name}",
                    method="GRPC",
                    handler=rpc_name,
                    framework="grpc",
                ),
            )
    return routes


def _extract_messages(text: str) -> list[StructNode]:
    """Extract ``message`` definitions as ``StructNode`` instances."""
    structs: list[StructNode] = []
    for msg_match in _MESSAGE_RE.finditer(text):
        name = msg_match.group(1)
        body = msg_match.group(2)
        fields = _parse_message_fields(body)
        structs.append(
            StructNode(
                name=name,
                visibility=Visibility.PUBLIC,
                fields=tuple(fields),
            ),
        )
    return structs


def _extract_services(text: str) -> list[TraitNode]:
    """Extract ``service`` definitions as ``TraitNode`` instances."""
    traits: list[TraitNode] = []
    for svc_match in _SERVICE_RE.finditer(text):
        service_name = svc_match.group(1)
        body = svc_match.group(2)
        items = _parse_rpc_items(body)
        traits.append(
            TraitNode(
                name=service_name,
                visibility=Visibility.PUBLIC,
                items=tuple(items),
            ),
        )
    return traits


def _parse_message_fields(body: str) -> list[FieldNode]:
    """Parse field declarations inside a ``message`` body."""
    fields: list[FieldNode] = []
    for m in _FIELD_RE.finditer(body):
        repeated = m.group(1) is not None
        raw_type = m.group(2).strip()
        name = m.group(3)
        type_str = f"repeated {raw_type}" if repeated else raw_type
        fields.append(
            FieldNode(
                name=name,
                type=type_str,
                visibility=Visibility.PUBLIC,
            ),
        )
    return fields


def _parse_rpc_items(body: str) -> list[TraitItemNode]:
    """Parse RPC methods inside a ``service`` body as trait items."""
    items: list[TraitItemNode] = []
    for m in _RPC_RE.finditer(body):
        rpc_name = m.group(1)
        _request_type = m.group(2)
        response_type = m.group(3)
        items.append(
            TraitItemNode(
                kind=TraitItemKind.REQUIRED_METHOD,
                name=rpc_name,
                return_type=response_type,
            ),
        )
    return items


# ---------------------------------------------------------------------------
# endregion: --- Internal Helpers
# ---------------------------------------------------------------------------
