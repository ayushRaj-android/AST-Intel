"""C# / .NET language extractor — tree-sitter based AST extraction.

Parses ``.cs`` files using ``tree-sitter-c-sharp`` and maps C#-specific
constructs to the normalized AST schema.

C# → Normalized mapping:
    ================================  =================
    C# Concept                        Normalized To
    ================================  =================
    ``class``                         StructNode
    ``record``                        StructNode
    ``struct`` (value type)           StructNode
    ``interface``                     TraitNode
    ``abstract class``                TraitNode
    ``class : IFoo``                  ImplBlockNode
    ``enum``                          EnumNode
    ``method`` (free / static)        FunctionNode
    ``method`` (in class)             MethodNode
    ``using X;``                      uses (raw str)
    ``Type.Method()``                 imported_package_methods
    ``using Alias = Type``            TypeAliasNode
    ``const`` / ``static readonly``   ConstantNode
    ``[Attribute]``                    attributes
    ================================  =================

Also parses ``.csproj`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

import defusedxml.ElementTree as DET  # type: ignore[import-untyped]  # noqa: N814
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

__all__: list[str] = ["CSharpExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews in extracted nodes.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Minimum number of consecutive `identifier` children needed to
# distinguish a type identifier from a name identifier (e.g. `Guid Id`).
_MIN_IDENTS_FOR_TYPE_NAME = 2

# Access modifier → Visibility mapping.
_VISIBILITY_MAP: dict[str, Visibility] = {
    "public": Visibility.PUBLIC,
    "internal": Visibility.CRATE,
    "protected": Visibility.PROTECTED,
    "private": Visibility.PRIVATE,
}

# Modifier keywords that are NOT visibility modifiers.
_NON_VIS_MODIFIERS: frozenset[str] = frozenset({
    "static",
    "abstract",
    "virtual",
    "override",
    "sealed",
    "readonly",
    "async",
    "new",
    "partial",
    "extern",
    "volatile",
    "unsafe",
    "required",
    "const",
    "ref",
    "event",
})


class _TypeInfo(NamedTuple):
    """Parsed metadata for a C# type declaration, reducing argument passing."""

    name: str
    bases: tuple[str, ...]
    generics: str
    attributes: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool
    is_static: bool


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
    """Extract the name identifier from a declaration node.

    For declarations like ``public Guid Id { get; set; }`` or
    ``public void Process(...)``, the name is the ``identifier`` child
    that comes AFTER any type nodes.  We find the identifier immediately
    before ``parameter_list``, ``accessor_list``, ``base_list``,
    ``declaration_list``, ``arrow_expression_clause``, ``enum_member_declaration_list``,
    or the last identifier if none of those follow.
    """
    # For nodes with parameter_list, the identifier right before it is the name
    last_ident: Node | None = None
    delimiter_nodes = {
        "parameter_list",
        "accessor_list",
        "base_list",
        "declaration_list",
        "arrow_expression_clause",
        "enum_member_declaration_list",
        "type_parameter_list",
        ";",
    }
    for child in node.children:
        if child.type == "identifier":
            last_ident = child
        elif child.type in delimiter_nodes and last_ident is not None:
            return _node_text(last_ident, src)
    # Fallback: return last identifier found
    if last_ident is not None:
        return _node_text(last_ident, src)
    return ""


def _get_visibility(node: Node) -> Visibility:
    """Extract visibility from modifier children.

    C# uses explicit access modifiers: ``public``, ``private``, ``internal``,
    ``protected``.  If no access modifier present, defaults to ``private``
    (closest to C# defaults for members).
    """
    for child in node.children:
        if child.type == "modifier":
            keyword = child.children[0].type if child.children else ""
            vis = _VISIBILITY_MAP.get(keyword)
            if vis is not None:
                return vis
    return Visibility.PRIVATE


def _has_modifier(node: Node, modifier_name: str) -> bool:
    """Check if the declaration has a specific modifier keyword."""
    for child in node.children:
        if child.type == "modifier":
            keyword = child.children[0].type if child.children else ""
            if keyword == modifier_name:
                return True
    return False


