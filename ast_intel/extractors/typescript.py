"""TypeScript / JavaScript language extractor — tree-sitter based AST extraction.

Parses ``.ts``, ``.tsx``, ``.js``, and ``.jsx`` files using
``tree-sitter-typescript`` and ``tree-sitter-javascript``, then maps
language constructs to the normalized AST schema.

TS/JS → Normalized mapping:
    =================================  =================
    TS/JS Concept                      Normalized To
    =================================  =================
    ``interface``                      TraitNode
    ``type X = { … }``                StructNode (object)
    ``type X = …``                    TypeAliasNode (non-object)
    ``class``                         StructNode
    ``class … implements IFoo``       ImplBlockNode
    ``class … extends Base``          ImplBlockNode
    ``function``                      FunctionNode
    ``const x = () => {}``            FunctionNode (arrow)
    class method                      MethodNode
    ``enum``                          EnumNode
    ``export``                        Visibility.PUBLIC
    ``import``                        uses (raw str)
    ``X.method()``                    imported_package_methods
    ``const X: T = …``               ConstantNode
    ``abstract class``                TraitNode
    =================================  =================

Also parses ``package.json`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from tree_sitter import Language, Node, Parser

from ast_intel.extractors._call_graph import (
    collect_defined_names as _collect_defined_names,
)
from ast_intel.extractors._call_graph import (
    extract_call_edges,
)
from ast_intel.extractors._rationale import extract_rationale_comments
from ast_intel.extractors.base import ExtractorBase, span_from_node
from ast_intel.models.ast_node import (
    ConstantNode,
    EnumNode,
    EnumVariantKind,
    EnumVariantNode,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MethodNode,
    ParamNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
    Visibility,
)
from ast_intel.models.workspace_model import CrateDependency, CrateModel

__all__: list[str] = ["TypeScriptExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews in extracted nodes.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Pattern to detect a module-level constant: ``UPPER_CASE`` identifier.
_CONSTANT_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Node types in ``class_heritage`` that represent base/interface types.
_HERITAGE_NODE_TYPES = ("type_identifier", "identifier", "generic_type")


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node | None, src: bytes) -> str:
    """Return decoded text for a node, or empty string if *node* is ``None``."""
    if node is None:
        return ""
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first child with the given ``type``, or ``None``."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _children_by_type(node: Node, type_name: str) -> list[Node]:
    """Return all children with the given ``type``."""
    return [c for c in node.children if c.type == type_name]


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract a JSDoc comment (``/** … */``) immediately before *node*.

    Looks at the previous sibling; returns the comment body stripped
    of leading ``*`` characters.
    """
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = _node_text(prev, src)
        if text.startswith("/**"):
            # Strip /** ... */ markers and leading * on each line
            body = text[3:].removesuffix("*/")
            lines = [line.strip().lstrip("*").strip() for line in body.splitlines()]
            return "\n".join(line for line in lines if line).strip()
    return ""


def _is_exported(node: Node) -> bool:
    """Check whether *node* is directly exported via ``export`` statement.

    Handles both ``export class Foo`` (node is inside export_statement)
    and ``export default function foo`` patterns.
    """
    parent = node.parent
    return parent is not None and parent.type == "export_statement"


def _extract_type_annotation(node: Node, src: bytes) -> str:
    """Extract the type annotation text from a node's ``type_annotation`` child.

    Returns the text after the colon, e.g. ``"string"`` from ``: string``.
    """
    ann = _child_by_type(node, "type_annotation")
    if ann is None:
        return ""
    # type_annotation has children: ":" + type_node
    for child in ann.children:
        if child.type != ":":
            return _node_text(child, src)
    return ""


def _extract_generics(node: Node, src: bytes) -> str:
    """Extract ``<T, U>`` generic type parameters from a declaration node."""
    tp = _child_by_type(node, "type_parameters")
    return _node_text(tp, src) if tp is not None else ""


