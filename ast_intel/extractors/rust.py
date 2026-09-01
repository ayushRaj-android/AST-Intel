"""Rust language extractor — reference implementation for AST Intel.

Parses ``.rs`` files using ``tree-sitter-rust`` and maps Rust-specific
constructs to the normalized AST schema. This is the first extractor,
and its structure serves as the template for all subsequent language
extractors.

Rust → Normalized mapping:
    ======================  ================
    Rust Concept            Normalized To
    ======================  ================
    ``struct``              StructNode
    ``enum``                EnumNode
    ``trait``               TraitNode
    ``impl Trait for Type`` ImplBlockNode
    ``impl Type``           ImplBlockNode
    ``fn``                  FunctionNode
    ``use a::b::{C, D}``   imports (raw str)
    ``Type::method()``      imported_package_methods
    ``type Alias = Orig``   TypeAliasNode
    ``const`` / ``static``  ConstantNode
    ``mod``                 ModuleNode
    ``macro_rules!``        MacroNode
    ======================  ================

Also parses ``Cargo.toml`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import ClassVar, NamedTuple

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

__all__: list[str] = ["RustExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews in extracted nodes.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, src: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
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


def _get_visibility(node: Node, src: bytes) -> Visibility:
    """Extract visibility modifier from a node's children."""
    for child in node.children:
        if child.type == "visibility_modifier":
            text = _node_text(child, src)
            if "crate" in text:
                return Visibility.CRATE
            return Visibility.PUBLIC
    return Visibility.PRIVATE


def _get_identifier(node: Node, src: bytes) -> str:
    """Extract the first identifier or type_identifier from children."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(child, src)
    return ""


def _get_generics(node: Node, src: bytes) -> str:
    """Extract type parameters text (e.g., ``<S, T>``)."""
    for child in node.children:
        if child.type in ("type_parameters", "type_arguments"):
            return _node_text(child, src)
    return ""


def _get_where_clause(node: Node, src: bytes) -> str:
    """Extract where clause text."""
    for child in node.children:
        if child.type == "where_clause":
            return _node_text(child, src)
    return ""


def _extract_attributes(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``#[...]`` attributes attached to an item.

    In tree-sitter-rust, attributes are **preceding siblings**, not children
    of the annotated item. We walk backwards from the item node.
    """
    if node.parent is None:
        return ()
    siblings = list(node.parent.children)
    try:
        idx = siblings.index(node)
    except ValueError:
        return ()
    attrs: list[str] = []
    for i in range(idx - 1, -1, -1):
        sib = siblings[i]
        if sib.type == "attribute_item":
            attrs.insert(0, _node_text(sib, src))
        elif sib.type == "line_comment":
            # Doc comments may precede attributes; keep walking
            continue
        else:
            break
    return tuple(attrs)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract leading ``///`` doc comments from preceding siblings.

    In tree-sitter-rust, doc comments appear as ``line_comment`` siblings
    **before** the item, potentially with ``attribute_item`` nodes in between.
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
        if sib.type == "line_comment":
            text = _node_text(sib, src)
            if text.startswith("///"):
                docs.insert(0, text[3:].strip())
            else:
                break
        elif sib.type == "attribute_item":
            # Attributes sit between doc comments and the item; skip them
            continue
        elif sib.type == "block_comment":
            docs.insert(0, _node_text(sib, src))
            break
        else:
            break
    return " ".join(docs) if docs else ""


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Extraction sub-functions
# ---------------------------------------------------------------------------


def _extract_parameters(params_node: Node | None, src: bytes) -> tuple[ParamNode, ...]:
    """Parse function parameters into ParamNode tuples."""
    if params_node is None:
        return ()
    params: list[ParamNode] = []
    for child in params_node.named_children:
        if child.type == "self_parameter":
            params.append(ParamNode(name=_node_text(child, src), type="Self"))
        elif child.type == "parameter":
            named = child.named_children
            if len(named) >= 2:  # noqa: PLR2004
                params.append(
                    ParamNode(
                        name=_node_text(named[0], src),
                        type=_node_text(named[1], src),
                    )
                )
            elif len(named) == 1:
                params.append(ParamNode(name=_node_text(named[0], src)))
    return tuple(params)