def _extract_attributes(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``[Attribute]`` annotations from a declaration.

    Handles both ``attribute_list`` on the node itself and on a parent
    decorated definition.
    """
    attrs: list[str] = []
    for child in node.children:
        if child.type == "attribute_list":
            # Each attribute_list: [Attr1, Attr2]
            attrs.extend(
                _node_text(attr, src)
                for attr in child.children
                if attr.type == "attribute"
            )
    return tuple(attrs)


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Extract XML doc comment (``///``) preceding a declaration.

    In tree-sitter-c-sharp, doc comments appear as ``comment`` siblings
    immediately before the declaration node.
    """
    parent = node.parent
    if parent is None:
        return ""

    # Find this node's index among its parent's children
    idx = -1
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            idx = i
            break
    if idx <= 0:
        return ""

    # Collect contiguous /// comment lines above
    lines: list[str] = []
    for i in range(idx - 1, -1, -1):
        sibling = parent.children[i]
        if sibling.type == "comment":
            text = _node_text(sibling, src).strip()
            if text.startswith("///"):
                lines.append(text[3:].strip())
            else:
                break
        else:
            break

    if not lines:
        return ""
    lines.reverse()

    # Strip XML tags for cleaner doc text
    raw = " ".join(lines)
    return re.sub(r"<[^>]+>", "", raw).strip()


def _extract_type_text(node: Node, src: bytes) -> str:
    """Extract the return type or field type from a declaration.

    Handles: predefined_type, generic_name, nullable_type, array_type,
    tuple_type, qualified_name, ref_type.

    For method/property declarations where the type is a plain ``identifier``
    (e.g., ``Guid``), the type identifier is the one that comes BEFORE the
    name identifier.  We skip ``identifier`` children that are followed by
    ``parameter_list`` or ``accessor_list`` (those are names, not types).
    """
    type_nodes = {
        "predefined_type",
        "generic_name",
        "nullable_type",
        "array_type",
        "tuple_type",
        "qualified_name",
        "ref_type",
    }
    for child in node.children:
        if child.type in type_nodes:
            return _node_text(child, src)

    # Fallback: look for identifier used as a type.
    # In declaration nodes, the type identifier is the FIRST identifier
    # (before the name identifier).
    identifiers = [c for c in node.children if c.type == "identifier"]
    if len(identifiers) >= _MIN_IDENTS_FOR_TYPE_NAME:
        # First is type, second is name
        return _node_text(identifiers[0], src)
    return ""


def _extract_generics(node: Node, src: bytes) -> str:
    """Extract generic type parameters ``<T, U>`` from a declaration."""
    tp = _child_by_type(node, "type_parameter_list")
    if tp is not None:
        return _node_text(tp, src)
    return ""


def _extract_method_return_type(
    node: Node,
    src: bytes,
    method_name: str,
) -> str:
    """Extract return type from a method declaration.

    For methods where the return type is a user-defined identifier (e.g.,
    ``Guid`` or ``Job``), we need to distinguish it from the method name
    identifier.  The return type is the first type/identifier child that
    is NOT the method name.
    """
    type_nodes = {
        "predefined_type",
        "generic_name",
        "nullable_type",
        "array_type",
        "tuple_type",
        "qualified_name",
        "ref_type",
    }
    for child in node.children:
        if child.type in type_nodes:
            return _node_text(child, src)

    # Check for identifier-as-type: the first identifier that isn't the
    # method name itself
    for child in node.children:
        if child.type == "identifier":
            text = _node_text(child, src)
            if text != method_name:
                return text
    return ""


def _extract_base_list(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract base types from ``base_list`` (inheritance / implementation).

    Returns a tuple of base type names.
    """
    bl = _child_by_type(node, "base_list")
    if bl is None:
        return ()
    return tuple(
        _node_text(child, src)
        for child in bl.children
        if child.type in ("identifier", "generic_name", "qualified_name")
    )


def _extract_parameters(node: Node, src: bytes) -> tuple[ParamNode, ...]:
    """Extract parameters from a ``parameter_list`` child.

    Handles typed parameters, default values, ``params`` keyword, ``this``
    extension method parameter, ref/out/in modifiers.
    """
    param_list = _child_by_type(node, "parameter_list")
    if param_list is None:
        return ()

    params: list[ParamNode] = []
    for child in param_list.children:
        if child.type == "parameter":
            pname, ptype = _parse_parameter(child, src)
            if pname:
                params.append(ParamNode(name=pname, type=ptype))
    return tuple(params)


def _parse_parameter(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a single ``parameter`` node into (name, type).

    Parameter structure: ``modifier? type_node identifier (= default)?``
    where type_node is predefined_type, generic_name, or identifier.
    When the type is an identifier (e.g., ``Guid id``), there are two
    consecutive identifier children: type first, name second.
    """
    ptype = ""
    name = ""
    type_nodes = {
        "predefined_type",
        "generic_name",
        "nullable_type",
        "array_type",
        "tuple_type",
        "qualified_name",
        "ref_type",
    }

    identifiers: list[Node] = []
    for child in node.children:
        if child.type in type_nodes:
            ptype = _node_text(child, src)
        elif child.type == "identifier":
            identifiers.append(child)

    if ptype and identifiers:
        # Type was explicit → name is the last identifier
        name = _node_text(identifiers[-1], src)
    elif len(identifiers) >= _MIN_IDENTS_FOR_TYPE_NAME:
        # Two identifiers: first is type (e.g., Guid), second is name (e.g., id)
        ptype = _node_text(identifiers[0], src)
        name = _node_text(identifiers[-1], src)
    elif identifiers:
        name = _node_text(identifiers[0], src)

    return (name, ptype)


def _extract_record_params(node: Node, src: bytes) -> tuple[FieldNode, ...]:
    """Extract positional record parameters as fields.

    For ``public record Point(int X, int Y);``, the ``parameter_list``
    on the record declaration itself defines the positional fields.
    """
    param_list = _child_by_type(node, "parameter_list")
    if param_list is None:
        return ()

    fields: list[FieldNode] = []
    for child in param_list.children:
        if child.type == "parameter":
            pname, ptype = _parse_parameter(child, src)
            if pname:
                fields.append(FieldNode(
                    name=pname,
                    type=ptype,
                    visibility=Visibility.PUBLIC,
                ))
    return tuple(fields)


def _is_const_field(node: Node) -> bool:
    """Check if a ``field_declaration`` is ``const``."""
    return _has_modifier(node, "const")


def _is_static_readonly_field(node: Node) -> bool:
    """Check if a ``field_declaration`` is ``static readonly``."""
    return _has_modifier(node, "static") and _has_modifier(node, "readonly")


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


def build_import_map(uses: list[str]) -> dict[str, str]:
    """Build a mapping from short name → qualified path from ``using`` stmts.

    Examples:
        ``using System.Text.Json`` → ``{"Json": "System.Text.Json"}``
        ``using static System.Math`` → ``{"Math": "System.Math"}``
        ``using Alias = Namespace.Type`` → ``{"Alias": "Namespace.Type"}``

    Args:
        uses: List of raw ``using`` statement strings.

    Returns:
        A dict mapping short names to their full namespace/type path.
    """
    import_map: dict[str, str] = {}
    for stmt in uses:
        cleaned = stmt.rstrip(";").strip()

        # Skip global directives like "global using ..."
        cleaned = cleaned.removeprefix("global ")

        # using static X.Y.Z → Z maps to X.Y.Z
        if cleaned.startswith("using static "):
            path = cleaned[len("using static "):].strip()
            short = path.rsplit(".", maxsplit=1)[-1]
            import_map[short] = path
            continue

        # using Alias = Namespace.Type
        if cleaned.startswith("using ") and "=" in cleaned:
            rest = cleaned[len("using "):].strip()
            alias, _, target = rest.partition("=")
            import_map[alias.strip()] = target.strip()
            continue

        # using Namespace.Type → Type maps to Namespace.Type
        if cleaned.startswith("using "):
            path = cleaned[len("using "):].strip()
            short = path.rsplit(".", maxsplit=1)[-1]
            import_map[short] = path
            continue

    return import_map


# endregion: --- Import map builder


# ---------------------------------------------------------------------------
# region:    --- CSharpExtractor class
# ---------------------------------------------------------------------------


class CSharpExtractor(ExtractorBase):
    """C# language extractor using ``tree-sitter-c-sharp``.

    Parses ``.cs`` source files and ``.csproj`` manifests
    into the normalized AST schema.
    """

    language_id: str = "csharp"
    file_extensions: list[str] = [".cs"]  # noqa: RUF012

    def __init__(self) -> None:
        import tree_sitter_c_sharp as tscsharp

        self._language = Language(tscsharp.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------
    # Public API — ExtractorBase contract
    # ------------------------------------------------------------------

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a ``.cs`` file into the normalized AST.

        Args:
            file_path: Absolute path to the source file.
            source: Raw file contents as UTF-8 bytes.

        Returns:
            A populated :class:`FileAST`.
        """
        tree = self._parser.parse(source)
        root = tree.root_node
        ast = FileAST(file=str(file_path))

        # Walk top-level declarations (handles namespace nesting)
        self._walk_top_level(root, source, ast)

        # Post-pass 1: Collect self_methods from impl_blocks + functions
        ast.self_methods = _collect_self_methods(ast)

        # Post-pass 2: Build import map and extract scoped method calls
        import_map = build_import_map(ast.uses)
        ast.imported_package_methods = _extract_scoped_method_calls(
            root, source, import_map,
        )

        # Post-pass 3: rationale comments
        ast.rationale_comments = extract_rationale_comments(
            root, source,
        )

        # Post-pass 4: intra-file call graph
        defined_names = _collect_defined_names(ast)
        ast.call_edges = extract_call_edges(
            root, source, defined_names,
        )

        # Post-pass 5: HTTP route extraction (ASP.NET MVC/Web API + minimal APIs)
        from ast_intel.extractors._routes import (
            detect_aspnet_routes,
            detect_csharp_minimal_api_routes,
        )

        ast.routes = detect_aspnet_routes(root, source)
        ast.routes += detect_csharp_minimal_api_routes(root, source)

        # Post-pass 6: Cloud SDK client detection
        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_csharp,
        )

        ast.cloud_resources = detect_cloud_clients_csharp(root, source)

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``.csproj`` manifest file.

        Extracts project name (from filename), target framework, and
        ``PackageReference`` items as dependencies.

        Args:
            manifest_path: Absolute path to the ``.csproj`` file.

        Returns:
            A populated :class:`CrateModel`.
        """
        name = manifest_path.stem
        crate = CrateModel(
            name=name,
            language="csharp",
            manifest_path=str(manifest_path),
        )

        try:
            tree_xml = DET.parse(manifest_path)
            root_el = tree_xml.getroot()
        except (OSError, DET.ParseError) as exc:
            logger.warning("Cannot parse %s: %s", manifest_path, exc)
            return crate

        # Extract TargetFramework
        for prop_group in root_el.iter("PropertyGroup"):
            tf = prop_group.find("TargetFramework")
            if tf is not None and tf.text:
                crate.version = tf.text
                break

        # Extract PackageReferences
        deps: list[CrateDependency] = []
        for pkg_ref in root_el.iter("PackageReference"):
            pkg_name = pkg_ref.get("Include", "")
            if not pkg_name:
                continue
            version = pkg_ref.get("Version", "")
            is_dev = False
            # Check for PrivateAssets=all → dev dependency
            priv_assets = pkg_ref.find("PrivateAssets")
            if priv_assets is not None and priv_assets.text == "all":
                is_dev = True
            deps.append(CrateDependency(
                name=pkg_name,
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
        """Walk the tree, dispatching to handlers for each top-level node.

        Handles both ``compilation_unit`` root and namespace body contents.
        """
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

        if ntype == "using_directive":
            self._handle_using(node, src, ast)

        elif ntype in (
            "namespace_declaration",
            "file_scoped_namespace_declaration",
        ):
            self._handle_namespace(node, src, ast)

        elif ntype == "class_declaration":
            self._handle_class(node, src, ast)

        elif ntype == "interface_declaration":
            self._handle_interface(node, src, ast)

        elif ntype == "enum_declaration":
            self._handle_enum(node, src, ast)

        elif ntype == "struct_declaration":
            self._handle_struct_type(node, src, ast)

        elif ntype == "record_declaration":
            self._handle_record(node, src, ast)

        elif ntype == "method_declaration":
            # Top-level method (rare in C# — usually inside a class)
            self._handle_free_method(node, src, ast)

        elif ntype == "field_declaration":
            self._handle_top_level_field(node, src, ast)

        elif ntype == "ERROR":
            text = _node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]
            ast.errors.append(f"Parse error near: {text}")

    # ------------------------------------------------------------------
    # Internal — using directives
    # ------------------------------------------------------------------

    def _handle_using(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``using`` directive → ``uses`` list or ``TypeAliasNode``."""
        raw = _node_text(node, src).strip().rstrip(";").strip()

        # using Alias = Type → both uses AND type alias
        if "=" in raw and raw.startswith("using "):
            rest = raw[len("using "):].strip()
            alias_name, _, target = rest.partition("=")
            alias_name = alias_name.strip()
            target = target.strip()
            ast.type_aliases.append(TypeAliasNode(
                name=alias_name,
                aliased_to=target,
                visibility=Visibility.PRIVATE,
                span=span_from_node(node),
            ))

        ast.uses.append(_node_text(node, src).strip())

    # ------------------------------------------------------------------
    # Internal — namespace
    # ------------------------------------------------------------------

    def _handle_namespace(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Descend into a namespace declaration.

        Not a node itself, just a container whose children get dispatched.
        """
        body = _child_by_type(node, "declaration_list")
        if body is not None:
            self._walk_top_level(body, src, ast)
        else:
            # File-scoped namespace: children are direct
            for child in node.children:
                if child.type not in (
                    "namespace",
                    "identifier",
                    "qualified_name",
                    ";",
                ):
                    self._dispatch_node(child, src, ast)

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

        Also creates ImplBlockNode(s) for interface implementations.

        Abstract classes map to TraitNode. Concrete classes map to StructNode.
        Interface implementations create ImplBlockNode entries.
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

        # Constants and static readonly fields → ConstantNode
        self._extract_class_constants(node, src, ast)

        # Methods inside the class
        methods = self._extract_class_methods(node, src)

        if info.bases:
            # Each base type → one ImplBlockNode
            for base in info.bases:
                ast.impl_blocks.append(ImplBlockNode(
                    self_type=info.name,
                    trait_type=base,
                    generics=info.generics,
                    methods=methods,
                    span=span_from_node(node),
                ))
        elif methods:
            # Inherent impl block — classes with methods get one
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
        """Extract an ``abstract class`` → TraitNode.

        Abstract methods → REQUIRED_METHOD, virtual/concrete → DEFAULT_METHOD.
        """
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
        info = self._parse_type_info(node, src)
        items = self._extract_interface_items(node, src)

        ast.traits.append(TraitNode(
            name=info.name,
            visibility=info.vis,
            generics=info.generics,
            super_traits=info.bases,
            items=items,
            attributes=info.attributes,
            doc=info.doc,
            span=span_from_node(node),
        ))

    def _extract_interface_items(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[TraitItemNode, ...]:
        """Extract method/property signatures from an interface body."""
        items: list[TraitItemNode] = []
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return ()

        for child in body.children:
            if child.type == "method_declaration":
                mname = _get_identifier(child, src)
                rtype = _extract_method_return_type(child, src, mname)
                params = _extract_parameters(child, src)
                is_async = rtype.startswith("Task") or _has_modifier(
                    child, "async",
                )
                doc = _extract_doc_comment(child, src)
                items.append(TraitItemNode(
                    kind=TraitItemKind.REQUIRED_METHOD,
                    name=mname,
                    is_async=is_async,
                    params=params,
                    return_type=rtype,
                    doc=doc,
                    span=span_from_node(child),
                ))
            elif child.type == "property_declaration":
                field = CSharpExtractor._extract_property_field(child, src)
                if field is not None:
                    doc = _extract_doc_comment(child, src)
                    items.append(TraitItemNode(
                        kind=TraitItemKind.REQUIRED_METHOD,
                        name=field.name,
                        return_type=field.type,
                        doc=doc,
                        span=span_from_node(child),
                    ))

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
        """Handle ``enum_declaration`` → EnumNode with variants."""
        name = _get_identifier(node, src)
        vis = _get_visibility(node)
        doc = _extract_doc_comment(node, src)
        attrs = _extract_attributes(node, src)

        variants: list[EnumVariantNode] = []
        member_list = _child_by_type(node, "enum_member_declaration_list")
        if member_list is not None:
            for child in member_list.children:
                if child.type == "enum_member_declaration":
                    vname = _get_identifier(child, src)
                    if vname:
                        variants.append(EnumVariantNode(
                            name=vname,
                            kind=EnumVariantKind.UNIT,
                        ))

        ast.enums.append(EnumNode(
            name=name,
            visibility=vis,
            variants=tuple(variants),
            attributes=attrs,
            doc=doc,
            span=span_from_node(node),
        ))

    # ------------------------------------------------------------------
    # Internal — struct (value type) declarations
    # ------------------------------------------------------------------

    def _handle_struct_type(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``struct_declaration`` → StructNode + ImplBlockNode."""
        info = self._parse_type_info(node, src)
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

        methods = self._extract_class_methods(node, src)
        if methods:
            ast.impl_blocks.append(ImplBlockNode(
                self_type=info.name,
                trait_type="",
                generics=info.generics,
                methods=methods,
                span=span_from_node(node),
            ))

    # ------------------------------------------------------------------
    # Internal — record declarations
    # ------------------------------------------------------------------

    def _handle_record(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle ``record_declaration`` → StructNode.

        Positional records ``record Point(int X, int Y)`` extract
        parameters as fields.
        """
        info = self._parse_type_info(node, src)

        # Record positional params → fields
        fields = _extract_record_params(node, src)

        # Also check for body fields
        body_fields = self._extract_class_fields(node, src)
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

    # ------------------------------------------------------------------
    # Internal — free (top-level) methods/fields
    # ------------------------------------------------------------------

    def _handle_free_method(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle a top-level ``method_declaration`` → FunctionNode.

        Rare in C# (usually inside a class), but file-scoped methods
        in newer C# are possible.
        """
        fn = self._extract_function_node(node, src)
        if fn is not None:
            ast.functions.append(fn)

    def _handle_top_level_field(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle a top-level ``field_declaration`` (constant/static)."""
        if _is_const_field(node) or _is_static_readonly_field(node):
            cnode = self._extract_constant_from_field(node, src)
            if cnode is not None:
                ast.constants.append(cnode)

    # ------------------------------------------------------------------
    # Internal — helper: parse type info
    # ------------------------------------------------------------------

    def _parse_type_info(
        self,
        node: Node,
        src: bytes,
    ) -> _TypeInfo:
        """Extract common metadata from a class/struct/record/interface node."""
        name = _get_identifier(node, src)
        # Records may not have "identifier" directly if using generic_name
        if not name:
            gn = _child_by_type(node, "generic_name")
            if gn is not None:
                ident = _child_by_type(gn, "identifier")
                name = _node_text(ident, src) if ident else ""

        vis = _get_visibility(node)
        generics = _extract_generics(node, src)
        bases = _extract_base_list(node, src)
        attributes = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)
        is_abstract = _has_modifier(node, "abstract")
        is_static = _has_modifier(node, "static")

        return _TypeInfo(
            name=name,
            bases=bases,
            generics=generics,
            attributes=attributes,
            doc=doc,
            vis=vis,
            is_abstract=is_abstract,
            is_static=is_static,
        )

    # ------------------------------------------------------------------
    # Internal — field extraction
    # ------------------------------------------------------------------

    def _extract_class_fields(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract fields from a class/struct/record body.

        Includes:
        - Property declarations (``public string Name { get; set; }``)
        - Regular field declarations (``private readonly int _count;``)

        Excludes: const and static readonly (those are constants).
        """
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return ()

        fields: list[FieldNode] = []
        for child in body.children:
            if child.type == "property_declaration":
                field = self._extract_property_field(child, src)
                if field is not None:
                    fields.append(field)
            elif child.type == "field_declaration":
                # Skip const/static readonly — those become ConstantNode
                if _is_const_field(child) or _is_static_readonly_field(child):
                    continue
                extracted = self._extract_field_declaration(child, src)
                fields.extend(extracted)

        return tuple(fields)

    @staticmethod
    def _extract_property_field(
        node: Node,
        src: bytes,
    ) -> FieldNode | None:
        """Extract a property declaration as a FieldNode.

        Property structure: ``modifier? type_node identifier accessor_list``
        where type_node can be ``predefined_type``, ``identifier``,
        ``generic_name``, ``nullable_type``, etc.
        """
        vis = _get_visibility(node)
        ptype = ""
        pname = ""

        # Walk children: the LAST identifier before accessor_list /
        # arrow_expression_clause is the property name; the type comes
        # before it.
        identifiers: list[Node] = []
        type_node: Node | None = None
        type_nodes = {
            "predefined_type",
            "generic_name",
            "nullable_type",
            "array_type",
            "tuple_type",
            "qualified_name",
            "ref_type",
        }
        for child in node.children:
            if child.type in type_nodes:
                type_node = child
            elif child.type == "identifier":
                identifiers.append(child)

        if type_node is not None:
            ptype = _node_text(type_node, src)
            # Name is the first identifier AFTER the type node
            pname = _node_text(identifiers[-1], src) if identifiers else ""
        elif len(identifiers) >= _MIN_IDENTS_FOR_TYPE_NAME:
            # No predefined type — type is first identifier (e.g., Guid),
            # name is second identifier (e.g., Id)
            ptype = _node_text(identifiers[0], src)
            pname = _node_text(identifiers[-1], src)
        elif identifiers:
            pname = _node_text(identifiers[0], src)

        if pname:
            return FieldNode(name=pname, type=ptype, visibility=vis)
        return None

    @staticmethod
    def _extract_field_declaration(
        node: Node,
        src: bytes,
    ) -> list[FieldNode]:
        """Extract field(s) from a ``field_declaration``.

        A single declaration can have multiple variables:
        ``private int x, y;``
        """
        vis = _get_visibility(node)
        ftype = ""
        var_decl = _child_by_type(node, "variable_declaration")
        if var_decl is None:
            return []

        ftype = _extract_type_text(var_decl, src)
        fields: list[FieldNode] = []
        for vd in _children_by_type(var_decl, "variable_declarator"):
            vname = _get_identifier(vd, src)
            if vname:
                fields.append(FieldNode(
                    name=vname, type=ftype, visibility=vis,
                ))
        return fields

    # ------------------------------------------------------------------
    # Internal — method extraction
    # ------------------------------------------------------------------

    def _extract_class_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from a class/struct/record body.

        Includes regular methods and constructors.
        """
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return ()

        methods: list[MethodNode] = []
        for child in body.children:
            if child.type == "method_declaration":
                m = self._extract_method_node(child, src)
                if m is not None:
                    methods.append(m)
            elif child.type == "constructor_declaration":
                m = self._extract_constructor_node(child, src)
                if m is not None:
                    methods.append(m)

        return tuple(methods)

    def _extract_method_node(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Extract a ``method_declaration`` → MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None

        vis = _get_visibility(node)
        is_async = _has_modifier(node, "async")
        is_static = _has_modifier(node, "static")
        rtype = _extract_method_return_type(node, src, name)
        params = _extract_parameters(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=is_async,
            is_static=is_static,
            params=params,
            return_type=rtype,
            attributes=attrs,
            doc=doc,
            span=span_from_node(node),
        )

    def _extract_constructor_node(
        self,
        node: Node,
        src: bytes,
    ) -> MethodNode | None:
        """Extract a ``constructor_declaration`` → MethodNode."""
        name = _get_identifier(node, src)
        if not name:
            return None

        vis = _get_visibility(node)
        params = _extract_parameters(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        return MethodNode(
            name=name,
            visibility=vis,
            is_async=False,
            is_static=False,
            params=params,
            return_type="",
            attributes=attrs,
            doc=doc,
            span=span_from_node(node),
        )

    def _extract_function_node(
        self,
        node: Node,
        src: bytes,
    ) -> FunctionNode | None:
        """Extract a ``method_declaration`` → FunctionNode (free function)."""
        name = _get_identifier(node, src)
        if not name:
            return None

        vis = _get_visibility(node)
        is_async = _has_modifier(node, "async")
        rtype = _extract_method_return_type(node, src, name)
        params = _extract_parameters(node, src)
        generics = _extract_generics(node, src)
        attrs = _extract_attributes(node, src)
        doc = _extract_doc_comment(node, src)

        return FunctionNode(
            name=name,
            visibility=vis,
            is_async=is_async,
            generics=generics,
            params=params,
            return_type=rtype,
            attributes=attrs,
            doc=doc,
            span=span_from_node(node),
        )

    def _extract_trait_method_item(
        self,
        node: Node,
        src: bytes,
    ) -> TraitItemNode | None:
        """Extract a method from an abstract class body → TraitItemNode."""
        name = _get_identifier(node, src)
        if not name:
            return None

        is_abstract = _has_modifier(node, "abstract")
        kind = (
            TraitItemKind.REQUIRED_METHOD
            if is_abstract
            else TraitItemKind.DEFAULT_METHOD
        )
        rtype = _extract_method_return_type(node, src, name)
        params = _extract_parameters(node, src)
        is_async = _has_modifier(node, "async") or rtype.startswith("Task")
        doc = _extract_doc_comment(node, src)

        return TraitItemNode(
            kind=kind,
            name=name,
            is_async=is_async,
            params=params,
            return_type=rtype,
            doc=doc,
            span=span_from_node(node),
        )

    # ------------------------------------------------------------------
    # Internal — constant extraction
    # ------------------------------------------------------------------

    def _extract_class_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract const and static readonly fields from a class body."""
        body = _child_by_type(node, "declaration_list")
        if body is None:
            return

        for child in body.children:
            if child.type == "field_declaration" and (
                _is_const_field(child) or _is_static_readonly_field(child)
            ):
                cnode = self._extract_constant_from_field(child, src)
                if cnode is not None:
                    ast.constants.append(cnode)

    @staticmethod
    def _extract_constant_from_field(
        node: Node,
        src: bytes,
    ) -> ConstantNode | None:
        """Extract a constant from a ``field_declaration``."""
        vis = _get_visibility(node)
        var_decl = _child_by_type(node, "variable_declaration")
        if var_decl is None:
            return None

        # Get variable name from first declarator
        vd = _child_by_type(var_decl, "variable_declarator")
        if vd is None:
            return None
        vname = _get_identifier(vd, src)
        if not vname:
            return None

        raw = _node_text(node, src).strip()
        if len(raw) > _MAX_RAW_CONSTANT_LENGTH:
            raw = raw[:_MAX_RAW_CONSTANT_LENGTH] + "…"

        return ConstantNode(name=vname, visibility=vis, raw=raw, span=span_from_node(node))


# endregion: --- CSharpExtractor class


# ---------------------------------------------------------------------------
# region:    --- Post-pass: self_methods collection
# ---------------------------------------------------------------------------


def _collect_self_methods(ast: FileAST) -> list[MethodNode]:
    """Build the flat ``self_methods`` list from impl_blocks and functions.

    Each method receives a ``context`` string:
    - ``"free"`` for top-level functions
    - ``"impl:ClassName"`` for inherent impl blocks
    - ``"impl:IFoo for ClassName"`` for interface implementation blocks
    """
    methods: list[MethodNode] = []

    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=fn.is_async,
            is_static=True,
            params=fn.params,
            return_type=fn.return_type,
            context="free",
            attributes=fn.attributes,
            doc=fn.doc,
            span=fn.span,
        )
        for fn in ast.functions
    )

    for impl in ast.impl_blocks:
        if impl.trait_type:
            ctx = f"impl:{impl.trait_type} for {impl.self_type}"
        else:
            ctx = f"impl:{impl.self_type}"

        methods.extend(
            MethodNode(
                name=m.name,
                visibility=m.visibility,
                is_async=m.is_async,
                is_unsafe=m.is_unsafe,
                is_static=m.is_static,
                params=m.params,
                return_type=m.return_type,
                context=ctx,
                attributes=m.attributes,
                doc=m.doc,
                span=m.span,
            )
            for m in impl.methods
        )

    return methods


# endregion: --- Post-pass: self_methods collection


# ---------------------------------------------------------------------------
# region:    --- Post-pass: imported_package_methods
# ---------------------------------------------------------------------------


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Detect ``TypeOrNamespace.Method()`` calls using the import map.

    Walks the AST looking for ``member_access_expression`` nodes that are
    part of ``invocation_expression`` nodes.  If the object part resolves
    to a known import, the call is recorded.

    Args:
        root: Root AST node.
        src: Source bytes.
        import_map: Short-name → qualified-path from ``build_import_map``.

    Returns:
        A dict mapping qualified type paths to lists of method names.
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
    """Recursively walk for ``invocation_expression`` nodes."""
    if node.type == "invocation_expression":
        _resolve_invocation(node, src, import_map, result)

    for child in node.children:
        _walk_for_invocations(child, src, import_map, result)


def _resolve_invocation(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    result: dict[str, list[str]],
) -> None:
    """Try to resolve a single invocation into a scoped method call.

    Patterns handled:
    - ``Type.Method(args)`` — member_access_expression
    - ``Namespace.Type.Method(args)`` — nested member_access_expression
    """
    func = node.children[0] if node.children else None
    if func is None or func.type != "member_access_expression":
        return

    # member_access_expression has: expression . name
    parts: list[str] = []
    _collect_member_parts(func, src, parts)

    _min_parts_for_method_call = 2
    if len(parts) < _min_parts_for_method_call:
        return

    method_name = parts[-1]
    obj_parts = parts[:-1]

    # Try resolving the leftmost identifier via import map
    leftmost = obj_parts[0]
    if leftmost in import_map:
        qualified_base = import_map[leftmost]
        if len(obj_parts) > 1:
            rest = "::".join(obj_parts[1:])
            qualified = f"{qualified_base}::{rest}"
        else:
            qualified = qualified_base
        result.setdefault(qualified, [])
        if method_name not in result[qualified]:
            result[qualified].append(method_name)


def _collect_member_parts(
    node: Node,
    src: bytes,
    parts: list[str],
) -> None:
    """Recursively collect parts of a ``member_access_expression`` chain."""
    if node.type == "identifier":
        parts.append(_node_text(node, src))
    elif node.type == "member_access_expression":
        for child in node.children:
            if child.type in ("identifier", "generic_name"):
                parts.append(_node_text(child, src))
            elif child.type == "member_access_expression":
                _collect_member_parts(child, src, parts)
    elif node.type == "generic_name":
        parts.append(_node_text(node, src))


# endregion: --- Post-pass: imported_package_methods
