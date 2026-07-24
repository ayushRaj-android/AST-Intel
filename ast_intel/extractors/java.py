"""Java language extractor — tree-sitter based AST extraction.

Parses ``.java`` files using ``tree-sitter-java`` and maps Java-specific
constructs to the normalized AST schema.

Java → Normalized mapping:
    ================================  =================
    Java Concept                      Normalized To
    ================================  =================
    ``class``                         StructNode
    ``record``                        StructNode
    ``interface``                     TraitNode
    ``abstract class``                TraitNode
    ``class : IFoo``                  ImplBlockNode
    ``enum``                          EnumNode
    ``method`` (free / static)        FunctionNode
    ``method`` (in class)             MethodNode
    ``import x.y.Z``                  uses (raw str)
    ``Type.method()``                 imported_package_methods
    ``const`` / ``static final``      ConstantNode
    ``@Annotation``                   attributes
    ================================  =================

Also parses ``pom.xml`` and ``build.gradle`` manifests via
:meth:`parse_manifest`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

import defusedxml.ElementTree as DET  # type: ignore[import-untyped]  # noqa: N814
from defusedxml import DefusedXmlException
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
    Visibility,
)
from ast_intel.models.workspace_model import CrateDependency, CrateModel

__all__: list[str] = ["JavaExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Java access modifier → Visibility mapping.
_VISIBILITY_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "protected": Visibility.PROTECTED,
    "private": Visibility.PRIVATE,
}

# Non-visibility modifier keywords.
_NON_VIS_MODIFIERS: frozenset[str] = frozenset({
    "static",
    "abstract",
    "final",
    "synchronized",
    "native",
    "strictfp",
    "transient",
    "volatile",
    "default",
    "sealed",
    "non-sealed",
})


# ---------------------------------------------------------------------------
# region:    --- Helper NamedTuple
# ---------------------------------------------------------------------------


class _TypeInfo(NamedTuple):
    """Parsed metadata for a Java type declaration."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool
    is_static: bool


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
    return ""


def _get_visibility(node: Node) -> Visibility:
    """Extract visibility from ``modifiers`` child.

    Java defaults to package-private when no modifier is present.
    We map that to ``CRATE`` (closest analogue).
    """
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return Visibility.CRATE  # package-private
    for child in mods.children:
        vis = _VISIBILITY_MAP.get(child.type)
        if vis is not None:
            return vis
    return Visibility.CRATE  # package-private


def _has_modifier(node: Node, modifier_name: str) -> bool:
    """Check if the declaration has a specific modifier keyword."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return False
    return any(child.type == modifier_name for child in mods.children)


def _extract_annotations(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@Annotation`` values from a declaration's modifiers."""
    mods = _child_by_type(node, "modifiers")
    if mods is None:
        return ()
    return tuple(
        _node_text(child, src)
        for child in mods.children
        if child.type in ("marker_annotation", "annotation")
    )


def _find_preceding_sibling_index(node: Node) -> int:
    """Return the index of *node* in its parent's children, or -1."""
    parent = node.parent
    if parent is None:
        return -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return i
    return -1


def _parse_javadoc_block(text: str) -> str:
    """Parse a ``/** ... */`` block comment into a clean doc string."""
    raw = text[3:].rstrip("*/").strip()
    lines = [ln.strip().lstrip("* ").strip() for ln in raw.splitlines()]
    clean = [ln for ln in lines if ln and not ln.startswith("@")]
    return " ".join(clean)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract Javadoc ``/** ... */`` comment preceding a declaration.

    In tree-sitter-java, block comments appear as ``block_comment``
    siblings immediately before the declaration node.
    """
    idx = _find_preceding_sibling_index(node)
    if idx <= 0:
        return ""

    parent = node.parent
    assert parent is not None  # guaranteed by idx > 0

    sibling = parent.children[idx - 1]
    if sibling.type == "block_comment":
        text = _node_text(sibling, src).strip()
        if text.startswith("/**"):
            return _parse_javadoc_block(text)

    # Also check for line comments (///)
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
    """Extract ``<T, U extends Comparable>`` type parameters."""
    tp = _child_by_type(node, "type_parameters")
    if tp is not None:
        return _node_text(tp, src)
    return ""


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract the return type from a method declaration.

    Java method declarations have the form:
    ``modifiers? type_parameters? return_type name formal_parameters ...``
    """
    type_nodes = {
        "void_type",
        "integral_type",
        "floating_point_type",
        "boolean_type",
        "type_identifier",
        "generic_type",
        "array_type",
        "scoped_type_identifier",
    }
    for child in node.children:
        if child.type in type_nodes:
            return _node_text(child, src)
    return ""