def _extract_return_type(node: Node, src: bytes) -> str:
    """Find ``->`` token and grab the subsequent type text."""
    found_arrow = False
    for child in node.children:
        if child.type == "->":
            found_arrow = True
            continue
        if found_arrow and child.is_named:
            return _node_text(child, src)
    return ""


def _extract_function_modifiers(node: Node, src: bytes) -> tuple[bool, bool]:
    """Extract async and unsafe flags from function_modifiers."""
    for child in node.children:
        if child.type == "function_modifiers":
            text = _node_text(child, src)
            return ("async" in text, "unsafe" in text)
    return (False, False)


class _FunctionSignature(NamedTuple):
    """Parsed components of a Rust function/method signature."""

    name: str
    visibility: Visibility
    generics: str
    params: tuple[ParamNode, ...]
    return_type: str
    is_async: bool
    is_unsafe: bool
    where_clause: str


def _extract_function_signature(
    node: Node,
    src: bytes,
) -> _FunctionSignature:
    """Parse a function/method signature into its components.

    Returns:
        Named tuple with all signature components.
    """
    name = _get_identifier(node, src)
    visibility = _get_visibility(node, src)
    generics = _get_generics(node, src)
    params_node = _child_by_type(node, "parameters")
    params = _extract_parameters(params_node, src)
    return_type = _extract_return_type(node, src)
    is_async, is_unsafe = _extract_function_modifiers(node, src)
    where_clause = _get_where_clause(node, src)
    return _FunctionSignature(
        name=name,
        visibility=visibility,
        generics=generics,
        params=params,
        return_type=return_type,
        is_async=is_async,
        is_unsafe=is_unsafe,
        where_clause=where_clause,
    )


def _extract_struct_fields(body_node: Node, src: bytes) -> tuple[FieldNode, ...]:
    """Parse struct fields from a field_declaration_list."""
    fields: list[FieldNode] = []
    for child in body_node.named_children:
        if child.type == "field_declaration":
            vis = _get_visibility(child, src)
            name = ""
            ftype = ""
            for c in child.children:
                if c.type == "field_identifier":
                    name = _node_text(c, src)
                elif (
                    c.type not in ("visibility_modifier", "field_identifier", ":")
                    and c.is_named
                ):
                    ftype = _node_text(c, src)
            fields.append(FieldNode(name=name, type=ftype, visibility=vis))
    return tuple(fields)


def _extract_tuple_struct_fields(body_node: Node, src: bytes) -> tuple[FieldNode, ...]:
    """Parse tuple struct fields from an ordered_field_declaration_list."""
    fields: list[FieldNode] = []
    for i, child in enumerate(body_node.named_children):
        vis = (
            _get_visibility(child, src)
            if child.type == "visibility_modifier"
            else Visibility.PRIVATE
        )
        ftype = _node_text(child, src)
        fields.append(FieldNode(name=str(i), type=ftype, visibility=vis))
    return tuple(fields)


def _extract_enum_variants(body_node: Node, src: bytes) -> tuple[EnumVariantNode, ...]:
    """Parse enum variants from an enum_variant_list."""
    variants: list[EnumVariantNode] = []
    for child in body_node.named_children:
        if child.type == "enum_variant":
            name = _get_identifier(child, src)
            kind = EnumVariantKind.UNIT
            variant_fields: tuple[FieldNode, ...] = ()
            for c in child.named_children:
                if c.type == "field_declaration_list":
                    kind = EnumVariantKind.STRUCT
                    variant_fields = _extract_struct_fields(c, src)
                elif c.type == "ordered_field_declaration_list":
                    kind = EnumVariantKind.TUPLE
                    variant_fields = _extract_tuple_struct_fields(c, src)
            variants.append(
                EnumVariantNode(name=name, kind=kind, fields=variant_fields)
            )
    return tuple(variants)


