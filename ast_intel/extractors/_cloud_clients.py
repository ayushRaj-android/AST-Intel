"""Cloud SDK client detection — detect cloud infrastructure usage across languages.

Detects instantiation of cloud SDK clients (``CosmosClient``, ``ServiceBusClient``,
``boto3.client('sqs')``, ``redis.Redis``, etc.) and returns them as
:class:`~ast_intel.models.ast_node.CloudResourceNode` instances.

All detectors use a **tree-sitter body walk** strategy since client
instantiations appear inside function bodies as ``object_creation_expression``
(C#), ``new_expression`` (TS/Java), or ``call`` (Python) nodes.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ast_intel.extractors._cloud_taxonomy import (
    CloudResourceInfo,
    classify_boto3_service,
    classify_client,
    classify_python_qualified,
)
from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import CloudResourceNode

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = [
    "detect_cloud_clients_csharp",
    "detect_cloud_clients_java",
    "detect_cloud_clients_python",
    "detect_cloud_clients_typescript",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------

_CALLER_KINDS: frozenset[str] = frozenset({
    "method_declaration", "function_declaration", "function_definition",
    "local_function_statement", "lambda_expression",
    "constructor_declaration", "property_declaration",
    "function", "arrow_function", "method_definition",
    "constructor_body",
    "function_item",  # Rust
})


def _find_caller(node: Node) -> str:
    """Walk up the tree to find the enclosing function/method name."""
    cur = node.parent
    while cur is not None:
        if cur.type in _CALLER_KINDS:
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                return name_node.text.decode("utf-8", errors="replace")
        cur = cur.parent
    return "<module>"


def _first_string_arg(node: Node) -> str:
    """Extract the first string-literal argument from a call/creation node.

    Returns the unquoted string content, or empty string if not found.
    """
    args = node.child_by_field_name("arguments") or node.child_by_field_name(
        "argument_list",
    )
    if args is None:
        return ""
    for child in args.children:
        if child.type in ("string", "string_literal", "interpreted_string_literal"):
            raw = child.text.decode("utf-8", errors="replace")
            return raw.strip("\"'`")
        if child.type == "verbatim_string_literal":
            raw = child.text.decode("utf-8", errors="replace")
            return raw.lstrip("@$").strip('"')
    return ""


def _make_node(
    info: CloudResourceInfo,
    client: str,
    caller: str,
    name: str,
    ts_node: Node,
) -> CloudResourceNode:
    return CloudResourceNode(
        provider=info.provider,
        service=info.service,
        category=info.category,
        client=client,
        caller=caller,
        name=name,
        span=span_from_node(ts_node),
    )


# endregion


# ---------------------------------------------------------------------------
# region:    --- C# detector
# ---------------------------------------------------------------------------

_CS_NEW_KINDS: frozenset[str] = frozenset({
    "object_creation_expression",
    "implicit_object_creation_expression",
})


def detect_cloud_clients_csharp(
    root: Node, source: bytes,
) -> list[CloudResourceNode]:
    """Detect cloud SDK client instantiations in C# source."""
    results: list[CloudResourceNode] = []
    stack = [root]

    while stack:
        node = stack.pop()
        if node.type in _CS_NEW_KINDS:
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                # Handle generic types: DbContext<T> → DbContext
                type_text = type_node.text.decode("utf-8", errors="replace")
                class_name = type_text.split("<")[0].split(".")[-1].strip()
                info = classify_client(class_name)
                if info is not None:
                    caller = _find_caller(node)
                    name = _first_string_arg(node)
                    results.append(_make_node(info, class_name, caller, name, node))

        # Also detect DI extension methods: .AddDbContext<T>(), .AddStackExchangeRedisCache()
        if node.type == "invocation_expression":
            # member_access_expression → name is the method
            name_node = node.child_by_field_name("function")
            if name_node is not None and name_node.type == "member_access_expression":
                method_node = name_node.child_by_field_name("name")
                if method_node is not None:
                    method_name = method_node.text.decode("utf-8", errors="replace")
                    # Strip generic type args: AddDbContext<AppDbContext> → AddDbContext
                    method_name = method_name.split("<")[0]
                    info = classify_client(method_name)
                    if info is not None:
                        caller = _find_caller(node)
                        name = _first_string_arg(node)
                        results.append(
                            _make_node(info, method_name, caller, name, node),
                        )

        stack.extend(node.children)

    return results


# endregion


# ---------------------------------------------------------------------------
# region:    --- Python detector
# ---------------------------------------------------------------------------

_BOTO3_PATTERN: re.Pattern[str] = re.compile(
    r"""boto3\s*\.\s*(?:client|resource)\s*\(\s*['"]([a-z0-9_]+)['"]""",
    re.VERBOSE,
)


