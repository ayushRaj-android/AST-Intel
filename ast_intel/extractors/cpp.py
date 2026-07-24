"""C / C++ language extractor — tree-sitter based AST extraction.

Parses ``.c`` and ``.h`` files using ``tree-sitter-c``, and ``.cpp``,
``.hpp``, ``.cc``, ``.hh``, ``.cxx`` files using ``tree-sitter-cpp``.

C/C++ → Normalized mapping:
    ================================  =================
    C/C++ Concept                     Normalized To
    ================================  =================
    ``struct Foo { ... };``           StructNode
    ``class Foo { ... };``            StructNode
    Pure virtual class                TraitNode
    ``class Foo : public IBar``       ImplBlockNode
    Free function                     FunctionNode
    ``void Foo::bar()``               MethodNode (linked)
    ``#include``                      uses (raw str)
    ``namespace a::b``                Module path
    ``typedef`` / ``using``           TypeAliasNode
    ``enum`` / ``enum class``         EnumNode
    ``#define MACRO``                 MacroNode
    ``template<typename T>``          generics field
    ================================  =================

Also parses ``CMakeLists.txt`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

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
    EnumNode,
    EnumVariantKind,
    EnumVariantNode,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MacroNode,
    MethodNode,
    ModuleNode,
    ParamNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
    Visibility,
)
from ast_intel.models.workspace_model import CrateDependency, CrateModel

__all__: list[str] = ["CppExtractor"]

logger = logging.getLogger(__name__)

_MAX_RAW_CONSTANT_LENGTH = 120

# C++ files use the C++ grammar; C / header files use the C grammar.
_CPP_EXTENSIONS: frozenset[str] = frozenset({".cpp", ".hpp", ".cc", ".hh", ".cxx"})
_C_EXTENSIONS: frozenset[str] = frozenset({".c", ".h"})

# CMakeLists.txt project() and find_package() patterns.
_CMAKE_PROJECT_RE: re.Pattern[str] = re.compile(
    r"project\s*\(\s*(\w+)", re.IGNORECASE,
)
_CMAKE_FIND_PKG_RE: re.Pattern[str] = re.compile(
    r"find_package\s*\(\s*(\w+)", re.IGNORECASE,
)

# Visibility mapping for C++ access specifiers.
_ACCESS_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "private": Visibility.PRIVATE,
    "protected": Visibility.PROTECTED,
}


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node | None, src: bytes) -> str:
    """Return decoded text for a node, or empty string if *node* is ``None``."""
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _child_by_type(node: Node, *types: str) -> Node | None:
    """Return the first child matching any of the given types."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _children_by_type(node: Node, *types: str) -> list[Node]:
    """Return all children matching any of the given types."""
    return [c for c in node.children if c.type in types]


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract leading ``//`` or ``/* */`` doc comments from preceding siblings."""
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
            elif text.startswith("/*") and text.endswith("*/"):
                body = text[2:-2].strip()
                docs.insert(0, body)
            else:
                break
        else:
            break
    return " ".join(docs) if docs else ""


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Parameter extraction
# ---------------------------------------------------------------------------


def _extract_parameters(params_node: Node | None, src: bytes) -> tuple[ParamNode, ...]:
    """Parse a ``parameter_list`` into ``ParamNode`` tuples."""
    if params_node is None:
        return ()
    params: list[ParamNode] = []
    for child in params_node.named_children:
        if child.type in (
            "parameter_declaration",
            "optional_parameter_declaration",
            "variadic_parameter_declaration",
        ):
            _extract_one_param(child, src, params)
    return tuple(params)


def _extract_one_param(
    decl: Node,
    src: bytes,
    params: list[ParamNode],
) -> None:
    """Extract a single parameter declaration."""
    # The declarator child holds the name; the type is everything else.
    declarator = _child_by_type(decl, "identifier", "pointer_declarator",
                                "reference_declarator", "array_declarator")
    if declarator is not None:
        name = _leaf_identifier(declarator, src)
        type_text = _node_text(decl, src)
        # Remove param name from the type text for cleaner output
        if name and name in type_text:
            type_text = type_text.replace(name, "").strip().rstrip(",")
        params.append(ParamNode(name=name, type=type_text.strip()))
    else:
        # Unnamed parameter (type only)
        params.append(ParamNode(name="", type=_node_text(decl, src)))


