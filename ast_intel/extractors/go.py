"""Go language extractor — tree-sitter based AST extraction.

Parses ``.go`` files using ``tree-sitter-go`` and maps Go-specific
constructs to the normalized AST schema.

Go → Normalized mapping:
    ================================  =================
    Go Concept                        Normalized To
    ================================  =================
    ``type X struct``                 StructNode
    ``type X interface``              TraitNode
    ``func F()``                      FunctionNode
    ``func (r *T) M()``              MethodNode (in ImplBlockNode)
    ``const``                         ConstantNode
    ``var`` (exported)                ConstantNode
    ``type X = Y`` (alias)            TypeAliasNode
    ``type X Y`` (definition)         TypeAliasNode
    ``import``                        uses (raw str)
    ``pkg.Func()``                    imported_package_methods
    ================================  =================

Also parses ``go.mod`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

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

__all__: list[str] = ["GoExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews in extracted nodes.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Pattern to match a ``go.mod`` *require* line:
#   github.com/gin-gonic/gin v1.9.1
#   github.com/stretchr/testify v1.8.4 // indirect
_REQUIRE_LINE_RE: re.Pattern[str] = re.compile(
    r"""
    ^\s*
    (?P<path>[^\s]+)       # module path
    \s+
    (?P<version>v[^\s]+)   # version (starts with 'v')
    (?:\s*//\s*(?P<comment>indirect))?   # optional "// indirect"
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node | None, src: bytes) -> str:
    """Return decoded text for a node, or empty string if *node* is ``None``."""
    if node is None:
        return ""
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _child_by_type(node: Node, *types: str) -> Node | None:
    """Return the first child matching any of the given types."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _children_by_type(node: Node, *types: str) -> list[Node]:
    """Return all children matching any of the given types."""
    return [c for c in node.children if c.type in types]


def _is_exported(name: str) -> bool:
    """Return ``True`` if the Go identifier is exported (uppercase first letter)."""
    return bool(name) and name[0].isupper()


def _go_visibility(name: str) -> Visibility:
    """Map a Go identifier to its visibility based on first-letter casing."""
    return Visibility.PUBLIC if _is_exported(name) else Visibility.PRIVATE


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Doc-comment extraction
# ---------------------------------------------------------------------------


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract leading ``//`` doc comments from preceding siblings.

    In the Go tree-sitter grammar, ``comment`` nodes are siblings of the
    declaration node at the ``source_file`` level.  We walk backwards from
    the declaration and collect consecutive ``//`` comment lines.
    """
    if node.parent is None:
        return ""
    siblings = list(node.parent.children)
    try:
        idx = siblings.index(node)
    except ValueError:
        return ""
    docs: list[str] = []
    for i in range(idx - 1, -1, -1):
        sib = siblings[i]
        if sib.type == "comment":
            text = _node_text(sib, src)
            if text.startswith("//"):
                docs.insert(0, text[2:].strip())
            else:
                break
        else:
            break
    return " ".join(docs) if docs else ""


# endregion: --- Doc-comment extraction


# ---------------------------------------------------------------------------
# region:    --- Parameter / return-type extraction
# ---------------------------------------------------------------------------


class _FunctionSignature(NamedTuple):
    """Parsed components of a Go function / method signature."""

    name: str
    visibility: Visibility
    generics: str
    params: tuple[ParamNode, ...]
    return_type: str


def _extract_parameters(params_node: Node | None, src: bytes) -> tuple[ParamNode, ...]:
    """Parse a ``parameter_list`` into ``ParamNode`` tuples.

    Handles:
    - ``(key string)`` — named parameter with type
    - ``(string)`` — unnamed parameter (type only)
    - ``(a, b int)`` — multiple names sharing one type
    - ``(values ...int)`` — variadic parameters
    """
    if params_node is None:
        return ()
    params: list[ParamNode] = []
    for child in params_node.named_children:
        if child.type == "parameter_declaration":
            _extract_param_declaration(child, src, params)
        elif child.type == "variadic_parameter_declaration":
            _extract_variadic_param(child, src, params)
    return tuple(params)


