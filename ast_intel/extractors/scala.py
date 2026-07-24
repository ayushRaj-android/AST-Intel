"""Scala language extractor — tree-sitter based AST extraction.

Parses ``.scala`` / ``.sc`` files using ``tree-sitter-scala`` and maps
Scala-specific constructs to the normalized AST schema.

Scala → Normalized mapping:
    ================================  =================
    Scala Concept                     Normalized To
    ================================  =================
    ``class``                         StructNode
    ``case class``                    StructNode (attr)
    ``sealed class``                  StructNode (attr)
    ``object``                        StructNode (attr)
    ``case object``                   StructNode (attr)
    ``trait``                         TraitNode
    ``abstract class``                TraitNode
    ``enum`` (Scala 3)                EnumNode
    ``def`` (top-level)               FunctionNode
    ``def`` (in class/trait)          MethodNode
    ``import x.y.Z``                  uses (raw str)
    ``Type.method()``                 imported_package_methods
    ``final val``                     ConstantNode
    ``type X = Y``                    TypeAliasNode
    ``@Annotation``                   attributes
    ================================  =================

Also parses ``build.sbt`` manifests via :meth:`parse_manifest`.
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

__all__: list[str] = ["ScalaExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60


# ---------------------------------------------------------------------------
# region:    --- Helper NamedTuple
# ---------------------------------------------------------------------------


class _TypeInfo(NamedTuple):
    """Parsed metadata for a Scala type declaration."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool
    is_trait: bool
    is_enum: bool


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
    """Extract the ``identifier`` child text from a declaration node."""
    id_node = _child_by_type(node, "identifier")
    if id_node is not None:
        return _node_text(id_node, src)
    return ""


def _find_preceding_sibling_index(node: Node) -> int:
    """Return the index of *node* within its parent's children list."""
    parent = node.parent
    if parent is None:
        return -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return i
    return -1


def _get_visibility(node: Node, src: bytes) -> Visibility:  # noqa: PLR0911
    """Parse Scala visibility from ``modifiers`` → ``access_modifier``.

    Mapping:
        ``private``              → PRIVATE
        ``private[this]``        → PRIVATE
        ``private[scope]``       → CRATE
        ``protected``            → PROTECTED
        ``protected[scope]``     → PROTECTED
        *(no modifier)*          → PUBLIC
    """
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return Visibility.PUBLIC

    am = _child_by_type(mods, "access_modifier")
    if am is None:
        return Visibility.PUBLIC

    text = _node_text(am, src).strip()
    if text.startswith("private"):
        qualifier = _child_by_type(am, "access_qualifier")
        if qualifier is not None:
            qt = _node_text(qualifier, src).strip()
            if qt == "[this]":
                return Visibility.PRIVATE
            return Visibility.CRATE
        return Visibility.PRIVATE
    if text.startswith("protected"):
        return Visibility.PROTECTED

    return Visibility.PUBLIC


