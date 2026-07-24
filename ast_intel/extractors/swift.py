"""Swift language extractor — tree-sitter based AST extraction.

Parses ``.swift`` files using ``tree-sitter-swift`` and maps
Swift-specific constructs to the normalized AST schema.

Swift → Normalized mapping:
    ================================  =================
    Swift Concept                     Normalized To
    ================================  =================
    ``class``                         StructNode
    ``struct``                        StructNode (attr)
    ``enum``                          EnumNode
    ``protocol``                      TraitNode
    ``extension T: P``                ImplBlockNode
    ``extension T``                   ImplBlockNode
    ``actor``                         StructNode (attr)
    ``func`` (top-level)              FunctionNode
    ``func`` (in type)                MethodNode
    ``import X``                      uses (raw str)
    ``let/var`` (static or global)    ConstantNode
    ``let/var`` (property)            FieldNode
    ``typealias``                     TypeAliasNode
    ``@Attribute``                    attributes
    ``async func``                    is_async=True
    ================================  =================

Also parses ``Package.swift`` manifests via :meth:`parse_manifest`.
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

__all__: list[str] = ["SwiftExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60


# ---------------------------------------------------------------------------
# region:    --- Helper NamedTuple
# ---------------------------------------------------------------------------


class _TypeInfo(NamedTuple):
    """Parsed metadata for a Swift type declaration."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility


# endregion: --- Helper NamedTuple


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, src: bytes) -> str:
    """Decode tree-sitter node text as UTF-8."""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first child with the given type, or ``None``."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _children_by_type(node: Node, type_name: str) -> list[Node]:
    """Return all children with the given type."""
    return [c for c in node.children if c.type == type_name]


def _get_identifier(node: Node, src: bytes) -> str:
    """Extract the name identifier from a declaration node.

    For ``class_declaration``, the name is in a ``type_identifier`` child.
    For ``function_declaration``, the name is in a ``simple_identifier``.
    For ``typealias_declaration``, the name is in a ``type_identifier``.
    """
    # type_identifier is used for class/struct/enum/protocol names
    ti = _child_by_type(node, "type_identifier")
    if ti is not None:
        return _node_text(ti, src).strip()
    # simple_identifier is used for function names
    si = _child_by_type(node, "simple_identifier")
    if si is not None:
        return _node_text(si, src).strip()
    return ""


# Mapping from Swift access-control keywords to Visibility.
_VISIBILITY_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "open": Visibility.PUBLIC,
    "internal": Visibility.CRATE,
    "fileprivate": Visibility.CRATE,
    "private": Visibility.PRIVATE,
}


def _get_visibility(node: Node, src: bytes) -> Visibility:
    """Parse Swift visibility from ``modifiers`` → ``visibility_modifier``.

    Swift default visibility is ``internal`` → ``CRATE``.
    """
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return Visibility.CRATE

    vm = _child_by_type(mods, "visibility_modifier")
    if vm is None:
        return Visibility.CRATE

    text = _node_text(vm, src).strip()
    return _VISIBILITY_MAP.get(text, Visibility.CRATE)


