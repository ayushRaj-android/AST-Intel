"""OpenAPI / Swagger contract parser.

Parses OpenAPI 3.x and Swagger 2.0 specification files (YAML or JSON)
and returns :class:`~ast_intel.models.ast_node.RouteNode` instances for
operations and :class:`~ast_intel.models.ast_node.StructNode` instances
for schema definitions.

All data is extracted with ``EXTRACTED`` confidence — API contracts are
the canonical truth of a service's interface.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ast_intel.models.ast_node import (
    FieldNode,
    RouteNode,
    StructNode,
    Visibility,
)

__all__: list[str] = [
    "parse_openapi",
]

logger = logging.getLogger(__name__)

# HTTP methods recognised in OpenAPI path items.
_HTTP_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head", "trace",
})


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def parse_openapi(
    source: bytes,
    file_path: str,
) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse an OpenAPI / Swagger spec and extract routes and schemas.

    Supports both JSON and YAML input.  YAML requires ``pyyaml`` to be
    installed — if missing, only JSON files can be parsed.

    Args:
        source: Raw file contents.
        file_path: Path for logging context.

    Returns:
        A tuple of ``(routes, structs)`` extracted from the spec.
        Returns ``([], [])`` if the file is not a valid OpenAPI spec.
    """
    spec = _load_spec(source, file_path)
    if spec is None:
        return [], []

    # Detect spec version.
    if "openapi" in spec:
        return _parse_v3(spec)
    if "swagger" in spec:
        return _parse_v2(spec)

    logger.debug("%s: not an OpenAPI spec (no 'openapi' or 'swagger' key)", file_path)
    return [], []


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Spec Loading
# ---------------------------------------------------------------------------


def _load_spec(source: bytes, file_path: str) -> dict[str, Any] | None:
    """Decode *source* as JSON or YAML, returning the top-level dict."""
    text = source.decode("utf-8", errors="replace")

    # Try JSON first (no external dep, faster).
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back to YAML.
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.debug(
            "%s: cannot parse YAML (pyyaml not installed). "
            "Install with: pip install 'ast-intel[contracts]'",
            file_path,
        )
        return None

    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        logger.debug("%s: invalid YAML", file_path)

    return None


# endregion: --- Spec Loading


# ---------------------------------------------------------------------------
# region:    --- OpenAPI 3.x Parsing
# ---------------------------------------------------------------------------


def _parse_v3(
    spec: dict[str, Any],
) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse an OpenAPI 3.x specification."""
    routes = _extract_routes(spec.get("paths", {}))
    structs = _extract_schemas_v3(spec)
    return routes, structs


def _extract_schemas_v3(
    spec: dict[str, Any],
) -> list[StructNode]:
    """Extract ``StructNode`` instances from ``components.schemas``."""
    components = spec.get("components", {})
    if not isinstance(components, dict):
        return []
    schemas = components.get("schemas", {})
    if not isinstance(schemas, dict):
        return []
    return _schemas_to_structs(schemas, spec)


# endregion: --- OpenAPI 3.x Parsing


# ---------------------------------------------------------------------------
# region:    --- Swagger 2.0 Parsing
# ---------------------------------------------------------------------------


def _parse_v2(
    spec: dict[str, Any],
) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse a Swagger 2.0 specification."""
    routes = _extract_routes(spec.get("paths", {}))
    structs = _extract_schemas_v2(spec)
    return routes, structs


def _extract_schemas_v2(
    spec: dict[str, Any],
) -> list[StructNode]:
    """Extract ``StructNode`` instances from ``definitions``."""
    definitions = spec.get("definitions", {})
    if not isinstance(definitions, dict):
        return []
    return _schemas_to_structs(definitions, spec)


# endregion: --- Swagger 2.0 Parsing


# ---------------------------------------------------------------------------
# region:    --- Shared Helpers
# ---------------------------------------------------------------------------


def _extract_routes(paths: dict[str, Any]) -> list[RouteNode]:
    """Walk ``paths`` and emit a :class:`RouteNode` per operation."""
    if not isinstance(paths, dict):
        return []

    routes: list[RouteNode] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            handler = operation.get("operationId", "")
            routes.append(
                RouteNode(
                    path=str(path),
                    method=method.upper(),
                    handler=str(handler),
                    framework="openapi",
                ),
            )
    return routes


def _schemas_to_structs(
    schemas: dict[str, Any],
    spec: dict[str, Any],
) -> list[StructNode]:
    """Convert a schema definitions dict to ``StructNode`` instances.

    Only ``type: "object"`` schemas (or schemas with ``properties``)
    are converted.
    """
    structs: list[StructNode] = []
    for name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        # Accept explicit objects or schemas that have properties.
        if schema.get("type") != "object" and "properties" not in schema:
            continue

        fields = _extract_fields(schema, spec)
        doc = schema.get("description", "")
        structs.append(
            StructNode(
                name=str(name),
                visibility=Visibility.PUBLIC,
                fields=tuple(fields),
                doc=str(doc) if doc else "",
            ),
        )
    return structs


def _extract_fields(
    schema: dict[str, Any],
    spec: dict[str, Any],
) -> list[FieldNode]:
    """Extract fields from a schema's ``properties``."""
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return []

    fields: list[FieldNode] = []
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_schema, dict):
            continue
        type_str = _resolve_type(prop_schema, spec)
        fields.append(
            FieldNode(
                name=str(prop_name),
                type=type_str,
                visibility=Visibility.PUBLIC,
            ),
        )
    return fields


def _resolve_type(  # noqa: C901, PLR0911
    prop: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    """Determine the type string for a property schema.

    Handles ``$ref``, ``type``, ``type: array`` with ``items``, and
    ``allOf`` / ``oneOf`` combinators.
    """
    # $ref: "#/components/schemas/Foo" or "#/definitions/Foo"
    ref = prop.get("$ref")
    if isinstance(ref, str):
        return _ref_basename(ref)

    schema_type = prop.get("type", "")

    # Array with items.
    if schema_type == "array":
        items = prop.get("items", {})
        if isinstance(items, dict):
            inner = _resolve_type(items, spec)
            return f"array<{inner}>"
        return "array"

    # allOf / oneOf / anyOf — pick first concrete type.
    for combinator in ("allOf", "oneOf", "anyOf"):
        parts = prop.get(combinator)
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    resolved = _resolve_type(part, spec)
                    if resolved:
                        return resolved

    # Primitive type with optional format.
    fmt = prop.get("format", "")
    if schema_type and fmt:
        return f"{schema_type}({fmt})"
    if schema_type:
        return str(schema_type)

    return "object"


def _ref_basename(ref: str) -> str:
    """Extract the schema name from a ``$ref`` pointer.

    ``"#/components/schemas/Pet"`` → ``"Pet"``
    ``"#/definitions/User"`` → ``"User"``
    """
    return ref.rsplit("/", 1)[-1] if "/" in ref else ref


# endregion: --- Shared Helpers
