"""SDK egress detection — outbound calls to known third-party SDKs.

SaaS SDK calls (``stripe.Charge.create(...)``, ``sentry_sdk.capture_exception``,
``openai.ChatCompletion.create(...)``) don't go through a raw HTTP client, so
they're invisible to :mod:`ast_intel.extractors._http_calls`.  This module maps
a file's imports to known vendors (via
:mod:`ast_intel.extractors._thirdparty_registry`) and flags method calls whose
receiver resolves to one of those SDKs, capturing the arguments as the data
being sent.

Scope: direct module/alias calls (``stripe.X``, ``Client(...)``,
``capture_exception(...)``).  Calls on instance variables assigned from an SDK
(``client = OpenAI(); client.chat...``) are resolved later by the intra-function
data-flow pass (L2).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ast_intel.extractors._egress_dataflow import resolve_payload_sources
from ast_intel.extractors._payload import (
    describe_value_node,
    dict_fields,
    find_enclosing_function,
    make_payload_field,
    node_text,
    payload_confidence,
)
from ast_intel.extractors._thirdparty_registry import ThirdPartyInfo, classify_sdk
from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import PayloadField, SdkCallNode

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = [
    "detect_python_sdk_calls",
]

# Top-level standard-library module names (never third-party egress).
_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)

# Third-party roots captured by *other* detectors — excluded here to avoid
# double counting: raw HTTP clients (HTTP-call detector) and cloud SDKs
# (cloud-resource detector).
_HANDLED_ELSEWHERE: frozenset[str] = frozenset({
    "requests", "httpx", "aiohttp", "urllib3", "urllib", "http", "websockets",
    "boto3", "botocore", "azure", "google",
})

# Generic classification for an unrecognized third-party package.
_UNKNOWN_INFO: ThirdPartyInfo = ThirdPartyInfo("unknown", "unknown", "outbound")


# ---------------------------------------------------------------------------
# region:    --- Import resolution
# ---------------------------------------------------------------------------


def _resolve_sdk_root(root: str) -> ThirdPartyInfo | None:
    """Classify an imported package root as an egress SDK.

    Returns the known :class:`ThirdPartyInfo`, a generic ``unknown`` info for
    an unrecognized third-party package (heuristic egress), or ``None`` when
    the package is stdlib, handled by another detector, or empty (a relative
    import).
    """
    if not root:
        return None
    info = classify_sdk(root)
    if info is not None:
        return info
    if root in _STDLIB or root in _HANDLED_ELSEWHERE:
        return None
    return _UNKNOWN_INFO


def _parse_python_sdk_imports(
    uses: list[str],
) -> dict[str, tuple[ThirdPartyInfo, str]]:
    """Map local names to ``(vendor_info, package_root)`` for known SDKs.

    Parses raw ``import`` / ``from ... import ...`` statements and keeps only
    the aliases whose package resolves to a known third-party SDK.
    """
    aliases: dict[str, tuple[ThirdPartyInfo, str]] = {}
    for raw in uses:
        line = raw.replace("(", " ").replace(")", " ").strip()
        if line.startswith("from "):
            _parse_from_import(line, aliases)
        elif line.startswith("import "):
            _parse_plain_import(line, aliases)
    return aliases


def _parse_from_import(
    line: str, aliases: dict[str, tuple[ThirdPartyInfo, str]],
) -> None:
    """Handle ``from pkg.sub import a, b as c``."""
    rest = line[len("from "):]
    if " import " not in rest:
        return
    pkg_path, names = rest.split(" import ", 1)
    root = pkg_path.strip().split(".")[0]
    info = _resolve_sdk_root(root)
    if info is None:
        return
    for name_part in names.split(","):
        entry = name_part.strip()
        if not entry or entry == "*":
            continue
        parts = entry.split(" as ")
        alias = parts[1].strip() if len(parts) == 2 else parts[0].strip()  # noqa: PLR2004
        if alias:
            aliases[alias] = (info, root)


def _parse_plain_import(
    line: str, aliases: dict[str, tuple[ThirdPartyInfo, str]],
) -> None:
    """Handle ``import mod.sub [as alias][, mod2 ...]``."""
    rest = line[len("import "):]
    for part in rest.split(","):
        token = part.strip()
        if not token:
            continue
        parts = token.split(" as ")
        module_path = parts[0].strip()
        root = module_path.split(".")[0]
        info = _resolve_sdk_root(root)
        if info is None:
            continue
        alias = parts[1].strip() if len(parts) == 2 else root  # noqa: PLR2004
        aliases[alias] = (info, root)


# endregion: --- Import resolution


# ---------------------------------------------------------------------------
# region:    --- Python SDK call detection
# ---------------------------------------------------------------------------


def _leftmost_root(callee: Node, src: bytes) -> str | None:
    """Return the leftmost identifier of a call target.

    ``stripe.Charge.create`` → ``"stripe"``; a bare ``capture_exception`` →
    ``"capture_exception"``.  Returns ``None`` when the receiver is not a
    simple identifier chain (e.g. a subscript or call result).
    """
    cur: Node | None = callee
    while cur is not None and cur.type == "attribute":
        cur = cur.child_by_field_name("object")
    if cur is not None and cur.type == "identifier":
        return node_text(cur, src)
    return None


def _extract_sdk_payload(args_node: Node, src: bytes) -> tuple[PayloadField, ...]:
    """Capture every argument of an SDK call as a body field.

    Unlike HTTP calls (where only specific kwargs carry data), *all*
    arguments of an SDK method are data sent to the vendor.
    """
    fields: list[PayloadField] = []
    for arg in args_node.named_children:
        if arg.type == "keyword_argument":
            name_node = arg.child_by_field_name("name")
            val_node = arg.child_by_field_name("value")
            if name_node is None or val_node is None:
                continue
            value, kind = describe_value_node(val_node, src)
            fields.append(make_payload_field("body", node_text(name_node, src), value, kind))
        elif arg.type == "dictionary":
            fields.extend(dict_fields(arg, src, "body"))
        elif arg.type == "comment":
            continue
        else:
            value, kind = describe_value_node(arg, src)
            fields.append(make_payload_field("body", "", value, kind))
    return tuple(fields)


def detect_python_sdk_calls(
    root: Node, src: bytes, uses: list[str],
) -> list[SdkCallNode]:
    """Detect calls to known third-party SDKs in Python source.

    Args:
        root: Tree-sitter root node of the file.
        src: Raw source bytes.
        uses: Raw import statements from :attr:`FileAST.uses`.
    """
    aliases = _parse_python_sdk_imports(uses)
    if not aliases:
        return []

    calls: list[SdkCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call":
            callee = node.child_by_field_name("function")
            if callee is not None:
                root_id = _leftmost_root(callee, src)
                if root_id is not None and root_id in aliases:
                    info, sdk_root = aliases[root_id]
                    args_node = node.child_by_field_name("arguments")
                    payload = (
                        _extract_sdk_payload(args_node, src)
                        if args_node is not None
                        else ()
                    )
                    payload = resolve_payload_sources(payload, node, src)
                    calls.append(SdkCallNode(
                        vendor=info.vendor,
                        category=info.category,
                        sdk=sdk_root,
                        method=node_text(callee, src),
                        caller=find_enclosing_function(node, src),
                        span=span_from_node(node),
                        payload=payload,
                        payload_confidence=payload_confidence(list(payload)),
                    ))

        stack.extend(node.children)

    return calls


# endregion: --- Python SDK call detection