def _extract_annotations(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@Annotation`` names from a declaration node.

    Annotations appear as direct ``annotation`` children.
    """
    annotations: list[str] = []
    for child in node.children:
        if child.type == "annotation":
            ti = _child_by_type(child, "type_identifier")
            if ti is not None:
                annotations.append(f"@{_node_text(ti, src)}")
    return tuple(annotations)


def _extract_modifiers(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract non-visibility modifier keywords from ``modifiers``.

    Returns modifiers like ``sealed``, ``abstract``, ``case``,
    ``final``, ``lazy``, ``override``, ``implicit``, ``open``.
    """
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()

    keywords: list[str] = [
        child.type
        for child in mods.children
        if child.type in (
            "sealed", "abstract", "final", "lazy",
            "override", "implicit", "open",
        )
    ]
    return tuple(keywords)


def _has_case_modifier(node: Node) -> bool:
    """Check if a declaration node has a ``case`` keyword child."""
    return any(c.type == "case" for c in node.children)


def _parse_scaladoc_block(text: str) -> str:
    """Parse a ``/** ... */`` ScalaDoc block into a clean doc string."""
    raw = text[3:].rstrip("*/").strip()
    lines = [ln.strip().lstrip("* ").strip() for ln in raw.splitlines()]
    clean = [ln for ln in lines if ln and not ln.startswith("@")]
    return " ".join(clean)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract ScalaDoc ``/** ... */`` comment preceding a declaration.

    In tree-sitter-scala, block comments appear as ``block_comment``
    siblings before the declaration node.
    """
    idx = _find_preceding_sibling_index(node)
    if idx <= 0:
        return ""

    parent = node.parent
    assert parent is not None

    sibling = parent.children[idx - 1]
    if sibling.type == "block_comment":
        text = _node_text(sibling, src).strip()
        if text.startswith("/**"):
            return _parse_scaladoc_block(text)

    # Check for line comments (///)
    lines: list[str] = []
    for i in range(idx - 1, -1, -1):
        sib = parent.children[i]
        if sib.type != "comment":
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
    """Extract ``[T, U <: Bound]`` type parameters."""
    tp = _child_by_type(node, "type_parameters")
    if tp is not None:
        return _node_text(tp, src)
    return ""


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract return type after ``:`` in a function/method declaration.

    Looks for the type node that follows the ``:`` token, skipping
    past any ``parameters`` children.
    """
    found_colon = False
    past_params = False
    for child in node.children:
        if child.type == "parameters":
            past_params = True
            continue
        if past_params and child.type == ":":
            found_colon = True
            continue
        if found_colon and child.type in (
            "type_identifier", "generic_type",
            "function_type", "tuple_type",
            "compound_type", "infix_type",
        ):
            return _node_text(child, src)
    return ""


def _extract_type_after_colon(node: Node, src: bytes) -> str:
    """Extract a type node that follows a ``:`` in a val/var declaration."""
    found_colon = False
    for c in node.children:
        if c.type == ":":
            found_colon = True
            continue
        if found_colon and c.type in (
            "type_identifier", "generic_type",
            "function_type", "tuple_type",
            "compound_type", "infix_type",
        ):
            return _node_text(c, src)
    return ""


def _extract_extends_clause(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract base types from ``extends_clause``.

    Handles ``extends Foo with Bar with Baz``.
    Returns a tuple of type names.
    """
    ec = _child_by_type(node, "extends_clause")
    if ec is None:
        return ()

    bases: list[str] = []
    for child in ec.children:
        if child.type in ("type_identifier", "generic_type"):
            bases.append(_node_text(child, src))
        elif child.type == "constructor_invocation":
            ti = _child_by_type(child, "type_identifier")
            if ti is None:
                ti = _child_by_type(child, "generic_type")
            if ti is not None:
                bases.append(_node_text(ti, src))
    return tuple(bases)


def _has_function_body(node: Node) -> bool:
    """Check if a function_definition has a body (``=`` + expression)."""
    return any(c.type == "=" for c in node.children)


def _is_final_val(node: Node, src: bytes) -> bool:
    """Check if a val_definition has the ``final`` modifier."""
    mods = _extract_modifiers(node, src)
    return "final" in mods


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    """Build a mapping from short name → qualified path from import stmts.

    Examples:
        ``import com.example.User`` → ``{"User": "com.example.User"}``
        ``import com.example._`` → skipped (wildcard)

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
            if short not in ("_", "*"):
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
# region:    --- ScalaExtractor class
# ---------------------------------------------------------------------------


class ScalaExtractor(ExtractorBase):
    """Scala language extractor using ``tree-sitter-scala``.

    Parses ``.scala`` / ``.sc`` source files and ``build.sbt``
    manifests into the normalized AST schema.
    """

    language_id: str = "scala"
    file_extensions: list[str] = [".scala", ".sc"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_scala as tsscala

        self._language = Language(tsscala.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.scala`` file into the normalized AST.

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
        """Parse a ``build.sbt`` manifest.

        Args:
            manifest_path: Absolute path to the manifest file.

        Returns:
            A populated :class:`CrateModel`.
        """
        fname = manifest_path.name
        if fname == "build.sbt":
            return self._parse_build_sbt(manifest_path)
        return CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="scala",
            manifest_path=str(manifest_path),
        )

    # ------------------------------------------------------------------
    # Manifest — build.sbt
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_build_sbt(manifest_path: Path) -> CrateModel:
        """Parse ``build.sbt`` with regex.

        Extracts ``libraryDependencies`` with ``%`` / ``%%`` operators.
        """
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="scala",
            manifest_path=str(manifest_path),
        )

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", manifest_path, exc)
            return crate

        # Extract name and version
        name_m = re.search(r"""name\s*:=\s*"([^"]+)""", content)
        version_m = re.search(r"""version\s*:=\s*"([^"]+)""", content)
        if name_m:
            crate.name = name_m.group(1)
        if version_m:
            crate.version = version_m.group(1)

        # Dependency patterns:
        #   "group" %% "artifact" % "version"
        #   "group" %  "artifact" % "version"  # noqa: ERA001
        #   "group" %% "artifact" % "version" % Test
        _dep_re = re.compile(
            r'"([^"]+)"\s*%%?\s*"([^"]+)"\s*%\s*"([^"]+)"'
            r'(?:\s*%\s*(\w+))?',
        )
        deps: list[CrateDependency] = []
        test_scopes = {"Test", "IntegrationTest", "test"}

        for m in _dep_re.finditer(content):
            group = m.group(1).strip()
            artifact = m.group(2).strip()
            version = m.group(3).strip()
            scope = m.group(4)
            is_dev = scope in test_scopes if scope else False
            deps.append(CrateDependency(
                name=f"{group}:{artifact}",
                version=version,
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

    def _dispatch_node(  # noqa: C901, PLR0912
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Route a single AST node to the correct handler."""
        ntype = node.type

        if ntype == "package_clause":
            self._handle_package(node, src, ast)

        elif ntype == "import_declaration":
            self._handle_import(node, src, ast)

        elif ntype == "class_definition":
            self._handle_class_definition(node, src, ast)

        elif ntype == "trait_definition":
            self._handle_trait_definition(node, src, ast)

        elif ntype == "object_definition":
            self._handle_object_definition(node, src, ast)

        elif ntype == "enum_definition":
            self._handle_enum_definition(node, src, ast)

        elif ntype in ("function_definition", "function_declaration"):
            self._handle_top_level_function(node, src, ast)

        elif ntype == "val_definition":
            self._handle_top_level_val(node, src, ast)

        elif ntype == "type_definition":
            self._handle_type_definition(node, src, ast)

        elif ntype == "package_object":
            self._handle_package_object(node, src, ast)

        elif ntype == "extension_definition":
            self._handle_extension_definition(node, src, ast)

        elif ntype == "given_definition":
            self._handle_given_definition(node, src, ast)

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
        """Handle ``package_clause`` → module_path."""
        pi = _child_by_type(node, "package_identifier")
        if pi is not None:
            ast.module_path = _node_text(pi, src).strip()

    def _handle_import(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``import_declaration`` → uses list.

        Handles:
        - ``import a.b.C`` → ``"import a.b.C"``
        - ``import a.b._`` → ``"import a.b._"``
        - ``import a.b.{C, D}`` → ``"import a.b.C"``, ``"import a.b.D"``
        - ``import a.b.{C => Renamed}`` → ``"import a.b.C"``
        """
        # Build path prefix from identifier chain
        parts: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                parts.append(_node_text(child, src))
            elif child.type == "namespace_wildcard":
                full = ".".join(parts) + "._"
                ast.uses.append(f"import {full}")
                return
            elif child.type == "namespace_selectors":
                prefix = ".".join(parts)
                for sel in child.children:
                    if sel.type == "identifier":
                        name = _node_text(sel, src)
                        ast.uses.append(f"import {prefix}.{name}")
                    elif sel.type == "arrow_renamed_identifier":
                        orig = _child_by_type(sel, "identifier")
                        if orig is not None:
                            name = _node_text(orig, src)
                            ast.uses.append(f"import {prefix}.{name}")
                return

        # Simple import: import a.b.C
        if parts:
            ast.uses.append(f"import {'.'.join(parts)}")

    # ------------------------------------------------------------------
    # Internal — class definitions
    # ------------------------------------------------------------------

    def _handle_class_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``class_definition``.

        Dispatches based on modifiers:
        - ``abstract`` modifier → TraitNode
        - ``case class`` → StructNode with ``case`` attribute
        - ``sealed class`` → StructNode with ``sealed`` attribute
        - plain ``class`` → StructNode
        """
        info = self._parse_type_info(node, src, is_trait=False)

        if info.is_abstract:
            self._extract_abstract_class_as_trait(node, src, ast, info)
        else:
            self._extract_class_as_struct(node, src, ast, info)

    def _parse_type_info(
        self,
        node: Node,
        src: bytes,
        *,
        is_trait: bool = False,
        is_enum: bool = False,
    ) -> _TypeInfo:
        """Extract shared metadata from a type declaration."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _extract_generics(node, src)
        annotations = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        mod_keywords = _extract_modifiers(node, src)

        is_abstract = "abstract" in mod_keywords
        is_case = _has_case_modifier(node)
        is_sealed = "sealed" in mod_keywords

        bases = _extract_extends_clause(node, src)

        # Build attributes from annotations + modifier keywords
        attrs: list[str] = list(annotations)
        if is_case:
            attrs.append("case")
        if is_sealed:
            attrs.append("sealed")
        attrs.extend(
            m for m in mod_keywords
            if m not in ("abstract", "sealed", "open")
        )

        return _TypeInfo(
            name=name,
            bases=bases,
            generics=generics,
            attributes=tuple(attrs),
            doc=doc,
            vis=vis,
            is_abstract=is_abstract,
            is_trait=is_trait,
            is_enum=is_enum,
        )

    # ------------------------------------------------------------------
    # Internal — struct extraction
    # ------------------------------------------------------------------

    def _extract_class_as_struct(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract a concrete class → StructNode + ImplBlockNode(s)."""
        is_case = _has_case_modifier(node)

        # Constructor fields
        fields = self._extract_constructor_fields(node, src, is_case=is_case)

        # Body fields
        body_fields = self._extract_body_fields(node, src)
        all_fields = fields + body_fields

        # Check for value class (extends AnyVal)
        attrs = list(info.attributes)
        if "AnyVal" in info.bases:
            attrs.append("value")

        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=tuple(all_fields),
            attributes=tuple(attrs),
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Methods
        methods = self._extract_body_methods(node, src)
        all_methods = methods

        # Constants from body
        self._extract_body_constants(node, src, ast)

        # Nested declarations
        self._walk_body_declarations(node, src, ast)

        if info.bases:
            filtered = tuple(b for b in info.bases if b != "AnyVal")
            for base in filtered:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=tuple(all_methods),
                    span=span_from_node(node),
                ))
        elif all_methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type=None,
                methods=tuple(all_methods),
                span=span_from_node(node),
            ))

    def _extract_constructor_fields(
        self,
        node: Node,
        src: bytes,
        *,
        is_case: bool = False,
    ) -> list[FieldNode]:
        """Extract fields from ``class_parameters``.

        For case classes, all params are implicitly ``val``.
        For regular classes, only ``val``/``var`` params become fields.
        """
        cp = _child_by_type(node, "class_parameters")
        if cp is None:
            return []

        fields: list[FieldNode] = []
        for param in _children_by_type(cp, "class_parameter"):
            has_val = any(c.type == "val" for c in param.children)
            has_var = any(c.type == "var" for c in param.children)

            if not is_case and not has_val and not has_var:
                continue

            name = _get_identifier(param, src)
            if not name:
                continue

            field_type = _extract_type_after_colon(param, src)
            vis = _get_visibility(param, src)

            fields.append(FieldNode(
                name=name,
                type=field_type,
                visibility=vis,
            ))
        return fields

    def _extract_body_fields(
        self,
        node: Node,
        src: bytes,
    ) -> list[FieldNode]:
        """Extract ``val``/``var`` declarations from ``template_body``."""
        body = _child_by_type(node, "template_body")
        if body is None:
            return []

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type in ("val_definition", "var_definition",
                              "val_declaration", "var_declaration"):
                name = _get_identifier(child, src)
                if not name:
                    continue

                mods = _extract_modifiers(child, src)
                if "final" in mods:
                    continue  # handled as constant

                field_type = _extract_type_after_colon(child, src)
                vis = _get_visibility(child, src)

                attrs: list[str] = []
                if "lazy" in mods:
                    attrs.append("lazy")

                fields.append(FieldNode(
                    name=name,
                    type=field_type,
                    visibility=vis,
                ))
        return fields

    def _extract_body_methods(
        self,
        node: Node,
        src: bytes,
    ) -> list[MethodNode]:
        """Extract ``function_definition`` from ``template_body``."""
        body = _child_by_type(node, "template_body")
        if body is None:
            return []

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "function_definition":
                method = self._parse_method(child, src)
                if method is not None:
                    methods.append(method)
        return methods

    def _parse_method(self, node: Node, src: bytes) -> MethodNode | None:
        """Parse a ``function_definition`` into a MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None

        vis = _get_visibility(node, src)
        return_type = _extract_return_type(node, src)
        annotations = _extract_annotations(node, src)
        mod_keywords = _extract_modifiers(node, src)

        params = self._extract_all_params(node, src)

        attrs: list[str] = list(annotations)
        attrs.extend(
            m for m in mod_keywords
            if m in ("override", "final", "implicit")
        )

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=False,
            params=tuple(params),
            return_type=return_type,
            attributes=tuple(attrs),
            span=span_from_node(node),
        )

    def _extract_all_params(
        self,
        node: Node,
        src: bytes,
    ) -> list[ParamNode]:
        """Extract params from all parameter lists (handles currying)."""
        params: list[ParamNode] = []

        for child in node.children:
            if child.type == "parameters":
                for param in _children_by_type(child, "parameter"):
                    pname = _get_identifier(param, src)
                    if not pname:
                        continue

                    ptype = _extract_type_after_colon(param, src)
                    params.append(ParamNode(
                        name=pname,
                        type=ptype,
                    ))

        return params

    def _extract_body_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract ``final val`` from ``template_body`` as constants."""
        body = _child_by_type(node, "template_body")
        if body is None:
            return

        for child in body.children:
            if child.type == "val_definition" and _is_final_val(child, src):
                self._add_constant(child, src, ast)

    def _add_constant(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Create a ConstantNode from a val_definition."""
        name = _get_identifier(node, src)
        if not name:
            return

        vis = _get_visibility(node, src)

        raw = _node_text(node, src).strip()
        if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
            raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."

        ast.constants.append(ConstantNode(
            name=name,
            raw=raw,
            visibility=vis,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — trait definitions
    # ------------------------------------------------------------------

    def _handle_trait_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``trait_definition`` → TraitNode."""
        info = self._parse_type_info(node, src, is_trait=True)
        self._extract_trait(node, src, ast, info)

    def _extract_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract a trait → TraitNode with TraitItemNodes."""
        items = self._extract_trait_items(node, src)

        ast.traits.append(TraitNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            items=tuple(items),
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Nested declarations
        self._walk_body_declarations(node, src, ast)

        # Supertraits
        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=(),
                    span=span_from_node(node),
                ))

    def _extract_abstract_class_as_trait(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
        info: _TypeInfo,
    ) -> None:
        """Extract ``abstract class`` → TraitNode."""
        items = self._extract_trait_items(node, src)

        # Constructor fields are still relevant for abstract classes
        self._extract_constructor_fields(node, src, is_case=False)

        # Add "abstract" to attributes for abstract classes
        attrs = info.attributes
        if "abstract" not in attrs:
            attrs = (*attrs, "abstract")

        # We include constructor fields as struct-like data attached via
        # the abstract class StructNode, but map the class itself as TraitNode
        ast.traits.append(TraitNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            items=tuple(items),
            attributes=attrs,
            doc=info.doc,
            span=span_from_node(node),
        ))

        if info.bases:
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    methods=(),
                    span=span_from_node(node),
                ))

    def _extract_trait_items(
        self,
        node: Node,
        src: bytes,
    ) -> list[TraitItemNode]:
        """Extract trait items from ``template_body``.

        - ``function_declaration`` (no body) → REQUIRED_METHOD
        - ``function_definition`` (has body) → DEFAULT_METHOD
        - ``type_definition`` / ``type`` in traits → skipped for now
        """
        body = _child_by_type(node, "template_body")
        if body is None:
            return []

        items: list[TraitItemNode] = []
        for child in body.children:
            if child.type == "function_declaration":
                items.append(self._parse_trait_item(
                    child, src, TraitItemKind.REQUIRED_METHOD,
                ))
            elif child.type == "function_definition":
                items.append(self._parse_trait_item(
                    child, src, TraitItemKind.DEFAULT_METHOD,
                ))
        return items

    def _parse_trait_item(
        self,
        node: Node,
        src: bytes,
        kind: TraitItemKind,
    ) -> TraitItemNode:
        """Parse a function declaration/definition into a TraitItemNode."""
        name = _get_identifier(node, src)
        return_type = _extract_return_type(node, src)
        params = self._extract_all_params(node, src)

        return TraitItemNode(
            name=name,
            kind=kind,
            is_async=False,
            params=tuple(params),
            return_type=return_type,
        )

    # ------------------------------------------------------------------
    # Internal — object definitions
    # ------------------------------------------------------------------

    def _handle_object_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``object_definition`` → StructNode.

        - ``case object`` → StructNode with ``case`` + ``object`` attrs
        - plain ``object`` → StructNode with ``object`` attr
        - companion detection via name matching
        """
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        annotations = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        bases = _extract_extends_clause(node, src)

        is_case = _has_case_modifier(node)

        attrs: list[str] = list(annotations)
        if is_case:
            attrs.append("case")
        attrs.append("object")

        # Detect companion: check if a class/trait with same name exists
        is_companion = self._is_companion_object(node, name, src)
        if is_companion:
            attrs.append("companion")

        ast.structs.append(StructNode(
            name=name,
            visibility=vis,
            generics="",
            fields=(),
            attributes=tuple(attrs),
            doc=doc,
            span=span_from_node(node),
        ))

        # Body methods → functions (object methods are effectively static)
        methods = self._extract_body_methods(node, src)

        # Body constants
        self._extract_body_constants(node, src, ast)

        # Nested declarations
        self._walk_body_declarations(node, src, ast)

        # Body fields (non-final vals)
        self._extract_body_fields(node, src)
        # We don't attach fields to StructNode for objects since they
        # have no constructor, but we could if needed

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

    def _is_companion_object(
        self, node: Node, name: str, src: bytes,
    ) -> bool:
        """Check if an object has a companion class/trait with the same name."""
        parent = node.parent
        if parent is None:
            return False

        for sibling in parent.children:
            if sibling.id == node.id:
                continue
            if sibling.type in ("class_definition", "trait_definition"):
                sib_name = _get_identifier(sibling, src)
                if sib_name == name:
                    return True
        return False

    # ------------------------------------------------------------------
    # Internal — enum definitions (Scala 3)
    # ------------------------------------------------------------------

    def _handle_enum_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``enum_definition`` → EnumNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node, src)
        generics = _extract_generics(node, src)
        doc = _extract_doc_comment(node, src)
        annotations = _extract_annotations(node, src)

        variants = self._extract_enum_variants(node, src)

        ast.enums.append(EnumNode(
            name=name,
            visibility=vis,
            generics=generics,
            variants=tuple(variants),
            attributes=tuple(annotations),
            doc=doc,
            span=span_from_node(node),
        ))

    def _extract_enum_variants(
        self,
        node: Node,
        src: bytes,
    ) -> list[EnumVariantNode]:
        """Extract variants from ``enum_body``."""
        body = _child_by_type(node, "enum_body")
        if body is None:
            return []

        variants: list[EnumVariantNode] = []
        for case_defs in _children_by_type(body, "enum_case_definitions"):
            for child in case_defs.children:
                if child.type == "simple_enum_case":
                    vname = _get_identifier(child, src)
                    if vname:
                        variants.append(EnumVariantNode(
                            name=vname,
                            kind=EnumVariantKind.UNIT,
                        ))
                elif child.type == "full_enum_case":
                    vname = _get_identifier(child, src)
                    if vname:
                        cp = _child_by_type(child, "class_parameters")
                        kind = (
                            EnumVariantKind.TUPLE
                            if cp is not None
                            else EnumVariantKind.UNIT
                        )
                        variants.append(EnumVariantNode(
                            name=vname,
                            kind=kind,
                        ))
        return variants

    # ------------------------------------------------------------------
    # Internal — top-level functions
    # ------------------------------------------------------------------

    def _handle_top_level_function(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``function_definition`` → FunctionNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        vis = _get_visibility(node, src)
        return_type = _extract_return_type(node, src)
        generics = _extract_generics(node, src)
        annotations = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        params = self._extract_all_params(node, src)

        ast.functions.append(FunctionNode(
            name=name,
            visibility=vis,
            is_async=False,
            params=tuple(params),
            return_type=return_type,
            generics=generics,
            attributes=tuple(annotations),
            doc=doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — top-level vals
    # ------------------------------------------------------------------

    def _handle_top_level_val(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level ``val_definition``.

        ``final val`` or top-level ``val`` with literal → ConstantNode.
        """
        self._add_constant(node, src, ast)

    # ------------------------------------------------------------------
    # Internal — type aliases
    # ------------------------------------------------------------------

    def _handle_type_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``type_definition`` → TypeAliasNode."""
        # type_definition uses type_identifier for the name, not identifier
        ti = _child_by_type(node, "type_identifier")
        name = _node_text(ti, src) if ti is not None else ""
        if not name:
            return

        vis = _get_visibility(node, src)

        # Get the aliased type (after "=")
        aliased = ""
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq and child.type in (
                "type_identifier", "generic_type",
                "function_type", "tuple_type",
                "compound_type", "infix_type",
            ):
                aliased = _node_text(child, src)
                break

        ast.type_aliases.append(TypeAliasNode(
            name=name,
            visibility=vis,
            aliased_to=aliased,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — package object
    # ------------------------------------------------------------------

    def _handle_package_object(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``package_object`` → StructNode with attrs."""
        name = _get_identifier(node, src)
        doc = _extract_doc_comment(node, src)

        ast.structs.append(StructNode(
            name=name,
            visibility=Visibility.PUBLIC,
            generics="",
            fields=(),
            attributes=("package", "object"),
            doc=doc,
            span=span_from_node(node),
        ))

        # Walk body for functions, vals, type aliases
        body = _child_by_type(node, "template_body")
        if body is not None:
            for child in body.children:
                if child.type == "function_definition":
                    self._handle_top_level_function(child, src, ast)
                elif child.type == "val_definition":
                    self._handle_top_level_val(child, src, ast)
                elif child.type == "type_definition":
                    self._handle_type_definition(child, src, ast)

    # ------------------------------------------------------------------
    # Internal — extension definitions (Scala 3)
    # ------------------------------------------------------------------

    def _handle_extension_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``extension_definition`` → MethodNodes with extension attr.

        Extension methods in Scala 3:
        ``extension (s: String) def foo(): Unit = ...``
        """
        # Extract the target type from the extension's parameter
        target_type = ""
        ext_params = _child_by_type(node, "parameters")
        if ext_params is not None:
            param = _child_by_type(ext_params, "parameter")
            if param is not None:
                target_type = _extract_type_after_colon(param, src)

        # Extract methods defined inside the extension
        for child in node.children:
            if child.type == "function_definition":
                method = self._parse_method(child, src)
                if method is not None:
                    attrs = (*method.attributes, "extension")
                    ext_method = MethodNode(
                        name=method.name,
                        visibility=method.visibility,
                        is_async=False,
                        params=method.params,
                        return_type=method.return_type,
                        attributes=attrs,
                        span=method.span,
                    )
                    # Add as impl block with self_type as the target
                    ast.impl_blocks.append(ImplBlockNode(
                        self_type=target_type or "Self",
                        trait_type=None,
                        methods=(ext_method,),
                        span=span_from_node(node),
                    ))

    # ------------------------------------------------------------------
    # Internal — given definitions (Scala 3)
    # ------------------------------------------------------------------

    def _handle_given_definition(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``given_definition`` → ConstantNode."""
        name = _get_identifier(node, src)
        if not name:
            return

        _extract_type_after_colon(node, src)

        raw = _node_text(node, src).strip()
        if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
            raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."

        ast.constants.append(ConstantNode(
            name=name,
            raw=raw,
            visibility=Visibility.PUBLIC,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — nested class walking
    # ------------------------------------------------------------------

    def _walk_body_declarations(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Walk template_body children for nested declarations."""
        body = _child_by_type(node, "template_body")
        if body is None:
            return

        for child in body.children:
            if child.type in (
                "class_definition", "trait_definition",
                "object_definition", "enum_definition",
            ):
                self._dispatch_node(child, src, ast)


# endregion: --- ScalaExtractor class


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

    Scala call_expression: ``obj.method(args)``
    field_expression → identifier . identifier
    """
    # Look for field_expression as the callee
    fe = _child_by_type(node, "field_expression")
    if fe is None:
        return

    children = [c for c in fe.children if c.type != "."]
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