def _extract_param_declaration(
    decl: Node,
    src: bytes,
    params: list[ParamNode],
) -> None:
    """Extract one ``parameter_declaration`` which may contain multiple names."""
    identifiers: list[Node] = []
    type_node: Node | None = None
    for child in decl.named_children:
        if child.type == "identifier":
            identifiers.append(child)
        else:
            # First non-identifier named child is the type
            type_node = child
            break

    if identifiers and type_node is not None:
        # Named params: ``a int`` or ``a, b int``
        type_text = _node_text(type_node, src)
        params.extend(
            ParamNode(name=_node_text(ident, src), type=type_text)
            for ident in identifiers
        )
    elif identifiers:
        # No type? Treat identifiers as names without types
        params.extend(
            ParamNode(name=_node_text(ident, src)) for ident in identifiers
        )
    elif type_node is not None:
        # Unnamed parameter (type only)
        params.append(ParamNode(name="", type=_node_text(type_node, src)))
    else:
        # Edge case: fall back to full text
        text = _node_text(decl, src)
        if text:
            params.append(ParamNode(name="", type=text))


def _extract_variadic_param(
    decl: Node,
    src: bytes,
    params: list[ParamNode],
) -> None:
    """Extract a ``variadic_parameter_declaration`` (``values ...int``)."""
    ident = _child_by_type(decl, "identifier")
    name = _node_text(ident, src) if ident else ""
    # The type is the last named child that is not an identifier
    type_node: Node | None = None
    for child in decl.named_children:
        if child.type != "identifier":
            type_node = child
    type_text = f"...{_node_text(type_node, src)}" if type_node else "..."
    params.append(ParamNode(name=name, type=type_text))


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract the return type from a function / method declaration.

    For ``function_declaration``: 1st ``parameter_list`` = params,
    then return type follows (may be another ``parameter_list`` for multi-return).

    For ``method_declaration``: 1st ``parameter_list`` = receiver,
    2nd = params, then return type follows.
    """
    min_params = 2 if node.type == "method_declaration" else 1
    param_lists_seen = 0

    for child in node.children:
        if child.type == "parameter_list":
            param_lists_seen += 1
            if param_lists_seen > min_params:
                # This parameter_list IS the multi-return type
                return _node_text(child, src)
            continue
        if child.type == "block":
            break
        if child.type == "type_parameter_list":
            continue
        if child.type == "field_identifier":
            continue
        # After the required parameter list(s), any named node is the return type
        if param_lists_seen >= min_params and child.is_named:
            return _node_text(child, src)
    return ""


def _extract_generics(node: Node, src: bytes) -> str:
    """Extract type parameter list text (e.g., ``[T any, U comparable]``)."""
    tp = _child_by_type(node, "type_parameter_list")
    return _node_text(tp, src) if tp is not None else ""


# endregion: --- Parameter / return-type extraction


# ---------------------------------------------------------------------------
# region:    --- Struct extraction (→ StructNode)
# ---------------------------------------------------------------------------


def _extract_struct_fields(
    field_list: Node,
    src: bytes,
) -> tuple[FieldNode, ...]:
    """Extract fields from a ``field_declaration_list``."""
    fields: list[FieldNode] = []
    for child in field_list.named_children:
        if child.type == "field_declaration":
            _extract_one_field(child, src, fields)
    return tuple(fields)


def _extract_one_field(
    decl: Node,
    src: bytes,
    fields: list[FieldNode],
) -> None:
    """Extract a single ``field_declaration`` (may be embedding)."""
    field_idents = _children_by_type(decl, "field_identifier")
    if field_idents:
        # Named field(s): ``Name type``
        # Find the type node (first named child that is not field_identifier)
        type_node: Node | None = None
        for child in decl.named_children:
            if child.type != "field_identifier":
                type_node = child
                break
        type_text = _node_text(type_node, src) if type_node else ""
        for fi in field_idents:
            name = _node_text(fi, src)
            fields.append(
                FieldNode(
                    name=name,
                    type=type_text,
                    visibility=_go_visibility(name),
                )
            )
    else:
        # Embedded field (e.g., ``StorageConfig`` or ``*Config``)
        # Use the full text as both name and type
        text = _node_text(decl, src)
        # Strip pointer prefix for the name
        name = text.lstrip("*")
        # For qualified types like ``pkg.Type``, just use ``Type``
        if "." in name:
            name = name.rsplit(".", maxsplit=1)[-1]
        fields.append(
            FieldNode(
                name=name,
                type=text,
                visibility=_go_visibility(name),
            )
        )


def _extract_struct(node: Node, src: bytes) -> StructNode:
    """Extract a struct ``type_spec`` into a ``StructNode``."""
    name = _node_text(_child_by_type(node, "type_identifier"), src)
    generics = _extract_generics(node, src)
    doc = _extract_doc_comment(node.parent, src) if node.parent else ""

    fields: tuple[FieldNode, ...] = ()
    struct_type = _child_by_type(node, "struct_type")
    if struct_type is not None:
        fdl = _child_by_type(struct_type, "field_declaration_list")
        if fdl is not None:
            fields = _extract_struct_fields(fdl, src)

    return StructNode(
        name=name,
        visibility=_go_visibility(name),
        generics=generics,
        fields=fields,
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Struct extraction


# ---------------------------------------------------------------------------
# region:    --- Interface extraction (→ TraitNode)
# ---------------------------------------------------------------------------


def _extract_interface_items(
    iface_node: Node,
    src: bytes,
) -> tuple[tuple[TraitItemNode, ...], tuple[str, ...]]:
    """Extract methods and embedded interfaces from an ``interface_type``.

    Returns:
        (items, super_traits) tuple.
    """
    items: list[TraitItemNode] = []
    super_traits: list[str] = []

    for child in iface_node.named_children:
        if child.type == "method_elem":
            _extract_interface_method(child, src, items)
        elif child.type == "type_elem":
            # Embedded interface
            super_traits.extend(
                _node_text(sub, src) for sub in child.named_children
            )

    return tuple(items), tuple(super_traits)


def _extract_interface_method(
    method: Node,
    src: bytes,
    items: list[TraitItemNode],
) -> None:
    """Extract a single ``method_elem`` from an interface."""
    name_node = _child_by_type(method, "field_identifier")
    name = _node_text(name_node, src) if name_node else ""
    if not name:
        return

    # Parameters: first parameter_list after the field_identifier
    param_lists = _children_by_type(method, "parameter_list")
    params = _extract_parameters(param_lists[0] if param_lists else None, src)

    # Return type: either a second parameter_list (multi-return) or type node
    return_type = ""
    if len(param_lists) >= 2:  # noqa: PLR2004
        # Multi-return: ``(T, error)``
        return_type = _node_text(param_lists[1], src)
    else:
        # Single return type: find a type node after the parameter_list
        after_params = False
        for child in method.children:
            if child.type == "parameter_list":
                after_params = True
                continue
            if after_params and child.is_named:
                return_type = _node_text(child, src)
                break

    items.append(
        TraitItemNode(
            kind=TraitItemKind.REQUIRED_METHOD,
            name=name,
            params=params,
            return_type=return_type,
            span=span_from_node(method),
        )
    )


def _extract_interface(node: Node, src: bytes) -> TraitNode:
    """Extract an interface ``type_spec`` into a ``TraitNode``."""
    name = _node_text(_child_by_type(node, "type_identifier"), src)
    generics = _extract_generics(node, src)
    doc = _extract_doc_comment(node.parent, src) if node.parent else ""

    items: tuple[TraitItemNode, ...] = ()
    super_traits: tuple[str, ...] = ()
    iface_node = _child_by_type(node, "interface_type")
    if iface_node is not None:
        items, super_traits = _extract_interface_items(iface_node, src)

    return TraitNode(
        name=name,
        visibility=_go_visibility(name),
        generics=generics,
        super_traits=super_traits,
        items=items,
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Interface extraction


# ---------------------------------------------------------------------------
# region:    --- Function extraction (→ FunctionNode)
# ---------------------------------------------------------------------------


def _extract_function_signature(
    node: Node,
    src: bytes,
) -> _FunctionSignature:
    """Parse a ``function_declaration`` into its components."""
    name = _node_text(_child_by_type(node, "identifier"), src)
    generics = _extract_generics(node, src)

    # Parameters: the first ``parameter_list`` child
    param_list = _child_by_type(node, "parameter_list")
    params = _extract_parameters(param_list, src)

    return_type = _extract_return_type(node, src)

    return _FunctionSignature(
        name=name,
        visibility=_go_visibility(name),
        generics=generics,
        params=params,
        return_type=return_type,
    )


def _extract_function(node: Node, src: bytes) -> FunctionNode:
    """Extract a ``function_declaration`` into a ``FunctionNode``."""
    sig = _extract_function_signature(node, src)
    doc = _extract_doc_comment(node, src)

    return FunctionNode(
        name=sig.name,
        visibility=sig.visibility,
        generics=sig.generics,
        params=sig.params,
        return_type=sig.return_type,
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Function extraction


# ---------------------------------------------------------------------------
# region:    --- Method extraction (→ MethodNode, grouped into ImplBlockNode)
# ---------------------------------------------------------------------------


class _ReceiverInfo(NamedTuple):
    """Parsed receiver from a Go method declaration."""

    receiver_name: str
    receiver_type: str
    is_pointer: bool


def _parse_receiver(node: Node, src: bytes) -> _ReceiverInfo:
    """Parse the receiver from a ``method_declaration``.

    The receiver is the **first** ``parameter_list`` child and contains
    a ``parameter_declaration`` with an ``identifier`` and either a
    ``pointer_type`` or ``type_identifier``.
    """
    first_param_list = _child_by_type(node, "parameter_list")
    if first_param_list is None:
        return _ReceiverInfo("", "", is_pointer=False)

    param_decl = _child_by_type(first_param_list, "parameter_declaration")
    if param_decl is None:
        return _ReceiverInfo("", "", is_pointer=False)

    recv_name = ""
    recv_type = ""
    is_pointer = False

    for child in param_decl.named_children:
        if child.type == "identifier":
            recv_name = _node_text(child, src)
        elif child.type == "pointer_type":
            is_pointer = True
            # May contain type_identifier or generic_type
            type_ident = _child_by_type(child, "type_identifier")
            if type_ident is not None:
                recv_type = _node_text(type_ident, src)
            else:
                generic = _child_by_type(child, "generic_type")
                if generic is not None:
                    # Use just the base type name, not the type args
                    base = _child_by_type(generic, "type_identifier")
                    recv_type = _node_text(base, src) if base else _node_text(generic, src)
        elif child.type == "type_identifier":
            recv_type = _node_text(child, src)
        elif child.type == "generic_type":
            base = _child_by_type(child, "type_identifier")
            recv_type = _node_text(base, src) if base else _node_text(child, src)
            is_pointer = False

    return _ReceiverInfo(recv_name, recv_type, is_pointer)


def _extract_method(
    node: Node,
    src: bytes,
) -> tuple[str, MethodNode]:
    """Extract a ``method_declaration`` into a receiver-type and MethodNode.

    Returns:
        ``(receiver_type, method_node)`` tuple.
    """
    receiver = _parse_receiver(node, src)
    name_node = _child_by_type(node, "field_identifier")
    name = _node_text(name_node, src)

    # Parameters: the **second** parameter_list (first is the receiver)
    param_lists = _children_by_type(node, "parameter_list")
    params = _extract_parameters(
        param_lists[1] if len(param_lists) >= 2 else None,  # noqa: PLR2004
        src,
    )

    return_type = _extract_return_type(node, src)
    doc = _extract_doc_comment(node, src)

    method = MethodNode(
        name=name,
        visibility=_go_visibility(name),
        params=params,
        return_type=return_type,
        doc=doc,
        span=span_from_node(node),
    )

    return receiver.receiver_type, method


# endregion: --- Method extraction


# ---------------------------------------------------------------------------
# region:    --- Constant / variable extraction (→ ConstantNode)
# ---------------------------------------------------------------------------


def _extract_constants(node: Node, src: bytes) -> list[ConstantNode]:
    """Extract ``const_declaration`` or ``var_declaration`` into ConstantNodes.

    Each ``const_spec`` / ``var_spec`` inside the declaration produces one node.
    Handles both grouped (``const ( ... )``) and standalone forms.
    """
    constants: list[ConstantNode] = []
    spec_type = "const_spec" if node.type == "const_declaration" else "var_spec"

    # Direct spec children (standalone declarations)
    for spec in _children_by_type(node, spec_type):
        _add_constant_from_spec(spec, src, constants)

    # Specs inside a spec_list (block declarations)
    list_type = "const_spec_list" if node.type == "const_declaration" else "var_spec_list"
    for spec_list in _children_by_type(node, list_type):
        for spec in _children_by_type(spec_list, spec_type):
            _add_constant_from_spec(spec, src, constants)

    return constants


def _add_constant_from_spec(
    spec: Node,
    src: bytes,
    constants: list[ConstantNode],
) -> None:
    """Add a ConstantNode from a single ``const_spec`` or ``var_spec``."""
    ident = _child_by_type(spec, "identifier")
    name = _node_text(ident, src) if ident else ""
    if not name:
        return
    raw = _node_text(spec, src)[:_MAX_RAW_CONSTANT_LENGTH]
    constants.append(
        ConstantNode(
            name=name,
            visibility=_go_visibility(name),
            raw=raw,
            span=span_from_node(spec),
        )
    )


# endregion: --- Constant / variable extraction


# ---------------------------------------------------------------------------
# region:    --- Type alias / definition extraction (→ TypeAliasNode)
# ---------------------------------------------------------------------------


def _extract_type_alias(node: Node, src: bytes) -> TypeAliasNode:
    """Extract a ``type_alias`` (``type X = Y``) into a TypeAliasNode."""
    name = _node_text(_child_by_type(node, "type_identifier"), src)

    # The aliased-to type is every named child after ``type_identifier``
    # that is not ``type_identifier`` (already captured as name)
    aliased_to = ""
    found_name = False
    for child in node.named_children:
        if child.type == "type_identifier" and not found_name:
            found_name = True
            continue
        if found_name:
            aliased_to = _node_text(child, src)
            break

    return TypeAliasNode(
        name=name,
        aliased_to=aliased_to,
        visibility=_go_visibility(name),
        span=span_from_node(node),
    )


def _extract_type_definition(node: Node, src: bytes) -> TypeAliasNode:
    """Extract a non-struct/interface ``type_spec`` (``type X Y``) into a TypeAliasNode."""
    name = _node_text(_child_by_type(node, "type_identifier"), src)

    # The defined type is the next named child after the type_identifier
    aliased_to = ""
    found_name = False
    for child in node.named_children:
        if child.type == "type_identifier" and not found_name:
            found_name = True
            continue
        if child.type == "type_parameter_list":
            continue
        if found_name:
            aliased_to = _node_text(child, src)
            break

    return TypeAliasNode(
        name=name,
        aliased_to=aliased_to,
        visibility=_go_visibility(name),
        span=span_from_node(node),
    )


# endregion: --- Type alias / definition extraction


# ---------------------------------------------------------------------------
# region:    --- Import map & scoped method calls
# ---------------------------------------------------------------------------


def _build_import_map(root: Node, src: bytes) -> dict[str, str]:
    """Build a map of local import names → qualified module paths.

    Handles:
    - ``import "fmt"`` → ``{"fmt": "fmt"}``
    - ``import "net/http"`` → ``{"http": "net/http"}``
    - ``import alias "github.com/pkg/errors"`` → ``{"alias": "github.com/pkg/errors"}``
    - Grouped imports with ``import ( ... )``
    """
    import_map: dict[str, str] = {}

    for child in root.children:
        if child.type != "import_declaration":
            continue
        # Single import or grouped imports
        for sub in child.named_children:
            if sub.type == "import_spec":
                _process_import_spec(sub, src, import_map)
            elif sub.type == "import_spec_list":
                for spec in sub.named_children:
                    if spec.type == "import_spec":
                        _process_import_spec(spec, src, import_map)

    return import_map


def _process_import_spec(
    spec: Node,
    src: bytes,
    import_map: dict[str, str],
) -> None:
    """Process a single ``import_spec`` into the import map."""
    alias_node = _child_by_type(spec, "package_identifier")
    string_node = _child_by_type(spec, "interpreted_string_literal")
    if string_node is None:
        return

    # Extract the path from inside the quotes
    path = _extract_import_path(string_node, src)
    if not path:
        return

    if alias_node is not None:
        # Aliased import: ``alias "pkg/path"``
        local_name = _node_text(alias_node, src)
    else:
        # Unaliased: local name is the last segment of the path
        local_name = path.rsplit("/", maxsplit=1)[-1]

    import_map[local_name] = path


def _extract_import_path(string_node: Node, src: bytes) -> str:
    """Extract the import path from an ``interpreted_string_literal``."""
    content = _child_by_type(string_node, "interpreted_string_literal_content")
    if content is not None:
        return _node_text(content, src)
    # Fallback: strip quotes manually
    text = _node_text(string_node, src)
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Extract ``pkg.Function()`` calls where ``pkg`` is an imported name.

    Iteratively walks the tree looking for ``call_expression`` with a
    ``selector_expression`` child of the form ``identifier.field_identifier``.
    """
    result: dict[str, list[str]] = {}

    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            selector = _child_by_type(node, "selector_expression")
            if selector is not None:
                obj = _child_by_type(selector, "identifier")
                method = _child_by_type(selector, "field_identifier")
                if obj is not None and method is not None:
                    obj_name = _node_text(obj, src)
                    method_name = _node_text(method, src)
                    qualified = import_map.get(obj_name)
                    if qualified is not None:
                        result.setdefault(qualified, [])
                        if method_name not in result[qualified]:
                            result[qualified].append(method_name)
        stack.extend(node.children)

    return result