def detect_cloud_clients_python(
    root: Node, source: bytes,
) -> list[CloudResourceNode]:
    """Detect cloud SDK client instantiations in Python source."""
    results: list[CloudResourceNode] = []
    stack = [root]

    while stack:
        node = stack.pop()
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func is not None:
                func_text = func.text.decode("utf-8", errors="replace").strip()

                # Pattern 1: boto3.client('service') / boto3.resource('service')
                if "boto3" in func_text:
                    snippet = node.text.decode("utf-8", errors="replace")
                    m = _BOTO3_PATTERN.search(snippet)
                    if m:
                        svc_name = m.group(1)
                        info = classify_boto3_service(svc_name)
                        if info:
                            caller = _find_caller(node)
                            results.append(
                                _make_node(info, f"boto3.client('{svc_name}')", caller, "", node),
                            )

                # Pattern 2: module.Class(...) e.g. redis.Redis(), storage.Client()
                else:
                    info = classify_python_qualified(func_text)
                    if info:
                        caller = _find_caller(node)
                        name = _first_string_arg(node)
                        results.append(
                            _make_node(info, func_text, caller, name, node),
                        )
                    else:
                        # Pattern 3: Class(...) e.g. MongoClient(), CosmosClient()
                        class_name = func_text.split(".")[-1].split("(")[0]
                        info = classify_client(class_name)
                        if info:
                            caller = _find_caller(node)
                            name = _first_string_arg(node)
                            results.append(
                                _make_node(info, class_name, caller, name, node),
                            )

        stack.extend(node.children)

    return results


# endregion


# ---------------------------------------------------------------------------
# region:    --- Java detector
# ---------------------------------------------------------------------------


def detect_cloud_clients_java(
    root: Node, source: bytes,
) -> list[CloudResourceNode]:
    """Detect cloud SDK client instantiations in Java source."""
    results: list[CloudResourceNode] = []
    stack = [root]

    while stack:
        node = stack.pop()
        # `new XxxClient(...)` or builder patterns `XxxClient.builder().build()`
        if node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                type_text = type_node.text.decode("utf-8", errors="replace")
                class_name = type_text.split("<")[0].split(".")[-1].strip()
                info = classify_client(class_name)
                if info:
                    caller = _find_caller(node)
                    name = _first_string_arg(node)
                    results.append(_make_node(info, class_name, caller, name, node))

        # Builder pattern: SqsClient.builder()...build() — detect .builder() calls
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                method = name_node.text.decode("utf-8", errors="replace")
                if method in ("builder", "create"):
                    obj = node.child_by_field_name("object")
                    if obj is not None:
                        obj_text = obj.text.decode("utf-8", errors="replace")
                        class_name = obj_text.split(".")[-1].strip()
                        info = classify_client(class_name)
                        if info:
                            caller = _find_caller(node)
                            results.append(
                                _make_node(info, class_name, caller, "", node),
                            )

        stack.extend(node.children)

    return results


# endregion


# ---------------------------------------------------------------------------
# region:    --- TypeScript / JavaScript detector
# ---------------------------------------------------------------------------


def detect_cloud_clients_typescript(
    root: Node, source: bytes,
) -> list[CloudResourceNode]:
    """Detect cloud SDK client instantiations in TypeScript/JavaScript source."""
    results: list[CloudResourceNode] = []
    stack = [root]

    while stack:
        node = stack.pop()
        # `new CosmosClient(...)`, `new S3Client(...)`
        if node.type == "new_expression":
            constructor = node.child_by_field_name("constructor")
            if constructor is not None:
                ctor_text = constructor.text.decode("utf-8", errors="replace")
                class_name = ctor_text.split("(")[0].split(".")[-1].strip()
                info = classify_client(class_name)
                if info:
                    caller = _find_caller(node)
                    name = _first_string_arg(node)
                    results.append(_make_node(info, class_name, caller, name, node))

        # Detect function-call-style constructors (createClient, Sequelize, etc.)
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func is not None:
                func_text = func.text.decode("utf-8", errors="replace")
                # Handle Rust path (X::new) and JS dot (X.create)
                parts = re.split(r"\.|::", func_text.split("(")[0])
                # Try the first segment (Rust: RedisClient from RedisClient::new)
                class_name = parts[0].strip()
                info = classify_client(class_name)
                if info is None and len(parts) > 1:
                    # Fallback to last segment (JS: createClient from redis.createClient)
                    class_name = parts[-1].strip()
                    info = classify_client(class_name)
                if info:
                    caller = _find_caller(node)
                    name = _first_string_arg(node)
                    results.append(_make_node(info, class_name, caller, name, node))

        stack.extend(node.children)

    return results


# endregion
