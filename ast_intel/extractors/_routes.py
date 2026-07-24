"""HTTP route extraction — detect endpoint definitions across web frameworks.

Detects server-side HTTP route registrations from popular web frameworks
and returns them as :class:`~ast_intel.models.ast_node.RouteNode` instances.

Two extraction strategies:

1. **Attribute/decorator post-pass** — For frameworks where routes are
   expressed as decorators or annotations (actix, FastAPI, Flask, Spring).
   These operate on already-extracted ``FunctionNode`` / ``MethodNode`` lists.

2. **Programmatic body walk** — For frameworks where routes are registered
   via method calls inside function bodies (Axum ``.route()``, Express
   ``router.get()``).  These walk the tree-sitter AST directly.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import RouteNode

if TYPE_CHECKING:
    from tree_sitter import Node

    from ast_intel.models.ast_node import FunctionNode, MethodNode

__all__: list[str] = [
    "detect_actix_routes",
    "detect_aspnet_routes",
    "detect_axum_routes",
    "detect_csharp_minimal_api_routes",
    "detect_express_routes",
    "detect_fastapi_routes",
    "detect_flask_routes",
    "detect_spring_routes",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

# HTTP methods that are valid as framework routing identifiers.
_HTTP_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "delete", "patch", "head", "options",
})

# Actix-web attribute macros that define routes.
_ACTIX_RE: re.Pattern[str] = re.compile(
    r'#\[(?:actix_web::)?('
    r'get|post|put|delete|patch|head|options'
    r')\(\s*"([^"]+)"',
    re.IGNORECASE,
)

# FastAPI/Flask decorator pattern:  @app.get("/path") or @router.post("/path")
_FASTAPI_RE: re.Pattern[str] = re.compile(
    r'@\w+\.(get|post|put|delete|patch|head|options)\(\s*"([^"]+)"',
    re.IGNORECASE,
)

# Flask @app.route("/path") or @app.route("/path", methods=["GET", "POST"])
_FLASK_ROUTE_RE: re.Pattern[str] = re.compile(
    r'@\w+\.route\(\s*"([^"]+)"',
)
_FLASK_METHODS_RE: re.Pattern[str] = re.compile(
    r"methods\s*=\s*\[([^\]]+)\]",
)

# Spring annotation patterns: @GetMapping("/path"), @PostMapping, @RequestMapping
_SPRING_MAPPING: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "*",
}
_SPRING_RE: re.Pattern[str] = re.compile(
    r"@(" + "|".join(_SPRING_MAPPING) + r')\s*(?:\(\s*(?:value\s*=\s*)?'
    r'"([^"]*)")?',
)

# --- ASP.NET Core: controller attribute + minimal-API constants ---

# Method-level HTTP verb attributes on controller actions.
_ASPNET_HTTP_ATTRS: dict[str, str] = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
}

# Minimal-API endpoint registration methods (app.MapGet("/path", handler)).
_ASPNET_MINIMAL_MAP: dict[str, str] = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapDelete": "DELETE",
    "MapPatch": "PATCH",
}

# [controller] / [action] route token replacement (case-insensitive).
_ASPNET_TOKEN_RE: re.Pattern[str] = re.compile(
    r"\[(controller|action)\]", re.IGNORECASE,
)

# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, src: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
    return src[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace",
    ).strip()


def _dedup_key(
    fn: FunctionNode | MethodNode,
) -> tuple[str, int, int]:
    """Build a deduplication key from name + span position."""
    sl = fn.span.start_line if fn.span else 0
    sc = fn.span.start_col if fn.span else 0
    return (fn.name, sl, sc)


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Rust: Axum programmatic routes
# ---------------------------------------------------------------------------


def detect_axum_routes(root: Node, src: bytes) -> list[RouteNode]:
    """Detect Axum ``Router::new().route("/path", method(handler))`` patterns.

    Walks the full AST looking for ``call_expression`` nodes where the
    method name is ``route`` and the arguments contain a string literal
    path and a method-wrapper call (``get``, ``post``, etc.).
    """
    routes: list[RouteNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            route = _try_parse_axum_route(node, src)
            if route is not None:
                routes.append(route)

        stack.extend(node.children)

    return routes


def _try_parse_axum_route(node: Node, src: bytes) -> RouteNode | None:  # noqa: C901, PLR0912
    """Try to parse a single call_expression as an Axum .route() call.

    Expected tree-sitter structure::

        call_expression
          field_expression
            <receiver>                    ← Router::new() or chain
            field_identifier: "route"
          arguments
            string_literal: "/path"
            call_expression               ← get(handler) / post(handler)
              identifier: "get"
              arguments
                identifier: "handler_fn"

    Also handles the ``axum::routing::method_router`` form::

        call_expression
          scoped_identifier -> ... "route"
          arguments  (same as above)
    """
    # Find the function being called and its arguments.
    fn_node: Node | None = None
    args_node: Node | None = None
    for child in node.children:
        if child.type in ("field_expression", "scoped_identifier"):
            fn_node = child
        elif child.type == "arguments":
            args_node = child

    if fn_node is None or args_node is None:
        return None

    # Check that the method being called is "route".
    method_name = ""
    if fn_node.type == "field_expression":
        for child in fn_node.children:
            if child.type == "field_identifier":
                method_name = _node_text(child, src)
    elif fn_node.type == "scoped_identifier":
        # axum::Router::new().route(...)  — last segment is the method
        children = [c for c in fn_node.children if c.type != "::"]
        if children:
            method_name = _node_text(children[-1], src)

    if method_name != "route":
        return None

    # Parse arguments: first should be string literal, second should be
    # a call to get/post/put/delete/patch wrapping the handler.
    named_args = list(args_node.named_children)
    if len(named_args) < 2:  # noqa: PLR2004
        return None

    # First arg: path string
    path_node = named_args[0]
    if path_node.type != "string_literal":
        return None
    path = _node_text(path_node, src).strip('"')

    # Second arg: method_router call — get(handler), post(handler), etc.
    method_node = named_args[1]
    http_method, handler = _parse_method_router(method_node, src)
    if not http_method:
        return None

    return RouteNode(
        path=path,
        method=http_method.upper(),
        handler=handler,
        framework="axum",
        span=span_from_node(node),
    )


def _parse_method_router(node: Node, src: bytes) -> tuple[str, str]:
    """Parse ``get(handler)`` or ``axum::routing::post(handler)`` into (method, handler).

    Returns ``("", "")`` if the node doesn't match.
    """
    if node.type == "call_expression":
        fn_name = ""
        handler = ""

        for child in node.children:
            if child.type == "identifier":
                fn_name = _node_text(child, src)
            elif child.type == "scoped_identifier":
                # axum::routing::post — take the last segment
                children = [c for c in child.children if c.type != "::"]
                if children:
                    fn_name = _node_text(children[-1], src)
            elif child.type == "arguments":
                named = child.named_children
                if named:
                    handler = _node_text(named[0], src)

        if fn_name in _HTTP_METHODS:
            return fn_name, handler

    return "", ""


# endregion: --- Rust: Axum programmatic routes


# ---------------------------------------------------------------------------
# region:    --- Rust: Actix attribute routes
# ---------------------------------------------------------------------------


def detect_actix_routes(
    functions: list[FunctionNode],
    methods: list[MethodNode],
) -> list[RouteNode]:
    """Detect actix-web ``#[get("/path")]`` attributes on functions/methods.

    Operates on already-extracted attribute strings — no tree-sitter needed.
    """
    routes: list[RouteNode] = []
    seen: set[tuple[str, int, int]] = set()
    all_fns: list[FunctionNode | MethodNode] = list(functions) + list(methods)

    for fn in all_fns:
        for attr in fn.attributes:
            m = _ACTIX_RE.search(attr)
            if m:
                key = _dedup_key(fn)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(RouteNode(
                    path=m.group(2),
                    method=m.group(1).upper(),
                    handler=fn.name,
                    framework="actix",
                    span=fn.span,
                ))

    return routes


# endregion: --- Rust: Actix attribute routes


# ---------------------------------------------------------------------------
# region:    --- Python: FastAPI decorator routes
# ---------------------------------------------------------------------------


def detect_fastapi_routes(
    functions: list[FunctionNode],
    methods: list[MethodNode],
) -> list[RouteNode]:
    """Detect FastAPI ``@app.get("/path")`` / ``@router.post("/path")`` decorators.

    Operates on already-extracted decorator strings.
    """
    routes: list[RouteNode] = []
    seen: set[tuple[str, int, int]] = set()
    all_fns: list[FunctionNode | MethodNode] = list(functions) + list(methods)

    for fn in all_fns:
        for attr in fn.attributes:
            m = _FASTAPI_RE.search(attr)
            if m:
                key = _dedup_key(fn)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(RouteNode(
                    path=m.group(2),
                    method=m.group(1).upper(),
                    handler=fn.name,
                    framework="fastapi",
                    span=fn.span,
                ))

    return routes


# endregion: --- Python: FastAPI decorator routes


# ---------------------------------------------------------------------------
# region:    --- Python: Flask decorator routes
# ---------------------------------------------------------------------------


def detect_flask_routes(
    functions: list[FunctionNode],
    methods: list[MethodNode],
) -> list[RouteNode]:
    """Detect Flask ``@app.route("/path", methods=["GET"])`` decorators.

    If ``methods=`` is not specified, defaults to ``GET``.
    """
    routes: list[RouteNode] = []
    seen: set[tuple[str, int, int]] = set()
    all_fns: list[FunctionNode | MethodNode] = list(functions) + list(methods)

    for fn in all_fns:
        for attr in fn.attributes:
            path_match = _FLASK_ROUTE_RE.search(attr)
            if not path_match:
                continue
            path = path_match.group(1)
            key = _dedup_key(fn)
            if key in seen:
                continue
            seen.add(key)

            # Extract methods= if present
            methods_match = _FLASK_METHODS_RE.search(attr)
            if methods_match:
                raw_methods = methods_match.group(1)
                http_methods = [
                    m.strip().strip("\"'").upper()
                    for m in raw_methods.split(",")
                    if m.strip().strip("\"'")
                ]
            else:
                http_methods = ["GET"]

            routes.extend(
                RouteNode(
                    path=path,
                    method=method,
                    handler=fn.name,
                    framework="flask",
                    span=fn.span,
                )
                for method in http_methods
            )

    return routes


# endregion: --- Python: Flask decorator routes


# ---------------------------------------------------------------------------
# region:    --- TypeScript: Express programmatic routes
# ---------------------------------------------------------------------------


def detect_express_routes(root: Node, src: bytes) -> list[RouteNode]:
    """Detect Express ``router.get("/path", handler)`` patterns.

    Walks the AST for ``call_expression`` nodes where:
    - The callee is a ``member_expression`` with property ``get``/``post``/etc.
    - The first argument is a string starting with ``/``.
    """
    routes: list[RouteNode] = []
    stack: list[Node] = list(root.children)

    while stack:
        node = stack.pop()

        if node.type == "call_expression":
            route = _try_parse_express_route(node, src)
            if route is not None:
                routes.append(route)

        stack.extend(node.children)

    return routes


def _try_parse_express_route(node: Node, src: bytes) -> RouteNode | None:  # noqa: C901
    """Try to parse a single ``call_expression`` as an Express route.

    Args:
        node: A tree-sitter ``call_expression`` node to inspect.
        src: The full source bytes for text extraction.

    Returns:
        A ``RouteNode`` if the node is an Express route registration,
        otherwise ``None``.
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

    # Extract method name from member_expression
    method_name = ""
    for child in fn_node.children:
        if child.type == "property_identifier":
            method_name = _node_text(child, src).lower()

    if method_name not in _HTTP_METHODS:
        return None

    # First argument must be a string starting with /
    named_args = args_node.named_children
    if not named_args:
        return None

    path_node = named_args[0]
    if path_node.type != "string":
        return None
    path = _node_text(path_node, src).strip("\"'`")
    if not path.startswith("/"):
        return None

    # Handler: second argument name (if it's an identifier)
    handler = ""
    if len(named_args) >= 2:  # noqa: PLR2004
        handler_node = named_args[1]
        handler = _node_text(handler_node, src)
        # For arrow functions/callbacks, use a descriptive name
        if handler_node.type in ("arrow_function", "function_expression"):
            handler = f"<anonymous:{method_name}:{path}>"

    return RouteNode(
        path=path,
        method=method_name.upper(),
        handler=handler,
        framework="express",
        span=span_from_node(node),
    )


