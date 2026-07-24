"""Kotlin language extractor — tree-sitter based AST extraction.

Parses ``.kt`` / ``.kts`` files using ``tree-sitter-kotlin`` and maps
Kotlin-specific constructs to the normalized AST schema.

Kotlin → Normalized mapping:
    ================================  =================
    Kotlin Concept                    Normalized To
    ================================  =================
    ``class``                         StructNode
    ``data class``                    StructNode (attr)
    ``sealed class``                  StructNode (attr)
    ``object``                        StructNode (attr)
    ``interface``                     TraitNode
    ``abstract class``                TraitNode
    ``enum class``                    EnumNode
    ``fun`` (top-level)               FunctionNode
    ``fun`` (in class)                MethodNode
    ``import x.y.Z``                  uses (raw str)
    ``Type.method()``                 imported_package_methods
    ``const val``                     ConstantNode
    ``typealias``                     TypeAliasNode
    ``@Annotation``                   attributes
    ``suspend``                       is_async=True
    ================================  =================

Also parses ``build.gradle.kts`` manifests via :meth:`parse_manifest`.
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

__all__: list[str] = ["KotlinExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Kotlin visibility modifier text → Visibility mapping.
_VISIBILITY_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "protected": Visibility.PROTECTED,
    "private": Visibility.PRIVATE,
    "internal": Visibility.CRATE,
}

# Kotlin class modifier keywords that map to attributes.
_CLASS_MODIFIERS: frozenset[str] = frozenset({
    "data", "sealed", "inner", "value", "open",
})

# Kotlin function modifier keywords that map to attributes.
_FUNCTION_MODIFIERS: frozenset[str] = frozenset({
    "inline", "operator", "infix", "tailrec", "external",
})


# ---------------------------------------------------------------------------
# region:    --- Helper NamedTuple
# ---------------------------------------------------------------------------


class _TypeInfo(NamedTuple):
    """Parsed metadata for a Kotlin type declaration."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool
    is_interface: bool
    is_enum: bool
    class_modifiers: tuple[str, ...]


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
    ident = _child_by_type(node, "identifier")
    if ident is not None:
        return _node_text(ident, src)
    ident = _child_by_type(node, "type_identifier")
    if ident is not None:
        return _node_text(ident, src)
    ident = _child_by_type(node, "simple_identifier")
    if ident is not None:
        return _node_text(ident, src)
    return ""


def _get_visibility(node: Node, src: bytes) -> Visibility:
    """Extract visibility from ``modifiers`` child.

    Kotlin defaults to public when no modifier is present.
    """
    return _get_visibility_from_src(node, src)


def _get_visibility_from_src(node: Node, src: bytes) -> Visibility:
    """Extract visibility from ``modifiers`` child using source bytes."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return Visibility.PUBLIC
    for child in mods.children:
        if child.type == "visibility_modifier":
            text = _node_text(child, src).strip()
            vis = _VISIBILITY_MAP.get(text)
            if vis is not None:
                return vis
    return Visibility.PUBLIC


def _has_modifier(node: Node, modifier_type: str, modifier_text: str) -> bool:
    """Check if the declaration has a specific modifier.

    Args:
        node: The declaration node.
        modifier_type: The tree-sitter type of the modifier
            (e.g., ``"class_modifier"``, ``"function_modifier"``).
        modifier_text: The text of the modifier (e.g., ``"data"``, ``"suspend"``).
    """
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return False
    for child in mods.children:
        if child.type == modifier_type:
            for sub in child.children:
                if sub.type == modifier_text:
                    return True
            # Also check via text for simple modifiers
            text = child.text.decode("utf-8", errors="replace") if child.text else ""
            if text == modifier_text:
                return True
    return False


def _has_any_modifier_text(node: Node, src: bytes, text: str) -> bool:
    """Check if the declaration has a modifier with the given text."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return False
    return any(_node_text(child, src).strip() == text for child in mods.children)


