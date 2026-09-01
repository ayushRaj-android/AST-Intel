r"""PHP language extractor — tree-sitter based AST extraction.

Parses ``.php`` files using ``tree-sitter-php`` and maps PHP-specific
constructs to the normalized AST schema.

PHP → Normalized mapping:
    ================================  =================
    PHP Concept                       Normalized To
    ================================  =================
    ``class``                         StructNode
    ``final class``                   StructNode (attr)
    ``readonly class``                StructNode (attr)
    ``abstract class``                TraitNode
    ``interface``                     TraitNode
    ``trait``                         TraitNode (attr)
    ``enum``                          EnumNode
    ``function`` (top-level)          FunctionNode
    ``method`` (in type)              MethodNode
    ``use X\Y\Z``                     uses (raw str)
    ``use X as Alias``                TypeAliasNode
    ``const``                         ConstantNode
    ``#[Attribute]``                  attributes
    ``namespace``                     module_path
    ================================  =================

Also parses ``composer.json`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import json
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

__all__: list[str] = ["PhpExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# PHP visibility mapping.
_VISIBILITY_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "protected": Visibility.PROTECTED,
    "private": Visibility.PRIVATE,
}


# ---------------------------------------------------------------------------
# region:    --- Helper NamedTuple
# ---------------------------------------------------------------------------


class _TypeInfo(NamedTuple):
    """Parsed metadata for a PHP type declaration."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool


# endregion: --- Helper NamedTuple


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node | None, src: bytes) -> str:
    """Return decoded text for a node, or empty string if *node* is None."""
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first child with the given ``type``, or None."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _children_by_type(node: Node, type_name: str) -> list[Node]:
    """Return all children with the given ``type``."""
    return [c for c in node.children if c.type == type_name]


def _get_identifier(node: Node, src: bytes) -> str:
    """Extract the name identifier from a declaration node."""
    ident = _child_by_type(node, "name")
    if ident is not None:
        return _node_text(ident, src)
    return ""


def _get_visibility(node: Node) -> Visibility:
    """Extract visibility from the declaration node's children.

    PHP defaults to **public** for class members when no modifier is
    present.  Top-level constructs use ``CRATE``.
    """
    vm = _child_by_type(node, "visibility_modifier")
    if vm is None:
        return Visibility.PUBLIC
    for child in vm.children:
        vis = _VISIBILITY_MAP.get(child.type)
        if vis is not None:
            return vis
    return Visibility.PUBLIC


def _get_visibility_with_src(node: Node, src: bytes) -> Visibility:
    """Extract visibility, using src bytes for fallback parsing."""
    vm = _child_by_type(node, "visibility_modifier")
    if vm is None:
        return Visibility.PUBLIC
    text = _node_text(vm, src).strip()
    return _VISIBILITY_MAP.get(text, Visibility.PUBLIC)


def _has_modifier(node: Node, modifier_type: str) -> bool:
    """Check if the declaration has a specific modifier child node."""
    return _child_by_type(node, modifier_type) is not None