# endregion: --- TypeScript: Express programmatic routes


# ---------------------------------------------------------------------------
# region:    --- Java: Spring annotation routes
# ---------------------------------------------------------------------------


def detect_spring_routes(
    functions: list[FunctionNode],
    methods: list[MethodNode],
) -> list[RouteNode]:
    """Detect Spring ``@GetMapping("/path")`` / ``@PostMapping`` annotations.

    Operates on already-extracted annotation strings.
    """
    routes: list[RouteNode] = []
    seen: set[tuple[str, int, int]] = set()
    all_fns: list[FunctionNode | MethodNode] = list(functions) + list(methods)

    for fn in all_fns:
        for attr in fn.attributes:
            m = _SPRING_RE.search(attr)
            if m:
                key = _dedup_key(fn)
                if key in seen:
                    continue
                seen.add(key)
                annotation = m.group(1)
                path = m.group(2) or "/"
                http_method = _SPRING_MAPPING.get(annotation, "*")
                routes.append(RouteNode(
                    path=path,
                    method=http_method,
                    handler=fn.name,
                    framework="spring",
                    span=fn.span,
                ))

    return routes


# endregion: --- Java: Spring annotation routes


# ---------------------------------------------------------------------------
# region:    --- C#: ASP.NET Core attribute + minimal-API routes
# ---------------------------------------------------------------------------