def _extract_trait_items(body_node: Node, src: bytes) -> tuple[TraitItemNode, ...]:
    """Parse trait body items (methods, associated types, consts)."""
    items: list[TraitItemNode] = []
    for child in body_node.named_children:
        if child.type == "function_signature_item":
            sig = _extract_function_signature(child, src)
            items.append(
                TraitItemNode(
                    kind=TraitItemKind.REQUIRED_METHOD,
                    name=sig.name,
                    is_async=sig.is_async,
                    params=sig.params,
                    return_type=sig.return_type,
                    doc=_extract_doc_comment(child, src),
                    span=span_from_node(child),
                )
            )
        elif child.type == "function_item":
            sig = _extract_function_signature(child, src)
            items.append(
                TraitItemNode(
                    kind=TraitItemKind.DEFAULT_METHOD,
                    name=sig.name,
                    is_async=sig.is_async,
                    params=sig.params,
                    return_type=sig.return_type,
                    doc=_extract_doc_comment(child, src),
                    span=span_from_node(child),
                )
            )
        elif child.type == "associated_type":
            items.append(
                TraitItemNode(
                    kind=TraitItemKind.ASSOCIATED_TYPE,
                    name=_get_identifier(child, src),
                    span=span_from_node(child),
                )
            )
        elif child.type == "const_item":
            items.append(
                TraitItemNode(
                    kind=TraitItemKind.CONSTANT,
                    name=_get_identifier(child, src),
                    span=span_from_node(child),
                )
            )
    return tuple(items)


def _extract_impl_methods(body_node: Node, src: bytes) -> tuple[MethodNode, ...]:
    """Parse methods inside an impl block's declaration_list."""
    methods: list[MethodNode] = []
    for child in body_node.named_children:
        if child.type in ("function_item", "function_signature_item"):
            sig = _extract_function_signature(child, src)
            methods.append(
                MethodNode(
                    name=sig.name,
                    visibility=sig.visibility,
                    is_async=sig.is_async,
                    is_unsafe=sig.is_unsafe,
                    params=sig.params,
                    return_type=sig.return_type,
                    doc=_extract_doc_comment(child, src),
                    span=span_from_node(child),
                )
            )
    return tuple(methods)


def _parse_impl_types(node: Node, src: bytes) -> tuple[str, str]:
    """Parse impl block to extract self_type and trait_type.

    Returns:
        Tuple of (self_type, trait_type). trait_type is empty for inherent impls.
    """
    has_for = any(c.type == "for" for c in node.children)
    type_refs = [
        _node_text(c, src)
        for c in node.children
        if c.is_named
        and c.type
        not in (
            "impl",
            "type_parameters",
            "declaration_list",
            "where_clause",
            "block",
            "visibility_modifier",
        )
    ]

    if has_for and len(type_refs) >= 2:  # noqa: PLR2004
        return (type_refs[1], type_refs[0])  # (self_type, trait_type)
    if type_refs:
        return (type_refs[0], "")
    return ("", "")


# endregion: --- Extraction sub-functions


# ---------------------------------------------------------------------------
# region:    --- Import map & scoped method calls
# ---------------------------------------------------------------------------


