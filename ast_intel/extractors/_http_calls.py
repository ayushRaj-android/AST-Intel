"""HTTP client call detection — detect outgoing HTTP requests across languages.

Detects calls to HTTP client libraries (``reqwest``, ``requests``,
``fetch``, ``axios``, etc.) and returns them as
:class:`~ast_intel.models.ast_node.HttpCallNode` instances.

All detectors use a **tree-sitter body walk** strategy since HTTP
client calls appear inside function bodies as ``call_expression``
nodes — never as decorators or annotations.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ast_intel.extractors._egress_dataflow import resolve_payload_sources
from ast_intel.extractors._payload import (
    describe_value_node as _describe_value_node,
)
from ast_intel.extractors._payload import (
    dict_fields as _dict_fields,
)
from ast_intel.extractors._payload import (
    find_enclosing_function as _find_enclosing_function,
)
from ast_intel.extractors._payload import (
    make_payload_field as _make_payload_field,
)
from ast_intel.extractors._payload import (
    node_text as _node_text,
)
from ast_intel.extractors._payload import (
    object_fields as _object_fields,
)
from ast_intel.extractors._payload import (
    payload_confidence as _payload_confidence,
)
from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import HttpCallNode, PayloadField

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = [
    "detect_axios_calls",
    "detect_fetch_calls",
    "detect_go_http_calls",
    "detect_python_http_calls",
    "detect_reqwest_calls",
    "detect_spring_http_calls",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

_HTTP_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "delete", "patch", "head", "options",
})

# Python: known HTTP client object names.
_PY_HTTP_OBJECTS: frozenset[str] = frozenset({
    "requests", "httpx", "client", "session",
    "aiohttp", "self",
})

# Python: method names that are HTTP methods on known clients.
_PY_HTTP_METHODS: frozenset[str] = _HTTP_METHODS | frozenset({
    "request",
})

# Rust: reqwest scoped identifiers that are HTTP calls.
_REQWEST_SCOPED: frozenset[str] = frozenset({
    "reqwest",
})

# Java RestTemplate methods → HTTP method.
_SPRING_METHODS: dict[str, str] = {
    "getForObject": "GET",
    "getForEntity": "GET",
    "postForObject": "POST",
    "postForEntity": "POST",
    "postForLocation": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patchForObject": "PATCH",
    "exchange": "UNKNOWN",
    "execute": "UNKNOWN",
}

# Java: known REST client object names.
_JAVA_HTTP_OBJECTS: frozenset[str] = frozenset({
    "restTemplate", "webClient",
})

# Go net/http function → HTTP method.
_GO_HTTP_FUNCS: dict[str, str] = {
    "Get": "GET",
    "Post": "POST",
    "Head": "HEAD",
    "PostForm": "POST",
    "NewRequest": "UNKNOWN",
}

# Go: package selectors for HTTP clients.
_GO_HTTP_PACKAGES: frozenset[str] = frozenset({
    "http",
})

# f-string interpolation pattern — Python ``{expr}``.
_FSTRING_INTERP_RE: re.Pattern[str] = re.compile(r"\{[^}]*\}")

# Rust format! — ``{}`` or ``{name}``.
_FORMAT_INTERP_RE: re.Pattern[str] = re.compile(r"\{[^}]*\}")

# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _extract_url_from_node(node: Node, src: bytes) -> str:
    """Extract a URL string from various node types.

    Returns the URL path for string literals, replaces interpolation
    segments with ``{param}`` for f-strings/templates, or returns
    ``"<dynamic>"`` for fully dynamic expressions.
    """
    ntype = node.type

    # Plain string literals (and Python f-strings share type "string").
    if ntype in ("string_literal", "string", "raw_string_literal",
                 "interpreted_string_literal"):
        raw = _node_text(node, src)
        # Python f-string: replace interpolation segments.
        if raw.startswith(('f"', "f'", 'f"""')):
            inner = raw.lstrip("fFbBrRuU").strip("\"'")
            return _FSTRING_INTERP_RE.sub("{param}", inner)
        return raw.strip("\"'`")

    # TypeScript template string: `/api/${userId}`.
    if ntype == "template_string":
        raw = _node_text(node, src).strip("`")
        # Replace ${...} substitutions with {param}.
        return re.sub(r"\$\{[^}]*\}", "{param}", raw)

    # Rust macro_invocation (format!).
    if ntype == "macro_invocation":
        ident = None
        for child in node.children:
            if child.type == "identifier":
                ident = _node_text(child, src)
            elif child.type == "token_tree" and ident == "format":
                return _extract_url_from_format_macro(child, src)
        return "<dynamic>"

    # Reference to a variable / expression — unresolvable.
    return "<dynamic>"