def _csharp_string_value(node: Node, src: bytes) -> str | None:
    """Return the inner text of a C# string literal, or ``None``.

    Handles regular (``"..."``) and verbatim (``@"..."``) string
    literals.  Interpolated strings (``$"..."``) are dynamic and yield
    ``None``.
    """
    if node.type == "string_literal":
        for c in node.children:
            if c.type == "string_literal_content":
                return _node_text(c, src)
        return _node_text(node, src).strip('"')
    if node.type == "verbatim_string_literal":
        return _node_text(node, src).lstrip("@").strip('"')
    return None


def _csharp_attr_name(attr_node: Node, src: bytes) -> str:
    """Normalize a C# attribute name.

    Strips any namespace qualifier and the optional ``Attribute``
    suffix::

        Microsoft.AspNetCore.Mvc.HttpGetAttribute  ->  HttpGet
        Route                                       ->  Route
    """
    name_node = attr_node.child_by_field_name("name")
    raw = _node_text(name_node, src) if name_node else ""
    raw = raw.rsplit(".", 1)[-1]  # drop namespace qualifier
    return raw.removesuffix("Attribute")


def _csharp_attr_first_string(attr_node: Node, src: bytes) -> str | None:
    """Return the first *positional* string argument of an attribute.

    Skips named arguments such as ``Name = "x"`` so that only the route
    template (always positional and first) is returned.
    """
    arglist = next(
        (c for c in attr_node.children
         if c.type == "attribute_argument_list"),
        None,
    )
    if arglist is None:
        return None
    for arg in arglist.named_children:
        if arg.type != "attribute_argument":
            continue
        named = arg.named_children
        if named and named[0].type in (
            "string_literal", "verbatim_string_literal",
        ):
            return _csharp_string_value(named[0], src)
    return None