def _leaf_identifier(node: Node, src: bytes) -> str:
    """Drill into pointer/reference/array declarators to find the identifier."""
    if node.type in ("identifier", "field_identifier"):
        return _node_text(node, src)
    ident = _child_by_type(node, "identifier", "field_identifier")
    if ident is not None:
        return _node_text(ident, src)
    # Recursive unwrap for nested pointer/reference
    inner = _child_by_type(node, "pointer_declarator", "reference_declarator",
                           "array_declarator")
    if inner is not None:
        return _leaf_identifier(inner, src)
    return _node_text(node, src)


# endregion: --- Parameter extraction


# ---------------------------------------------------------------------------
# region:    --- Return type extraction
# ---------------------------------------------------------------------------


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract the return type from a function/method declaration node."""
    # In C/C++ tree-sitter, the type specifier comes before the declarator.
    type_node = _child_by_type(node, "type_identifier", "primitive_type",
                               "sized_type_specifier", "template_type",
                               "qualified_identifier", "auto",
                               "dependent_type", "placeholder_type_specifier")
    if type_node is not None:
        return _node_text(type_node, src)
    return ""


# endregion: --- Return type extraction


# ---------------------------------------------------------------------------
# region:    --- Struct / class extraction
# ---------------------------------------------------------------------------


def _extract_struct_fields(
    body: Node | None,
    src: bytes,
    *,
    is_c: bool,
) -> tuple[tuple[FieldNode, ...], Visibility]:
    """Extract fields from a struct/class body.

    Returns the fields and the current access specifier (for classes).
    For C structs all fields are public.
    """
    if body is None:
        return (), Visibility.PUBLIC
    fields: list[FieldNode] = []
    current_vis = Visibility.PUBLIC if is_c else Visibility.PRIVATE  # C++ class default
    for child in body.children:
        if child.type == "access_specifier":
            spec_text = _node_text(child, src).rstrip(":").strip()
            current_vis = _ACCESS_MAP.get(spec_text, Visibility.PRIVATE)
        elif child.type == "field_declaration":
            _extract_field_decl(child, src, current_vis, fields)
    return tuple(fields), current_vis


def _extract_field_decl(
    node: Node,
    src: bytes,
    vis: Visibility,
    fields: list[FieldNode],
) -> None:
    """Extract a single field_declaration into FieldNode(s)."""
    # Get the type portion
    type_text = _extract_return_type(node, src)
    if not type_text:
        type_text = _node_text(node, src).split(";")[0].strip()

    # Get declarator(s) — may have multiple: `int a, b;`
    declarators = _children_by_type(
        node, "field_identifier", "pointer_declarator",
        "reference_declarator", "array_declarator",
    )
    if declarators:
        for d in declarators:
            name = _leaf_identifier(d, src)
            if name:
                fields.append(FieldNode(name=name, type=type_text, visibility=vis))
    else:
        # Fallback: try to find any identifier
        ident = _child_by_type(node, "identifier", "field_identifier")
        if ident is not None:
            fields.append(
                FieldNode(
                    name=_node_text(ident, src),
                    type=type_text,
                    visibility=vis,
                )
            )


def _extract_methods_from_body(  # noqa: C901
    body: Node | None,
    src: bytes,
    class_name: str,
    *,
    is_c: bool,
) -> tuple[list[MethodNode], list[TraitItemNode]]:
    """Extract methods from a class/struct body.

    Returns (concrete_methods, pure_virtual_items).
    """
    if body is None:
        return [], []
    methods: list[MethodNode] = []
    trait_items: list[TraitItemNode] = []
    current_vis = Visibility.PUBLIC if is_c else Visibility.PRIVATE

    for child in body.children:
        if child.type == "access_specifier":
            spec_text = _node_text(child, src).rstrip(":").strip()
            current_vis = _ACCESS_MAP.get(spec_text, Visibility.PRIVATE)
        elif child.type in ("function_definition", "declaration",
                           "field_declaration"):
            m, is_pure = _extract_class_method(child, src, current_vis)
            if m is not None:
                if is_pure:
                    trait_items.append(TraitItemNode(
                        kind=TraitItemKind.REQUIRED_METHOD,
                        name=m.name,
                        params=m.params,
                        return_type=m.return_type,
                        span=m.span,
                    ))
                else:
                    methods.append(m)
        elif child.type == "template_declaration":
            # Template method inside class
            inner = _child_by_type(child, "function_definition", "declaration")
            if inner is not None:
                m, is_pure = _extract_class_method(inner, src, current_vis)
                if m is not None:
                    if is_pure:
                        trait_items.append(TraitItemNode(
                            kind=TraitItemKind.REQUIRED_METHOD,
                            name=m.name,
                            params=m.params,
                            return_type=m.return_type,
                            span=m.span,
                        ))
                    else:
                        methods.append(m)
    return methods, trait_items


def _extract_class_method(
    node: Node,
    src: bytes,
    vis: Visibility,
) -> tuple[MethodNode | None, bool]:
    """Extract a single method from a class body.

    Returns (MethodNode, is_pure_virtual).
    """
    full_text = _node_text(node, src)
    is_pure = full_text.rstrip(";").rstrip().endswith("= 0")
    is_static = "static" in full_text.split("(")[0] if "(" in full_text else False
    is_virtual = "virtual" in full_text.split("(")[0] if "(" in full_text else False

    # Get the declarator (function_declarator)
    declarator = _child_by_type(node, "function_declarator")
    if declarator is None:
        # Might be a nested declarator or init_declarator
        init_decl = _child_by_type(node, "init_declarator")
        if init_decl is not None:
            declarator = _child_by_type(init_decl, "function_declarator")
    if declarator is None:
        # For field_declaration: type name(params);  — check for ( in text
        full_raw = _node_text(node, src)
        if "(" not in full_raw:
            return None, False
        # Try finding declarator deeper
        for child in node.named_children:
            fd = _child_by_type(child, "function_declarator")
            if fd is not None:
                declarator = fd
                break
    if declarator is None:
        return None, False

    # Name
    name_node = _child_by_type(
        declarator, "identifier", "field_identifier",
        "destructor_name", "operator_name", "qualified_identifier",
    )
    name = _node_text(name_node, src) if name_node else ""
    if not name:
        return None, False

    # Skip destructors for trait detection
    is_destructor = name.startswith("~")

    # Params
    params_node = _child_by_type(declarator, "parameter_list")
    params = _extract_parameters(params_node, src)

    # Return type
    return_type = _extract_return_type(node, src)

    method = MethodNode(
        name=name,
        visibility=vis,
        is_static=is_static,
        params=params,
        return_type=return_type,
        span=span_from_node(node),
    )
    # Destructors are not pure virtual for trait detection purposes
    return method, is_pure and not is_destructor and is_virtual


def _is_pure_virtual_class(
    methods: list[MethodNode],
    trait_items: list[TraitItemNode],
    fields: tuple[FieldNode, ...],
) -> bool:
    """A class is a trait if it has no concrete non-special methods and ≥1 pure virtual."""
    if not trait_items:
        return False
    if fields:
        return False
    # Allow destructors and static methods in trait classes
    return all(
        m.name.startswith("~") or m.is_static for m in methods
    )


def _extract_struct_or_class(
    node: Node,
    src: bytes,
    namespace: str,
    *,
    is_c: bool,
) -> tuple[
    StructNode | None,
    TraitNode | None,
    list[ImplBlockNode],
    list[MethodNode],
]:
    """Extract a struct or class declaration.

    Returns (struct_or_none, trait_or_none, impl_blocks, methods).
    """
    name_node = _child_by_type(node, "type_identifier", "name")
    name = _node_text(name_node, src)
    if not name:
        return None, None, [], []

    doc = _extract_doc_comment(node, src)
    generics = _extract_template_params(node)
    body = _child_by_type(node, "field_declaration_list")
    vis = Visibility.PUBLIC  # Top-level C/C++ types are public

    fields, _ = _extract_struct_fields(body, src, is_c=is_c)
    methods, trait_items = _extract_methods_from_body(
        body, src, name, is_c=is_c,
    )

    # Base classes
    base_classes = _extract_base_classes(node, src)
    impl_blocks: list[ImplBlockNode] = []

    # Check for pure virtual → TraitNode
    if not is_c and _is_pure_virtual_class(methods, trait_items, fields):
        trait = TraitNode(
            name=name,
            visibility=vis,
            generics=generics,
            items=tuple(trait_items),
            doc=doc,
            span=span_from_node(node),
        )
        return None, trait, impl_blocks, methods

    # Create struct
    struct = StructNode(
        name=name,
        visibility=vis,
        generics=generics,
        fields=fields,
        doc=doc,
        span=span_from_node(node),
    )

    # Create impl block for methods
    if methods:
        impl_blocks.append(ImplBlockNode(
            self_type=name,
            methods=tuple(methods),
            span=span_from_node(node),
        ))

    # Create impl blocks for base class inheritance
    impl_blocks.extend(
        ImplBlockNode(
            self_type=name,
            trait_type=base,
            span=span_from_node(node),
        )
        for base in base_classes
    )

    return struct, None, impl_blocks, methods


def _extract_base_classes(node: Node, src: bytes) -> list[str]:
    """Extract base class names from a class declaration."""
    base_clause = _child_by_type(node, "base_class_clause")
    if base_clause is None:
        return []
    bases: list[str] = []
    for child in base_clause.named_children:
        if child.type == "base_class_specifier":
            type_id = _child_by_type(child, "type_identifier", "qualified_identifier",
                                     "template_type")
            if type_id is not None:
                bases.append(_node_text(type_id, src))
        elif child.type in ("type_identifier", "qualified_identifier", "template_type"):
            # Direct type identifier (not wrapped in base_class_specifier)
            bases.append(_node_text(child, src))
    return bases


def _extract_template_params(node: Node) -> str:
    """Walk up to find a parent ``template_declaration`` and extract generics."""
    if node.parent is not None and node.parent.type == "template_declaration":
        tpl = node.parent
        for child in tpl.children:
            if child.type == "template_parameter_list":
                # Return the raw text including angle brackets
                return child.text.decode("utf-8", errors="replace").strip() if child.text else ""
    return ""


# endregion: --- Struct / class extraction


# ---------------------------------------------------------------------------
# region:    --- Free function extraction
# ---------------------------------------------------------------------------


def _extract_function(
    node: Node,
    src: bytes,
    namespace: str,
) -> FunctionNode | None:
    """Extract a top-level function definition into a FunctionNode."""
    declarator = _child_by_type(node, "function_declarator")
    if declarator is None:
        return None

    name_node = _child_by_type(
        declarator, "identifier", "qualified_identifier",
    )
    name = _node_text(name_node, src) if name_node else ""
    if not name:
        return None

    # If qualified (Foo::bar), this is an out-of-class definition — skip here
    if "::" in name:
        return None

    full_text = _node_text(node, src)
    is_static = full_text.startswith("static ")

    params_node = _child_by_type(declarator, "parameter_list")
    params = _extract_parameters(params_node, src)
    return_type = _extract_return_type(node, src)
    doc = _extract_doc_comment(node, src)

    vis = Visibility.PRIVATE if is_static else Visibility.PUBLIC

    return FunctionNode(
        name=name,
        visibility=vis,
        params=params,
        return_type=return_type,
        doc=doc,
        span=span_from_node(node),
    )


def _extract_out_of_class_method(
    node: Node,
    src: bytes,
) -> tuple[str, MethodNode] | None:
    """Extract ``void Foo::bar() { }`` → (class_name, MethodNode)."""
    declarator = _child_by_type(node, "function_declarator")
    if declarator is None:
        return None

    name_node = _child_by_type(declarator, "qualified_identifier")
    if name_node is None:
        return None

    qualified = _node_text(name_node, src)
    if "::" not in qualified:
        return None

    parts = qualified.rsplit("::", maxsplit=1)
    class_name = parts[0]
    method_name = parts[1]

    params_node = _child_by_type(declarator, "parameter_list")
    params = _extract_parameters(params_node, src)
    return_type = _extract_return_type(node, src)
    doc = _extract_doc_comment(node, src)

    method = MethodNode(
        name=method_name,
        visibility=Visibility.PUBLIC,
        params=params,
        return_type=return_type,
        doc=doc,
        span=span_from_node(node),
    )
    return class_name, method


# endregion: --- Free function extraction


# ---------------------------------------------------------------------------
# region:    --- Enum extraction
# ---------------------------------------------------------------------------


def _extract_enum(node: Node, src: bytes) -> EnumNode | None:
    """Extract an enum or enum class into an EnumNode."""
    name_node = _child_by_type(node, "type_identifier")
    name = _node_text(name_node, src) if name_node else ""
    if not name:
        return None

    doc = _extract_doc_comment(node, src)
    body = _child_by_type(node, "enumerator_list")
    variants: list[EnumVariantNode] = []
    if body is not None:
        for child in body.named_children:
            if child.type == "enumerator":
                ident = _child_by_type(child, "identifier")
                vname = _node_text(ident, src) if ident else ""
                if vname:
                    variants.append(EnumVariantNode(
                        name=vname, kind=EnumVariantKind.UNIT,
                    ))

    return EnumNode(
        name=name,
        visibility=Visibility.PUBLIC,
        variants=tuple(variants),
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Enum extraction


# ---------------------------------------------------------------------------
# region:    --- Typedef / using / macro extraction
# ---------------------------------------------------------------------------


def _extract_typedef(node: Node, src: bytes) -> TypeAliasNode | None:
    """Extract ``typedef X Y;`` into a TypeAliasNode."""
    full = _node_text(node, src).rstrip(";").strip()
    rest = full[len("typedef"):].strip() if full.startswith("typedef") else full

    # Last word is the alias name
    parts = rest.rsplit(maxsplit=1)
    if len(parts) < 2:  # noqa: PLR2004
        return None
    aliased_to = parts[0].strip()
    name = parts[1].strip().rstrip(";")
    # Clean up function pointer typedefs: `void (*callback_fn)(int, int)`
    if "(*" in rest:
        paren_start = rest.index("(*")
        paren_end = rest.index(")", paren_start + 2)
        name = rest[paren_start + 2:paren_end]
        aliased_to = rest[:paren_start].strip() + rest[paren_end + 1:].strip()

    if not name:
        return None
    return TypeAliasNode(
        name=name,
        aliased_to=aliased_to,
        visibility=Visibility.PUBLIC,
        span=span_from_node(node),
    )


def _extract_using_alias(node: Node, src: bytes) -> TypeAliasNode | None:
    """Extract ``using X = Y;`` (C++) into a TypeAliasNode."""
    # using alias: `using Name = Type;`
    full = _node_text(node, src).rstrip(";").strip()
    if "=" not in full:
        return None
    parts = full.split("=", maxsplit=1)
    name = parts[0].replace("using", "").strip()
    aliased_to = parts[1].strip()
    if not name:
        return None
    return TypeAliasNode(
        name=name,
        aliased_to=aliased_to,
        visibility=Visibility.PUBLIC,
        span=span_from_node(node),
    )


def _extract_macro(node: Node, src: bytes) -> MacroNode | None:
    """Extract ``#define NAME ...`` into a MacroNode."""
    name_node = _child_by_type(node, "identifier")
    if name_node is None:
        return None
    name = _node_text(name_node, src)
    if not name:
        return None
    doc = _extract_doc_comment(node, src)
    return MacroNode(
        name=name,
        visibility=Visibility.PUBLIC,
        doc=doc,
        span=span_from_node(node),
    )