def _extract_field_type(node: Node, src: bytes) -> str:
    """Extract the type from a ``field_declaration``."""
    type_nodes = {
        "integral_type",
        "floating_point_type",
        "boolean_type",
        "type_identifier",
        "generic_type",
        "array_type",
        "scoped_type_identifier",
    }
    for child in node.children:
        if child.type in type_nodes:
            return _node_text(child, src)
    return ""


def _extract_parameters(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract parameters from ``formal_parameters``."""
    fp = _child_by_type(node, "formal_parameters")
    if fp is None:
        return ()

    params: list[ParamNode] = []
    for child in fp.children:
        if child.type in ("formal_parameter", "spread_parameter"):
            pname, ptype = _parse_parameter(child, src)
            if pname:
                params.append(ParamNode(name=pname, type=ptype))
    return tuple(params)


def _parse_parameter(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a single ``formal_parameter`` into (name, type)."""
    ptype = ""
    name = ""
    type_nodes = {
        "integral_type",
        "floating_point_type",
        "boolean_type",
        "type_identifier",
        "generic_type",
        "array_type",
        "scoped_type_identifier",
    }
    for child in node.children:
        if child.type in type_nodes:
            ptype = _node_text(child, src)
        elif child.type == "identifier":
            name = _node_text(child, src)
        elif child.type == "...":
            ptype = ptype + "..."
    return (name, ptype)


def _extract_superclass(node: Node, src: bytes) -> str:
    """Extract ``extends X`` from ``superclass`` child."""
    sc = _child_by_type(node, "superclass")
    if sc is None:
        return ""
    for child in sc.children:
        if child.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
            return _node_text(child, src)
    return ""


def _extract_interfaces(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``implements A, B`` from ``super_interfaces`` child."""
    si = _child_by_type(node, "super_interfaces")
    if si is None:
        return ()
    type_list = _child_by_type(si, "type_list")
    if type_list is None:
        return ()
    return tuple(
        _node_text(child, src)
        for child in type_list.children
        if child.type in ("type_identifier", "generic_type", "scoped_type_identifier")
    )


def _extract_extends_interfaces(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``extends A, B`` from interface ``extends_interfaces`` child."""
    ei = _child_by_type(node, "extends_interfaces")
    if ei is None:
        return ()
    type_list = _child_by_type(ei, "type_list")
    if type_list is None:
        return ()
    return tuple(
        _node_text(child, src)
        for child in type_list.children
        if child.type in ("type_identifier", "generic_type", "scoped_type_identifier")
    )


def _is_static_final(node: Node) -> bool:
    """Check if a ``field_declaration`` is ``static final`` (constant)."""
    return _has_modifier(node, "static") and _has_modifier(node, "final")


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    """Build a mapping from short name → qualified path from import stmts.

    Examples:
        ``import java.util.List`` → ``{"List": "java.util.List"}``
        ``import static java.lang.Math.abs`` → ``{"abs": "java.lang.Math.abs"}``
        ``import java.util.*`` → skipped (wildcard, not resolvable)

    Args:
        uses: List of raw ``import`` statement strings.

    Returns:
        A dict mapping short names to their full qualified path.
    """
    import_map: dict[str, str] = {}
    for stmt in uses:
        cleaned = stmt.rstrip(";").strip()

        # import static x.y.Z.method
        if cleaned.startswith("import static "):
            path = cleaned[len("import static "):].strip()
            short = path.rsplit(".", maxsplit=1)[-1]
            if short != "*":
                import_map[short] = path
            continue

        if cleaned.startswith("import "):
            path = cleaned[len("import "):].strip()
            short = path.rsplit(".", maxsplit=1)[-1]
            if short != "*":
                import_map[short] = path
            continue

    return import_map


# endregion: --- Import map builder


# ---------------------------------------------------------------------------
# region:    --- Self-methods collector
# ---------------------------------------------------------------------------


def _collect_self_methods(ast: FileAST) -> list[MethodNode]:
    """Collect all methods from impl blocks and free functions into a flat list."""
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
# region:    --- JavaExtractor class
# ---------------------------------------------------------------------------


class JavaExtractor(ExtractorBase):
    """Java language extractor using ``tree-sitter-java``.

    Parses ``.java`` source files and ``pom.xml`` / ``build.gradle``
    manifests into the normalized AST schema.
    """

    language_id: str = "java"
    file_extensions: list[str] = [".java"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_java as tsjava

        self._language = Language(tsjava.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.java`` file into the normalized AST.

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

        # Post-pass 5: HTTP route extraction
        from ast_intel.extractors._routes import detect_spring_routes
        ast.routes = detect_spring_routes(ast.functions, ast.self_methods)

        # Post-pass 6: HTTP client call detection
        from ast_intel.extractors._http_calls import (
            detect_spring_http_calls,
        )
        ast.http_calls = detect_spring_http_calls(root, source)

        # Post-pass 7: Cloud SDK client detection
        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_java,
        )

        ast.cloud_resources = detect_cloud_clients_java(root, source)

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a Maven ``pom.xml`` or Gradle ``build.gradle`` manifest.

        Args:
            manifest_path: Absolute path to the manifest file.

        Returns:
            A populated :class:`CrateModel`.
        """
        fname = manifest_path.name
        if fname == "pom.xml":
            return self._parse_pom(manifest_path)
        if fname in ("build.gradle", "build.gradle.kts"):
            return self._parse_gradle(manifest_path)
        return CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="java",
            manifest_path=str(manifest_path),
        )

    # ------------------------------------------------------------------
    # Manifest — Maven POM
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pom(manifest_path: Path) -> CrateModel:
        """Parse ``pom.xml`` using defusedxml."""
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="java",
            manifest_path=str(manifest_path),
        )

        try:
            tree_xml = DET.parse(manifest_path)
            root_el = tree_xml.getroot()
        except OSError as exc:
            logger.warning("Cannot read POM %s: %s", manifest_path, exc)
            return crate
        except (DET.ParseError, DefusedXmlException) as exc:
            logger.warning("Invalid XML in POM %s: %s", manifest_path, exc)
            return crate

        # POM namespace handling
        ns = ""
        tag = root_el.tag
        if tag.startswith("{") and "}" in tag:
            ns = tag[: tag.index("}") + 1]

        # Extract artifactId as name
        artifact_id = root_el.find(f"{ns}artifactId")
        if artifact_id is not None and artifact_id.text:
            crate.name = artifact_id.text

        # Extract version
        version_el = root_el.find(f"{ns}version")
        if version_el is not None and version_el.text:
            crate.version = version_el.text

        # Extract dependencies
        deps: list[CrateDependency] = []
        deps_el = root_el.find(f"{ns}dependencies")
        if deps_el is not None:
            for dep_el in deps_el.findall(f"{ns}dependency"):
                group = dep_el.find(f"{ns}groupId")
                artifact = dep_el.find(f"{ns}artifactId")
                ver = dep_el.find(f"{ns}version")
                scope = dep_el.find(f"{ns}scope")

                if artifact is None or not artifact.text:
                    continue

                dep_name = artifact.text
                if group is not None and group.text:
                    dep_name = f"{group.text}:{artifact.text}"

                is_dev = scope is not None and scope.text == "test"

                deps.append(CrateDependency(
                    name=dep_name,
                    version=ver.text if ver is not None and ver.text else "",
                    is_dev=is_dev,
                ))
        crate.dependencies = deps

        return crate

    # ------------------------------------------------------------------
    # Manifest — Gradle
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_gradle(manifest_path: Path) -> CrateModel:
        """Parse ``build.gradle`` / ``build.gradle.kts`` with regex.

        Extracts ``implementation``, ``api``, ``testImplementation``,
        ``compileOnly`` dependency declarations.
        """
        crate = CrateModel(
            name=manifest_path.parent.name or "unknown",
            language="java",
            manifest_path=str(manifest_path),
        )

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", manifest_path, exc)
            return crate

        # Extract group and version from top-level assignments
        group_m = re.search(r"""group\s*=\s*['"]([^'"]+)['"]""", content)
        version_m = re.search(r"""version\s*=\s*['"]([^'"]+)['"]""", content)
        if group_m:
            crate.name = group_m.group(1).rsplit(".", maxsplit=1)[-1]
        if version_m:
            crate.version = version_m.group(1)

        # Extract dependencies (implementation/api/testImpl etc.)
        _dep_re = re.compile(
            r"""(?:implementation|api|testImplementation|compileOnly|"""
            r"""runtimeOnly|annotationProcessor)\s*"""
            r"""[('"]([^'"()]+)['")]""",
        )
        deps: list[CrateDependency] = []
        test_configs = {"testImplementation", "testCompileOnly", "testRuntimeOnly"}

        for m in _dep_re.finditer(content):
            coord = m.group(1).strip()
            parts = coord.split(":")
            if len(parts) >= 2:  # noqa: PLR2004
                dep_name = f"{parts[0]}:{parts[1]}"
                dep_version = parts[2] if len(parts) > 2 else ""  # noqa: PLR2004
                # Determine if test dependency from the matched configuration
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
        """Walk the tree, dispatching to handlers for each top-level node."""
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

        if ntype == "package_declaration":
            self._handle_package(node, src, ast)

        elif ntype == "import_declaration":
            self._handle_import(node, src, ast)

        elif ntype == "class_declaration":
            self._handle_class(node, src, ast)

        elif ntype == "interface_declaration":
            self._handle_interface(node, src, ast)

        elif ntype == "enum_declaration":
            self._handle_enum(node, src, ast)

        elif ntype == "record_declaration":
            self._handle_record(node, src, ast)

        elif ntype == "annotation_type_declaration":
            # Annotation declarations (@interface) → skip for now
            pass

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
        """Handle ``package`` declaration → module_path."""
        raw = _node_text(node, src).strip().rstrip(";").strip()
        if raw.startswith("package "):
            ast.module_path = raw[len("package "):].strip()

    def _handle_import(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``import`` declaration → uses list."""
        raw = _node_text(node, src).strip()
        ast.uses.append(raw)

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
        Inheritance/implementation → ImplBlockNode entries.
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
        ast.structs.append(StructNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            fields=fields,
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

        # Constants (static final fields) → ConstantNode
        self._extract_class_constants(node, src, ast)

        # Methods
        methods = self._extract_class_methods(node, src)

        if info.bases:
            for base in info.bases:
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
        body = _child_by_type(node, "class_body")
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

    def _parse_type_info(self, node: Node, src: bytes) -> _TypeInfo:
        """Extract shared metadata from a type declaration."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node)
        generics = _extract_generics(node, src)
        attributes = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        is_abstract = _has_modifier(node, "abstract")
        is_static = _has_modifier(node, "static")

        # Build bases list: superclass + interfaces
        bases: list[str] = []
        superclass = _extract_superclass(node, src)
        if superclass:
            bases.append(superclass)
        bases.extend(_extract_interfaces(node, src))

        return _TypeInfo(
            name=name,
            bases=tuple(bases),
            generics=generics,
            attributes=attributes,
            doc=doc,
            vis=vis,
            is_abstract=is_abstract,
            is_static=is_static,
        )

    def _extract_class_fields(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract non-constant fields from a class body."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type == "field_declaration" and not _is_static_final(child):
                ftype = _extract_field_type(child, src)
                vis = _get_visibility(child)
                # Get variable name from variable_declarator
                vd = _child_by_type(child, "variable_declarator")
                fname = ""
                if vd is not None:
                    ident = _child_by_type(vd, "identifier")
                    if ident is not None:
                        fname = _node_text(ident, src)
                if fname:
                    fields.append(FieldNode(
                        name=fname,
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
        """Extract ``static final`` fields as ConstantNode."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return

        for child in body.children:
            if child.type == "field_declaration" and _is_static_final(child):
                vis = _get_visibility(child)
                vd = _child_by_type(child, "variable_declarator")
                fname = ""
                if vd is not None:
                    ident = _child_by_type(vd, "identifier")
                    if ident is not None:
                        fname = _node_text(ident, src)
                if fname:
                    raw = _node_text(child, src).strip()
                    if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
                        raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "..."
                    ast.constants.append(ConstantNode(
                        name=fname,
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
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
            elif child.type == "constructor_declaration":
                m = self._extract_constructor(child, src)
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
        vis = _get_visibility(node)
        rtype = _extract_return_type(node, src)
        params = _extract_parameters(node, src)
        attrs = _extract_annotations(node, src)
        is_async = False  # Java doesn't have async keyword; check CompletableFuture
        if "CompletableFuture" in rtype or "Future" in rtype:
            is_async = True

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=is_async,
            params=params,
            return_type=rtype,
            attributes=attrs,
            span=span_from_node(node),
        )

    def _extract_constructor(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Extract a constructor declaration → MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None
        vis = _get_visibility(node)
        params = _extract_parameters(node, src)

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=False,
            params=params,
            return_type="",
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

        is_abstract = _has_modifier(node, "abstract")
        is_default = _has_modifier(node, "default")
        # If it has no body (ends with ;), it's required (interface method)
        has_body = _child_by_type(node, "block") is not None

        if is_abstract or not has_body:
            kind = TraitItemKind.REQUIRED_METHOD
        elif is_default or has_body:
            kind = TraitItemKind.DEFAULT_METHOD
        else:
            kind = TraitItemKind.REQUIRED_METHOD

        return TraitItemNode(
            kind=kind,
            name=name,
            is_async=False,
            params=params,
            return_type=rtype,
            doc=doc,
            span=span_from_node(node),
        )

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
        vis = _get_visibility(node)
        generics = _extract_generics(node, src)
        attributes = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)
        super_traits = _extract_extends_interfaces(node, src)

        items = self._extract_interface_items(node, src)

        ast.traits.append(TraitNode(
            name=name,
            visibility=vis,
            generics=generics,
            super_traits=super_traits,
            items=items,
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

    def _extract_interface_items(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[TraitItemNode, ...]:
        """Extract method signatures from an interface body."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "interface_body")
        if body is None:
            return ()

        for child in body.children:
            if child.type == "method_declaration":
                item = self._extract_trait_method_item(child, src)
                if item is not None:
                    items.append(item)
        return tuple(items)

    # ------------------------------------------------------------------
    # Internal — enum declarations
    # ------------------------------------------------------------------

    def _handle_enum(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``enum_declaration`` → EnumNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node)
        generics = _extract_generics(node, src)
        attributes = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)

        variants: list[EnumVariantNode] = []
        body = _child_by_type(node, "enum_body")
        if body is not None:
            for child in body.children:
                if child.type == "enum_constant":
                    vname = _get_identifier(child, src)
                    # Enum constants with arguments are tuple-like
                    args = _child_by_type(child, "argument_list")
                    kind = EnumVariantKind.TUPLE if args else EnumVariantKind.UNIT
                    variants.append(EnumVariantNode(
                        name=vname,
                        kind=kind,
                    ))

        ast.enums.append(EnumNode(
            name=name,
            visibility=vis,
            generics=generics,
            variants=tuple(variants),
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

        # Enum methods → ImplBlockNode
        methods = self._extract_enum_methods(node, src)
        if methods:
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
        """Extract methods from ``enum_body_declarations``."""
        body = _child_by_type(node, "enum_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        body_decls = _child_by_type(body, "enum_body_declarations")
        if body_decls is not None:
            for child in body_decls.children:
                if child.type == "method_declaration":
                    m = self._extract_method(child, src)
                    if m is not None:
                        methods.append(m)
                elif child.type == "constructor_declaration":
                    m = self._extract_constructor(child, src)
                    if m is not None:
                        methods.append(m)
        return tuple(methods)

    # ------------------------------------------------------------------
    # Internal — record declarations
    # ------------------------------------------------------------------

    def _handle_record(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``record_declaration`` → StructNode."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node)
        generics = _extract_generics(node, src)
        attributes = _extract_annotations(node, src)
        doc = _extract_doc_comment(node, src)

        # Record parameters → fields
        fields = self._extract_record_params(node, src)

        ast.structs.append(StructNode(
            name=name,
            visibility=vis,
            generics=generics,
            fields=fields,
            attributes=attributes,
            doc=doc,
            span=span_from_node(node),
        ))

        # Record methods
        methods = self._extract_record_methods(node, src)
        if methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=name,
                trait_type="",
                generics=generics,
                methods=methods,
                span=span_from_node(node),
            ))

    def _extract_record_params(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract record components as fields."""
        fp = _child_by_type(node, "formal_parameters")
        if fp is None:
            return ()

        fields: list[FieldNode] = []
        for child in fp.children:
            if child.type == "formal_parameter":
                pname, ptype = _parse_parameter(child, src)
                if pname:
                    fields.append(FieldNode(
                        name=pname,
                        type=ptype,
                        visibility=Visibility.PUBLIC,
                    ))
        return tuple(fields)

    def _extract_record_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from a record's class body."""
        body = _child_by_type(node, "class_body")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                m = self._extract_method(child, src)
                if m is not None:
                    methods.append(m)
        return tuple(methods)


# endregion: --- JavaExtractor class


# ---------------------------------------------------------------------------
# region:    --- Post-pass: imported_package_methods
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Detect ``Type.method()`` calls using the import map.

    Walks the AST looking for ``method_invocation`` nodes where the
    object part resolves to a known import.
    """
    result: dict[str, list[str]] = {}
    _walk_for_invocations(root, src, import_map, result)
    return result


def _walk_for_invocations(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Recursively walk for ``method_invocation`` nodes."""
    if node.type == "method_invocation":
        _resolve_invocation(node, src, import_map, result)

    for child in node.children:
        _walk_for_invocations(child, src, import_map, result)


def _resolve_invocation(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Try to resolve a single method_invocation into a scoped method call.

    Java method_invocation has children:
    - object (identifier or field_access) . name (identifier) argument_list
    """
    # In method_invocation: object.method(args)
    # Filter out punctuation and argument_list to get [object, method_name]
    children = [c for c in node.children if c.type not in (".", "argument_list", "(", ")")]
    if len(children) < 2:  # noqa: PLR2004
        return

    # First child is the object, last identifier is the method name
    object_node = children[0]
    method_name = _node_text(children[-1], src) if children[-1].type == "identifier" else ""
    if not method_name:
        return

    # Get the object text
    obj_text = _node_text(object_node, src)
    if not obj_text:
        return

    # Resolve via import map: the leftmost identifier
    leftmost = obj_text.split(".")[0]
    if leftmost in import_map:
        qualified = import_map[leftmost]
        result.setdefault(qualified, [])
        if method_name not in result[qualified]:
            result[qualified].append(method_name)


# endregion: --- Post-pass: imported_package_methods