def _csharp_collect_attributes(node: Node) -> list[Node]:
    """Collect every ``attribute`` node across a declaration's lists."""
    attrs: list[Node] = []
    for child in node.children:
        if child.type == "attribute_list":
            attrs.extend(
                a for a in child.children if a.type == "attribute"
            )
    return attrs


def _aspnet_substitute_tokens(
    template: str, controller: str, action: str,
) -> str:
    """Replace ``[controller]`` / ``[action]`` route tokens."""
    def repl(m: re.Match[str]) -> str:
        return controller if m.group(1).lower() == "controller" else action

    return _ASPNET_TOKEN_RE.sub(repl, template)


def _aspnet_join_path(
    prefix: str, template: str | None, class_name: str, action: str,
) -> str:
    """Combine a controller route prefix with a method template.

    A method template beginning with ``/`` or ``~/`` is absolute and
    overrides the controller prefix (ASP.NET routing semantics).
    """
    controller = class_name.removesuffix("Controller")
    prefix = _aspnet_substitute_tokens(prefix or "", controller, action)
    tmpl = _aspnet_substitute_tokens(template or "", controller, action)

    if tmpl.startswith("~/"):
        path = tmpl[1:]
    elif tmpl.startswith("/"):
        path = tmpl
    elif prefix and tmpl:
        path = f"{prefix.rstrip('/')}/{tmpl.lstrip('/')}"
    elif prefix:
        path = prefix
    else:
        path = tmpl

    if not path.startswith("/"):
        path = "/" + path
    return path or "/"


def _aspnet_class_routes(  # noqa: C901
    class_node: Node, src: bytes,
) -> list[RouteNode]:
    """Extract controller-action routes from one ``class_declaration``."""
    routes: list[RouteNode] = []
    name_node = class_node.child_by_field_name("name")
    class_name = _node_text(name_node, src) if name_node else ""

    class_prefix = ""
    for attr in _csharp_collect_attributes(class_node):
        if _csharp_attr_name(attr, src) in ("Route", "RoutePrefix"):
            class_prefix = _csharp_attr_first_string(attr, src) or ""
            break

    body = class_node.child_by_field_name("body")
    if body is None:
        return routes

    for member in body.named_children:
        if member.type != "method_declaration":
            continue
        m_name = member.child_by_field_name("name")
        handler = _node_text(m_name, src) if m_name else ""

        verb_paths: list[tuple[str, str | None]] = []
        route_only_path: str | None = None
        has_route_attr = False
        for attr in _csharp_collect_attributes(member):
            nm = _csharp_attr_name(attr, src)
            if nm in _ASPNET_HTTP_ATTRS:
                verb_paths.append(
                    (_ASPNET_HTTP_ATTRS[nm],
                     _csharp_attr_first_string(attr, src)),
                )
            elif nm == "Route":
                has_route_attr = True
                route_only_path = _csharp_attr_first_string(attr, src)

        # [Route] with no HTTP verb attribute → matches all methods.
        if not verb_paths and has_route_attr:
            verb_paths.append(("*", route_only_path))

        for http_method, path in verb_paths:
            template = path if path is not None else route_only_path
            full = _aspnet_join_path(
                class_prefix, template, class_name, handler,
            )
            routes.append(RouteNode(
                path=full,
                method=http_method,
                handler=handler,
                framework="aspnet",
                span=span_from_node(member),
            ))

    return routes