# endregion: --- Import map & scoped method calls


# ---------------------------------------------------------------------------
# region:    --- Import statements for uses[]
# ---------------------------------------------------------------------------


def _collect_use_statements(root: Node, src: bytes) -> list[str]:
    """Collect raw import statements as strings for ``FileAST.uses``."""
    uses: list[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            for sub in child.named_children:
                if sub.type == "import_spec":
                    uses.append(_format_import_spec(sub, src))
                elif sub.type == "import_spec_list":
                    uses.extend(
                        _format_import_spec(spec, src)
                        for spec in sub.named_children
                        if spec.type == "import_spec"
                    )
    return uses


def _format_import_spec(spec: Node, src: bytes) -> str:
    """Format an ``import_spec`` node as a readable import string."""
    alias_node = _child_by_type(spec, "package_identifier")
    string_node = _child_by_type(spec, "interpreted_string_literal")
    path = _extract_import_path(string_node, src) if string_node else ""

    if alias_node is not None:
        return f'import {_node_text(alias_node, src)} "{path}"'
    return f'import "{path}"'


# endregion: --- Import statements for uses[]


# ---------------------------------------------------------------------------
# region:    --- self_methods builder
# ---------------------------------------------------------------------------


def _collect_self_methods(
    functions: list[FunctionNode],
    impl_blocks: list[ImplBlockNode],
) -> list[MethodNode]:
    """Build flat list of every function/method defined in a file.

    Includes free functions and all methods from impl blocks, each
    tagged with a ``context`` string indicating where it was defined.
    """
    methods: list[MethodNode] = []

    # Free functions
    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=fn.is_async,
            params=fn.params,
            return_type=fn.return_type,
            context="free",
            doc=fn.doc,
            span=fn.span,
        )
        for fn in functions
    )

    # Impl block methods
    for impl_block in impl_blocks:
        context = f"impl:{impl_block.self_type}"
        methods.extend(
            MethodNode(
                name=method.name,
                visibility=method.visibility,
                params=method.params,
                return_type=method.return_type,
                context=context,
                doc=method.doc,
                span=method.span,
            )
            for method in impl_block.methods
        )

    return methods