# endregion: --- Typedef / using / macro extraction


# ---------------------------------------------------------------------------
# region:    --- Include / namespace extraction
# ---------------------------------------------------------------------------


def _collect_includes(root: Node, src: bytes) -> list[str]:
    """Collect ``#include`` directives as import strings."""
    includes: list[str] = []
    for child in root.children:
        if child.type == "preproc_include":
            path_node = _child_by_type(
                child, "string_literal", "system_lib_string",
            )
            if path_node is not None:
                includes.append(_node_text(path_node, src))
    return includes


def _extract_namespace(node: Node, src: bytes) -> str:
    """Extract the namespace name from a namespace_definition."""
    # namespace a::b { ... }
    name_node = _child_by_type(
        node, "namespace_identifier", "nested_namespace_specifier",
    )
    if name_node is not None:
        return _node_text(name_node, src)
    # Fallback: concatenate identifiers
    idents = _children_by_type(node, "identifier")
    if idents:
        return "::".join(_node_text(i, src) for i in idents)
    return ""


# endregion: --- Include / namespace extraction


# ---------------------------------------------------------------------------
# region:    --- Declaration extraction (function declarations in headers)
# ---------------------------------------------------------------------------


def _extract_function_declaration(
    node: Node,
    src: bytes,
) -> FunctionNode | None:
    """Extract a function declaration (no body) from a header file."""
    declarator = _child_by_type(node, "function_declarator")
    if declarator is None:
        # Try init_declarator wrapping
        init_decl = _child_by_type(node, "init_declarator")
        if init_decl is not None:
            declarator = _child_by_type(init_decl, "function_declarator")
    if declarator is None:
        return None

    name_node = _child_by_type(declarator, "identifier", "qualified_identifier")
    name = _node_text(name_node, src) if name_node else ""
    if not name or "::" in name:
        return None

    params_node = _child_by_type(declarator, "parameter_list")
    params = _extract_parameters(params_node, src)
    return_type = _extract_return_type(node, src)

    return FunctionNode(
        name=name,
        visibility=Visibility.PUBLIC,
        params=params,
        return_type=return_type,
        span=span_from_node(node),
    )