def _extract_url_from_format_macro(
    token_tree: Node, src: bytes,
) -> str:
    """Extract URL from a Rust ``format!(...)`` token_tree.

    The first ``string_literal`` child contains the format template.
    """
    for child in token_tree.children:
        if child.type == "string_literal":
            # Pull the string_content from inside the quotes.
            for sub in child.children:
                if sub.type == "string_content":
                    raw = _node_text(sub, src)
                    return _FORMAT_INTERP_RE.sub("{param}", raw)
            # Fallback: strip quotes from the literal directly.
            return _FORMAT_INTERP_RE.sub(
                "{param}", _node_text(child, src).strip('"'),
            )
    return "<dynamic>"


def _get_named_args(node: Node) -> list[Node]:
    """Return named children of an ``arguments`` node."""
    return list(node.named_children)


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Rust: reqwest HTTP calls
# ---------------------------------------------------------------------------


def detect_reqwest_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect ``reqwest`` HTTP client calls in Rust source.

    Patterns detected:

    - ``reqwest::get(url).await``  (scoped call)
    - ``client.get(url).send().await``  (method chain)
    - ``self.client.post(url).body(x).send().await``
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            result = _try_parse_reqwest_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_reqwest_call(  # noqa: C901, PLR0912
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a ``call_expression`` as a reqwest HTTP call.

    Two patterns:

    1. **Scoped**: ``reqwest::get(url)`` — ``scoped_identifier`` with
       ``reqwest`` scope and HTTP method name.
    2. **Method**: ``client.post(url)`` — ``field_expression`` where the
       field_identifier is an HTTP method name.
    """
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type in (
            "field_expression", "scoped_identifier", "identifier",
        ):
            fn_node = child
        elif child.type == "arguments":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    http_method = ""
    # Pattern 1: reqwest::get(url)
    if fn_node.type == "scoped_identifier":
        segs = [c for c in fn_node.children if c.type != "::"]
        if len(segs) >= 2:  # noqa: PLR2004
            scope = _node_text(segs[0], src)
            method = _node_text(segs[-1], src)
            if scope in _REQWEST_SCOPED and method in _HTTP_METHODS:
                http_method = method

    # Pattern 2: client.post(url)
    elif fn_node.type == "field_expression":
        for child in fn_node.children:
            if child.type == "field_identifier":
                method = _node_text(child, src)
                if method in _HTTP_METHODS:
                    http_method = method

    if not http_method:
        return None

    # Extract URL from first argument.
    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    url = _extract_url_from_node(named_args[0], src)
    caller = _find_enclosing_function(node, src)

    return HttpCallNode(
        url=url,
        method=http_method.upper(),
        library="reqwest",
        caller=caller,
        span=span_from_node(node),
    )


# endregion: --- Rust: reqwest HTTP calls


# ---------------------------------------------------------------------------
# region:    --- Python: requests / httpx / aiohttp HTTP calls
# ---------------------------------------------------------------------------


def detect_python_http_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect Python HTTP client calls.

    Patterns detected:

    - ``requests.get(url)`` / ``requests.post(url, ...)``
    - ``httpx.get(url)`` / ``httpx.AsyncClient().get(url)``
    - ``session.get(url)``  (aiohttp pattern)
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call":
            result = _try_parse_python_http_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_python_http_call(
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a Python ``call`` node as an HTTP client call.

    Expected structure::

        call
          attribute
            identifier: "requests" / "httpx" / "client" / "session"
            identifier: "get" / "post" / ...
          argument_list
            string: "url"
    """
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type == "attribute":
            fn_node = child
        elif child.type == "argument_list":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    # Extract object.method from the attribute node.
    idents = [
        c for c in fn_node.children if c.type == "identifier"
    ]
    if len(idents) < 2:  # noqa: PLR2004
        return None

    obj_name = _node_text(idents[0], src)
    method_name = _node_text(idents[-1], src)

    if obj_name not in _PY_HTTP_OBJECTS:
        return None
    if method_name not in _PY_HTTP_METHODS:
        return None

    # Determine library name.
    lib = obj_name
    if obj_name in ("client", "session", "self"):
        lib = "httpx"

    # Extract URL from first argument.
    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    url = _extract_url_from_node(named_args[0], src)
    caller = _find_enclosing_function(node, src)
    payload = _extract_python_payload(args_node, src)
    payload = resolve_payload_sources(payload, node, src)

    return HttpCallNode(
        url=url,
        method=method_name.upper() if method_name != "request" else "UNKNOWN",
        library=lib,
        caller=caller,
        span=span_from_node(node),
        payload=payload,
        payload_confidence=_payload_confidence(list(payload)),
    )


# Python HTTP kwarg → payload location.
_PY_PAYLOAD_KWARGS: dict[str, str] = {
    "json": "body",
    "data": "body",
    "content": "body",
    "files": "body",
    "params": "query",
    "headers": "header",
    "cookies": "header",
    "auth": "header",
}


def _extract_python_payload(
    args_node: Node, src: bytes,
) -> tuple[PayloadField, ...]:
    """Extract body/header/query fields from a Python HTTP call's kwargs.

    Handles ``json=``, ``data=``, ``params=``, ``headers=`` (and related)
    keyword arguments.  Dict literals contribute one field per key; a bare
    variable (e.g. ``json=payload``) is captured as a single unnamed field
    for later intra-function resolution.
    """
    fields: list[PayloadField] = []
    for arg in args_node.named_children:
        if arg.type != "keyword_argument":
            continue
        name_node = arg.child_by_field_name("name")
        val_node = arg.child_by_field_name("value")
        if name_node is None or val_node is None:
            continue
        location = _PY_PAYLOAD_KWARGS.get(_node_text(name_node, src))
        if location is None:
            continue
        if val_node.type == "dictionary":
            fields.extend(_dict_fields(val_node, src, location))
        else:
            value, kind = _describe_value_node(val_node, src)
            fields.append(_make_payload_field(location, "", value, kind))
    return tuple(fields)



# endregion: --- Python: requests / httpx / aiohttp HTTP calls


# ---------------------------------------------------------------------------
# region:    --- TypeScript: fetch() calls
# ---------------------------------------------------------------------------


def detect_fetch_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect ``fetch(url)`` and ``fetch(url, options)`` calls.

    If the second argument contains ``method: "POST"`` etc., the HTTP
    method is extracted; otherwise defaults to ``"GET"``.
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            result = _try_parse_fetch_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_fetch_call(
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a ``call_expression`` as a ``fetch()`` call.

    Expected structure::

        call_expression
          identifier: "fetch"
          arguments
            string | template_string: "/url"
            [object]?
    """
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type == "identifier":
            fn_node = child
        elif child.type == "arguments":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    if _node_text(fn_node, src) != "fetch":
        return None

    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    url = _extract_url_from_node(named_args[0], src)

    # Try to extract HTTP method from options object.
    method = "GET"
    payload: tuple[PayloadField, ...] = ()
    if len(named_args) >= 2:  # noqa: PLR2004
        method = _extract_method_from_options(named_args[1], src)
        payload = _extract_fetch_payload(named_args[1], src)

    caller = _find_enclosing_function(node, src)

    return HttpCallNode(
        url=url,
        method=method,
        library="fetch",
        caller=caller,
        span=span_from_node(node),
        payload=payload,
        payload_confidence=_payload_confidence(list(payload)),
    )


def _unwrap_json_stringify(node: Node, src: bytes) -> Node:
    """Return the object inside ``JSON.stringify(obj)``, else ``node``."""
    if node.type != "call_expression":
        return node
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type == "member_expression":
            fn_node = child
        elif child.type == "arguments":
            args_node = child
    if (
        fn_node is not None
        and args_node is not None
        and "stringify" in _node_text(fn_node, src)
    ):
        inner = _get_named_args(args_node)
        if inner:
            return inner[0]
    return node


def _extract_fetch_payload(
    options_node: Node, src: bytes,
) -> tuple[PayloadField, ...]:
    """Extract ``body`` and ``headers`` fields from a fetch options object."""
    if options_node.type != "object":
        return ()
    fields: list[PayloadField] = []
    for pair in options_node.named_children:
        if pair.type != "pair":
            continue
        key_node = pair.child_by_field_name("key")
        val_node = pair.child_by_field_name("value")
        if key_node is None or val_node is None:
            continue
        key = _node_text(key_node, src).strip("\"'`")
        if key == "body":
            obj = _unwrap_json_stringify(val_node, src)
            if obj.type == "object":
                fields.extend(_object_fields(obj, src, "body"))
            else:
                value, kind = _describe_value_node(obj, src)
                fields.append(_make_payload_field("body", "", value, kind))
        elif key == "headers" and val_node.type == "object":
            fields.extend(_object_fields(val_node, src, "header"))
    return tuple(fields)


def _extract_method_from_options(
    node: Node, src: bytes,
) -> str:
    """Extract ``method: "POST"`` from a JS/TS object literal.

    Returns ``"GET"`` if no method property is found.
    """
    if node.type != "object":
        return "GET"
    for child in node.children:
        if child.type == "pair":
            key_node = None
            val_node = None
            for pair_child in child.children:
                if pair_child.type == "property_identifier":
                    key_node = pair_child
                elif pair_child.type == "string":
                    val_node = pair_child
            if (
                key_node is not None
                and _node_text(key_node, src) == "method"
                and val_node is not None
            ):
                return _node_text(val_node, src).strip("\"'").upper()
    return "GET"


# endregion: --- TypeScript: fetch() calls


# ---------------------------------------------------------------------------
# region:    --- TypeScript: axios calls
# ---------------------------------------------------------------------------


def detect_axios_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect ``axios.get(url)`` / ``axios.post(url, ...)`` calls.

    Pattern: ``member_expression`` with ``axios`` receiver and HTTP
    method name as property.
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            result = _try_parse_axios_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_axios_call(  # noqa: C901
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a ``call_expression`` as an axios HTTP call.

    Expected tree-sitter structure: ``call_expression`` containing a
    ``member_expression`` (``identifier:"axios"`` + ``property_identifier``)
    and an ``arguments`` node with the URL string.
    """
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type == "member_expression":
            fn_node = child
        elif child.type == "arguments":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    # Extract object.method from the member_expression.
    obj_name = ""
    method_name = ""
    for child in fn_node.children:
        if child.type == "identifier":
            obj_name = _node_text(child, src)
        elif child.type == "property_identifier":
            method_name = _node_text(child, src)

    if obj_name != "axios":
        return None
    if method_name not in _HTTP_METHODS:
        return None

    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    url = _extract_url_from_node(named_args[0], src)
    caller = _find_enclosing_function(node, src)
    payload = _extract_axios_payload(named_args, src)

    return HttpCallNode(
        url=url,
        method=method_name.upper(),
        library="axios",
        caller=caller,
        span=span_from_node(node),
        payload=payload,
        payload_confidence=_payload_confidence(list(payload)),
    )


def _extract_axios_payload(
    named_args: list[Node], src: bytes,
) -> tuple[PayloadField, ...]:
    """Extract body/header/query fields from ``axios.post(url, data, config)``.

    The 2nd argument is the request body; the optional 3rd argument is a
    config object whose ``headers`` / ``params`` are captured.
    """
    fields: list[PayloadField] = []
    if len(named_args) >= 2:  # noqa: PLR2004
        data = named_args[1]
        if data.type == "object":
            fields.extend(_object_fields(data, src, "body"))
        else:
            value, kind = _describe_value_node(data, src)
            fields.append(_make_payload_field("body", "", value, kind))
    if len(named_args) >= 3:  # noqa: PLR2004
        config = named_args[2]
        if config.type == "object":
            for pair in config.named_children:
                if pair.type != "pair":
                    continue
                key_node = pair.child_by_field_name("key")
                val_node = pair.child_by_field_name("value")
                if key_node is None or val_node is None:
                    continue
                key = _node_text(key_node, src).strip("\"'`")
                if key in ("headers", "params") and val_node.type == "object":
                    location = "header" if key == "headers" else "query"
                    fields.extend(_object_fields(val_node, src, location))
    return tuple(fields)


# endregion: --- TypeScript: axios calls


# ---------------------------------------------------------------------------
# region:    --- Java: Spring RestTemplate / WebClient HTTP calls
# ---------------------------------------------------------------------------


def detect_spring_http_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect Spring ``RestTemplate`` and ``WebClient`` HTTP calls.

    Patterns detected:

    - ``restTemplate.getForObject(url, ...)``
    - ``restTemplate.postForObject(url, ...)``
    - ``restTemplate.exchange(url, ...)``
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "method_invocation":
            result = _try_parse_spring_http_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_spring_http_call(
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a ``method_invocation`` as a Spring HTTP call.

    Expected tree-sitter structure::

        method_invocation
          identifier: "restTemplate"
          identifier: "getForObject"
          argument_list
            string_literal: "url"
    """
    idents = [
        c for c in node.children if c.type == "identifier"
    ]
    args_node: Node | None = None
    for child in node.children:
        if child.type == "argument_list":
            args_node = child

    if len(idents) < 2 or args_node is None:  # noqa: PLR2004
        return None

    obj_name = _node_text(idents[0], src)
    method_name = _node_text(idents[1], src)

    if obj_name not in _JAVA_HTTP_OBJECTS:
        return None
    if method_name not in _SPRING_METHODS:
        return None

    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    url = _extract_url_from_node(named_args[0], src)
    http_method = _SPRING_METHODS[method_name]
    caller = _find_enclosing_function(node, src)
    payload = _extract_spring_body(http_method, named_args, src)

    return HttpCallNode(
        url=url,
        method=http_method,
        library="spring",
        caller=caller,
        span=span_from_node(node),
        payload=payload,
        payload_confidence=_payload_confidence(list(payload)),
    )


def _extract_spring_body(
    http_method: str, named_args: list[Node], src: bytes,
) -> tuple[PayloadField, ...]:
    """Best-effort capture of the request body for Spring write calls.

    For ``postForObject`` / ``put`` / ``patchForObject`` the 2nd positional
    argument is the request body.  Response-type class literals
    (``User.class``) are skipped.
    """
    if http_method not in ("POST", "PUT", "PATCH") or len(named_args) < 2:  # noqa: PLR2004
        return ()
    body = named_args[1]
    if _node_text(body, src).endswith(".class"):
        return ()
    value, kind = _describe_value_node(body, src)
    return (_make_payload_field("body", "", value, kind),)


# endregion: --- Java: Spring RestTemplate / WebClient HTTP calls


# ---------------------------------------------------------------------------
# region:    --- Go: net/http HTTP calls
# ---------------------------------------------------------------------------


def detect_go_http_calls(
    root: Node, src: bytes,
) -> list[HttpCallNode]:
    """Detect Go ``net/http`` client calls.

    Patterns detected:

    - ``http.Get(url)``
    - ``http.Post(url, contentType, body)``
    - ``http.NewRequest("METHOD", url, body)``
    """
    calls: list[HttpCallNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            result = _try_parse_go_http_call(node, src)
            if result is not None:
                calls.append(result)

        stack.extend(node.children)

    return calls


def _try_parse_go_http_call(  # noqa: C901, PLR0912
    node: Node, src: bytes,
) -> HttpCallNode | None:
    """Try to parse a Go ``call_expression`` as an HTTP call.

    Expected structure::

        call_expression
          selector_expression
            identifier: "http"
            field_identifier: "Get" / "Post" / "NewRequest"
          argument_list
            interpreted_string_literal: "url"
    """
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type == "selector_expression":
            fn_node = child
        elif child.type == "argument_list":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    pkg_name = ""
    func_name = ""
    for child in fn_node.children:
        if child.type == "identifier":
            pkg_name = _node_text(child, src)
        elif child.type == "field_identifier":
            func_name = _node_text(child, src)

    if pkg_name not in _GO_HTTP_PACKAGES:
        return None
    if func_name not in _GO_HTTP_FUNCS:
        return None

    named_args = _get_named_args(args_node)
    if not named_args:
        return None

    http_method = _GO_HTTP_FUNCS[func_name]

    # http.NewRequest("METHOD", url, body) — method is first arg, URL
    # is second.
    if func_name == "NewRequest":
        if len(named_args) >= 2:  # noqa: PLR2004
            raw_method = _node_text(named_args[0], src).strip("\"'")
            http_method = raw_method.upper() if raw_method else "UNKNOWN"
            url = _extract_url_from_node(named_args[1], src)
        else:
            return None
    else:
        url = _extract_url_from_node(named_args[0], src)

    caller = _find_enclosing_function(node, src)
    payload = _extract_go_body(func_name, named_args, src)

    return HttpCallNode(
        url=url,
        method=http_method,
        library="net/http",
        caller=caller,
        span=span_from_node(node),
        payload=payload,
        payload_confidence=_payload_confidence(list(payload)),
    )


def _extract_go_body(
    func_name: str, named_args: list[Node], src: bytes,
) -> tuple[PayloadField, ...]:
    """Best-effort capture of the request body for Go ``net/http`` calls.

    ``http.Post(url, contentType, body)`` and
    ``http.NewRequest(method, url, body)`` both carry the body as the 3rd
    argument.  A ``nil`` body yields no payload.
    """
    if func_name not in ("Post", "NewRequest") or len(named_args) < 3:  # noqa: PLR2004
        return ()
    body = named_args[2]
    if _node_text(body, src).strip() in ("nil", ""):
        return ()
    value, kind = _describe_value_node(body, src)
    return (_make_payload_field("body", "", value, kind),)


# endregion: --- Go: net/http HTTP calls