def _extract_decorators(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@Decorator`` annotations from a class or method declaration.

    Decorators appear as ``decorator`` children of the class node in TS.
    """
    decorators: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            text = _node_text(child, src).strip()
            decorators.append(text)
    return tuple(decorators)


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Parameter extraction
# ---------------------------------------------------------------------------


def _parse_parameter(param_node: Node, src: bytes) -> ParamNode:
    """Parse a single parameter node into a ``ParamNode``.

    Handles both ``required_parameter`` and ``optional_parameter`` node types.
    For JS (no types), the parameter is just an ``identifier``.
    """
    name = ""
    param_type = ""

    for child in param_node.children:
        if child.type == "identifier":
            name = _node_text(child, src)
        elif child.type == "type_annotation":
            param_type = _extract_type_annotation(param_node, src)

    # For JS, the param node itself may be an identifier
    if not name and param_node.type == "identifier":
        name = _node_text(param_node, src)

    return ParamNode(name=name, type=param_type)


def _extract_params(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract parameters from a ``formal_parameters`` child.

    Skips accessibility modifiers (``private``, ``public``, etc.) on
    constructor parameter properties — those are captured as fields instead.
    """
    params_node = _child_by_type(node, "formal_parameters")
    if params_node is None:
        return ()

    result: list[ParamNode] = []
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            result.append(_parse_parameter(child, src))
        elif child.type == "identifier":
            # JS parameter — plain identifier without type
            result.append(ParamNode(name=_node_text(child, src)))

    return tuple(result)


# endregion: --- Parameter extraction


# ---------------------------------------------------------------------------
# region:    --- Interface extraction (→ TraitNode)
# ---------------------------------------------------------------------------


def _extract_interface(node: Node, src: bytes) -> TraitNode:
    """Extract an ``interface_declaration`` into a ``TraitNode``."""
    name = _node_text(_child_by_type(node, "type_identifier"), src)
    generics = _extract_generics(node, src)
    vis = Visibility.PUBLIC if _is_exported(node) else Visibility.PRIVATE
    doc = _extract_doc_comment(node, src)

    # Super-interfaces from extends_type_clause
    super_traits: list[str] = []
    extends_clause = _child_by_type(node, "extends_type_clause")
    if extends_clause is not None:
        super_traits.extend(
            _node_text(child, src)
            for child in extends_clause.children
            if child.type in ("type_identifier", "generic_type")
        )

    # Interface items (methods + properties)
    items: list[TraitItemNode] = []
    body = _child_by_type(node, "interface_body")
    if body is not None:
        for child in body.children:
            if child.type == "method_signature":
                item_name = _node_text(
                    _child_by_type(child, "property_identifier"), src,
                )
                params = _extract_params(child, src)
                ret = _extract_type_annotation(child, src)
                items.append(TraitItemNode(
                    kind=TraitItemKind.REQUIRED_METHOD,
                    name=item_name,
                    params=params,
                    return_type=ret,
                    span=span_from_node(child),
                ))
            elif child.type == "property_signature":
                # Readonly properties in interfaces → associated types
                prop_name = _node_text(
                    _child_by_type(child, "property_identifier"), src,
                )
                prop_type = _extract_type_annotation(child, src)
                if prop_name:
                    items.append(TraitItemNode(
                        kind=TraitItemKind.ASSOCIATED_TYPE,
                        name=prop_name,
                        return_type=prop_type,
                        span=span_from_node(child),
                    ))

    return TraitNode(
        name=name,
        visibility=vis,
        generics=generics,
        super_traits=tuple(super_traits),
        items=tuple(items),
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Interface extraction


# ---------------------------------------------------------------------------
# region:    --- Class extraction (→ StructNode + ImplBlockNode)
# ---------------------------------------------------------------------------


def _extract_class_fields(class_body: Node, src: bytes) -> list[FieldNode]:
    """Extract property declarations from a class body.

    Handles ``public_field_definition`` nodes (e.g. ``private name: string;``).
    """
    fields: list[FieldNode] = []
    for child in class_body.children:
        if child.type == "public_field_definition":
            # Determine visibility from accessibility_modifier
            vis = Visibility.PRIVATE
            acc = _child_by_type(child, "accessibility_modifier")
            if acc is not None:
                keyword = _node_text(acc, src).strip()
                if keyword == "public":
                    vis = Visibility.PUBLIC
                elif keyword == "protected":
                    vis = Visibility.PROTECTED
                # private remains default

            prop_name = _node_text(
                _child_by_type(child, "property_identifier"), src,
            )
            prop_type = _extract_type_annotation(child, src)

            if prop_name:
                fields.append(FieldNode(
                    name=prop_name,
                    type=prop_type,
                    visibility=vis,
                ))
    return fields


def _extract_constructor_fields(class_body: Node, src: bytes) -> list[FieldNode]:  # noqa: C901
    """Extract fields from constructor parameter properties.

    In TypeScript, ``constructor(private name: string, public age: number)``
    declares class fields. Only params with ``accessibility_modifier`` or
    ``readonly`` are captured as fields.
    """
    fields: list[FieldNode] = []
    for child in class_body.children:
        if child.type != "method_definition":
            continue
        # Check if this is a constructor
        ident = _child_by_type(child, "property_identifier")
        if ident is None or _node_text(ident, src) != "constructor":
            continue

        params_node = _child_by_type(child, "formal_parameters")
        if params_node is None:
            continue

        for param in params_node.children:
            if param.type not in ("required_parameter", "optional_parameter"):
                continue
            has_accessor = _child_by_type(param, "accessibility_modifier") is not None
            has_readonly = any(c.type == "readonly" for c in param.children)
            if not has_accessor and not has_readonly:
                continue

            param_name = _node_text(_child_by_type(param, "identifier"), src)
            param_type = _extract_type_annotation(param, src)

            vis = Visibility.PRIVATE
            acc = _child_by_type(param, "accessibility_modifier")
            if acc is not None:
                keyword = _node_text(acc, src).strip()
                if keyword == "public":
                    vis = Visibility.PUBLIC
                elif keyword == "protected":
                    vis = Visibility.PROTECTED

            if param_name:
                fields.append(FieldNode(
                    name=param_name,
                    type=param_type,
                    visibility=vis,
                ))
    return fields


def _extract_class_methods(
    class_body: Node,
    src: bytes,
    class_name: str,
) -> tuple[list[MethodNode], list[MethodNode]]:
    """Extract methods from a class body.

    Returns a tuple of (methods_for_impl_block, self_methods).
    Constructor method is excluded from both lists.
    """
    methods: list[MethodNode] = []
    self_methods: list[MethodNode] = []

    pending_decorators: list[str] = []

    for child in class_body.children:
        if child.type == "decorator":
            pending_decorators.append(_node_text(child, src).strip())
            continue

        if child.type == "method_definition":
            prop_ident = _child_by_type(child, "property_identifier")
            if prop_ident is None:
                pending_decorators.clear()
                continue
            method_name = _node_text(prop_ident, src)

            # Skip constructor
            if method_name == "constructor":
                pending_decorators.clear()
                continue

            is_async = any(c.type == "async" for c in child.children)
            is_static = any(c.type == "static" for c in child.children)

            params = _extract_params(child, src)
            ret_type = _extract_type_annotation(child, src)
            doc = _extract_doc_comment(child, src)

            attrs = tuple(pending_decorators)
            pending_decorators.clear()

            method = MethodNode(
                name=method_name,
                visibility=Visibility.PUBLIC,
                is_async=is_async,
                is_static=is_static,
                params=params,
                return_type=ret_type,
                context=f"impl:{class_name}",
                attributes=attrs,
                doc=doc,
                span=span_from_node(child),
            )
            methods.append(method)
            self_methods.append(method)

        elif child.type == "abstract_method_signature":
            # Abstract methods in abstract classes — skip from impl methods
            pending_decorators.clear()

        else:
            pending_decorators.clear()

    return methods, self_methods


def _extract_class(  # noqa: C901, PLR0912, PLR0915
    node: Node,
    src: bytes,
    *,
    is_abstract: bool = False,
    extra_decorators: tuple[str, ...] = (),
) -> tuple[
    StructNode | TraitNode,
    list[ImplBlockNode],
    list[MethodNode],
]:
    """Extract a class or abstract class declaration.

    Returns:
        - A ``StructNode`` (regular class) or ``TraitNode`` (abstract class)
        - A list of ``ImplBlockNode`` entries (one per implemented interface/base)
        - A flat list of ``MethodNode`` for ``self_methods``
    """
    name = _node_text(
        _child_by_type(node, "type_identifier") or _child_by_type(node, "identifier"),
        src,
    )
    generics = _extract_generics(node, src)
    vis = Visibility.PUBLIC if _is_exported(node) else Visibility.PRIVATE
    doc = _extract_doc_comment(node, src)
    decorators = extra_decorators + _extract_decorators(node, src)

    # Parse heritage (extends / implements)
    extends_types: list[str] = []
    implements_types: list[str] = []

    heritage = _child_by_type(node, "class_heritage")
    if heritage is not None:
        for clause in heritage.children:
            if clause.type == "extends_clause":
                extends_types.extend(
                    _node_text(c, src)
                    for c in clause.children
                    if c.type in _HERITAGE_NODE_TYPES
                )
            elif clause.type == "implements_clause":
                implements_types.extend(
                    _node_text(c, src)
                    for c in clause.children
                    if c.type in _HERITAGE_NODE_TYPES
                )
            elif clause.type in _HERITAGE_NODE_TYPES:
                # JS grammar: heritage children are directly the types
                # after an ``extends`` keyword, without a wrapping clause.
                extends_types.append(_node_text(clause, src))

    # Extract fields and methods from body
    body = _child_by_type(node, "class_body")
    fields: list[FieldNode] = []
    methods: list[MethodNode] = []
    self_methods: list[MethodNode] = []

    if body is not None:
        fields.extend(_extract_class_fields(body, src))
        fields.extend(_extract_constructor_fields(body, src))
        methods, self_methods = _extract_class_methods(body, src, name)

    # Abstract class → TraitNode
    if is_abstract:
        trait_items: list[TraitItemNode] = []
        abstract_self_methods: list[MethodNode] = []

        if body is not None:
            pending_decos: list[str] = []
            for child in body.children:
                if child.type == "decorator":
                    pending_decos.append(_node_text(child, src).strip())
                    continue

                if child.type == "abstract_method_signature":
                    item_name = _node_text(
                        _child_by_type(child, "property_identifier"), src,
                    )
                    params = _extract_params(child, src)
                    ret_type = _extract_type_annotation(child, src)
                    trait_items.append(TraitItemNode(
                        kind=TraitItemKind.REQUIRED_METHOD,
                        name=item_name,
                        params=params,
                        return_type=ret_type,
                        span=span_from_node(child),
                    ))
                    abstract_self_methods.append(MethodNode(
                        name=item_name,
                        visibility=Visibility.PUBLIC,
                        params=params,
                        return_type=ret_type,
                        context=f"impl:{name}",
                        span=span_from_node(child),
                    ))
                    pending_decos.clear()

                elif child.type == "method_definition":
                    item_name = _node_text(
                        _child_by_type(child, "property_identifier"), src,
                    )
                    if item_name == "constructor":
                        pending_decos.clear()
                        continue
                    params = _extract_params(child, src)
                    ret_type = _extract_type_annotation(child, src)
                    is_async = any(c.type == "async" for c in child.children)
                    trait_items.append(TraitItemNode(
                        kind=TraitItemKind.DEFAULT_METHOD,
                        name=item_name,
                        is_async=is_async,
                        params=params,
                        return_type=ret_type,
                        span=span_from_node(child),
                    ))
                    abstract_self_methods.append(MethodNode(
                        name=item_name,
                        visibility=Visibility.PUBLIC,
                        is_async=is_async,
                        params=params,
                        return_type=ret_type,
                        context=f"impl:{name}",
                        span=span_from_node(child),
                    ))
                    pending_decos.clear()

                else:
                    pending_decos.clear()

        trait = TraitNode(
            name=name,
            visibility=vis,
            generics=generics,
            items=tuple(trait_items),
            attributes=decorators,
            doc=doc,
            span=span_from_node(node),
        )
        return trait, [], abstract_self_methods + self_methods

    # Regular class → StructNode + ImplBlockNodes
    struct = StructNode(
        name=name,
        visibility=vis,
        generics=generics,
        fields=tuple(fields),
        attributes=decorators,
        doc=doc,
        span=span_from_node(node),
    )

    impl_blocks: list[ImplBlockNode] = [
        ImplBlockNode(
            self_type=name,
            trait_type=base,
            methods=tuple(methods),
            span=span_from_node(node),
        )
        for base in extends_types
    ]

    # Each implements → ImplBlockNode
    impl_blocks.extend(
        ImplBlockNode(
            self_type=name,
            trait_type=iface,
            methods=tuple(methods),
            span=span_from_node(node),
        )
        for iface in implements_types
    )

    # If no heritage, create an inherent impl block for non-empty methods
    if not extends_types and not implements_types and methods:
        impl_blocks.append(ImplBlockNode(
            self_type=name,
            methods=tuple(methods),
            span=span_from_node(node),
        ))

    return struct, impl_blocks, self_methods


# endregion: --- Class extraction


# ---------------------------------------------------------------------------
# region:    --- Enum extraction (→ EnumNode)
# ---------------------------------------------------------------------------


def _extract_enum(node: Node, src: bytes) -> EnumNode:
    """Extract an ``enum_declaration`` into an ``EnumNode``."""
    name = _node_text(_child_by_type(node, "identifier"), src)
    vis = Visibility.PUBLIC if _is_exported(node) else Visibility.PRIVATE
    doc = _extract_doc_comment(node, src)

    variants: list[EnumVariantNode] = []
    body = _child_by_type(node, "enum_body")
    if body is not None:
        for child in body.children:
            if child.type == "enum_assignment":
                var_name = _node_text(
                    _child_by_type(child, "property_identifier"), src,
                )
                if var_name:
                    variants.append(EnumVariantNode(
                        name=var_name, kind=EnumVariantKind.UNIT,
                    ))
            elif child.type == "property_identifier":
                var_name = _node_text(child, src)
                if var_name:
                    variants.append(EnumVariantNode(
                        name=var_name, kind=EnumVariantKind.UNIT,
                    ))

    return EnumNode(
        name=name,
        visibility=vis,
        variants=tuple(variants),
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Enum extraction


# ---------------------------------------------------------------------------
# region:    --- Function extraction (→ FunctionNode)
# ---------------------------------------------------------------------------


def _extract_function(node: Node, src: bytes) -> FunctionNode:
    """Extract a ``function_declaration`` into a ``FunctionNode``."""
    name = _node_text(_child_by_type(node, "identifier"), src)
    vis = Visibility.PUBLIC if _is_exported(node) else Visibility.PRIVATE
    is_async = any(c.type == "async" for c in node.children)
    generics = _extract_generics(node, src)
    params = _extract_params(node, src)
    ret_type = _extract_type_annotation(node, src)
    doc = _extract_doc_comment(node, src)

    return FunctionNode(
        name=name,
        visibility=vis,
        is_async=is_async,
        generics=generics,
        params=params,
        return_type=ret_type,
        doc=doc,
        span=span_from_node(node),
    )


def _extract_arrow_function(
    decl_node: Node,
    var_node: Node,
    arrow_node: Node,
    src: bytes,
    *,
    is_exported: bool,
) -> FunctionNode | None:
    """Extract an arrow function from ``const x = () => { … }``.

    Args:
        decl_node: The ``lexical_declaration`` node.
        var_node: The ``variable_declarator`` node.
        arrow_node: The ``arrow_function`` node.
        src: Raw source bytes.
        is_exported: Whether the declaration is exported.

    Returns:
        A ``FunctionNode``, or ``None`` if the name is empty.
    """
    name = _node_text(_child_by_type(var_node, "identifier"), src)
    if not name:
        return None

    is_async = any(c.type == "async" for c in arrow_node.children)
    params = _extract_params(arrow_node, src)
    ret_type = _extract_type_annotation(arrow_node, src)
    doc = _extract_doc_comment(decl_node, src)
    vis = Visibility.PUBLIC if is_exported else Visibility.PRIVATE

    return FunctionNode(
        name=name,
        visibility=vis,
        is_async=is_async,
        params=params,
        return_type=ret_type,
        doc=doc,
        span=span_from_node(arrow_node),
    )


# endregion: --- Function extraction


# ---------------------------------------------------------------------------
# region:    --- Type alias extraction (→ TypeAliasNode / StructNode)
# ---------------------------------------------------------------------------


def _extract_type_alias(
    node: Node,
    src: bytes,
) -> TypeAliasNode | StructNode:
    """Extract a ``type_alias_declaration``.

    If the aliased type is an ``object_type``, extracts fields and
    returns a ``StructNode``. Otherwise returns a ``TypeAliasNode``.
    """
    name = _node_text(_child_by_type(node, "type_identifier"), src)
    vis = Visibility.PUBLIC if _is_exported(node) else Visibility.PRIVATE
    doc = _extract_doc_comment(node, src)

    # Find the type value (after the '=' token)
    value_node: Node | None = None
    after_eq = False
    for child in node.children:
        if child.type == "=":
            after_eq = True
            continue
        if after_eq and child.type != ";":
            value_node = child
            break

    # Object type → StructNode
    if value_node is not None and value_node.type == "object_type":
        fields = _extract_object_type_fields(value_node, src)
        return StructNode(
            name=name,
            visibility=vis,
            fields=tuple(fields),
            doc=doc,
            span=span_from_node(node),
        )

    # Other type → TypeAliasNode
    aliased_to = _node_text(value_node, src) if value_node is not None else ""
    return TypeAliasNode(
        name=name,
        aliased_to=aliased_to,
        visibility=vis,
        span=span_from_node(node),
    )


def _extract_object_type_fields(
    object_node: Node,
    src: bytes,
) -> list[FieldNode]:
    """Extract fields from a ``type X = { name: string; … }`` object type."""
    fields: list[FieldNode] = []
    for child in object_node.children:
        if child.type == "property_signature":
            prop_name = _node_text(
                _child_by_type(child, "property_identifier"), src,
            )
            prop_type = _extract_type_annotation(child, src)
            if prop_name:
                fields.append(FieldNode(
                    name=prop_name,
                    type=prop_type,
                    visibility=Visibility.PUBLIC,
                ))
    return fields


# endregion: --- Type alias extraction


# ---------------------------------------------------------------------------
# region:    --- Import extraction & import map
# ---------------------------------------------------------------------------


def _extract_import_statement(node: Node, src: bytes) -> str:
    """Extract raw import statement text."""
    return _node_text(node, src).strip()


def _build_import_map(root: Node, src: bytes) -> dict[str, str]:
    """Build a map of local names → qualified package paths.

    Handles:
    - ``import { Router } from 'express'`` → ``{"Router": "express::Router"}``
    - ``import axios from 'axios'`` → ``{"axios": "axios"}``
    - ``import * as fs from 'fs'`` → ``{"fs": "fs"}``
    - ``const X = require('pkg')`` → ``{"X": "pkg"}``
    - ``const { A, B } = require('pkg')`` → ``{"A": "pkg::A", "B": "pkg::B"}``
    """
    import_map: dict[str, str] = {}

    for child in root.children:
        actual = child
        # Unwrap export_statement to get the inner declaration
        if child.type == "export_statement":
            for sub in child.children:
                if sub.type == "import_statement":
                    actual = sub
                    break

        if actual.type == "import_statement":
            _process_import_statement(actual, src, import_map)
        elif actual.type == "lexical_declaration":
            _process_require_declaration(actual, src, import_map)

    return import_map


def _process_import_statement(  # noqa: C901
    node: Node,
    src: bytes,
    import_map: dict[str, str],
) -> None:
    """Process ``import ... from '...'`` and populate the import map."""
    # Find the source string (module name)
    source_module = ""
    for child in node.children:
        if child.type == "string":
            source_module = _extract_string_content(child, src)
            break

    if not source_module:
        return

    clause = _child_by_type(node, "import_clause")
    if clause is None:
        return

    for child in clause.children:
        if child.type == "identifier":
            # Default import: import axios from 'axios'
            local_name = _node_text(child, src)
            import_map[local_name] = source_module
        elif child.type == "named_imports":
            # Named import: import { A, B } from 'pkg'
            for spec in child.children:
                if spec.type == "import_specifier":
                    _process_import_specifier(spec, src, source_module, import_map)
        elif child.type == "namespace_import":
            # Namespace import: import * as fs from 'fs'
            ident = _child_by_type(child, "identifier")
            if ident is not None:
                local_name = _node_text(ident, src)
                import_map[local_name] = source_module


def _process_import_specifier(
    spec: Node,
    src: bytes,
    source_module: str,
    import_map: dict[str, str],
) -> None:
    """Process a single import specifier: ``{ A }`` or ``{ A as B }``."""
    identifiers = _children_by_type(spec, "identifier")
    if len(identifiers) >= 2:  # noqa: PLR2004
        # import { A as B } from 'pkg' → local is B, qualified is pkg::A
        original = _node_text(identifiers[0], src)
        local = _node_text(identifiers[1], src)
        import_map[local] = f"{source_module}::{original}"
    elif len(identifiers) == 1:
        local = _node_text(identifiers[0], src)
        import_map[local] = f"{source_module}::{local}"


def _process_require_declaration(  # noqa: C901, PLR0912
    node: Node,
    src: bytes,
    import_map: dict[str, str],
) -> None:
    """Process ``const X = require('pkg')`` and destructured forms."""
    for decl in node.children:
        if decl.type != "variable_declarator":
            continue

        # Find the require call
        call = _child_by_type(decl, "call_expression")
        if call is None:
            continue
        fn_name_node = _child_by_type(call, "identifier")
        if fn_name_node is None or _node_text(fn_name_node, src) != "require":
            continue

        # Extract package name from arguments
        args = _child_by_type(call, "arguments")
        if args is None:
            continue
        pkg_name = ""
        for arg_child in args.children:
            if arg_child.type == "string":
                pkg_name = _extract_string_content(arg_child, src)
                break
        if not pkg_name:
            continue

        # Simple: const X = require('pkg')
        ident = _child_by_type(decl, "identifier")
        if ident is not None:
            import_map[_node_text(ident, src)] = pkg_name
            continue

        # Destructured: const { A, B } = require('pkg')
        pattern = _child_by_type(decl, "object_pattern")
        if pattern is not None:
            for prop in pattern.children:
                if prop.type == "shorthand_property_identifier_pattern":
                    local = _node_text(prop, src)
                    import_map[local] = f"{pkg_name}::{local}"
                elif prop.type == "pair_pattern":
                    # Destructured with rename: { key: alias }
                    key_node = _child_by_type(prop, "property_identifier")
                    val_node = _child_by_type(prop, "identifier")
                    if key_node and val_node:
                        key = _node_text(key_node, src)
                        alias = _node_text(val_node, src)
                        import_map[alias] = f"{pkg_name}::{key}"


def _extract_string_content(string_node: Node, src: bytes) -> str:
    """Extract the string content from a string literal node, removing quotes."""
    for child in string_node.children:
        if child.type == "string_fragment":
            return _node_text(child, src)
    # Fallback: strip quotes manually
    text = _node_text(string_node, src)
    if (text.startswith("'") and text.endswith("'")) or (
        text.startswith('"') and text.endswith('"')
    ):
        return text[1:-1]
    return text


# endregion: --- Import extraction & import map


# ---------------------------------------------------------------------------
# region:    --- Scoped method call extraction
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Extract ``X.method()`` calls where ``X`` is an imported name.

    Recursively walks the tree looking for ``call_expression`` nodes
    that have a ``member_expression`` child of the form ``identifier.method``.
    """
    result: dict[str, list[str]] = {}
    _walk_for_calls(root, src, import_map, result)
    return result


def _walk_for_calls(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Recursive walker for scoped call extraction."""
    if node.type == "call_expression":
        member = _child_by_type(node, "member_expression")
        if member is not None:
            obj = _child_by_type(member, "identifier")
            prop = _child_by_type(member, "property_identifier")
            if obj is not None and prop is not None:
                obj_name = _node_text(obj, src)
                method_name = _node_text(prop, src)
                qualified = import_map.get(obj_name)
                if qualified is not None:
                    result.setdefault(qualified, [])
                    if method_name not in result[qualified]:
                        result[qualified].append(method_name)

    for child in node.children:
        _walk_for_calls(child, src, import_map, result)


# endregion: --- Scoped method call extraction


# ---------------------------------------------------------------------------
# region:    --- Constant extraction (→ ConstantNode)
# ---------------------------------------------------------------------------


def _extract_constant(
    decl_node: Node,
    var_node: Node,
    src: bytes,
    *,
    is_exported: bool,
) -> ConstantNode | None:
    """Extract a constant from ``const X: T = value;``.

    Only captures constants with UPPER_CASE names or with explicit
    type annotations (to filter out general variable declarations).
    """
    name_node = _child_by_type(var_node, "identifier")
    if name_node is None:
        return None

    name = _node_text(name_node, src)
    if not name:
        return None

    # Only capture UPPER_CASE or type-annotated const declarations
    has_type = _child_by_type(var_node, "type_annotation") is not None
    if not _CONSTANT_NAME_RE.match(name) and not has_type:
        return None

    vis = Visibility.PUBLIC if is_exported else Visibility.PRIVATE
    raw = _node_text(decl_node, src)
    if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
        raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "…"

    return ConstantNode(name=name, visibility=vis, raw=raw, span=span_from_node(decl_node))


# endregion: --- Constant extraction


# ---------------------------------------------------------------------------
# region:    --- Collect self_methods
# ---------------------------------------------------------------------------


def _collect_self_methods(
    root: Node,
    src: bytes,
) -> list[MethodNode]:
    """Collect free-standing functions as self_methods with context ``"free"``."""
    methods: list[MethodNode] = []
    for child in root.children:
        actual = child
        exported = False
        if child.type == "export_statement":
            exported = True
            for sub in child.children:
                if sub.type == "function_declaration":
                    actual = sub
                    break
            else:
                continue

        if actual.type == "function_declaration":
            name = _node_text(_child_by_type(actual, "identifier"), src)
            is_async = any(c.type == "async" for c in actual.children)
            params = _extract_params(actual, src)
            ret_type = _extract_type_annotation(actual, src)
            methods.append(MethodNode(
                name=name,
                visibility=Visibility.PUBLIC if exported else Visibility.PRIVATE,
                is_async=is_async,
                params=params,
                return_type=ret_type,
                context="free",
                span=span_from_node(actual),
            ))

    return methods


# endregion: --- Collect self_methods


# ---------------------------------------------------------------------------
# region:    --- Main dispatch loop
# ---------------------------------------------------------------------------


def _dispatch_node(  # noqa: C901, PLR0911, PLR0912
    node: Node,
    src: bytes,
    ast: FileAST,
    *,
    is_exported: bool = False,
    parent_decorators: tuple[str, ...] = (),
) -> None:
    """Dispatch a single top-level AST node to the appropriate extractor.

    This is inherently complex: each node type requires distinct
    handling. The cyclomatic complexity is unavoidable for a dispatch
    function.
    """
    if node.type == "export_statement":
        # Decorators on exported classes appear as children of export_statement.
        export_decorators = _extract_decorators(node, src)
        for child in node.children:
            if child.type in (
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "enum_declaration",
                "function_declaration",
                "lexical_declaration",
                "type_alias_declaration",
            ):
                _dispatch_node(
                    child, src, ast,
                    is_exported=True,
                    parent_decorators=export_decorators,
                )
        return

    if node.type == "interface_declaration":
        trait = _extract_interface(node, src)
        ast.traits.append(trait)
        return

    if node.type == "abstract_class_declaration":
        type_node, impl_blocks, self_methods = _extract_class(
            node, src, is_abstract=True,
            extra_decorators=parent_decorators,
        )
        if isinstance(type_node, TraitNode):
            ast.traits.append(type_node)
        else:
            ast.structs.append(type_node)
        ast.impl_blocks.extend(impl_blocks)
        ast.self_methods.extend(self_methods)
        return

    if node.type == "class_declaration":
        struct, impl_blocks, self_methods = _extract_class(
            node, src, is_abstract=False,
            extra_decorators=parent_decorators,
        )
        if isinstance(struct, StructNode):
            ast.structs.append(struct)
        elif isinstance(struct, TraitNode):
            ast.traits.append(struct)
        ast.impl_blocks.extend(impl_blocks)
        ast.self_methods.extend(self_methods)
        return

    if node.type == "enum_declaration":
        enum = _extract_enum(node, src)
        ast.enums.append(enum)
        return

    if node.type == "function_declaration":
        fn = _extract_function(node, src)
        if is_exported:
            fn = FunctionNode(
                name=fn.name,
                visibility=Visibility.PUBLIC,
                is_async=fn.is_async,
                generics=fn.generics,
                params=fn.params,
                return_type=fn.return_type,
                doc=fn.doc,
                attributes=fn.attributes,
                span=fn.span,
            )
        ast.functions.append(fn)
        return

    if node.type == "type_alias_declaration":
        result = _extract_type_alias(node, src)
        if is_exported:
            if isinstance(result, StructNode):
                result = StructNode(
                    name=result.name,
                    visibility=Visibility.PUBLIC,
                    generics=result.generics,
                    fields=result.fields,
                    doc=result.doc,
                    span=result.span,
                )
            else:
                result = TypeAliasNode(
                    name=result.name,
                    aliased_to=result.aliased_to,
                    visibility=Visibility.PUBLIC,
                    span=result.span,
                )
        if isinstance(result, StructNode):
            ast.structs.append(result)
        else:
            ast.type_aliases.append(result)
        return

    if node.type == "lexical_declaration":
        _process_lexical_declaration(node, src, ast, is_exported=is_exported)
        return

    if node.type == "import_statement":
        ast.uses.append(_extract_import_statement(node, src))
        return


def _process_lexical_declaration(
    node: Node,
    src: bytes,
    ast: FileAST,
    *,
    is_exported: bool,
) -> None:
    """Process ``const/let/var`` declarations for arrow functions and constants."""
    is_const = any(c.type == "const" for c in node.children)

    for child in node.children:
        if child.type != "variable_declarator":
            continue

        # Check if this is an arrow function
        arrow = _child_by_type(child, "arrow_function")
        if arrow is not None:
            fn = _extract_arrow_function(
                node, child, arrow, src, is_exported=is_exported,
            )
            if fn is not None:
                ast.functions.append(fn)
            continue

        # Check if this is a require() call — it's an import
        call = _child_by_type(child, "call_expression")
        if call is not None:
            fn_name = _child_by_type(call, "identifier")
            if fn_name is not None and _node_text(fn_name, src) == "require":
                raw = _node_text(node, src).strip()
                ast.uses.append(raw)
                continue

        # Otherwise it may be a constant
        if is_const:
            const = _extract_constant(
                node, child, src, is_exported=is_exported,
            )
            if const is not None:
                ast.constants.append(const)


# endregion: --- Main dispatch loop


# ---------------------------------------------------------------------------
# region:    --- Extractor Class
# ---------------------------------------------------------------------------


class TypeScriptExtractor(ExtractorBase):
    """AST extractor for TypeScript and JavaScript files.

    Handles ``.ts``, ``.tsx``, ``.js``, and ``.jsx`` via the
    ``tree-sitter-typescript`` and ``tree-sitter-javascript`` grammars.
    Both languages are unified under a single extractor since TS is a
    strict superset of JS (from a structural extraction standpoint).
    """

    language_id: str = "typescript"
    file_extensions: list[str] = [".ts", ".tsx", ".js", ".jsx"]  # noqa: RUF012

    def __init__(self) -> None:
        self._ts_parser: Parser | None = None
        self._tsx_parser: Parser | None = None
        self._js_parser: Parser | None = None

    def _get_parser(self, file_path: Path) -> Parser:
        """Return the appropriate parser based on file extension."""
        suffix = file_path.suffix.lower()

        if suffix == ".tsx":
            if self._tsx_parser is None:
                import tree_sitter_typescript as ts_ts

                lang = Language(ts_ts.language_tsx())
                self._tsx_parser = Parser(lang)
            return self._tsx_parser

        if suffix in (".js", ".jsx"):
            if self._js_parser is None:
                import tree_sitter_javascript as ts_js

                lang = Language(ts_js.language())
                self._js_parser = Parser(lang)
            return self._js_parser

        # Default: TypeScript (.ts)
        if self._ts_parser is None:
            import tree_sitter_typescript as ts_ts

            lang = Language(ts_ts.language_typescript())
            self._ts_parser = Parser(lang)
        return self._ts_parser

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a TypeScript or JavaScript file and return its AST.

        Args:
            file_path: Path to the source file.
            source: Raw file contents (UTF-8).

        Returns:
            A ``FileAST`` containing all extracted symbols.
        """
        ast = FileAST(file=str(file_path))

        if not source.strip():
            return ast

        parser = self._get_parser(file_path)
        tree = parser.parse(source)
        root = tree.root_node

        # Check for parse errors
        if root.has_error:
            error_nodes = _find_error_nodes(root, source)
            ast.errors.extend(error_nodes)

        # Extract imports first to build the import map
        import_map = _build_import_map(root, source)

        # Main dispatch: walk top-level statements
        for child in root.children:
            _dispatch_node(child, source, ast)

        # Scoped method calls
        ast.imported_package_methods = _extract_scoped_method_calls(
            root, source, import_map,
        )

        # Collect self_methods for free functions
        free_methods = _collect_self_methods(root, source)
        ast.self_methods = free_methods + ast.self_methods

        # Post-pass: rationale comments
        ast.rationale_comments = extract_rationale_comments(
            root, source,
        )

        # Post-pass: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(
            root, source, defined_names,
        )

        # Post-pass: HTTP route extraction
        from ast_intel.extractors._routes import detect_express_routes
        ast.routes = detect_express_routes(root, source)

        # Post-pass: HTTP client call detection
        from ast_intel.extractors._http_calls import (
            detect_axios_calls,
            detect_fetch_calls,
        )
        ast.http_calls = detect_fetch_calls(root, source)
        ast.http_calls += detect_axios_calls(root, source)

        # Post-pass: Cloud SDK client detection
        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_typescript,
        )

        ast.cloud_resources = detect_cloud_clients_typescript(root, source)

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``package.json`` file into a ``CrateModel``.

        Args:
            manifest_path: Absolute path to the ``package.json`` file.

        Returns:
            A ``CrateModel`` with package name, version, and dependencies.
        """
        try:
            raw = manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot parse %s: %s", manifest_path, exc)
            return CrateModel(
                name=manifest_path.parent.name or "unknown",
                language=self.language_id,
                manifest_path=str(manifest_path),
            )

        if not isinstance(data, dict):
            logger.warning("Unexpected JSON type in %s", manifest_path)
            return CrateModel(
                name=manifest_path.parent.name or "unknown",
                language=self.language_id,
                manifest_path=str(manifest_path),
            )

        name = data.get("name", manifest_path.parent.name or "unknown")
        version = data.get("version", "")

        # Dependency groups: (key, is_dev)
        _dep_groups: tuple[tuple[str, bool], ...] = (
            ("dependencies", False),
            ("devDependencies", True),
            ("peerDependencies", False),
            ("optionalDependencies", False),
        )
        deps: list[CrateDependency] = [
            CrateDependency(
                name=dep_name,
                version=str(dep_ver),
                is_dev=is_dev,
            )
            for group_key, is_dev in _dep_groups
            for dep_name, dep_ver in data.get(group_key, {}).items()
        ]

        return CrateModel(
            name=name,
            version=version,
            language=self.language_id,
            manifest_path=str(manifest_path),
            dependencies=deps,
        )


# endregion: --- Extractor Class


# ---------------------------------------------------------------------------
# region:    --- Error collection helper
# ---------------------------------------------------------------------------


def _find_error_nodes(node: Node, src: bytes) -> list[str]:
    """Recursively find ``ERROR`` nodes and format them for ``FileAST.errors``."""
    errors: list[str] = []
    if node.type == "ERROR":
        start = node.start_point
        text = src[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace",
        )
        if len(text) > _MAX_ERROR_PREVIEW_LENGTH:
            text = text[:_MAX_ERROR_PREVIEW_LENGTH] + "…"
        errors.append(
            f"Parse error at line {start[0] + 1}:{start[1]}: {text}",
        )
    for child in node.children:
        errors.extend(_find_error_nodes(child, src))
    return errors


# endregion: --- Error collection helper