# endregion: --- Declaration extraction


# ---------------------------------------------------------------------------
# region:    --- self_methods builder
# ---------------------------------------------------------------------------


def _collect_self_methods(
    functions: list[FunctionNode],
    impl_blocks: list[ImplBlockNode],
) -> list[MethodNode]:
    """Build flat list of every function/method defined in a file."""
    methods: list[MethodNode] = []
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
# region:    --- CppExtractor class
# ---------------------------------------------------------------------------


class CppExtractor(ExtractorBase):
    """C / C++ language extractor using tree-sitter.

    Uses ``tree-sitter-c`` for ``.c`` / ``.h`` files and
    ``tree-sitter-cpp`` for ``.cpp`` / ``.hpp`` / ``.cc`` / ``.hh`` / ``.cxx``.
    """

    language_id: str = "cpp"
    file_extensions: list[str] = [  # noqa: RUF012
        ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx",
    ]

    def __init__(self) -> None:
        """Initialize parsers for both C and C++ grammars."""
        import tree_sitter_c
        import tree_sitter_cpp

        self._c_lang = Language(tree_sitter_c.language())
        self._cpp_lang = Language(tree_sitter_cpp.language())
        self._c_parser = Parser(self._c_lang)
        self._cpp_parser = Parser(self._cpp_lang)

    def _parser_for(self, file_path: Path) -> tuple[Parser, bool]:
        """Select the correct parser based on file extension.

        Returns (parser, is_c).
        """
        suffix = file_path.suffix.lower()
        if suffix in _CPP_EXTENSIONS:
            return self._cpp_parser, False
        return self._c_parser, True

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a C or C++ file into a ``FileAST``."""
        parser, is_c = self._parser_for(file_path)
        tree = parser.parse(source)
        ast = FileAST(file=str(file_path))

        out_of_class_methods: dict[str, list[MethodNode]] = {}

        self._walk_top_level(
            tree.root_node, source, ast,
            namespace="", is_c=is_c,
            out_of_class_methods=out_of_class_methods,
        )

        # Post-pass 1: merge out-of-class methods into existing impl blocks
        self._merge_out_of_class(ast, out_of_class_methods)

        # Post-pass 2: self_methods
        ast.self_methods = _collect_self_methods(ast.functions, ast.impl_blocks)

        # Post-pass 3: includes → uses
        ast.uses = _collect_includes(tree.root_node, source)

        # Post-pass 4: rationale comments
        ast.rationale_comments = extract_rationale_comments(
            tree.root_node, source,
        )

        # Post-pass 5: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(
            tree.root_node, source, defined_names,
        )

        return ast

    def _walk_top_level(  # noqa: PLR0913
        self,
        root: Node,
        src: bytes,
        ast: FileAST,
        *,
        namespace: str,
        is_c: bool,
        out_of_class_methods: dict[str, list[MethodNode]],
    ) -> None:
        """Walk top-level AST children, dispatching to type-specific handlers."""
        for child in root.children:
            if not child.is_named:
                continue
            self._dispatch_node(
                child, src, ast, namespace=namespace, is_c=is_c,
                out_of_class_methods=out_of_class_methods,
            )

    def _dispatch_node(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        *,
        namespace: str,
        is_c: bool,
        out_of_class_methods: dict[str, list[MethodNode]],
    ) -> None:
        """Dispatch a single top-level node to its handler."""
        ntype = node.type

        if ntype in ("struct_specifier", "class_specifier"):
            struct, trait, impls, _ = _extract_struct_or_class(
                node, src, namespace, is_c=is_c,
            )
            if struct is not None:
                ast.structs.append(struct)
            if trait is not None:
                ast.traits.append(trait)
            ast.impl_blocks.extend(impls)

        elif ntype == "function_definition":
            # Check if it's an out-of-class method
            oc = _extract_out_of_class_method(node, src)
            if oc is not None:
                cls_name, method = oc
                out_of_class_methods.setdefault(cls_name, []).append(method)
            else:
                fn = _extract_function(node, src, namespace)
                if fn is not None:
                    ast.functions.append(fn)

        elif ntype == "declaration":
            # Could be: function declaration, typedef, variable, etc.
            self._handle_declaration(node, src, ast, namespace=namespace, is_c=is_c)

        elif ntype == "enum_specifier":
            enum = _extract_enum(node, src)
            if enum is not None:
                ast.enums.append(enum)

        elif ntype == "type_definition":
            # typedef
            alias = _extract_typedef(node, src)
            if alias is not None:
                # Check if it's actually a typedef enum
                if _child_by_type(node, "enum_specifier") is not None:
                    enum = _extract_enum(
                        _child_by_type(node, "enum_specifier"),  # type: ignore[arg-type]
                        src,
                    )
                    if enum is None:
                        # Anonymous enum with typedef name
                        body = _child_by_type(
                            _child_by_type(node, "enum_specifier"),  # type: ignore[arg-type]
                            "enumerator_list",
                        )
                        variants: list[EnumVariantNode] = []
                        if body is not None:
                            for v in body.named_children:
                                if v.type == "enumerator":
                                    id_node = _child_by_type(v, "identifier")
                                    vname = _node_text(id_node, src) if id_node else ""
                                    if vname:
                                        variants.append(EnumVariantNode(
                                            name=vname, kind=EnumVariantKind.UNIT,
                                        ))
                        if variants:
                            ast.enums.append(EnumNode(
                                name=alias.name,
                                visibility=Visibility.PUBLIC,
                                variants=tuple(variants),
                                span=span_from_node(node),
                            ))
                    else:
                        ast.enums.append(enum)
                elif _child_by_type(node, "struct_specifier") is not None:
                    # typedef struct { } Name;
                    struct_spec = _child_by_type(node, "struct_specifier")
                    assert struct_spec is not None  # guarded above
                    body = _child_by_type(struct_spec, "field_declaration_list")
                    # Check if anonymous struct (no type_identifier)
                    sname_node = _child_by_type(struct_spec, "type_identifier")
                    if sname_node is None and body is not None:
                        # Anonymous typedef struct — use the typedef name
                        fields, _ = _extract_struct_fields(
                            body, src, is_c=is_c,
                        )
                        ast.structs.append(StructNode(
                            name=alias.name,
                            visibility=Visibility.PUBLIC,
                            fields=fields,
                            span=span_from_node(node),
                        ))
                    else:
                        s, _t, impls, _ = _extract_struct_or_class(
                            struct_spec, src, namespace, is_c=is_c,
                        )
                        if s is not None:
                            ast.structs.append(s)
                        ast.impl_blocks.extend(impls)
                else:
                    ast.type_aliases.append(alias)

        elif ntype == "preproc_def":
            macro = _extract_macro(node, src)
            if macro is not None:
                ast.macros.append(macro)

        elif ntype == "namespace_definition":
            ns_name = _extract_namespace(node, src)
            full_ns = f"{namespace}::{ns_name}" if namespace else ns_name
            if full_ns:
                ast.modules.append(ModuleNode(
                    name=full_ns, visibility=Visibility.PUBLIC,
                    inline=True, span=span_from_node(node),
                ))
            # Recurse into namespace body
            body = _child_by_type(node, "declaration_list")
            if body is not None:
                self._walk_top_level(
                    body, src, ast, namespace=full_ns, is_c=is_c,
                    out_of_class_methods=out_of_class_methods,
                )

        elif ntype == "template_declaration":
            # Unwrap: dispatch the inner declaration
            for child in node.named_children:
                if child.type != "template_parameter_list":
                    self._dispatch_node(
                        child, src, ast, namespace=namespace, is_c=is_c,
                        out_of_class_methods=out_of_class_methods,
                    )

        elif ntype == "using_declaration":
            alias = _extract_using_alias(node, src)
            if alias is not None:
                ast.type_aliases.append(alias)

        elif ntype == "linkage_specification":
            # extern "C" { ... }
            body = _child_by_type(node, "declaration_list")
            if body is not None:
                self._walk_top_level(
                    body, src, ast, namespace=namespace, is_c=is_c,
                    out_of_class_methods=out_of_class_methods,
                )

        elif ntype in ("preproc_ifdef", "preproc_if", "preproc_elif"):
            # Recurse into preprocessor conditionals — contents are children
            for child in node.children:
                if child.is_named:
                    self._dispatch_node(
                        child, src, ast, namespace=namespace, is_c=is_c,
                        out_of_class_methods=out_of_class_methods,
                    )

    def _handle_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        *,
        namespace: str,
        is_c: bool,
    ) -> None:
        """Handle a generic ``declaration`` node.

        This can be a function declaration (prototype), a variable, or
        a struct/class forward declaration. We check for function
        declarators first.
        """
        # Check for struct/class with body inside declaration
        for child_type in ("struct_specifier", "class_specifier"):
            spec = _child_by_type(node, child_type)
            if spec is not None and _child_by_type(spec, "field_declaration_list"):
                struct, trait, impls, _ = _extract_struct_or_class(
                    spec, src, namespace, is_c=is_c,
                )
                if struct is not None:
                    ast.structs.append(struct)
                if trait is not None:
                    ast.traits.append(trait)
                ast.impl_blocks.extend(impls)
                return

        # Check for enum inside declaration
        enum_spec = _child_by_type(node, "enum_specifier")
        if enum_spec is not None:
            enum = _extract_enum(enum_spec, src)
            if enum is not None:
                ast.enums.append(enum)
            return

        # Function declaration (prototype)
        fn = _extract_function_declaration(node, src)
        if fn is not None:
            ast.functions.append(fn)

    def _merge_out_of_class(
        self,
        ast: FileAST,
        out_of_class_methods: dict[str, list[MethodNode]],
    ) -> None:
        """Merge out-of-class method definitions into existing impl blocks."""
        for cls_name, methods in out_of_class_methods.items():
            # Find existing impl block for this class
            found = False
            for impl in ast.impl_blocks:
                if impl.self_type == cls_name and not impl.trait_type:
                    # Merge methods
                    existing = list(impl.methods)
                    existing.extend(methods)
                    # Replace the impl block (frozen dataclass)
                    idx = ast.impl_blocks.index(impl)
                    ast.impl_blocks[idx] = ImplBlockNode(
                        self_type=impl.self_type,
                        trait_type=impl.trait_type,
                        generics=impl.generics,
                        methods=tuple(existing),
                        span=impl.span,
                    )
                    found = True
                    break
            if not found:
                # Create new impl block
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=cls_name,
                    methods=tuple(methods),
                ))

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``CMakeLists.txt`` file (best-effort).

        Extracts project name and ``find_package()`` dependencies.
        """
        content = manifest_path.read_text(encoding="utf-8", errors="replace")

        # Project name
        project_match = _CMAKE_PROJECT_RE.search(content)
        name = project_match.group(1) if project_match else manifest_path.parent.name

        # Dependencies
        deps = [
            CrateDependency(name=m.group(1), version="")
            for m in _CMAKE_FIND_PKG_RE.finditer(content)
        ]

        return CrateModel(
            name=name,
            version="",
            language="cpp",
            dependencies=deps,
        )


# endregion: --- CppExtractor class