def detect_aspnet_routes(root: Node, src: bytes) -> list[RouteNode]:
    """Detect ASP.NET Core controller routes (``[HttpGet]``, ``[Route]``).

    Walks every ``class_declaration`` and combines class-level
    ``[Route("api/[controller]")]`` prefixes with method-level HTTP verb
    attributes, performing ``[controller]`` / ``[action]`` token
    substitution.
    """
    routes: list[RouteNode] = []
    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "class_declaration":
            routes.extend(_aspnet_class_routes(node, src))
        stack.extend(node.children)
    return routes


def _csharp_minimal_handler(
    node: Node, src: bytes, method: str, path: str,
) -> str:
    """Derive a handler name for a minimal-API endpoint argument."""
    inner = node.named_children[0] if node.named_children else node
    if inner.type in ("lambda_expression", "anonymous_method_expression"):
        return f"<anonymous:{method}:{path}>"
    return _node_text(inner, src)


def _csharp_methods_array(node: Node, src: bytes) -> list[str]:
    """Collect HTTP method strings from a ``MapMethods`` array argument."""
    methods: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("string_literal", "verbatim_string_literal"):
            val = _csharp_string_value(n, src)
            if val:
                methods.append(val.upper())
        stack.extend(n.children)
    return methods


def _try_parse_minimal_api(  # noqa: PLR0911
    node: Node, src: bytes,
) -> list[RouteNode]:
    """Parse one ``invocation_expression`` as a minimal-API route."""
    member = next(
        (c for c in node.children
         if c.type == "member_access_expression"),
        None,
    )
    args = next(
        (c for c in node.children if c.type == "argument_list"), None,
    )
    if member is None or args is None:
        return []
    name_node = member.child_by_field_name("name")
    if name_node is None:
        return []
    map_name = _node_text(name_node, src)
    if map_name not in _ASPNET_MINIMAL_MAP and map_name != "MapMethods":
        return []

    arg_nodes = [a for a in args.named_children if a.type == "argument"]
    if not arg_nodes:
        return []

    first_inner = (
        arg_nodes[0].named_children[0]
        if arg_nodes[0].named_children else None
    )
    path = _csharp_string_value(first_inner, src) if first_inner else None
    if path is None or not path.startswith("/"):
        return []

    if map_name in _ASPNET_MINIMAL_MAP:
        handler = (
            _csharp_minimal_handler(arg_nodes[1], src, map_name, path)
            if len(arg_nodes) >= 2 else ""  # noqa: PLR2004
        )
        return [RouteNode(
            path=path,
            method=_ASPNET_MINIMAL_MAP[map_name],
            handler=handler,
            framework="aspnet",
            span=span_from_node(node),
        )]

    # MapMethods("/path", new[]{"GET","POST"}, handler)
    methods = (
        _csharp_methods_array(arg_nodes[1], src)
        if len(arg_nodes) >= 2 else []  # noqa: PLR2004
    )
    handler = (
        _csharp_minimal_handler(arg_nodes[2], src, "MapMethods", path)
        if len(arg_nodes) >= 3 else ""  # noqa: PLR2004
    )
    return [RouteNode(
        path=path,
        method=m,
        handler=handler,
        framework="aspnet",
        span=span_from_node(node),
    ) for m in methods]


def detect_csharp_minimal_api_routes(
    root: Node, src: bytes,
) -> list[RouteNode]:
    """Detect ASP.NET minimal-API routes (``app.MapGet("/path", handler)``).

    Walks ``invocation_expression`` nodes for ``Map{Verb}`` and
    ``MapMethods`` registrations carrying a literal path.
    """
    routes: list[RouteNode] = []
    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "invocation_expression":
            routes.extend(_try_parse_minimal_api(node, src))
        stack.extend(node.children)
    return routes


# endregion: --- C#: ASP.NET Core attribute + minimal-API routes