# endregion: --- self_methods builder


# ---------------------------------------------------------------------------
# region:    --- GoExtractor class
# ---------------------------------------------------------------------------


class GoExtractor(ExtractorBase):
    """Go language extractor using ``tree-sitter-go``.

    Parses ``.go`` source files and ``go.mod`` manifests into the
    normalized AST schema.
    """

    language_id: str = "go"
    file_extensions: list[str] = [".go"]  # noqa: RUF012

    def __init__(self) -> None:
        """Initialize the Go parser with tree-sitter-go grammar."""
        import tree_sitter_go

        self._language = Language(tree_sitter_go.language())
        self._parser = Parser(self._language)

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a single ``.go`` file into a ``FileAST``.

        Args:
            file_path: Absolute path to the ``.go`` file.
            source: Raw file bytes (UTF-8).

        Returns:
            Populated FileAST with all Go constructs extracted.
        """
        tree = self._parser.parse(source)
        ast = FileAST(file=str(file_path))

        # Accumulator for receiver methods: receiver_type → list[MethodNode]
        receiver_methods: dict[str, list[MethodNode]] = {}

        self._walk_top_level(tree.root_node, source, ast, receiver_methods)

        # Post-pass 1: group receiver methods into ImplBlockNodes
        for recv_type, methods in receiver_methods.items():
            ast.impl_blocks.append(
                ImplBlockNode(
                    self_type=recv_type,
                    methods=tuple(methods),
                    span=None,
                )
            )

        # Post-pass 2: self_methods
        ast.self_methods = _collect_self_methods(ast.functions, ast.impl_blocks)

        # Post-pass 3: imported_package_methods
        import_map = _build_import_map(tree.root_node, source)
        ast.imported_package_methods = _extract_scoped_method_calls(
            tree.root_node, source, import_map,
        )

        # Post-pass 4: uses (import statements)
        ast.uses = _collect_use_statements(tree.root_node, source)

        # Post-pass 5: rationale comments
        ast.rationale_comments = extract_rationale_comments(
            tree.root_node, source,
        )

        # Post-pass 6: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(
            tree.root_node, source, defined_names,
        )

        # Post-pass 7: HTTP client call detection
        from ast_intel.extractors._http_calls import detect_go_http_calls
        ast.http_calls = detect_go_http_calls(tree.root_node, source)

        return ast

    def _walk_top_level(
        self,
        root: Node,
        src: bytes,
        ast: FileAST,
        receiver_methods: dict[str, list[MethodNode]],
    ) -> None:
        """Walk top-level AST children, dispatching to type-specific handlers."""
        for child in root.children:
            if not child.is_named:
                continue
            self._dispatch_node(child, src, ast, receiver_methods)

    def _dispatch_node(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        receiver_methods: dict[str, list[MethodNode]],
    ) -> None:
        """Route a single AST node to its extraction handler."""
        ntype = node.type

        if ntype == "type_declaration":
            self._handle_type_declaration(node, src, ast)
        elif ntype == "function_declaration":
            ast.functions.append(_extract_function(node, src))
        elif ntype == "method_declaration":
            recv_type, method = _extract_method(node, src)
            if recv_type:
                receiver_methods.setdefault(recv_type, []).append(method)
        elif ntype in ("const_declaration", "var_declaration"):
            ast.constants.extend(_extract_constants(node, src))
        elif ntype == "ERROR":
            ast.errors.append(
                f"parse error at byte {node.start_byte}: "
                f"{_node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]}"
            )

    def _handle_type_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Dispatch ``type_declaration`` children to struct/interface/alias."""
        for child in node.named_children:
            if child.type == "type_spec":
                self._handle_type_spec(child, src, ast)
            elif child.type == "type_alias":
                ast.type_aliases.append(_extract_type_alias(child, src))

    def _handle_type_spec(
        self,
        spec: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Route a ``type_spec`` to struct, interface, or type definition."""
        struct_node = _child_by_type(spec, "struct_type")
        iface_node = _child_by_type(spec, "interface_type")

        if struct_node is not None:
            ast.structs.append(_extract_struct(spec, src))
        elif iface_node is not None:
            ast.traits.append(_extract_interface(spec, src))
        else:
            # Type definition: ``type X Y`` where Y is not struct/interface
            ast.type_aliases.append(_extract_type_definition(spec, src))

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``go.mod`` file.

        Args:
            manifest_path: Absolute path to ``go.mod``.

        Returns:
            CrateModel with module name, Go version, and dependencies.
        """
        try:
            text = manifest_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read go.mod: %s", manifest_path)
            return CrateModel(
                name=manifest_path.parent.name,
                language="go",
                manifest_path=str(manifest_path),
            )

        return self._parse_go_mod(text, manifest_path)

    @staticmethod
    def _parse_go_mod(text: str, manifest_path: Path) -> CrateModel:
        """Parse ``go.mod`` content into a CrateModel."""
        module_name = ""
        go_version = ""
        deps: list[CrateDependency] = []

        in_require = False
        for line in text.splitlines():
            stripped = line.strip()

            # Module declaration
            if stripped.startswith("module "):
                module_name = stripped[len("module "):].strip()
                continue

            # Go version
            if stripped.startswith("go "):
                go_version = stripped[len("go "):].strip()
                continue

            # Require block
            if stripped.startswith("require ("):
                in_require = True
                continue
            if stripped == ")" and in_require:
                in_require = False
                continue

            # Single-line require
            if stripped.startswith("require ") and "(" not in stripped:
                req_line = stripped[len("require "):].strip()
                dep = _parse_require_line(req_line)
                if dep is not None:
                    deps.append(dep)
                continue

            # Inside require block
            if in_require and stripped:
                dep = _parse_require_line(stripped)
                if dep is not None:
                    deps.append(dep)

        return CrateModel(
            name=module_name or manifest_path.parent.name,
            version=go_version,
            manifest_path=str(manifest_path),
            language="go",
            dependencies=deps,
        )


def _parse_require_line(line: str) -> CrateDependency | None:
    """Parse a single ``go.mod`` require line into a ``CrateDependency``."""
    match = _REQUIRE_LINE_RE.match(line)
    if match is None:
        return None
    return CrateDependency(
        name=match.group("path"),
        version=match.group("version"),
        is_dev=match.group("comment") == "indirect",
    )


# endregion: --- GoExtractor class