def _extract_attributes(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@Attribute`` names from a declaration's ``modifiers``."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        # Attributes can also be direct children of function_declaration
        return tuple(
            _parse_attribute_name(child, src)
            for child in node.children
            if child.type == "attribute"
        )

    return tuple(
        _parse_attribute_name(child, src)
        for child in mods.children
        if child.type == "attribute"
    )


def _parse_attribute_name(attr_node: Node, src: bytes) -> str:
    """Extract the attribute name from an ``attribute`` node.

    Returns e.g. ``"@available"`` or ``"@MainActor"``.
    """
    ut = _child_by_type(attr_node, "user_type")
    if ut is not None:
        ti = _child_by_type(ut, "type_identifier")
        if ti is not None:
            return f"@{_node_text(ti, src).strip()}"
    return f"@{_node_text(attr_node, src).strip().lstrip('@')}"


def _extract_modifiers(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract modifier keywords (static, final, class, mutating, etc.)."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()

    modifier_types = {
        "property_modifier", "mutation_modifier",
        "inheritance_modifier", "ownership_modifier",
    }

    keywords: list[str] = [
        _node_text(child, src).strip()
        for child in mods.children
        if child.type in modifier_types
    ]
    return tuple(keywords)


def _extract_generics(node: Node, src: bytes) -> str:
    """Extract generic type parameters like ``<T, U: Protocol>``."""
    tp = _child_by_type(node, "type_parameters")
    if tp is None:
        return ""
    return _node_text(tp, src).strip()


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract the return type after ``->``."""
    found_arrow = False
    for child in node.children:
        if child.type == "->":
            found_arrow = True
            continue
        if found_arrow and child.type not in ("throws", "async"):
            return _node_text(child, src).strip()
    return ""


def _extract_inheritance(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract base types from ``: Type1, Type2`` syntax.

    In tree-sitter-swift, inheritance appears as ``:`` followed by
    ``inheritance_specifier`` children of the declaration node.
    """
    specs = _children_by_type(node, "inheritance_specifier")
    bases: list[str] = []
    for spec in specs:
        ut = _child_by_type(spec, "user_type")
        if ut is not None:
            ti = _child_by_type(ut, "type_identifier")
            if ti is not None:
                bases.append(_node_text(ti, src).strip())
    return tuple(bases)


def _extract_doc_comment(node: Node, src: bytes) -> str:  # noqa: C901, PLR0912
    """Extract ``///`` or ``/** ... */`` doc comments preceding a node.

    Walks backwards through preceding siblings collecting comment nodes
    that are documentation comments.
    """
    parent = node.parent
    if parent is None:
        return ""

    idx = -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            idx = i
            break
    if idx <= 0:
        return ""

    doc_lines: list[str] = []
    for j in range(idx - 1, -1, -1):
        sib = parent.children[j]
        if sib.type == "comment":
            text = _node_text(sib, src).strip()
            if text.startswith("///"):
                # Strip the /// prefix
                line = text[3:].strip()
                doc_lines.append(line)
            else:
                break
        elif sib.type == "multiline_comment":
            text = _node_text(sib, src).strip()
            if text.startswith("/**"):
                # Parse block doc comment
                inner = text[3:].removesuffix("*/")
                lines = []
                for raw_line in inner.split("\n"):
                    stripped = raw_line.strip().lstrip("* ").strip()
                    if stripped:
                        lines.append(stripped)
                doc_lines.extend(reversed(lines))
            break
        else:
            break

    if not doc_lines:
        return ""
    doc_lines.reverse()
    return " ".join(doc_lines)


def _is_async(node: Node) -> bool:
    """Check if a function declaration has the ``async`` keyword."""
    return any(child.type == "async" for child in node.children)


def _is_static(node: Node, src: bytes) -> bool:
    """Check if a declaration has ``static`` or ``class`` modifier."""
    mods = _extract_modifiers(node, src)
    return "static" in mods or "class" in mods


def _has_body(node: Node) -> bool:
    """Check if a function has a ``function_body`` child."""
    return _child_by_type(node, "function_body") is not None


def _get_declaration_kind(node: Node) -> str:
    """Determine the kind of a ``class_declaration`` node.

    In tree-sitter-swift, ``class``, ``struct``, ``enum``, ``actor``,
    and ``extension`` all parse as ``class_declaration``. The actual
    keyword (``class``, ``struct``, etc.) appears as a child node.
    """
    for child in node.children:
        if child.type in ("class", "struct", "enum", "actor", "extension"):
            return child.type
    return "class"


def _extract_parameters(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract function/init parameters.

    Each ``parameter`` node has one or two ``simple_identifier`` children
    (external name, internal name) plus a type after ``:``.
    """
    params: list[ParamNode] = []
    for child in node.children:
        if child.type == "parameter":
            identifiers = _children_by_type(child, "simple_identifier")
            # Last identifier is the internal name; if two, first is
            # external label
            name = (
                _node_text(identifiers[-1], src).strip()
                if identifiers
                else ""
            )

            # Type annotation
            ta = _child_by_type(child, "type_annotation")
            ptype = ""
            if ta is not None:
                # Type is everything in the type_annotation after ":"
                for tc in ta.children:
                    if tc.type != ":":
                        ptype = _node_text(tc, src).strip()
                        break

            if name and name != "_":
                params.append(ParamNode(name=name, type=ptype))
    return tuple(params)


def _get_extended_type(node: Node, src: bytes) -> str:
    """Get the type being extended in an ``extension`` declaration.

    In tree-sitter-swift, the extended type appears as a ``user_type``
    child of the ``class_declaration`` (for extensions).
    """
    ut = _child_by_type(node, "user_type")
    if ut is not None:
        ti = _child_by_type(ut, "type_identifier")
        if ti is not None:
            return _node_text(ti, src).strip()
    return ""


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Self-methods collector
# ---------------------------------------------------------------------------


def _collect_self_methods(ast: FileAST) -> list[MethodNode]:
    """Collect all methods from all impl_blocks."""
    methods: list[MethodNode] = []
    for impl in ast.impl_blocks:
        methods.extend(impl.methods)
    return methods


# endregion: --- Self-methods collector


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    """Build a mapping from simple names to fully qualified paths.

    ``import Foundation`` → ``{"Foundation": "Foundation"}``
    ``import UIKit.UIView`` → ``{"UIView": "UIKit.UIView"}``
    """
    import_map: dict[str, str] = {}
    for stmt in uses:
        cleaned = stmt.strip()
        if not cleaned.startswith("import "):
            continue
        path = cleaned[len("import "):].strip()
        # Handle "import class Foundation.NSObject" etc.
        parts = path.split()
        if len(parts) > 1:
            path = parts[-1]
        short = path.rsplit(".", maxsplit=1)[-1]
        if short and short != "_":
            import_map[short] = path
    return import_map


# endregion: --- Import map builder


# ---------------------------------------------------------------------------
# region:    --- SwiftExtractor class
# ---------------------------------------------------------------------------


class SwiftExtractor(ExtractorBase):
    """Swift language extractor using ``tree-sitter-swift``.

    Parses ``.swift`` source files and ``Package.swift``
    manifests into the normalized AST schema.
    """

    language_id: str = "swift"
    file_extensions: list[str] = [".swift"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_swift as tsswift

        self._language = Language(tsswift.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.swift`` file into the normalized AST."""
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
        """Parse a ``Package.swift`` manifest."""
        fname = manifest_path.name
        if fname == "Package.swift":
            return self._parse_package_swift(manifest_path)
        return CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="swift",
            manifest_path=str(manifest_path),
        )

    # ------------------------------------------------------------------
    # Manifest — Package.swift
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_package_swift(manifest_path: Path) -> CrateModel:
        """Parse ``Package.swift`` with regex."""
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="swift",
            manifest_path=str(manifest_path),
        )

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", manifest_path, exc)
            return crate

        # Extract package name
        name_m = re.search(r'name:\s*"([^"]+)"', content)
        if name_m:
            crate.name = name_m.group(1)

        # Extract dependencies
        # .package(url: "https://...repo.git", from: "1.0.0")
        # .package(url: "https://...repo", .upToNextMajor(from: "2.0.0"))
        dep_re = re.compile(
            r'\.package\s*\(\s*url:\s*"([^"]+)"'
            r'.*?(?:from:\s*"([^"]+)"'
            r'|\.upToNextMajor\s*\(\s*from:\s*"([^"]+)"\s*\))',
            re.DOTALL,
        )
        deps: list[CrateDependency] = []
        for m in dep_re.finditer(content):
            url = m.group(1)
            version = m.group(2) or m.group(3) or ""
            # Extract package name from URL
            dep_name = url.rstrip("/").rsplit("/", maxsplit=1)[-1]
            dep_name = dep_name.removesuffix(".git")
            deps.append(CrateDependency(
                name=dep_name,
                version=version,
            ))

        crate.dependencies = deps
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

        if ntype == "import_declaration":
            self._handle_import(node, src, ast)

        elif ntype == "class_declaration":
            # In tree-sitter-swift, class/struct/enum/actor/extension
            # all use class_declaration. Disambiguate by keyword child.
            kind = _get_declaration_kind(node)
            if kind == "struct":
                self._handle_struct_declaration(node, src, ast)
            elif kind == "enum":
                self._handle_enum_declaration(node, src, ast)
            elif kind == "extension":
                self._handle_extension_declaration(node, src, ast)
            elif kind == "actor":
                self._handle_actor_declaration(node, src, ast)
            else:
                self._handle_class_declaration(node, src, ast)

        elif ntype == "protocol_declaration":
            self._handle_protocol_declaration(node, src, ast)

        elif ntype == "function_declaration":
            self._handle_top_level_function(node, src, ast)

        elif ntype == "property_declaration":
            self._handle_top_level_property(node, src, ast)

        elif ntype == "typealias_declaration":
            self._handle_typealias(node, src, ast)

        elif ntype == "ERROR":
            text = _node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]
            ast.errors.append(f"Parse error near: {text}")

    # ------------------------------------------------------------------
    # Internal — imports
    # ------------------------------------------------------------------

    def _handle_import(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``import_declaration`` → uses."""
        text = _node_text(node, src).strip()
        ast.uses.append(text)

    # ------------------------------------------------------------------
    # Internal — class declarations
    # ------------------------------------------------------------------

    def _handle_class_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``class`` → StructNode + ImplBlockNode(s)."""
        info = self._parse_type_info(node, src)

        fields = self._extract_body_fields(node, src)

        attrs: list[str] = list(info.attributes)
        # Add modifier keywords
        mods = _extract_modifiers(node, src)
        if "final" in mods:
            attrs.append("final")
        if "open" in _get_visibility_text(node, src):
            attrs.append("open")

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=tuple(fields),
            attributes=tuple(attrs),
            doc=info.doc,
            span=span_from_node(node),
        ))

        methods = self._extract_body_methods(node, src)
        self._extract_body_constants(node, src, ast)
        self._walk_body_declarations(node, src, ast)

        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=tuple(methods),
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type=None,
                methods=tuple(methods),
                span=span_from_node(node),
            ))

    # ------------------------------------------------------------------
    # Internal — struct declarations
    # ------------------------------------------------------------------

    def _handle_struct_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``struct`` → StructNode with ``struct`` attribute."""
        info = self._parse_type_info(node, src)

        fields = self._extract_body_fields(node, src)

        attrs: list[str] = list(info.attributes)
        attrs.append("struct")

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=tuple(fields),
            attributes=tuple(attrs),
            doc=info.doc,
            span=span_from_node(node),
        ))

        methods = self._extract_body_methods(node, src)
        self._extract_body_constants(node, src, ast)
        self._walk_body_declarations(node, src, ast)

        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=tuple(methods),
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type=None,
                methods=tuple(methods),
                span=span_from_node(node),
            ))

    # ------------------------------------------------------------------
    # Internal — actor declarations
    # ------------------------------------------------------------------

    def _handle_actor_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``actor`` → StructNode with ``actor`` attribute."""
        info = self._parse_type_info(node, src)

        fields = self._extract_body_fields(node, src)

        attrs = (*info.attributes, "actor")

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=tuple(fields),
            attributes=attrs,
            doc=info.doc,
            span=span_from_node(node),
        ))

        methods = self._extract_body_methods(node, src)
        self._extract_body_constants(node, src, ast)
        self._walk_body_declarations(node, src, ast)

        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=tuple(methods),
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type=None,
                methods=tuple(methods),
                span=span_from_node(node),
            ))

    # ------------------------------------------------------------------
    # Internal — protocol declarations
    # ------------------------------------------------------------------

    def _handle_protocol_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``protocol`` → TraitNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _extract_generics(node, src)
        doc = _extract_doc_comment(node, src)
        annotations = _extract_attributes(node, src)
        bases = _extract_inheritance(node, src)

        items = self._extract_protocol_items(node, src)

        ast.traits.append(TraitNode(
            name=name,
            visibility=vis,
            generics=generics,
            super_traits=bases,
            items=tuple(items),
            attributes=annotations,
            doc=doc,
            span=span_from_node(node),
        ))

    def _extract_protocol_items(
        self,
        node: Node,
        src: bytes,
    ) -> list[TraitItemNode]:
        """Extract protocol items from ``protocol_body``."""
        body = _child_by_type(node, "protocol_body")
        if body is None:
            return []

        items: list[TraitItemNode] = []
        for child in body.children:
            if child.type == "protocol_function_declaration":
                name = _get_identifier(child, src)
                return_type = _extract_return_type(child, src)
                params = _extract_parameters(child, src)
                items.append(TraitItemNode(
                    name=name,
                    kind=TraitItemKind.REQUIRED_METHOD,
                    is_async=_is_async(child),
                    params=params,
                    return_type=return_type,
                ))
            elif child.type == "associatedtype_declaration":
                name = _get_identifier(child, src)
                items.append(TraitItemNode(
                    name=name,
                    kind=TraitItemKind.ASSOCIATED_TYPE,
                    is_async=False,
                    params=(),
                    return_type="",
                ))
            elif child.type == "protocol_property_declaration":
                name = ""
                for gc in child.children:
                    if gc.type == "pattern":
                        si = _child_by_type(gc, "simple_identifier")
                        if si is not None:
                            name = _node_text(si, src).strip()
                            break
                if name:
                    items.append(TraitItemNode(
                        name=name,
                        kind=TraitItemKind.REQUIRED_METHOD,
                        is_async=False,
                        params=(),
                        return_type="",
                    ))
        return items

    # ------------------------------------------------------------------
    # Internal — enum declarations
    # ------------------------------------------------------------------

    def _handle_enum_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``enum`` → EnumNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _extract_generics(node, src)
        doc = _extract_doc_comment(node, src)
        annotations = _extract_attributes(node, src)
        bases = _extract_inheritance(node, src)

        variants = self._extract_enum_cases(node, src)
        methods = self._extract_body_methods(node, src, body_type="enum_class_body")

        ast.enums.append(EnumNode(
            name=name,
            visibility=vis,
            generics=generics,
            variants=tuple(variants),
            attributes=annotations,
            doc=doc,
            span=span_from_node(node),
        ))

        if bases:
            for base in bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=name,
                    trait_type=base,
                    methods=tuple(methods),
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=name,
                trait_type=None,
                methods=tuple(methods),
                span=span_from_node(node),
            ))

    def _extract_enum_cases(
        self,
        node: Node,
        src: bytes,
    ) -> list[EnumVariantNode]:
        """Extract ``enum_entry`` children from ``enum_class_body``."""
        body = _child_by_type(node, "enum_class_body")
        if body is None:
            return []

        variants: list[EnumVariantNode] = []
        for child in body.children:
            if child.type != "enum_entry":
                continue

            # Each enum_entry can have multiple case names
            # (comma-separated simple_identifier children)
            identifiers = _children_by_type(child, "simple_identifier")
            has_associated = _child_by_type(child, "enum_type_parameters") is not None

            for ident in identifiers:
                vname = _node_text(ident, src).strip()
                if vname:
                    kind = (
                        EnumVariantKind.TUPLE
                        if has_associated
                        else EnumVariantKind.UNIT
                    )
                    variants.append(EnumVariantNode(
                        name=vname,
                        kind=kind,
                    ))
        return variants

    # ------------------------------------------------------------------
    # Internal — extension declarations
    # ------------------------------------------------------------------

    def _handle_extension_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``extension`` → ImplBlockNode(s)."""
        self_type = _get_extended_type(node, src)
        bases = _extract_inheritance(node, src)
        methods = self._extract_body_methods(node, src)
        self._extract_body_constants(node, src, ast)
        self._walk_body_declarations(node, src, ast)

        if bases:
            for base in bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=self_type,
                    trait_type=base,
                    methods=tuple(methods),
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=self_type,
                trait_type=None,
                methods=tuple(methods),
                span=span_from_node(node),
            ))

    # ------------------------------------------------------------------
    # Internal — top-level functions
    # ------------------------------------------------------------------

    def _handle_top_level_function(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``function_declaration`` → FunctionNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        vis = _get_visibility(node, src)
        return_type = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        doc = _extract_doc_comment(node, src)

        ast.functions.append(FunctionNode(
            name=name,
            visibility=vis,
            is_async=_is_async(node),
            params=params,
            return_type=return_type,
            doc=doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — top-level properties
    # ------------------------------------------------------------------

    def _handle_top_level_property(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``property_declaration`` → ConstantNode."""
        name = self._get_property_name(node, src)
        if not name:
            return

        raw = _node_text(node, src).strip()
        if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
            raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."

        ast.constants.append(ConstantNode(
            name=name,
            raw=raw,
            visibility=_get_visibility(node, src),
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — typealias
    # ------------------------------------------------------------------

    def _handle_typealias(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``typealias_declaration`` → TypeAliasNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        # The target type is everything after "="
        target = ""
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq:
                target = _node_text(child, src).strip()
                break

        ast.type_aliases.append(TypeAliasNode(
            name=name,
            aliased_to=target,
            visibility=_get_visibility(node, src),
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — body extraction helpers
    # ------------------------------------------------------------------

    def _parse_type_info(
        self,
        node: Node,
        src: bytes,
    ) -> _TypeInfo:
        """Extract shared metadata from a type declaration."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _extract_generics(node, src)
        annotations = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)
        bases = _extract_inheritance(node, src)

        return _TypeInfo(
            name=name,
            bases=bases,
            generics=generics,
            attributes=annotations,
            doc=doc,
            vis=vis,
        )

    def _extract_body_fields(
        self,
        node: Node,
        src: bytes,
        body_type: str = "class_body",
    ) -> list[FieldNode]:
        """Extract property declarations from a type body as fields."""
        body = _child_by_type(node, body_type)
        if body is None:
            return []

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type != "property_declaration":
                continue
            if _is_static(child, src):
                continue

            name = self._get_property_name(child, src)
            if not name:
                continue

            ptype = self._get_property_type(child, src)
            fields.append(FieldNode(name=name, type=ptype))
        return fields

    def _extract_body_methods(
        self,
        node: Node,
        src: bytes,
        body_type: str = "class_body",
    ) -> list[MethodNode]:
        """Extract function/init declarations from a type body."""
        body = _child_by_type(node, body_type)
        if body is None:
            return []

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "function_declaration":
                method = self._parse_method(child, src)
                if method is not None:
                    methods.append(method)
            elif child.type == "init_declaration":
                method = self._parse_init(child, src)
                if method is not None:
                    methods.append(method)
        return methods

    def _extract_body_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        body_type: str = "class_body",
    ) -> None:
        """Extract static properties as constants from a type body."""
        body = _child_by_type(node, body_type)
        if body is None:
            return

        for child in body.children:
            if child.type != "property_declaration":
                continue
            if not _is_static(child, src):
                continue

            name = self._get_property_name(child, src)
            if not name:
                continue

            raw = _node_text(child, src).strip()
            if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
                raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."

            ast.constants.append(ConstantNode(
                name=name,
                raw=raw,
                visibility=_get_visibility(child, src),
                span=span_from_node(child),
            ))

    def _parse_method(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Parse a ``function_declaration`` into a MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None

        vis = _get_visibility(node, src)
        return_type = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        annotations = _extract_attributes(node, src)

        mods = _extract_modifiers(node, src)
        attrs: list[str] = list(annotations)
        attrs.extend(
            m for m in mods
            if m in ("override", "final", "mutating", "nonmutating")
        )

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=_is_async(node),
            is_static=_is_static(node, src),
            params=params,
            return_type=return_type,
            attributes=tuple(attrs),
            span=span_from_node(node),
        )

    def _parse_init(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Parse an ``init_declaration`` into a MethodNode named 'init'."""
        vis = _get_visibility(node, src)
        params = _extract_parameters(node, src)

        return MethodNode(
            name="init",
            visibility=vis,
            is_async=False,
            is_static=False,
            params=params,
            return_type="",
            attributes=(),
            span=span_from_node(node),
        )

    def _walk_body_declarations(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        body_type: str = "class_body",
    ) -> None:
        """Walk body children for nested type declarations."""
        body = _child_by_type(node, body_type)
        if body is None:
            return

        for child in body.children:
            if child.type in ("class_declaration", "protocol_declaration"):
                self._dispatch_node(child, src, ast)

    @staticmethod
    def _get_property_name(node: Node, src: bytes) -> str:
        """Extract the name from a ``property_declaration``."""
        pattern = _child_by_type(node, "pattern")
        if pattern is not None:
            si = _child_by_type(pattern, "simple_identifier")
            if si is not None:
                return _node_text(si, src).strip()
        return ""

    @staticmethod
    def _get_property_type(node: Node, src: bytes) -> str:
        """Extract the type from a ``property_declaration``."""
        ta = _child_by_type(node, "type_annotation")
        if ta is None:
            return ""
        for child in ta.children:
            if child.type != ":":
                return _node_text(child, src).strip()
        return ""


# endregion: --- SwiftExtractor class


# ---------------------------------------------------------------------------
# region:    --- Visibility text helper
# ---------------------------------------------------------------------------


def _get_visibility_text(node: Node, src: bytes) -> str:
    """Get the raw visibility modifier text (e.g. 'open', 'public')."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ""
    vm = _child_by_type(mods, "visibility_modifier")
    if vm is None:
        return ""
    return _node_text(vm, src).strip()


# endregion: --- Visibility text helper


# ---------------------------------------------------------------------------
# region:    --- Post-pass: imported_package_methods
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Detect ``Type.method()`` calls using the import map."""
    result: dict[str, list[str]] = {}
    _walk_for_calls(root, src, import_map, result)
    return result


def _walk_for_calls(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Recursively walk AST looking for scoped method calls."""
    if node.type == "call_expression":
        _resolve_call(node, src, import_map, result)

    for child in node.children:
        _walk_for_calls(child, src, import_map, result)


def _resolve_call(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Try to resolve a call_expression to a scoped method."""
    # Look for navigation_expression pattern: Type.method(args)
    nav = _child_by_type(node, "navigation_expression")
    if nav is None:
        return

    children = [c for c in nav.children if c.type != "."]
    if len(children) < 2:  # noqa: PLR2004
        return

    receiver_text = _node_text(children[0], src).strip()
    method_text = ""
    suffix = _child_by_type(nav, "navigation_suffix")
    if suffix is not None:
        si = _child_by_type(suffix, "simple_identifier")
        if si is not None:
            method_text = _node_text(si, src).strip()

    if not method_text:
        return

    if receiver_text in import_map:
        fqn = import_map[receiver_text]
        if fqn not in result:
            result[fqn] = []
        if method_text not in result[fqn]:
            result[fqn].append(method_text)


# endregion: --- Post-pass: imported_package_methods