def _extract_annotations(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@Annotation`` values from a declaration's modifiers."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()
    annotations = [
        _node_text(child, src).strip()
        for child in mods.children
        if child.type == "annotation"
    ]
    return tuple(annotations)


def _extract_class_modifiers(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract class-modifier keywords (data, sealed, inner, etc.)."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()
    result: list[str] = []
    for child in mods.children:
        if child.type == "class_modifier":
            text = _node_text(child, src).strip()
            if text in _CLASS_MODIFIERS:
                result.append(text)
        elif child.type == "inheritance_modifier":
            text = _node_text(child, src).strip()
            if text in ("abstract", "final", "open"):
                result.append(text)
        elif child.type == "member_modifier":
            text = _node_text(child, src).strip()
            if text == "override":
                result.append(text)
    return tuple(result)


def _extract_function_modifiers(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract function-modifier keywords (inline, operator, etc.)."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()
    result: list[str] = []
    for child in mods.children:
        if child.type == "function_modifier":
            text = _node_text(child, src).strip()
            if text in _FUNCTION_MODIFIERS:
                result.append(text)
        elif child.type == "member_modifier":
            text = _node_text(child, src).strip()
            if text == "override":
                result.append(text)
    return tuple(result)


def _is_suspend(node: Node, src: bytes) -> bool:
    """Check if a function has the ``suspend`` modifier."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return False
    for child in mods.children:
        if child.type == "function_modifier":
            text = _node_text(child, src).strip()
            if text == "suspend":
                return True
    return False


def _find_preceding_sibling_index(node: Node) -> int:
    """Return the index of *node* in its parent's children, or -1."""
    parent = node.parent
    if parent is None:
        return -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return i
    return -1


def _parse_kdoc_block(text: str) -> str:
    """Parse a ``/** ... */`` KDoc block into a clean doc string."""
    raw = text[3:].rstrip("*/").strip()
    lines = [ln.strip().lstrip("* ").strip() for ln in raw.splitlines()]
    clean = [ln for ln in lines if ln and not ln.startswith("@")]
    return " ".join(clean)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract KDoc ``/** ... */`` comment preceding a declaration.

    In tree-sitter-kotlin, block comments appear as ``multiline_comment``
    siblings before the declaration node.
    """
    idx = _find_preceding_sibling_index(node)
    if idx <= 0:
        return ""

    parent = node.parent
    assert parent is not None  # guaranteed by idx > 0

    sibling = parent.children[idx - 1]
    if sibling.type in ("multiline_comment", "block_comment"):
        text = _node_text(sibling, src).strip()
        if text.startswith("/**"):
            return _parse_kdoc_block(text)

    # Check for line comments (///)
    lines: list[str] = []
    for i in range(idx - 1, -1, -1):
        sib = parent.children[i]
        if sib.type != "line_comment":
            break
        text = _node_text(sib, src).strip()
        if not text.startswith("///"):
            break
        lines.append(text[3:].strip())
    if lines:
        lines.reverse()
        return " ".join(lines)
    return ""


def _extract_generics(node: Node, src: bytes) -> str:
    """Extract ``<T, U : Comparable>`` type parameters."""
    tp = _child_by_type(node, "type_parameters")
    if tp is not None:
        return _node_text(tp, src)
    return ""


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract the return type from a function declaration.

    Kotlin function declarations have the form:
    ``fun name(params): ReturnType { ... }``

    We look for a user_type or nullable_type after the ``:`` that follows
    the parameters.
    """
    # In tree-sitter-kotlin, the return type appears as a child after ":"
    found_colon_after_params = False
    found_params = False
    for child in node.children:
        if child.type == "function_value_parameters":
            found_params = True
            continue
        if found_params and child.type == ":":
            found_colon_after_params = True
            continue
        if found_colon_after_params and child.type in (
            "user_type", "nullable_type", "function_type",
            "type_identifier",
        ):
            return _node_text(child, src)
    return ""


def _extract_parameters(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract parameters from ``function_value_parameters``."""
    fp = _child_by_type(node, "function_value_parameters")
    if fp is None:
        return ()

    params: list[ParamNode] = []
    for child in fp.children:
        if child.type == "parameter":
            pname, ptype = _parse_parameter(child, src)
            if pname:
                params.append(ParamNode(name=pname, type=ptype))
    return tuple(params)


def _parse_parameter(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a single ``parameter`` into (name, type).

    Kotlin parameter: ``name: Type`` or ``name: Type = default``
    """
    name = ""
    ptype = ""
    ident = _child_by_type(node, "identifier")
    if ident is None:
        ident = _child_by_type(node, "simple_identifier")
    if ident is not None:
        name = _node_text(ident, src)

    # Type comes after ":"
    found_colon = False
    for child in node.children:
        if child.type == ":":
            found_colon = True
            continue
        if found_colon and child.type in (
            "user_type", "nullable_type", "function_type",
            "type_identifier",
        ):
            ptype = _node_text(child, src)
            break
    return (name, ptype)


def _extract_delegation_specifiers(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract base types from ``delegation_specifiers``.

    Returns names from ``delegation_specifier`` children, which contain
    either ``constructor_invocation`` (superclass) or ``user_type``
    (interface).
    """
    ds = _child_by_type(node, "delegation_specifiers")
    if ds is None:
        return ()

    bases: list[str] = []
    for child in ds.children:
        if child.type == "delegation_specifier":
            # constructor_invocation → user_type inside it
            ci = _child_by_type(child, "constructor_invocation")
            if ci is not None:
                ut = _child_by_type(ci, "user_type")
                if ut is not None:
                    bases.append(_node_text(ut, src))
                continue
            # Direct user_type (interface)
            ut = _child_by_type(child, "user_type")
            if ut is not None:
                bases.append(_node_text(ut, src))
    return tuple(bases)


def _is_interface_keyword(node: Node) -> bool:
    """Check if a class_declaration is actually an interface.

    In tree-sitter-kotlin, interfaces use ``class_declaration`` with
    an ``interface`` keyword child.
    """
    return any(child.type == "interface" for child in node.children)


def _extract_extension_receiver(node: Node, src: bytes) -> str:
    """Extract the receiver type for extension functions.

    Extension functions have a ``user_type`` + ``.`` before the function
    identifier.
    """
    # Walk children looking for user_type followed by "." followed by
    # simple_identifier (the function name)
    children = node.children
    for i, child in enumerate(children):
        if child.type == "." and i > 0:
            prev = children[i - 1]
            if prev.type in ("user_type", "nullable_type"):
                return _node_text(prev, src)
    return ""


def _is_const_property(node: Node, src: bytes) -> bool:
    """Check if a property_declaration has the ``const`` modifier."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return False
    for child in mods.children:
        if child.type == "property_modifier":
            text = _node_text(child, src).strip()
            if text == "const":
                return True
    return False


def _extract_type_after_colon(node: Node, src: bytes) -> str:
    """Extract a type node that follows a ``:`` in a declaration."""
    found_colon = False
    for c in node.children:
        if c.type == ":":
            found_colon = True
            continue
        if found_colon and c.type in (
            "user_type", "nullable_type", "function_type",
        ):
            return _node_text(c, src)
    return ""


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    """Build a mapping from short name → qualified path from import stmts.

    Examples:
        ``import com.example.User`` → ``{"User": "com.example.User"}``
        ``import com.example.*`` → skipped (wildcard)

    Args:
        uses: List of raw ``import`` statement strings.

    Returns:
        A dict mapping short names to their full qualified path.
    """
    import_map: dict[str, str] = {}
    for stmt in uses:
        cleaned = stmt.strip()
        if cleaned.startswith("import "):
            path = cleaned[len("import "):].strip()
            short = path.rsplit(".", maxsplit=1)[-1]
            if short != "*":
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
# region:    --- KotlinExtractor class
# ---------------------------------------------------------------------------


class KotlinExtractor(ExtractorBase):
    """Kotlin language extractor using ``tree-sitter-kotlin``.

    Parses ``.kt`` / ``.kts`` source files and ``build.gradle.kts``
    manifests into the normalized AST schema.
    """

    language_id: str = "kotlin"
    file_extensions: list[str] = [".kt", ".kts"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_kotlin as tskotlin

        self._language = Language(tskotlin.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.kt`` file into the normalized AST.

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

        # Post-pass 5: Spring routes (Kotlin reuses Spring annotations)
        from ast_intel.extractors._routes import detect_spring_routes
        ast.routes = detect_spring_routes(ast.functions, ast.self_methods)

        # Post-pass 6: HTTP client call detection
        from ast_intel.extractors._http_calls import (
            detect_spring_http_calls,
        )
        ast.http_calls = detect_spring_http_calls(root, source)

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``build.gradle.kts`` manifest.

        Args:
            manifest_path: Absolute path to the manifest file.

        Returns:
            A populated :class:`CrateModel`.
        """
        fname = manifest_path.name
        if fname == "build.gradle.kts":
            return self._parse_gradle_kts(manifest_path)
        return CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="kotlin",
            manifest_path=str(manifest_path),
        )

    # ------------------------------------------------------------------
    # Manifest — Gradle Kotlin DSL
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_gradle_kts(manifest_path: Path) -> CrateModel:
        """Parse ``build.gradle.kts`` with regex.

        Extracts ``implementation``, ``api``, ``testImplementation``,
        ``kapt``, ``ksp`` dependency declarations.
        """
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="kotlin",
            manifest_path=str(manifest_path),
        )

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", manifest_path, exc)
            return crate

        # Extract group and version
        group_m = re.search(r"""group\s*=\s*['"]([^'"]+)['"]""", content)
        version_m = re.search(
            r"""version\s*=\s*['"]([^'"]+)['"]""", content,
        )
        if group_m:
            crate.name = group_m.group(1).rsplit(".", maxsplit=1)[-1]
        if version_m:
            crate.version = version_m.group(1)

        # Extract dependencies
        # Kotlin DSL: implementation("group:artifact:version")
        # Also handles: testImplementation("...")
        _dep_re = re.compile(
            r"""(?:implementation|api|testImplementation|compileOnly|"""
            r"""runtimeOnly|annotationProcessor|kapt|ksp)\s*"""
            r"""\(\s*"([^"]+)"\s*\)""",
        )
        deps: list[CrateDependency] = []
        test_configs = {
            "testImplementation", "testCompileOnly", "testRuntimeOnly",
        }

        for m in _dep_re.finditer(content):
            coord = m.group(1).strip()
            parts = coord.split(":")
            if len(parts) >= 2:  # noqa: PLR2004
                dep_name = f"{parts[0]}:{parts[1]}"
                dep_version = parts[2] if len(parts) > 2 else ""  # noqa: PLR2004
                full_match = m.group(0)
                is_dev = any(tc in full_match for tc in test_configs)
                deps.append(CrateDependency(
                    name=dep_name,
                    version=dep_version,
                    is_dev=is_dev,
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

    def _dispatch_node(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Route a single AST node to the correct handler."""
        ntype = node.type

        if ntype == "package_header":
            self._handle_package(node, src, ast)

        elif ntype == "import":
            self._handle_import(node, src, ast)

        elif ntype == "class_declaration":
            self._handle_class_declaration(node, src, ast)

        elif ntype == "object_declaration":
            self._handle_object(node, src, ast)

        elif ntype == "function_declaration":
            self._handle_top_level_function(node, src, ast)

        elif ntype == "property_declaration":
            self._handle_top_level_property(node, src, ast)

        elif ntype == "type_alias":
            self._handle_type_alias(node, src, ast)

        elif ntype == "ERROR":
            text = _node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]
            ast.errors.append(f"Parse error near: {text}")

    # ------------------------------------------------------------------
    # Internal — package and imports
    # ------------------------------------------------------------------

    def _handle_package(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``package_header`` → module_path."""
        qi = _child_by_type(node, "qualified_identifier")
        if qi is not None:
            ast.module_path = _node_text(qi, src).strip()

    def _handle_import(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``import`` → uses list."""
        qi = _child_by_type(node, "qualified_identifier")
        if qi is not None:
            path = _node_text(qi, src).strip()
            ast.uses.append(f"import {path}")

    # ------------------------------------------------------------------
    # Internal — class declarations (class, interface, enum, etc.)
    # ------------------------------------------------------------------

    def _handle_class_declaration(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``class_declaration``.

        Dispatches based on modifiers:
        - ``interface`` keyword → TraitNode
        - ``abstract`` modifier → TraitNode
        - ``enum`` modifier → EnumNode
        - ``data`` / ``sealed`` → StructNode with attributes
        - plain ``class`` → StructNode
        """
        info = self._parse_type_info(node, src)

        if info.is_interface:
            self._extract_interface_as_trait(node, src, ast, info)
        elif info.is_abstract:
            self._extract_abstract_class_as_trait(node, src, ast, info)
        elif info.is_enum:
            self._extract_enum(node, src, ast, info)
        else:
            self._extract_class_as_struct(node, src, ast, info)

    def _parse_type_info(self, node: Node, src: bytes) -> _TypeInfo:
        """Extract shared metadata from a type declaration."""
        name = _get_identifier(node, src)
        vis = _get_visibility_from_src(node, src)
        generics = _extract_generics(node, src)
        annotations = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        class_mods = _extract_class_modifiers(node, src)

        is_abstract = "abstract" in class_mods
        is_interface = _is_interface_keyword(node)
        is_enum = _has_any_modifier_text(node, src, "enum")

        # Build bases from delegation_specifiers
        bases = _extract_delegation_specifiers(node, src)

        # Combine annotations + class modifiers as attributes
        attributes = annotations + tuple(
            m for m in class_mods if m not in ("abstract", "open")
        )

        return _TypeInfo(
            name=name,
            bases=bases,
            generics=generics,
            attributes=attributes,
            doc=doc,
            vis=vis,
            is_abstract=is_abstract,
            is_interface=is_interface,
            is_enum=is_enum,
            class_modifiers=class_mods,
        )

    # ------------------------------------------------------------------
    # Internal — struct extraction (class, data class, sealed class)
    # ------------------------------------------------------------------

    def _extract_class_as_struct(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract a concrete class → StructNode + ImplBlockNode(s)."""
        # Primary constructor fields
        fields = self._extract_primary_constructor_fields(node, src)

        # Class body fields (property_declarations)
        body_fields = self._extract_class_body_fields(node, src)
        all_fields = fields + body_fields

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=all_fields,
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Constants from body + companion object
        self._extract_class_constants(node, src, ast)

        # Methods from class body + companion object
        methods = self._extract_class_methods(node, src)
        companion_methods = self._extract_companion_methods(node, src)
        all_methods = methods + companion_methods

        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    generics=info.generics,
                    methods=all_methods,
                    span=span_from_node(node),
                ))
        elif all_methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type="",
                generics=info.generics,
                methods=all_methods,
                span=span_from_node(node),
            ))

        # Nested classes
        self._extract_nested_classes(node, src, ast)

    def _extract_primary_constructor_fields(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract fields from primary constructor parameters.

        Only ``class_parameter`` with ``val`` or ``var`` keyword become
        fields.
        """
        ctor = _child_by_type(node, "primary_constructor")
        if ctor is None:
            return ()

        params = _child_by_type(ctor, "class_parameters")
        if params is None:
            return ()

        fields: list[FieldNode] = []
        for child in params.children:
            if child.type == "class_parameter":
                field = self._parse_class_parameter(child, src)
                if field is not None:
                    fields.append(field)
        return tuple(fields)

    @staticmethod
    def _parse_class_parameter(
        node: Node,
        src: bytes,
    ) -> FieldNode | None:
        """Parse a single class_parameter into a FieldNode, or None."""
        has_val_var = any(
            c.type in ("val", "var") for c in node.children
        )
        if not has_val_var:
            return None

        ident = _child_by_type(node, "identifier")
        if ident is None:
            ident = _child_by_type(node, "simple_identifier")
        name = _node_text(ident, src) if ident is not None else ""
        if not name:
            return None

        ftype = _extract_type_after_colon(node, src)
        vis = _get_visibility_from_src(node, src)
        return FieldNode(name=name, type=ftype, visibility=vis)

    def _extract_class_body_fields(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract property declarations from class body as fields."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type == "property_declaration":
                if _is_const_property(child, src):
                    continue  # handled separately as constants
                name, ftype = self._parse_property(child, src)
                if name:
                    vis = _get_visibility_from_src(child, src)
                    fields.append(FieldNode(
                        name=name,
                        type=ftype,
                        visibility=vis,
                    ))
        return tuple(fields)

    def _extract_class_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract ``const val`` properties as ConstantNode."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return

        for child in body.children:
            if child.type == "property_declaration" and _is_const_property(
                child, src,
            ):
                self._add_constant(child, src, ast)
            elif child.type == "companion_object":
                cbody = _child_by_type(child, "class_body")
                if cbody is not None:
                    for gc in cbody.children:
                        if gc.type == "property_declaration" and _is_const_property(
                            gc, src,
                        ):
                            self._add_constant(gc, src, ast)

    def _add_constant(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Create a ConstantNode from a property_declaration."""
        name, _ = self._parse_property(node, src)
        if not name:
            return
        vis = _get_visibility_from_src(node, src)
        raw = _node_text(node, src).strip()
        if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
            raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."
        ast.constants.append(ConstantNode(
            name=name,
            visibility=vis,
            raw=raw,
            span=span_from_node(node),
        ))

    def _extract_class_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from a class body."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "function_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
        return tuple(methods)

    def _extract_companion_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from ``companion object`` blocks."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "companion_object":
                cbody = _child_by_type(child, "class_body")
                if cbody is not None:
                    for gc in cbody.children:
                        if gc.type == "function_declaration":
                            m = self._extract_method(gc, src)
                            if m is not None:
                                # Mark as static via attributes
                                attrs = (*m.attributes, "companion")
                                methods.append(MethodNode(
                                    name=m.name,
                                    visibility=m.visibility,
                                    is_async=m.is_async,
                                    params=m.params,
                                    return_type=m.return_type,
                                    attributes=attrs,
                                    span=m.span,
                                ))
        return tuple(methods)

    def _extract_nested_classes(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Recursively extract nested class/enum/interface declarations."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return

        for child in body.children:
            if child.type == "class_declaration":
                self._handle_class_declaration(child, src, ast)
            elif child.type == "object_declaration":
                self._handle_object(child, src, ast)

    def _extract_method(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Extract a single function_declaration → MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None
        vis = _get_visibility_from_src(node, src)
        rtype = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        annotations = _extract_annotations(node, src)
        func_mods = _extract_function_modifiers(node, src)
        is_async = _is_suspend(node, src)

        # Check for extension function receiver
        receiver = _extract_extension_receiver(node, src)
        attrs = annotations + func_mods
        if receiver:
            attrs = (*attrs, f"extension:{receiver}")

        # Check for override modifier
        if _has_any_modifier_text(node, src, "override") and "override" not in attrs:
            attrs = (*attrs, "override")

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=is_async,
            params=params,
            return_type=rtype,
            attributes=attrs,
            span=span_from_node(node),
        )

    @staticmethod
    def _parse_property(
        node: Node,
        src: bytes,
    ) -> tuple[str, str]:
        """Parse a ``property_declaration`` into (name, type).

        Kotlin properties: ``val name: Type = value``
        """
        name = ""
        ptype = ""

        # Variable declaration contains the identifier
        vd = _child_by_type(node, "variable_declaration")
        if vd is not None:
            ident = _child_by_type(vd, "identifier")
            if ident is None:
                ident = _child_by_type(vd, "simple_identifier")
            if ident is not None:
                name = _node_text(ident, src)
            # Type annotation after ":"
            found_colon = False
            for c in vd.children:
                if c.type == ":":
                    found_colon = True
                    continue
                if found_colon and c.type in (
                    "user_type", "nullable_type", "function_type",
                ):
                    ptype = _node_text(c, src)
                    break

        return (name, ptype)

    # ------------------------------------------------------------------
    # Internal — interface extraction
    # ------------------------------------------------------------------

    def _extract_interface_as_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract an ``interface`` → TraitNode."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "class_body")
        if body is not None:
            for child in body.children:
                if child.type == "function_declaration":
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

    def _extract_abstract_class_as_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract an ``abstract class`` → TraitNode."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "class_body")
        if body is not None:
            for child in body.children:
                if child.type == "function_declaration":
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

    def _extract_trait_method_item(
        self,
        node: Node,
        src: bytes,
    ) -> TraitItemNode | None:
        """Extract a function declaration from a trait/interface body."""
        name = _get_identifier(node, src)
        if not name:
            return None

        rtype = _extract_return_type(node, src)
        # Has a body → provided (default impl), otherwise required
        has_body = _child_by_type(node, "function_body") is not None
        kind = TraitItemKind.DEFAULT_METHOD if has_body else TraitItemKind.REQUIRED_METHOD

        params = _extract_parameters(node, src)
        is_async = _is_suspend(node, src)

        return TraitItemNode(
            name=name,
            kind=kind,
            is_async=is_async,
            params=params,
            return_type=rtype,
        )

    # ------------------------------------------------------------------
    # Internal — enum extraction
    # ------------------------------------------------------------------

    def _extract_enum(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Handle ``enum class`` → EnumNode."""
        variants: list[EnumVariantNode] = []
        body = _child_by_type(node, "enum_class_body")
        if body is not None:
            for child in body.children:
                if child.type == "enum_entry":
                    vname = _get_identifier(child, src)
                    args = _child_by_type(child, "value_arguments")
                    kind = (
                        EnumVariantKind.TUPLE if args
                        else EnumVariantKind.UNIT
                    )
                    variants.append(EnumVariantNode(
                        name=vname,
                        kind=kind,
                    ))

        ast.enums.append(EnumNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            variants=tuple(variants),
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Enum methods (from after the semicolon in the body)
        methods = self._extract_enum_methods(node, src)
        if methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
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
        """Extract methods from enum body (after the entries)."""
        body = _child_by_type(node, "enum_class_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "function_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
        return tuple(methods)

    # ------------------------------------------------------------------
    # Internal — object declarations
    # ------------------------------------------------------------------

    def _handle_object(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``object_declaration`` → StructNode with 'object' attr."""
        name = _get_identifier(node, src)
        vis = _get_visibility_from_src(node, src)
        generics = _extract_generics(node, src)
        annotations = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        attributes = (*annotations, "object")

        bases = _extract_delegation_specifiers(node, src)

        ast.structs.append(StructNode(
            name=name,
            visibility=vis,
            generics=generics,
            fields=(),
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

        # Object methods
        methods = self._extract_class_methods(node, src)
        if bases:
            for base in bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=name,
                    trait_type=base,
                    generics=generics,
                    methods=methods,
                    span=span_from_node(node),
                ))
        elif methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=name,
                trait_type="",
                generics=generics,
                methods=methods,
                span=span_from_node(node),
            ))

        # Object constants
        body = _child_by_type(node, "class_body")
        if body is not None:
            for child in body.children:
                if child.type == "property_declaration" and _is_const_property(
                    child, src,
                ):
                    self._add_constant(child, src, ast)

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
        from ast_intel.models.ast_node import FunctionNode

        name = _get_identifier(node, src)
        if not name:
            return

        vis = _get_visibility_from_src(node, src)
        rtype = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        annotations = _extract_annotations(node, src)
        func_mods = _extract_function_modifiers(node, src)
        is_async = _is_suspend(node, src)
        generics = _extract_generics(node, src)
        doc = _extract_doc_comment(node, src)

        # Extension function receiver
        receiver = _extract_extension_receiver(node, src)
        attrs = annotations + func_mods
        if receiver:
            attrs = (*attrs, f"extension:{receiver}")

        ast.functions.append(FunctionNode(
            name=name,
            visibility=vis,
            is_async=is_async,
            params=params,
            return_type=rtype,
            generics=generics,
            attributes=attrs,
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
        """Handle top-level ``property_declaration``.

        ``const val`` → ConstantNode, otherwise skip.
        """
        if _is_const_property(node, src):
            self._add_constant(node, src, ast)

    # ------------------------------------------------------------------
    # Internal — type aliases
    # ------------------------------------------------------------------

    def _handle_type_alias(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``type_alias`` → TypeAliasNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        vis = _get_visibility_from_src(node, src)

        # Get the aliased type (after "=")
        aliased = ""
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq and child.type in (
                "user_type", "nullable_type", "function_type",
            ):
                aliased = _node_text(child, src)
                break

        ast.type_aliases.append(TypeAliasNode(
            name=name,
            visibility=vis,
            aliased_to=aliased,
            span=span_from_node(node),
        ))


# endregion: --- KotlinExtractor class


# ---------------------------------------------------------------------------
# region:    --- Post-pass: imported_package_methods
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Detect ``Type.method()`` calls using the import map.

    Walks the AST looking for ``call_expression`` nodes where the
    object part resolves to a known import.
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
    """Recursively walk for ``call_expression`` nodes."""
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
    """Try to resolve a call_expression into a scoped method call.

    Kotlin call_expression: ``object.method(args)``
    navigation_expression → identifier . identifier
    """
    # Look for navigation_expression as the callee
    nav = _child_by_type(node, "navigation_expression")
    if nav is None:
        return

    # navigation_expression children: object . member
    children = [c for c in nav.children if c.type != "."]
    if len(children) < 2:  # noqa: PLR2004
        return

    obj_node = children[0]
    method_node = children[-1]

    obj_text = _node_text(obj_node, src)
    method_name = _node_text(method_node, src)

    if not obj_text or not method_name:
        return

    leftmost = obj_text.split(".")[0]
    if leftmost in import_map:
        qualified = import_map[leftmost]
        result.setdefault(qualified, [])
        if method_name not in result[qualified]:
            result[qualified].append(method_name)


# endregion: --- Post-pass: imported_package_methods
