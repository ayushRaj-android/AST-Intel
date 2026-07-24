"""Shared intra-file call-graph extractor — language-agnostic.

Walks every function/method body in a tree-sitter AST and emits
:class:`~ast_intel.models.ast_node.CallEdge` instances for every
call-expression found.

**Resolution strategy:**

- Build a *defined-symbols* map from all ``FunctionNode`` and
  ``MethodNode`` names in the file.
- For each call site whose callee is a plain ``identifier`` matching
  a defined symbol, set ``resolved_target`` and mark it resolved.
- ``attribute`` / ``member_expression`` style calls (``obj.method()``)
  are captured with ``is_method_call=True`` but left unresolved at
  file level (cross-file resolution happens in the indexer).

Each language extractor calls :func:`extract_call_edges` once at
the end of its ``extract()`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import CallEdge, FileAST

if TYPE_CHECKING:
    from tree_sitter import Node

__all__: list[str] = ["collect_defined_names", "extract_call_edges"]


# ---------------------------------------------------------------------------
# region:    --- Node Type Constants
# ---------------------------------------------------------------------------

# Tree-sitter call-expression node types per language.
_CALL_NODE_TYPES: frozenset[str] = frozenset({
    "call_expression",      # Rust, TypeScript, Go
    "call",                 # Python
    "invocation_expression",  # C#
    "method_invocation",    # Java
    "function_call_expression",  # PHP
})

# Tree-sitter node types for "simple name" callees (plain function call).
_IDENTIFIER_TYPES: frozenset[str] = frozenset({
    "identifier",
    "name",                 # PHP: callee()
})

# Tree-sitter node types for "member/attr" callees (obj.method()).
_MEMBER_CALL_TYPES: frozenset[str] = frozenset({
    "attribute",            # Python: obj.method
    "field_expression",     # Rust: obj.method
    "member_expression",    # TypeScript: obj.method
    "member_access_expression",  # C#: obj.Method
    "selector_expression",  # Go: obj.Method
})

# Tree-sitter node types for scoped calls (Rust Type::method,
# captured separately for completeness but treated as method calls).
_SCOPED_CALL_TYPES: frozenset[str] = frozenset({
    "scoped_identifier",  # Rust: Type::method
})

# Tree-sitter node types that define function/method scope boundaries.
# When collecting function bodies, we look for these node types.
_FUNCTION_DEF_TYPES: frozenset[str] = frozenset({
    # Rust
    "function_item",
    # Python
    "function_definition",
    # TypeScript / JavaScript
    "function_declaration",
    "method_definition",
    "arrow_function",
    # Go
    "method_declaration",
    # Java
    "constructor_declaration",
    # C#
    "local_function_statement",
})

# The body/block child within a function definition.
_BODY_NODE_TYPES: frozenset[str] = frozenset({
    "block",            # Rust, Python, Go, C#
    "statement_block",  # TypeScript / JavaScript
    "compound_statement",  # C / C++
})

# Types that define a named scope (class/impl/struct) for qualified
# name building.
_SCOPE_TYPES: frozenset[str] = frozenset({
    "impl_item",
    "class_definition",
    "class_declaration",
    "struct_declaration",
    "interface_declaration",
    "namespace_declaration",
    "object_declaration",    # Kotlin
    "object_definition",     # Scala
    "trait_definition",      # Scala
    "protocol_declaration",  # Swift
    "trait_declaration",     # PHP
    "enum_declaration",      # PHP
})

# endregion: --- Node Type Constants


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def extract_call_edges(
    root: Node,
    source: bytes,
    defined_names: frozenset[str],
) -> list[CallEdge]:
    """Walk the AST and extract intra-file call edges.

    For every function/method body, finds all call expressions and
    attempts to resolve the callee against *defined_names* — the set
    of function and method names defined in the same file.

    Args:
        root: Tree-sitter root node.
        source: Raw file bytes.
        defined_names: Frozenset of all function/method names defined
            in this file (used for intra-file resolution).

    Returns:
        A list of ``CallEdge`` instances sorted by source line.
    """
    bodies = _collect_function_bodies(root)
    edges: list[CallEdge] = []

    for caller_name, body_node in bodies:
        _extract_calls_from_body(
            caller_name, body_node, source, defined_names, edges,
        )

    edges.sort(key=lambda e: e.call_site.start_line)
    return edges


def collect_defined_names(ast: FileAST) -> frozenset[str]:
    """Build the set of all function/method names defined in a file.

    Used by each extractor to supply the *defined_names* parameter to
    :func:`extract_call_edges` for intra-file call resolution.

    Both ``ast.functions`` and ``ast.self_methods`` are scanned.  In
    practice ``self_methods`` is the unified superset (free functions +
    impl-block methods), so iterating ``functions`` too is a
    belt-and-suspenders safeguard that keeps this helper correct even
    if callers populate only one list.

    Args:
        ast: A populated ``FileAST``.

    Returns:
        Frozenset of all function and method names.
    """
    return frozenset(
        {fn.name for fn in ast.functions}
        | {m.name for m in ast.self_methods}
    )


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Internal Helpers
# ---------------------------------------------------------------------------


def _collect_function_bodies(
    root: Node,
) -> list[tuple[str, Node]]:
    """Walk the full tree and collect (caller_name, body_node) pairs.

    This descends into class/impl/module scopes to find nested
    function definitions as well.
    """
    results: list[tuple[str, Node]] = []
    _walk_for_functions(root, "", results)
    return results


def _walk_for_functions(
    node: Node,
    scope_prefix: str,
    results: list[tuple[str, Node]],
) -> None:
    """Recursively walk looking for function definitions."""
    for child in node.children:
        if child.type in _FUNCTION_DEF_TYPES:
            name = _extract_function_name(child)
            if name:
                qualified = (
                    f"{scope_prefix}{name}" if not scope_prefix
                    else f"{scope_prefix}.{name}"
                )
                body = _find_body(child)
                if body is not None:
                    results.append((qualified, body))
                # Also descend into nested function bodies
                # (closures, inner functions)
                _walk_for_functions(child, qualified, results)
        else:
            # Descend into class/impl/struct/trait scopes
            new_prefix = scope_prefix
            if child.type in _SCOPE_TYPES:
                scope_name = _extract_scope_name(child)
                if scope_name:
                    new_prefix = (
                        f"{scope_prefix}.{scope_name}"
                        if scope_prefix
                        else scope_name
                    )
            _walk_for_functions(child, new_prefix, results)
def _extract_function_name(node: Node) -> str:
    """Extract the name identifier from a function definition node."""
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return (
                child.text.decode("utf-8", errors="replace")
                if child.text
                else ""
            )
        # C/C++: name is inside function_declarator
        if child.type == "function_declarator":
            for sub in child.children:
                if sub.type in ("identifier", "field_identifier",
                                "qualified_identifier"):
                    return (
                        sub.text.decode("utf-8", errors="replace")
                        if sub.text
                        else ""
                    )
    return ""


def _extract_scope_name(node: Node) -> str:
    """Extract the name from a scope node (class, impl, etc.)."""
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier"):
            return (
                child.text.decode("utf-8", errors="replace")
                if child.text
                else ""
            )
    return ""


def _find_body(fn_node: Node) -> Node | None:
    """Find the body/block child of a function definition node."""
    for child in fn_node.children:
        if child.type in _BODY_NODE_TYPES:
            return child
    return None


def _extract_calls_from_body(
    caller: str,
    body: Node,
    source: bytes,
    defined_names: frozenset[str],
    edges: list[CallEdge],
) -> None:
    """Walk a function body and extract call edges."""
    _walk_for_calls(caller, body, source, defined_names, edges)


def _walk_for_calls(
    caller: str,
    node: Node,
    source: bytes,
    defined_names: frozenset[str],
    edges: list[CallEdge],
) -> None:
    """DFS walk to find call expressions, skipping nested functions."""
    if node.type in _FUNCTION_DEF_TYPES:
        # Don't descend into nested function definitions —
        # those are handled as separate callers.
        return

    if node.type in _CALL_NODE_TYPES:
        edge = _parse_call_node(caller, node, source, defined_names)
        if edge is not None:
            edges.append(edge)

    for child in node.children:
        _walk_for_calls(caller, child, source, defined_names, edges)


def _parse_call_node(
    caller: str,
    call_node: Node,
    source: bytes,
    defined_names: frozenset[str],
) -> CallEdge | None:
    """Parse a single call-expression node into a CallEdge.

    Returns ``None`` if the call cannot be meaningfully captured
    (e.g., computed callees like ``table[key](args)``).
    """
    if not call_node.children:
        return None

    func_child = call_node.children[0]
    func_text = (
        func_child.text.decode("utf-8", errors="replace")
        if func_child.text
        else ""
    )

    if not func_text:
        return None

    span = span_from_node(call_node)
    is_method = False
    callee_name = func_text
    resolved = ""

    if func_child.type in _IDENTIFIER_TYPES:
        # Plain function call: foo()
        callee_name = func_text
        if callee_name in defined_names:
            resolved = callee_name
    elif func_child.type in _MEMBER_CALL_TYPES:
        # Member/attribute call: obj.method()
        is_method = True
        callee_name = _extract_member_name(func_child) or func_text
    elif func_child.type in _SCOPED_CALL_TYPES:
        # Scoped call: Type::method() (Rust)
        is_method = True
        callee_name = func_text
    else:
        # Computed callee or unusual expression — still capture
        callee_name = func_text

    return CallEdge(
        caller=caller,
        callee=callee_name,
        call_site=span,
        resolved_target=resolved,
        is_method_call=is_method,
    )


def _extract_member_name(node: Node) -> str:
    """Extract the method name from a member/attribute access node.

    For ``obj.method``, returns ``"method"`` (the rightmost identifier).
    """
    # The last named child is typically the method name
    for child in reversed(node.children):
        if child.type in (
            "identifier",
            "property_identifier",
            "field_identifier",
        ):
            return (
                child.text.decode("utf-8", errors="replace")
                if child.text
                else ""
            )
    return ""


# endregion: --- Internal Helpers