def build_import_map(use_statements: list[str]) -> dict[str, str]:
    """Parse Rust ``use`` statements into a short_name -> qualified path map.

    Handles:
    - Braced imports: ``use bytes::{Buf, BytesMut}``
    - Aliased imports: ``use foo::Bar as Baz``
    - Simple imports: ``use std::collections::HashMap``
    - Nested braces: ``use a::{b::C, d::E}``

    Args:
        use_statements: Raw ``use`` statement strings.

    Returns:
        Dict mapping short names to fully qualified paths.
        Example: ``{"BytesMut": "bytes::BytesMut", "HashMap": "std::collections::HashMap"}``
    """
    import_map: dict[str, str] = {}
    for stmt in use_statements:
        cleaned = stmt.strip().removeprefix("use ").rstrip(";")

        if "::{" in cleaned:
            base, rest = cleaned.split("::{", 1)
            rest = rest.rstrip("}")
            for raw_item in rest.split(","):
                entry = raw_item.strip()
                if not entry or entry == "self":
                    continue
                # Handle nested paths inside braces: use a::{b::C, d::E}
                if "::" in entry:
                    short = entry.split("::")[-1].split(" as ")[0].strip()
                    full_path = f"{base}::{entry.split(' as ')[0].strip()}"
                else:
                    short = entry.split(" as ")[-1].strip()
                    original = entry.split(" as ")[0].strip()
                    full_path = f"{base}::{original}"
                import_map[short] = full_path
        elif " as " in cleaned:
            original = cleaned.split(" as ")[0].strip()
            alias = cleaned.split(" as ")[1].strip()
            import_map[alias] = original
        elif "::" in cleaned:
            short = cleaned.split("::")[-1]
            import_map[short] = cleaned
        else:
            import_map[cleaned] = cleaned

    return import_map


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Walk the AST to find all ``Type::method()`` scoped call expressions.

    Only captures calls where the type is _explicitly_ named in the call
    syntax (e.g., ``BytesMut::with_capacity(n)``). Instance method calls
    like ``buf.reserve(n)`` are not captured (type inference required).

    Args:
        root: Tree-sitter root node.
        src: Source bytes.
        import_map: Short name -> qualified path map from ``build_import_map``.

    Returns:
        Dict of ``{"qualified::Type": ["method1", "method2"]}``.
    """
    results: dict[str, set[str]] = {}

    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            fn_node: Node | None = None
            for child in node.children:
                if child.type in ("scoped_identifier", "generic_function"):
                    fn_node = child
                    break
                if child.is_named and child.type != "arguments":
                    fn_node = child
                    break
            if fn_node is not None:
                _resolve_scoped_call(fn_node, src, import_map, results)
        stack.extend(node.children)
    return {k: sorted(v) for k, v in results.items()}


def _resolve_scoped_call(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    results: dict[str, set[str]],
) -> None:
    """Resolve a single scoped call node into results."""
    if node.type == "scoped_identifier":
        children = [c for c in node.children if c.type != "::"]
        if len(children) >= 2:  # noqa: PLR2004
            type_text = _node_text(children[0], src)
            method_text = _node_text(children[-1], src)
            qualified = import_map.get(type_text, type_text)
            results.setdefault(qualified, set()).add(method_text)
    elif node.type == "generic_function":
        inner = node.named_children[0] if node.named_children else None
        if inner:
            _resolve_scoped_call(inner, src, import_map, results)


# endregion: --- Import map & scoped method calls


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

    Args:
        functions: Free functions extracted from the file.
        impl_blocks: Impl blocks extracted from the file.

    Returns:
        Flat list of MethodNode instances.
    """
    methods: list[MethodNode] = []

    # Free functions
    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=fn.is_async,
            is_unsafe=fn.is_unsafe,
            params=fn.params,
            return_type=fn.return_type,
            context="free",
            attributes=fn.attributes,
            doc=fn.doc,
            span=fn.span,
        )
        for fn in functions
    )

    # Impl block methods
    for impl_block in impl_blocks:
        if impl_block.trait_type:
            context = f"impl:{impl_block.trait_type} for {impl_block.self_type}"
        else:
            context = f"impl:{impl_block.self_type}"

        methods.extend(
            MethodNode(
                name=method.name,
                visibility=method.visibility,
                is_async=method.is_async,
                is_unsafe=method.is_unsafe,
                params=method.params,
                return_type=method.return_type,
                context=context,
                attributes=method.attributes,
                doc=method.doc,
                span=method.span,
            )
            for method in impl_block.methods
        )

    return methods


# endregion: --- self_methods builder