def _extract_attributes(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``#[Attribute]`` values from ``attribute_list`` children."""
    attr_list = _child_by_type(node, "attribute_list")
    if attr_list is None:
        return ()

    attrs: list[str] = []
    for group in _children_by_type(attr_list, "attribute_group"):
        for attr in _children_by_type(group, "attribute"):
            name = _child_by_type(attr, "name")
            if name is not None:
                text = _node_text(name, src)
                args_node = _child_by_type(attr, "arguments")
                if args_node is not None:
                    text += _node_text(args_node, src)
                attrs.append(f"#[{text}]")
    return tuple(attrs)


def _find_preceding_sibling_index(node: Node) -> int:
    """Return the index of *node* in its parent's children, or -1."""
    parent = node.parent
    if parent is None:
        return -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return i
    return -1


def _parse_phpdoc_block(text: str) -> str:
    """Parse a ``/** ... */`` block comment into a clean doc string."""
    raw = text[3:].rstrip("*/").strip()
    lines = [ln.strip().lstrip("* ").strip() for ln in raw.splitlines()]
    clean = [ln for ln in lines if ln and not ln.startswith("@")]
    return " ".join(clean)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract PHPDoc ``/** ... */`` comment preceding a declaration.

    In tree-sitter-php, doc comments appear as ``comment`` nodes
    immediately before the declaration node.
    """
    idx = _find_preceding_sibling_index(node)
    if idx <= 0:
        return ""

    parent = node.parent
    assert parent is not None  # guaranteed by idx > 0

    sibling = parent.children[idx - 1]
    # Check for attribute_list — doc may be before the attribute_list
    if sibling.type == "attribute_list" and idx >= 2:  # noqa: PLR2004
        sibling = parent.children[idx - 2]

    if sibling.type == "comment":
        text = _node_text(sibling, src).strip()
        if text.startswith("/**"):
            return _parse_phpdoc_block(text)

    return ""


def _extract_generics_from_phpdoc(node: Node, src: bytes) -> str:
    """Parse ``@template T`` from preceding PHPDoc → ``<T>``."""
    idx = _find_preceding_sibling_index(node)
    if idx <= 0:
        return ""

    parent = node.parent
    assert parent is not None

    sibling = parent.children[idx - 1]
    if sibling.type == "attribute_list" and idx >= 2:  # noqa: PLR2004
        sibling = parent.children[idx - 2]

    if sibling.type != "comment":
        return ""

    text = _node_text(sibling, src).strip()
    if not text.startswith("/**"):
        return ""

    templates: list[str] = []
    for m in re.finditer(r"@template\s+(\w+)(?:\s+of\s+(\w+))?", text):
        name = m.group(1)
        constraint = m.group(2)
        templates.append(f"{name}: {constraint}" if constraint else name)

    if templates:
        return "<" + ", ".join(templates) + ">"
    return ""


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract return type after ``:`` in a function/method declaration.

    Handles primitive types, named types, nullable, union, intersection.
    """
    # Look for the colon separator followed by a type node
    found_colon = False
    for child in node.children:
        if child.type == ":":
            found_colon = True
            continue
        if found_colon and child.type not in (
            "{", "compound_statement", ";",
        ):
            return _format_type_node(child, src)
    return ""


def _format_type_node(node: Node, src: bytes) -> str:
    """Format a type node into a readable string."""
    ntype = node.type
    if ntype == "optional_type":
        inner = _format_type_inner(node, src)
        return f"?{inner}"
    if ntype == "union_type":
        parts = [
            _format_type_node(c, src) for c in node.children
            if c.type != "|"
        ]
        return "|".join(parts)
    if ntype == "intersection_type":
        parts = [
            _format_type_node(c, src) for c in node.children
            if c.type != "&"
        ]
        return "&".join(parts)
    if ntype in ("named_type", "primitive_type"):
        return _node_text(node, src).strip()
    return _node_text(node, src).strip()


def _format_type_inner(node: Node, src: bytes) -> str:
    """Get the inner type of an optional_type (after the ?)."""
    for child in node.children:
        if child.type != "?":
            return _format_type_node(child, src)
    return _node_text(node, src).strip().lstrip("?")


def _extract_parameters(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract parameters from ``formal_parameters``."""
    fp = _child_by_type(node, "formal_parameters")
    if fp is None:
        return ()

    params: list[ParamNode] = []
    for child in fp.children:
        if child.type == "simple_parameter":
            pname, ptype = _parse_simple_parameter(child, src)
            if pname:
                params.append(ParamNode(name=pname, type=ptype))
        elif child.type == "property_promotion_parameter":
            pname, ptype = _parse_promoted_parameter(child, src)
            if pname:
                params.append(ParamNode(name=pname, type=ptype))
    return tuple(params)


def _parse_simple_parameter(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a ``simple_parameter`` → (name, type)."""
    ptype = ""
    name = ""
    type_node_types = {
        "primitive_type", "named_type", "optional_type",
        "union_type", "intersection_type",
    }
    for child in node.children:
        if child.type in type_node_types:
            ptype = _format_type_node(child, src)
        elif child.type == "variable_name":
            name = _get_variable_name(child, src)
    return (name, ptype)


def _parse_promoted_parameter(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a ``property_promotion_parameter`` → (name, type)."""
    ptype = ""
    name = ""
    type_node_types = {
        "primitive_type", "named_type", "optional_type",
        "union_type", "intersection_type",
    }
    for child in node.children:
        if child.type in type_node_types:
            ptype = _format_type_node(child, src)
        elif child.type == "variable_name":
            name = _get_variable_name(child, src)
    return (name, ptype)


def _get_variable_name(node: Node, src: bytes) -> str:
    """Extract variable name without the ``$`` prefix."""
    name_node = _child_by_type(node, "name")
    if name_node is not None:
        return _node_text(name_node, src)
    # Fallback: strip $ from full text
    return _node_text(node, src).strip().lstrip("$")


def _extract_property_type(node: Node, src: bytes) -> str:
    """Extract type from a ``property_declaration``."""
    type_node_types = {
        "primitive_type", "named_type", "optional_type",
        "union_type", "intersection_type",
    }
    for child in node.children:
        if child.type in type_node_types:
            return _format_type_node(child, src)
    return ""


def _extract_property_name(node: Node, src: bytes) -> str:
    """Extract property name from a ``property_declaration``."""
    pe = _child_by_type(node, "property_element")
    if pe is not None:
        vn = _child_by_type(pe, "variable_name")
        if vn is not None:
            return _get_variable_name(vn, src)
    return ""


def _extract_bases(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``extends`` and ``implements`` base types from a class.

    Returns a tuple of all base type names.
    """
    bases: list[str] = []

    # extends clause
    bc = _child_by_type(node, "base_clause")
    if bc is not None:
        for child in bc.children:
            if child.type == "name":
                bases.append(_node_text(child, src))
            elif child.type == "qualified_name":
                bases.append(_node_text(child, src).replace("\\\\", "\\"))

    # implements clause
    ic = _child_by_type(node, "class_interface_clause")
    if ic is not None:
        for child in ic.children:
            if child.type == "name":
                bases.append(_node_text(child, src))
            elif child.type == "qualified_name":
                bases.append(_node_text(child, src).replace("\\\\", "\\"))

    return tuple(bases)


def _extract_trait_uses(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``use TraitA, TraitB;`` from inside a class body."""
    body = _child_by_type(node, "declaration_list")
    if body is None:
        return ()

    traits: list[str] = []
    for child in body.children:
        if child.type == "use_declaration":
            for name_node in child.children:
                if name_node.type == "name":
                    traits.append(_node_text(name_node, src))
                elif name_node.type == "qualified_name":
                    traits.append(
                        _node_text(name_node, src).replace("\\\\", "\\"),
                    )
    return tuple(traits)


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    r"""Build a mapping from short name → qualified path from use stmts.

    Examples:
        ``use App\Models\User;`` → ``{"User": "App\\Models\\User"}``
        ``use App\Models\User as UserModel;`` → ``{"UserModel": "..."}``

    Args:
        uses: List of raw ``use`` statement strings (includes ``use`` keyword).

    Returns:
        A dict mapping short names to their full qualified path.
    """
    import_map: dict[str, str] = {}
    for stmt in uses:
        cleaned = stmt.rstrip(";").strip()

        # Skip function/const use imports for the import map
        if cleaned.startswith(("use function ", "use const ")):
            continue

        if not cleaned.startswith("use "):
            continue
        path = cleaned[4:].strip()

        # Handle aliased imports: use X\Y as Alias
        if " as " in path:
            parts = path.split(" as ", maxsplit=1)
            fqn = parts[0].strip()
            alias = parts[1].strip()
            import_map[alias] = fqn
        else:
            # use X\Y\Z → short name = Z
            short = path.rsplit("\\", maxsplit=1)[-1]
            if short and short != "*":
                import_map[short] = path

    return import_map


# endregion: --- Import map builder


# ---------------------------------------------------------------------------
# region:    --- Self-methods collector
# ---------------------------------------------------------------------------


def _collect_self_methods(ast: FileAST) -> list[MethodNode]:
    """Collect all methods from impl blocks and free functions."""
    methods: list[MethodNode] = []

    for impl in ast.impl_blocks:
        for method in impl.methods:
            context = (
                f"impl:{impl.trait_type} for {impl.self_type}"
                if impl.trait_type
                else f"impl:{impl.self_type}"
            )
            methods.append(MethodNode(
                name=method.name,
                visibility=method.visibility,
                is_async=method.is_async,
                params=method.params,
                return_type=method.return_type,
                attributes=method.attributes,
                context=context,
                span=method.span,
            ))

    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=fn.is_async,
            params=fn.params,
            return_type=fn.return_type,
            attributes=fn.attributes,
            context="free",
            span=fn.span,
        )
        for fn in ast.functions
    )

    return methods


# endregion: --- Self-methods collector


# ---------------------------------------------------------------------------
# region:    --- PhpExtractor class
# ---------------------------------------------------------------------------


class PhpExtractor(ExtractorBase):
    """PHP language extractor using ``tree-sitter-php``.

    Parses ``.php`` source files and ``composer.json`` manifests
    into the normalized AST schema.
    """

    language_id: str = "php"
    file_extensions: list[str] = [".php"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_php as tsphp

        self._language = Language(tsphp.language_php())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.php`` file into the normalized AST.

        Args:
            file_path: Absolute path to the source file.
            source: Raw file contents as UTF-8 bytes.

        Returns:
            A populated :class:`FileAST`.
        """
        tree = self._parser.parse(source)
        root = tree.root_node
        ast = FileAST(file=str(file_path))

        # Walk top-level declarations
        self._walk_top_level(root, source, ast)

        # Post-pass 1: self_methods
        ast.self_methods = _collect_self_methods(ast)

        # Post-pass 2: import map + scoped method calls
        import_map = build_import_map(ast.uses)
        ast.imported_package_methods = _extract_scoped_method_calls(
            root, source, import_map,
        )

        # Post-pass 3: rationale comments
        ast.rationale_comments = extract_rationale_comments(root, source)

        # Post-pass 4: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(root, source, defined_names)

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``composer.json`` manifest.

        Args:
            manifest_path: Absolute path to the manifest file.

        Returns:
            A populated :class:`CrateModel`.
        """
        fname = manifest_path.name
        if fname == "composer.json":
            return self._parse_composer_json(manifest_path)
        return CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="php",
            manifest_path=str(manifest_path),
        )

    # ------------------------------------------------------------------
    # Manifest — composer.json
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_composer_json(manifest_path: Path) -> CrateModel:
        """Parse ``composer.json`` using stdlib ``json``."""
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="php",
            manifest_path=str(manifest_path),
        )

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", manifest_path, exc)
            return crate

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in %s: %s", manifest_path, exc)
            return crate

        crate.name = data.get("name", crate.name)
        crate.version = data.get("version", "")

        deps: list[CrateDependency] = []
        for section, is_dev in [("require", False), ("require-dev", True)]:
            for pkg, ver in data.get(section, {}).items():
                # Skip the PHP version constraint itself
                if pkg == "php" or pkg.startswith("ext-"):
                    continue
                deps.append(CrateDependency(
                    name=pkg,
                    version=ver,
                    is_dev=is_dev,
                ))

        # Augment declared ranges with resolved pins + transitive packages from
        # a sibling ``composer.lock``.
        from ast_intel.core.lockfile_parser import augment_with_lockfile

        crate.dependencies = augment_with_lockfile(manifest_path.parent, "php", deps)

        return crate

    # ------------------------------------------------------------------
    # Internal — top-level walking
    # ------------------------------------------------------------------

    def _walk_top_level(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Walk the tree, dispatching to handlers for each node."""
        for child in node.children:
            self._dispatch_node(child, src, ast)

    def _dispatch_node(  # noqa: C901
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Route a single AST node to the correct handler."""
        ntype = node.type

        if ntype == "namespace_definition":
            self._handle_namespace(node, src, ast)

        elif ntype == "namespace_use_declaration":
            self._handle_use(node, src, ast)

        elif ntype == "class_declaration":
            self._handle_class(node, src, ast)

        elif ntype == "interface_declaration":
            self._handle_interface(node, src, ast)

        elif ntype == "trait_declaration":
            self._handle_trait(node, src, ast)

        elif ntype == "enum_declaration":
            self._handle_enum(node, src, ast)

        elif ntype == "function_definition":
            self._handle_function(node, src, ast)

        elif ntype == "const_declaration":
            self._handle_const(node, src, ast)

        elif ntype == "expression_statement":
            # Skip expression statements at top level
            pass

        elif ntype == "ERROR":
            text = _node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]
            ast.errors.append(f"Parse error near: {text}")

    # ------------------------------------------------------------------
    # Internal — namespace and imports
    # ------------------------------------------------------------------

    def _handle_namespace(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``namespace`` declaration → module_path."""
        nn = _child_by_type(node, "namespace_name")
        if nn is not None:
            ast.module_path = _node_text(nn, src).replace("\\\\", "\\")

    def _handle_use(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``use`` declaration → uses list + TypeAliasNode."""
        # Check for group use: use X\Y\{A, B}
        group = _child_by_type(node, "namespace_use_group")
        if group is not None:
            self._handle_group_use(node, group, src, ast)
            return

        # Single use clause
        for clause in _children_by_type(node, "namespace_use_clause"):
            raw_parts: list[str] = ["use"]
            # Check for function/const use
            func_kw = _child_by_type(clause, "function")
            const_kw = _child_by_type(clause, "const")
            if func_kw is not None:
                raw_parts.append("function")
            elif const_kw is not None:
                raw_parts.append("const")

            # Get the qualified name or simple name
            qn = _child_by_type(clause, "qualified_name")
            name_node = _child_by_type(clause, "name") if qn is None else None

            fqn = ""
            if qn is not None:
                fqn = _node_text(qn, src)
            elif name_node is not None:
                fqn = _node_text(name_node, src)
            raw_parts.append(fqn)

            # Check for alias: as Alias
            alias_node = None
            children = list(clause.children)
            for i, child in enumerate(children):
                if child.type == "as" and i + 1 < len(children):
                    alias_node = children[i + 1]
                    break

            if alias_node is not None:
                alias = _node_text(alias_node, src)
                raw_parts.extend(["as", alias])
                ast.uses.append(" ".join(raw_parts))
                # Also add TypeAliasNode
                ast.type_aliases.append(TypeAliasNode(
                    name=alias,
                    aliased_to=fqn,
                    visibility=Visibility.CRATE,
                    span=span_from_node(node),
                ))
            else:
                ast.uses.append(" ".join(raw_parts))

    def _handle_group_use(
        self,
        node: Node,
        group: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        r"""Handle group use: ``use X\Y\{A, B as C}``."""
        # Get the prefix from namespace_name before the group
        prefix = ""
        nn = _child_by_type(node, "namespace_name")
        if nn is not None:
            prefix = _node_text(nn, src)

        for clause in _children_by_type(group, "namespace_use_clause"):
            name_node = _child_by_type(clause, "name")
            if name_node is None:
                continue

            short = _node_text(name_node, src)
            fqn = f"{prefix}\\{short}" if prefix else short

            # Check for alias
            alias_node = None
            children = list(clause.children)
            for i, child in enumerate(children):
                if child.type == "as" and i + 1 < len(children):
                    alias_node = children[i + 1]
                    break

            if alias_node is not None:
                alias = _node_text(alias_node, src)
                ast.uses.append(f"use {fqn} as {alias}")
                ast.type_aliases.append(TypeAliasNode(
                    name=alias,
                    aliased_to=fqn,
                    visibility=Visibility.CRATE,
                    span=span_from_node(node),
                ))
            else:
                ast.uses.append(f"use {fqn}")

    # ------------------------------------------------------------------
    # Internal — class declarations
    # ------------------------------------------------------------------

    def _handle_class(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``class_declaration`` → StructNode or TraitNode.

        Abstract classes → TraitNode. Concrete classes → StructNode.
        """
        info = self._parse_type_info(node, src)

        if info.is_abstract:
            self._extract_abstract_class_as_trait(node, src, ast, info)
        else:
            self._extract_class_as_struct(node, src, ast, info)

    def _extract_class_as_struct(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract a concrete class → StructNode + ImplBlockNode(s)."""
        fields = self._extract_class_fields(node, src)
        # Include promoted constructor params as fields
        promoted_fields = self._extract_promoted_fields(node, src)

        all_fields = fields + promoted_fields

        attrs: list[str] = list(info.attributes)
        if _has_modifier(node, "final_modifier"):
            attrs.append("final")
        if _has_modifier(node, "readonly_modifier"):
            attrs.append("readonly")

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=tuple(all_fields),
            attributes=tuple(attrs),
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Constants
        self._extract_class_constants(node, src, ast)

        # Methods
        methods = self._extract_class_methods(node, src)

        # Trait uses
        trait_uses = _extract_trait_uses(node, src)

        # Build ImplBlockNodes
        all_bases = list(info.bases) + list(trait_uses)
        if all_bases:
            for base in all_bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    generics=info.generics,
                    methods=methods,
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type="",
                generics=info.generics,
                methods=methods,
                span=span_from_node(node),
            ))

    def _extract_abstract_class_as_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract an ``abstract class`` → TraitNode."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "method_declaration":
                    item = self._extract_trait_method_item(child, src)
                    if item is not None:
                        items.append(item)

        ast.traits.append(TraitNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            super_traits=info.bases,
            items=tuple(items),
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — interface declarations
    # ------------------------------------------------------------------

    def _handle_interface(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``interface_declaration`` → TraitNode."""
        name = _get_identifier(node, src)
        doc = _extract_doc_comment(node, src)
        generics = _extract_generics_from_phpdoc(node, src)
        attributes = _extract_attributes(node, src)

        # Interface extends
        super_traits = self._extract_interface_extends(node, src)

        items = self._extract_interface_items(node, src)

        # Also extract interface constants
        self._extract_interface_constants(node, src, ast)

        ast.traits.append(TraitNode(
            name=name,
            visibility=Visibility.PUBLIC,
            generics=generics,
            super_traits=super_traits,
            items=items,
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

    def _extract_interface_extends(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[str, ...]:
        """Extract ``extends A, B`` from an interface."""
        bc = _child_by_type(node, "base_clause")
        if bc is None:
            return ()
        return tuple(
            _node_text(child, src)
            for child in bc.children
            if child.type in ("name", "qualified_name")
        )

    def _extract_interface_items(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[TraitItemNode, ...]:
        """Extract method signatures from an interface body."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return ()

        for child in body.children:
            if child.type == "method_declaration":
                item = self._extract_trait_method_item(child, src)
                if item is not None:
                    items.append(item)
        return tuple(items)

    def _extract_interface_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract constants from an interface body."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return

        for child in body.children:
            if child.type == "const_declaration":
                self._extract_const_node(child, src, ast)

    # ------------------------------------------------------------------
    # Internal — trait declarations
    # ------------------------------------------------------------------

    def _handle_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``trait_declaration`` → TraitNode with ``trait`` attr."""
        name = _get_identifier(node, src)
        doc = _extract_doc_comment(node, src)
        attributes = (*_extract_attributes(node, src), "trait")

        items: list[TraitItemNode] = []
        body = _child_by_type(node, "declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "method_declaration":
                    item = self._extract_trait_method_item(child, src)
                    if item is not None:
                        items.append(item)

        ast.traits.append(TraitNode(
            name=name,
            visibility=Visibility.PUBLIC,
            generics="",
            super_traits=(),
            items=tuple(items),
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — enum declarations
    # ------------------------------------------------------------------

    def _handle_enum(  # noqa: C901, PLR0912
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``enum_declaration`` → EnumNode."""
        name = _get_identifier(node, src)
        doc = _extract_doc_comment(node, src)
        attributes = _extract_attributes(node, src)

        # Backed enum type (: string, : int)
        backed_type = ""
        found_colon = False
        for child in node.children:
            if child.type == ":":
                found_colon = True
                continue
            if found_colon and child.type == "primitive_type":
                backed_type = _node_text(child, src).strip()
                break
            if found_colon and child.type != "primitive_type":
                break

        variants: list[EnumVariantNode] = []
        body = _child_by_type(node, "enum_declaration_list")
        if body is not None:
            for child in body.children:
                if child.type == "enum_case":
                    vname = _get_identifier(child, src)
                    if vname:
                        variants.append(EnumVariantNode(
                            name=vname,
                            kind=EnumVariantKind.UNIT,
                        ))

        enum_attrs = list(attributes)
        if backed_type:
            enum_attrs.append(f"backed:{backed_type}")

        ast.enums.append(EnumNode(
            name=name,
            visibility=Visibility.PUBLIC,
            generics="",
            variants=tuple(variants),
            attributes=tuple(enum_attrs),
            doc=doc,
            span=span_from_node(node),
        ))

        # Enum methods → ImplBlockNode
        methods = self._extract_enum_methods(node, src)

        # Enum implements
        ic = _child_by_type(node, "class_interface_clause")
        ifaces: list[str] = []
        if ic is not None:
            ifaces.extend(
                _node_text(child, src)
                for child in ic.children
                if child.type in ("name", "qualified_name")
            )

        if ifaces:
            for iface in ifaces:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=name,
                    trait_type=iface,
                    generics="",
                    methods=methods,
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=name,
                trait_type="",
                generics="",
                methods=methods,
                span=span_from_node(node),
            ))

    def _extract_enum_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from ``enum_declaration_list``."""
        body = _child_by_type(node, "enum_declaration_list")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
        return tuple(methods)

    # ------------------------------------------------------------------
    # Internal — function declarations
    # ------------------------------------------------------------------

    def _handle_function(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``function_definition`` → FunctionNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        doc = _extract_doc_comment(node, src)
        params = _extract_parameters(node, src)
        rtype = _extract_return_type(node, src)
        attributes = _extract_attributes(node, src)

        ast.functions.append(FunctionNode(
            name=name,
            visibility=Visibility.PUBLIC,
            is_async=False,
            params=params,
            return_type=rtype,
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — constant declarations
    # ------------------------------------------------------------------

    def _handle_const(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``const_declaration`` → ConstantNode."""
        self._extract_const_node(node, src, ast)

    def _extract_const_node(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract ConstantNode from a ``const_declaration``."""
        vis = _get_visibility_with_src(node, src)

        for child in node.children:
            if child.type == "const_element":
                name_node = _child_by_type(child, "name")
                if name_node is not None:
                    name = _node_text(name_node, src)
                    raw = _node_text(child, src).strip()
                    if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
                        raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."
                    ast.constants.append(ConstantNode(
                        name=name,
                        visibility=vis,
                        raw=raw,
                        span=span_from_node(child),
                    ))

    # ------------------------------------------------------------------
    # Internal — type info extraction
    # ------------------------------------------------------------------

    def _parse_type_info(self, node: Node, src: bytes) -> _TypeInfo:
        """Extract shared metadata from a type declaration."""
        name = _get_identifier(node, src)
        vis = _get_visibility_with_src(node, src)
        generics = _extract_generics_from_phpdoc(node, src)
        attributes = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)
        is_abstract = _has_modifier(node, "abstract_modifier")

        bases = _extract_bases(node, src)

        # Default visibility for class is CRATE (namespace level)
        if _child_by_type(node, "visibility_modifier") is None:
            vis = Visibility.CRATE

        return _TypeInfo(
            name=name,
            bases=bases,
            generics=generics,
            attributes=attributes,
            doc=doc,
            vis=vis,
            is_abstract=is_abstract,
        )

    # ------------------------------------------------------------------
    # Internal — class member extraction
    # ------------------------------------------------------------------

    def _extract_class_fields(
        self,
        node: Node,
        src: bytes,
    ) -> list[FieldNode]:
        """Extract non-static properties from a class body."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return []

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type == "property_declaration":
                if _has_modifier(child, "static_modifier"):
                    continue
                pname = _extract_property_name(child, src)
                ptype = _extract_property_type(child, src)
                vis = _get_visibility_with_src(child, src)
                if pname:
                    fields.append(FieldNode(
                        name=pname,
                        type=ptype,
                        visibility=vis,
                    ))
        return fields

    def _extract_promoted_fields(
        self,
        node: Node,
        src: bytes,
    ) -> list[FieldNode]:
        """Extract promoted constructor parameters as FieldNodes."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return []

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                name = _get_identifier(child, src)
                if name == "__construct":
                    fp = _child_by_type(child, "formal_parameters")
                    if fp is None:
                        continue
                    for param in fp.children:
                        if param.type == "property_promotion_parameter":
                            pname, ptype = _parse_promoted_parameter(
                                param, src,
                            )
                            vis = _get_visibility_with_src(param, src)
                            if pname:
                                fields.append(FieldNode(
                                    name=pname,
                                    type=ptype,
                                    visibility=vis,
                                ))
        return fields

    def _extract_class_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract class constants and static properties as ConstantNode."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return

        for child in body.children:
            if child.type == "const_declaration":
                self._extract_const_node(child, src, ast)
            elif (
                child.type == "property_declaration"
                and _has_modifier(child, "static_modifier")
            ):
                pname = _extract_property_name(child, src)
                vis = _get_visibility_with_src(child, src)
                if pname:
                    raw = _node_text(child, src).strip()
                    if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
                        raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."
                    ast.constants.append(ConstantNode(
                        name=pname,
                        visibility=vis,
                        raw=raw,
                        span=span_from_node(child),
                    ))

    def _extract_class_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods (including constructors) from a class body."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
        return tuple(methods)

    def _extract_method(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Extract a single method declaration → MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None
        vis = _get_visibility_with_src(node, src)
        rtype = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        attrs = _extract_attributes(node, src)
        is_static = _has_modifier(node, "static_modifier")

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=False,
            is_static=is_static,
            params=params,
            return_type=rtype,
            attributes=attrs,
            span=span_from_node(node),
        )

    def _extract_trait_method_item(
        self,
        node: Node,
        src: bytes,
    ) -> TraitItemNode | None:
        """Extract a method from a trait/interface/abstract class."""
        name = _get_identifier(node, src)
        if not name:
            return None

        rtype = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        doc = _extract_doc_comment(node, src)

        is_abstract = _has_modifier(node, "abstract_modifier")
        # If it has no body (ends with ;), it's required
        has_body = _child_by_type(node, "compound_statement") is not None

        if is_abstract or not has_body:
            kind = TraitItemKind.REQUIRED_METHOD
        else:
            kind = TraitItemKind.DEFAULT_METHOD

        return TraitItemNode(
            kind=kind,
            name=name,
            is_async=False,
            params=params,
            return_type=rtype,
            doc=doc,
            span=span_from_node(node),
        )


# endregion: --- PhpExtractor class


# ---------------------------------------------------------------------------
# region:    --- Post-pass: imported_package_methods
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Detect ``Class::method()`` / ``$obj->method()`` calls.

    Uses the import map to resolve class references to FQN.
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
    """Recursively walk for scoped call nodes."""
    if node.type == "scoped_call_expression":
        _resolve_scoped_call(node, src, import_map, result)

    for child in node.children:
        _walk_for_calls(child, src, import_map, result)


def _resolve_scoped_call(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Resolve ``ClassName::methodName()`` into imported_package_methods."""
    children = [
        c for c in node.children
        if c.type not in ("::", "arguments", "(", ")")
    ]
    if len(children) < 2:  # noqa: PLR2004
        return

    scope_node = children[0]
    method_node = children[-1]

    scope_text = _node_text(scope_node, src)
    method_name = _node_text(method_node, src)

    if not scope_text or not method_name:
        return

    # Resolve via import map
    if scope_text in import_map:
        qualified = import_map[scope_text]
        result.setdefault(qualified, [])
        if method_name not in result[qualified]:
            result[qualified].append(method_name)


# endregion: --- Post-pass: imported_package_methods