# ---------------------------------------------------------------------------
# region:    --- RustExtractor class
# ---------------------------------------------------------------------------


class RustExtractor(ExtractorBase):
    """Rust language extractor using ``tree-sitter-rust``.

    Parses ``.rs`` source files and ``Cargo.toml`` manifests into the
    normalized AST schema.
    """

    language_id: str = "rust"
    file_extensions: list[str] = [".rs"]  # noqa: RUF012

    # Node types that contain bodies we must not recurse into
    _NO_RECURSE: ClassVar[frozenset[str]] = frozenset({
        "function_item",
        "impl_item",
        "trait_item",
        "mod_item",
    })

    def __init__(self) -> None:
        """Initialize the Rust parser with tree-sitter-rust grammar."""
        import tree_sitter_rust

        self._language = Language(tree_sitter_rust.language())
        self._parser = Parser(self._language)

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a single ``.rs`` file into a FileAST.

        Args:
            file_path: Absolute path to the ``.rs`` file.
            source: Raw file bytes (UTF-8).

        Returns:
            Populated FileAST with all Rust constructs extracted.
        """
        tree = self._parser.parse(source)
        # Note: is_test and module_path are populated by the workspace
        # orchestrator, not the per-file extractor (requires crate context).
        ast = FileAST(file=str(file_path))

        self._walk_top_level(tree.root_node, source, ast)

        # Post-pass 1: self_methods
        ast.self_methods = _collect_self_methods(ast.functions, ast.impl_blocks)

        # Post-pass 2: imported_package_methods
        import_map = build_import_map(ast.uses)
        ast.imported_package_methods = _extract_scoped_method_calls(
            tree.root_node, source, import_map,
        )

        # Post-pass 3: rationale comments
        ast.rationale_comments = extract_rationale_comments(
            tree.root_node, source,
        )

        # Post-pass 4: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(
            tree.root_node, source, defined_names,
        )

        # Post-pass 5: HTTP route extraction
        from ast_intel.extractors._routes import (
            detect_actix_routes,
            detect_axum_routes,
        )
        ast.routes = detect_axum_routes(tree.root_node, source)
        ast.routes += detect_actix_routes(ast.functions, ast.self_methods)

        # Post-pass 6: HTTP client call detection
        from ast_intel.extractors._http_calls import detect_reqwest_calls
        ast.http_calls = detect_reqwest_calls(tree.root_node, source)

        # Post-pass 7: Cloud SDK client detection (Rust uses path-style: Client::new)
        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_typescript,
        )

        ast.cloud_resources = detect_cloud_clients_typescript(
            tree.root_node, source,
        )

        return ast

    def _walk_top_level(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Walk top-level AST children, dispatching to type-specific handlers."""
        for child in node.children:
            if not child.is_named:
                continue
            self._dispatch_node(child, src, ast)

    def _dispatch_node(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Route a single AST node to its extraction handler."""
        ntype = node.type

        if ntype == "use_declaration":
            ast.uses.append(_node_text(node, src))
        elif ntype == "mod_item":
            self._handle_mod(node, src, ast)
            return
        elif ntype in ("struct_item", "enum_item"):
            self._handle_struct_or_enum(node, src, ast)
        elif ntype in ("trait_item", "function_item", "impl_item"):
            self._handle_trait_fn_impl(node, src, ast)
        elif ntype in ("type_item", "const_item", "static_item"):
            self._handle_type_const(node, src, ast)
        else:
            self._handle_macro_or_error(node, src, ast)

        # Recurse into containers; skip fn/impl/trait/mod bodies
        if ntype not in self._NO_RECURSE:
            for child in node.children:
                if child.is_named:
                    self._dispatch_node(child, src, ast)

    def _handle_mod(self, node: Node, src: bytes, ast: FileAST) -> None:
        """Extract mod_item and recurse into inline module bodies."""
        self._extract_mod(node, src, ast.modules)
        for child in node.children:
            if child.type == "declaration_list":
                for item in child.named_children:
                    self._dispatch_node(item, src, ast)

    def _handle_struct_or_enum(
        self, node: Node, src: bytes, ast: FileAST,
    ) -> None:
        """Dispatch struct_item / enum_item."""
        if node.type == "struct_item":
            self._extract_struct(node, src, ast.structs)
        else:
            self._extract_enum(node, src, ast.enums)

    def _handle_trait_fn_impl(
        self, node: Node, src: bytes, ast: FileAST,
    ) -> None:
        """Dispatch trait_item / function_item / impl_item."""
        if node.type == "trait_item":
            self._extract_trait(node, src, ast.traits)
        elif node.type == "function_item":
            self._extract_function(node, src, ast.functions)
        else:
            self._extract_impl(node, src, ast.impl_blocks)

    def _handle_type_const(
        self, node: Node, src: bytes, ast: FileAST,
    ) -> None:
        """Dispatch type_item / const_item / static_item."""
        if node.type == "type_item":
            self._extract_type_alias(node, src, ast.type_aliases)
        else:
            self._extract_constant(node, src, ast.constants)

    @staticmethod
    def _handle_macro_or_error(
        node: Node, src: bytes, ast: FileAST,
    ) -> None:
        """Handle macro_definition and ERROR nodes."""
        if node.type == "macro_definition":
            ast.macros.append(
                MacroNode(
                    name=_get_identifier(node, src),
                    visibility=_get_visibility(node, src),
                    doc=_extract_doc_comment(node, src),
                    span=span_from_node(node),
                )
            )
        elif node.type == "ERROR":
            ast.errors.append(
                f"parse error at byte {node.start_byte}: "
                f"{_node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]}"
            )

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``Cargo.toml`` manifest file.

        Args:
            manifest_path: Absolute path to ``Cargo.toml``.

        Returns:
            CrateModel with name, version, language, dependencies extracted.
        """
        try:
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            logger.warning("Failed to parse Cargo.toml: %s", manifest_path)
            return CrateModel(
                name=manifest_path.parent.name,
                language="rust",
                manifest_path=str(manifest_path),
            )

        pkg = data.get("package", {})
        crate_name = pkg.get("name", manifest_path.parent.name)
        version = pkg.get("version", "")

        # Parse dependencies
        deps = self._parse_cargo_deps(data.get("dependencies", {}), is_dev=False)
        dev_deps = self._parse_cargo_deps(
            data.get("dev-dependencies", {}), is_dev=True
        )

        # Augment declared ranges with resolved pins + transitive deps from
        # a sibling ``Cargo.lock``.
        from ast_intel.core.lockfile_parser import augment_with_lockfile

        merged = augment_with_lockfile(manifest_path.parent, "rust", deps + dev_deps)

        return CrateModel(
            name=crate_name,
            version=str(version) if version else "",
            manifest_path=str(manifest_path),
            language="rust",
            dependencies=merged,
        )

    # ------------------------------------------------------------------
    # Private extraction methods
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_mod(
        node: Node,
        src: bytes,
        modules: list[ModuleNode],
    ) -> None:
        """Extract a mod_item into a ModuleNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        has_block = any(c.type == "declaration_list" for c in node.children)
        modules.append(
            ModuleNode(
                name=name,
                visibility=vis,
                inline=has_block,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_struct(
        node: Node,
        src: bytes,
        structs: list[StructNode],
    ) -> None:
        """Extract a struct_item into a StructNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _get_generics(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        fields: tuple[FieldNode, ...] = ()
        for child in node.named_children:
            if child.type == "field_declaration_list":
                fields = _extract_struct_fields(child, src)
            elif child.type == "ordered_field_declaration_list":
                fields = _extract_tuple_struct_fields(child, src)

        structs.append(
            StructNode(
                name=name,
                visibility=vis,
                generics=generics,
                fields=fields,
                attributes=attrs,
                doc=doc,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_enum(
        node: Node,
        src: bytes,
        enums: list[EnumNode],
    ) -> None:
        """Extract an enum_item into an EnumNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _get_generics(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        variants: tuple[EnumVariantNode, ...] = ()
        for child in node.named_children:
            if child.type == "enum_variant_list":
                variants = _extract_enum_variants(child, src)

        enums.append(
            EnumNode(
                name=name,
                visibility=vis,
                generics=generics,
                variants=variants,
                attributes=attrs,
                doc=doc,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_trait(
        node: Node,
        src: bytes,
        traits: list[TraitNode],
    ) -> None:
        """Extract a trait_item into a TraitNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _get_generics(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        super_traits: tuple[str, ...] = ()
        for child in node.children:
            if child.type == "trait_bounds":
                super_traits = tuple(
                    _node_text(c, src) for c in child.named_children
                )

        items: tuple[TraitItemNode, ...] = ()
        for child in node.named_children:
            if child.type == "declaration_list":
                items = _extract_trait_items(child, src)

        traits.append(
            TraitNode(
                name=name,
                visibility=vis,
                generics=generics,
                super_traits=super_traits,
                items=items,
                attributes=attrs,
                doc=doc,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_function(
        node: Node,
        src: bytes,
        functions: list[FunctionNode],
    ) -> None:
        """Extract a top-level function_item into a FunctionNode."""
        sig = _extract_function_signature(node, src)
        functions.append(
            FunctionNode(
                name=sig.name,
                visibility=sig.visibility,
                is_async=sig.is_async,
                is_unsafe=sig.is_unsafe,
                generics=sig.generics,
                params=sig.params,
                return_type=sig.return_type,
                where_clause=sig.where_clause,
                attributes=_extract_attributes(node, src),
                doc=_extract_doc_comment(node, src),
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_impl(
        node: Node,
        src: bytes,
        impl_blocks: list[ImplBlockNode],
    ) -> None:
        """Extract an impl_item into an ImplBlockNode."""
        generics = _get_generics(node, src)
        self_type, trait_type = _parse_impl_types(node, src)

        methods: tuple[MethodNode, ...] = ()
        for child in node.named_children:
            if child.type == "declaration_list":
                methods = _extract_impl_methods(child, src)

        impl_blocks.append(
            ImplBlockNode(
                self_type=self_type,
                trait_type=trait_type,
                generics=generics,
                methods=methods,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_type_alias(
        node: Node,
        src: bytes,
        type_aliases: list[TypeAliasNode],
    ) -> None:
        """Extract a type_item into a TypeAliasNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        rhs = ""
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq and child.is_named:
                rhs = _node_text(child, src)
                break
        type_aliases.append(
            TypeAliasNode(
                name=name,
                aliased_to=rhs,
                visibility=vis,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _extract_constant(
        node: Node,
        src: bytes,
        constants: list[ConstantNode],
    ) -> None:
        """Extract a const_item or static_item into a ConstantNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        raw = _node_text(node, src)[:_MAX_RAW_CONSTANT_LENGTH]
        constants.append(
            ConstantNode(
                name=name,
                visibility=vis,
                raw=raw,
                span=span_from_node(node),
            )
        )

    @staticmethod
    def _parse_cargo_deps(
        deps_raw: dict[str, object],
        *,
        is_dev: bool,
    ) -> list[CrateDependency]:
        """Parse Cargo.toml dependency table into CrateDependency list."""
        deps: list[CrateDependency] = []
        for name, val in deps_raw.items():
            if isinstance(val, dict):
                deps.append(
                    CrateDependency(
                        name=name,
                        version=str(val.get("version", "")),
                        path=str(val.get("path", "")),
                        features=tuple(val.get("features", ())),
                        is_workspace=bool(val.get("workspace", False)),
                        is_dev=is_dev,
                    )
                )
            else:
                deps.append(
                    CrateDependency(
                        name=name,
                        version=str(val),
                        is_dev=is_dev,
                    )
                )
        return deps


# endregion: --- RustExtractor class
