"""Ruby language extractor — tree-sitter based AST extraction.

Parses ``.rb`` files using ``tree-sitter-ruby`` and maps Ruby-specific
constructs to the normalized AST schema.

Ruby → Normalized mapping:
    =============================  =================
    Ruby Concept                   Normalized To
    =============================  =================
    ``class``                      StructNode
    ``class < Parent``             ImplBlockNode
    ``module`` (mixin, has defs)   TraitNode
    ``module`` (namespace only)    ModuleNode
    ``def`` (module-level)         FunctionNode
    ``def`` (in class)             MethodNode
    ``def self.method``            MethodNode (static)
    ``class << self``              MethodNode (static)
    ``include Mod``                ImplBlockNode
    ``require`` / ``require_rel``  uses (raw str)
    ``CONST = val``                ConstantNode
    ``CamelCase = Type``           TypeAliasNode
    ``attr_accessor :x``           FieldNode
    ``@ivar = val`` (initialize)   FieldNode
    ``Struct.new(:x, :y)``         StructNode (fields)
    =============================  =================

Also parses ``Gemfile`` manifests via :meth:`parse_manifest`.
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
    SCORE_EXTRACTED,
    SCORE_INFERRED,
    Confidence,
    ConstantNode,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
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

__all__: list[str] = ["RubyExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Pattern for Ruby constants: ALL_UPPER_CASE identifiers.
_CONSTANT_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Gemfile parsing regexes.
_GEM_RE: re.Pattern[str] = re.compile(
    r"""gem\s+['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?""",
)
_GROUP_RE: re.Pattern[str] = re.compile(r"""group\s+""")

# attr_* method names that declare fields.
_ATTR_METHODS: frozenset[str] = frozenset({
    "attr_accessor",
    "attr_reader",
    "attr_writer",
})

# Visibility modifier method names.
_VISIBILITY_MODIFIERS: frozenset[str] = frozenset({
    "private",
    "protected",
    "public",
})

# Mixin method names.
_MIXIN_METHODS: frozenset[str] = frozenset({
    "include",
    "extend",
    "prepend",
})


# ---------------------------------------------------------------------------
# region:    --- Helper types
# ---------------------------------------------------------------------------


class _ClassInfo(NamedTuple):
    """Parsed metadata for a Ruby class declaration."""

    name: str
    superclass: str
    doc: str
    vis: Visibility


class _VisibilityState:
    """Tracks the current default visibility within a class body."""

    __slots__ = ("current",)

    def __init__(self) -> None:
        self.current: Visibility = Visibility.PUBLIC  # Ruby default


# endregion: --- Helper types


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node | None, src: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace",
    ).strip()


def _child_by_type(node: Node, *types: str) -> Node | None:
    """Return the first child matching any of the given types."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _children_by_type(node: Node, *types: str) -> list[Node]:
    """Return all children matching any of the given types."""
    return [c for c in node.children if c.type in types]


def _get_name_from_constant(node: Node, src: bytes) -> str:
    """Extract name from a ``constant`` or ``scope_resolution`` child."""
    # Try scope_resolution first (A::B::C)
    scope = _child_by_type(node, "scope_resolution")
    if scope is not None:
        return _node_text(scope, src)
    const = _child_by_type(node, "constant")
    if const is not None:
        return _node_text(const, src)
    return ""


def _get_method_name(node: Node, src: bytes) -> str:
    """Extract method name from a method/singleton_method node."""
    ident = _child_by_type(node, "identifier")
    if ident is not None:
        return _node_text(ident, src)
    # operator methods like <=>, ==, etc.
    op = _child_by_type(node, "operator")
    if op is not None:
        return _node_text(op, src)
    return ""


def _extract_superclass(node: Node, src: bytes) -> str:
    """Extract superclass name from a ``superclass`` child."""
    sup = _child_by_type(node, "superclass")
    if sup is None:
        return ""
    # The superclass node wraps a constant or scope_resolution
    const = _child_by_type(sup, "constant", "scope_resolution")
    return _node_text(const, src) if const else ""


def _extract_doc_comment(node: Node, src: bytes) -> str:
    """Collect preceding comment siblings as documentation."""
    parent = node.parent
    if parent is None:
        return ""
    siblings = parent.children
    idx = -1
    for i, child in enumerate(siblings):
        if child.id == node.id:
            idx = i
            break
    if idx <= 0:
        return ""

    comments: list[str] = []
    for i in range(idx - 1, -1, -1):
        sib = siblings[i]
        if sib.type == "comment":
            text = _node_text(sib, src).lstrip("#").strip()
            comments.append(text)
        elif sib.type in ("\n", "newline"):
            continue
        else:
            break
    if not comments:
        return ""
    comments.reverse()
    return "\n".join(comments)


def _extract_parameters(
    node: Node,
    src: bytes,
) -> tuple[ParamNode, ...]:
    """Extract parameters from a method/singleton_method node."""
    params_node = _child_by_type(node, "method_parameters")
    if params_node is None:
        return ()
    params: list[ParamNode] = []
    for child in params_node.named_children:
        param = _parse_single_param(child, src)
        if param is not None:
            params.append(param)
    return tuple(params)


def _parse_single_param(child: Node, src: bytes) -> ParamNode | None:
    """Parse a single parameter node into a ParamNode."""
    ntype = child.type
    name: str = ""

    if ntype == "identifier":
        name = _node_text(child, src)
    elif ntype == "optional_parameter":
        ident = _child_by_type(child, "identifier")
        name = _node_text(ident, src) if ident else ""
    elif ntype == "splat_parameter":
        ident = _child_by_type(child, "identifier")
        name = f"*{_node_text(ident, src)}" if ident else "*args"
    elif ntype == "hash_splat_parameter":
        ident = _child_by_type(child, "identifier")
        name = f"**{_node_text(ident, src)}" if ident else "**kwargs"
    elif ntype == "block_parameter":
        ident = _child_by_type(child, "identifier")
        name = f"&{_node_text(ident, src)}" if ident else "&block"
    elif ntype in ("keyword_parameter", "optional_keyword_parameter"):
        ident = _child_by_type(child, "identifier")
        name = f"{_node_text(ident, src)}:" if ident else ""

    return ParamNode(name=name) if name else None


def _get_call_method_name(node: Node, src: bytes) -> str:
    """Get the method name from a ``call`` node (e.g., require, private)."""
    # In tree-sitter-ruby, a bare call like `require "foo"` has:
    #   method: identifier child
    ident = _child_by_type(node, "identifier")
    return _node_text(ident, src) if ident else ""


def _get_call_arguments(node: Node) -> list[Node]:
    """Get the argument nodes from a ``call`` node."""
    arg_list = _child_by_type(node, "argument_list")
    if arg_list is not None:
        return list(arg_list.named_children)
    # Some calls have arguments without explicit argument_list
    return []


def _is_call_node(node: Node) -> bool:
    """Check if a node is a call (method_call or call)."""
    return node.type == "call"


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map & scoped method calls
# ---------------------------------------------------------------------------


def build_import_map(use_statements: list[str]) -> dict[str, str]:
    """Parse Ruby require statements into a short_name → qualified path map.

    Handles:
    - ``require "json"`` → ``{"json": "json"}``
    - ``require "net/http"`` → ``{"net/http": "net/http"}``
    - ``require_relative "./helpers"`` → ``{"./helpers": "./helpers"}``
    """
    import_map: dict[str, str] = {}
    for stmt in use_statements:
        cleaned = stmt.strip()
        # Extract the quoted string from require/require_relative
        match = re.search(r"""['"]([^'"]+)['"]""", cleaned)
        if match:
            path = match.group(1)
            # Use the last path component as the short name
            short = path.rsplit("/", maxsplit=1)[-1]
            import_map[short] = path
    return import_map


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Walk the AST to find ``Type.method()`` scoped call expressions."""
    results: dict[str, set[str]] = {}

    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "call":
            _resolve_scoped_call(node, src, import_map, results)
        stack.extend(node.children)
    return {k: sorted(v) for k, v in results.items()}


def _resolve_scoped_call(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    results: dict[str, set[str]],
) -> None:
    """Resolve ``Obj.method(...)`` into results if Obj is known."""
    # Look for receiver.method pattern
    children = node.children
    if len(children) < 3:  # noqa: PLR2004
        return
    receiver = children[0]
    method_node = _child_by_type(node, "identifier")
    if receiver.type not in ("constant", "identifier", "scope_resolution"):
        return
    if method_node is None:
        return
    obj_name = _node_text(receiver, src)
    method_name = _node_text(method_node, src)
    # Don't add the receiver name as a "method"
    if method_name == obj_name:
        return
    qualified = import_map.get(obj_name, obj_name)
    results.setdefault(qualified, set()).add(method_name)


# endregion: --- Import map & scoped method calls


# ---------------------------------------------------------------------------
# region:    --- self_methods builder
# ---------------------------------------------------------------------------


def _collect_self_methods(
    functions: list[FunctionNode],
    impl_blocks: list[ImplBlockNode],
) -> list[MethodNode]:
    """Build flat list of every function/method defined in a file."""
    methods: list[MethodNode] = []

    # Free functions
    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=False,
            is_unsafe=False,
            params=fn.params,
            return_type="",
            context="free",
            attributes=fn.attributes,
            doc=fn.doc,
            span=fn.span,
        )
        for fn in functions
    )

    # Class/module methods
    for impl_block in impl_blocks:
        if impl_block.trait_type:
            context = f"impl:{impl_block.trait_type} for {impl_block.self_type}"
        else:
            context = f"impl:{impl_block.self_type}"

        methods.extend(
            MethodNode(
                name=method.name,
                visibility=method.visibility,
                is_async=False,
                is_unsafe=False,
                is_static=method.is_static,
                params=method.params,
                return_type="",
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
# region:    --- RubyExtractor class
# ---------------------------------------------------------------------------


class RubyExtractor(ExtractorBase):
    """Ruby language extractor using ``tree-sitter-ruby``.

    Parses ``.rb`` source files and ``Gemfile`` manifests
    into the normalized AST schema.
    """

    language_id: str = "ruby"
    file_extensions: list[str] = [".rb"]  # noqa: RUF012

    def __init__(self) -> None:
        """Initialize the Ruby parser with tree-sitter-ruby grammar."""
        import tree_sitter_ruby

        self._language = Language(tree_sitter_ruby.language())
        self._parser = Parser(self._language)

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a single ``.rb`` file into a FileAST."""
        tree = self._parser.parse(source)
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

        return ast

    # ------------------------------------------------------------------
    # Top-level walk
    # ------------------------------------------------------------------

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

        if ntype == "class":
            self._handle_class(node, src, ast)

        elif ntype == "module":
            self._handle_module(node, src, ast)

        elif ntype in ("method", "singleton_method"):
            self._extract_top_function(node, src, ast)

        elif ntype == "call":
            self._handle_top_call(node, src, ast)

        elif ntype == "assignment":
            self._handle_assignment(node, src, ast)

        elif ntype == "ERROR":
            ast.errors.append(
                f"parse error at byte {node.start_byte}: "
                f"{_node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]}"
            )

    # ------------------------------------------------------------------
    # Class handling
    # ------------------------------------------------------------------

    def _handle_class(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract a Ruby class as StructNode + ImplBlockNode."""
        name = _get_name_from_constant(node, src)
        if not name:
            return
        superclass = _extract_superclass(node, src)
        doc = _extract_doc_comment(node, src)
        vis = Visibility.PUBLIC

        info = _ClassInfo(name=name, superclass=superclass, doc=doc, vis=vis)

        # Extract fields from attr_* and initialize
        fields = self._extract_class_fields(node, src)

        ast.structs.append(
            StructNode(
                name=info.name,
                visibility=info.vis,
                generics="",
                fields=fields,
                attributes=(),
                doc=info.doc,
                span=span_from_node(node),
            )
        )

        # Extract methods
        methods = self._extract_class_methods(node, src)

        # Superclass → ImplBlockNode (trait impl)
        if info.superclass:
            ast.impl_blocks.append(
                ImplBlockNode(
                    self_type=info.name,
                    trait_type=info.superclass,
                    generics="",
                    methods=methods,
                    span=span_from_node(node),
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                )
            )
        elif methods:
                ast.impl_blocks.append(
                    ImplBlockNode(
                        self_type=info.name,
                        trait_type="",
                        generics="",
                        methods=methods,
                        span=span_from_node(node),
                    )
                )

        # Extract include/extend/prepend as additional ImplBlockNodes
        self._extract_mixin_impls(node, src, info.name, ast)

        # Recurse into nested classes/modules
        self._walk_nested(node, src, ast)

    def _extract_class_fields(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[FieldNode, ...]:
        """Extract fields from attr_* macros and initialize @ivar assignments."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return ()

        fields: list[FieldNode] = []
        seen: set[str] = set()

        # Phase 1: attr_accessor/reader/writer
        for child in body.children:
            if not child.is_named:
                continue
            if child.type == "call":
                method_name = _get_call_method_name(child, src)
                if method_name in _ATTR_METHODS:
                    for arg in _get_call_arguments(child):
                        sym_name = _extract_symbol_name(arg, src)
                        if sym_name and sym_name not in seen:
                            seen.add(sym_name)
                            fields.append(
                                FieldNode(
                                    name=sym_name,
                                    type="",
                                    visibility=Visibility.PUBLIC,
                                )
                            )

        # Phase 2: @ivar assignments from initialize
        init_fields = self._extract_initialize_fields(node, src)
        for f in init_fields:
            if f.name not in seen:
                seen.add(f.name)
                fields.append(f)

        return tuple(fields)

    def _extract_initialize_fields(
        self,
        class_node: Node,
        src: bytes,
    ) -> list[FieldNode]:
        """Extract fields from ``initialize`` method's @ivar assignments."""
        body = _child_by_type(class_node, "body_statement")
        if body is None:
            return []

        # Find the initialize method
        init_node: Node | None = None
        for child in body.children:
            if child.type == "method":
                name = _get_method_name(child, src)
                if name == "initialize":
                    init_node = child
                    break

        if init_node is None:
            return []

        # Walk initialize body for @ivar = ... assignments
        init_body = _child_by_type(init_node, "body_statement")
        if init_body is None:
            return []

        fields: list[FieldNode] = []
        seen: set[str] = set()
        self._collect_ivar_assignments(init_body, src, fields, seen)
        return fields

    def _collect_ivar_assignments(
        self,
        node: Node,
        src: bytes,
        fields: list[FieldNode],
        seen: set[str],
    ) -> None:
        """Recursively collect @instance_variable = ... assignments."""
        for child in node.children:
            if child.type == "assignment":
                lhs = child.named_children[0] if child.named_children else None
                if lhs is not None and lhs.type == "instance_variable":
                    name = _node_text(lhs, src).lstrip("@")
                    if name and name not in seen:
                        seen.add(name)
                        fields.append(
                            FieldNode(
                                name=name,
                                type="",
                                visibility=Visibility.PRIVATE,
                            )
                        )
            elif child.named_children:
                self._collect_ivar_assignments(child, src, fields, seen)

    def _extract_class_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from a class body with visibility tracking."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return ()

        vis_state = _VisibilityState()
        methods: list[MethodNode] = []
        targeted_vis: dict[str, Visibility] = {}

        for child in body.children:
            if not child.is_named:
                continue

            if child.type in ("method", "singleton_method"):
                is_static = child.type == "singleton_method"
                methods.append(
                    _build_method_node(child, src, vis_state.current, is_static=is_static),
                )

            elif child.type == "singleton_class":
                self._extract_eigenclass_methods(
                    child, src, methods, vis_state,
                )

            elif child.type == "call":
                _process_visibility_call(child, src, vis_state, targeted_vis)

            elif child.type == "identifier":
                # Bare visibility modifier: `private`, `protected`, `public`
                text = _node_text(child, src)
                if text in _VISIBILITY_MODIFIERS:
                    _update_visibility(vis_state, text)

        # Apply targeted visibility overrides
        if targeted_vis:
            methods = _apply_targeted_visibility(methods, targeted_vis)

        return tuple(methods)

    def _extract_eigenclass_methods(
        self,
        node: Node,
        src: bytes,
        methods: list[MethodNode],
        parent_vis: _VisibilityState,
    ) -> None:
        """Extract methods from ``class << self`` block as static."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return

        vis_state = _VisibilityState()
        vis_state.current = parent_vis.current

        for child in body.children:
            if not child.is_named:
                continue

            if child.type == "method":
                methods.append(
                    _build_method_node(child, src, vis_state.current, is_static=True),
                )

            elif child.type == "call":
                method_name = _get_call_method_name(child, src)
                if method_name in _VISIBILITY_MODIFIERS:
                    args = _get_call_arguments(child)
                    if not args:
                        _update_visibility(vis_state, method_name)

            elif child.type == "identifier":
                text = _node_text(child, src)
                if text in _VISIBILITY_MODIFIERS:
                    _update_visibility(vis_state, text)

    def _extract_mixin_impls(
        self,
        node: Node,
        src: bytes,
        class_name: str,
        ast: FileAST,
    ) -> None:
        """Extract include/extend/prepend as ImplBlockNodes."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return

        for child in body.children:
            if not child.is_named or child.type != "call":
                continue
            method_name = _get_call_method_name(child, src)
            if method_name not in _MIXIN_METHODS:
                continue
            for arg in _get_call_arguments(child):
                mod_name = _node_text(arg, src)
                if mod_name:
                    ast.uses.append(f"include {mod_name}")
                    ast.impl_blocks.append(
                        ImplBlockNode(
                            self_type=class_name,
                            trait_type=mod_name,
                            generics="",
                            methods=(),
                            span=span_from_node(child),
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                        )
                    )

    def _walk_nested(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Recurse into nested class/module declarations inside a body."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return
        for child in body.children:
            if child.type == "class":
                self._handle_class(child, src, ast)
            elif child.type == "module":
                self._handle_module(child, src, ast)

    # ------------------------------------------------------------------
    # Module handling
    # ------------------------------------------------------------------

    def _handle_module(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract a Ruby module as TraitNode (mixin) or ModuleNode (namespace)."""
        name = _get_name_from_constant(node, src)
        if not name:
            return

        doc = _extract_doc_comment(node, src)
        classification = self._classify_module(node, src)

        if classification in ("mixin", "both"):
            # Extract as TraitNode with items
            items = self._extract_trait_items(node, src)
            ast.traits.append(
                TraitNode(
                    name=name,
                    visibility=Visibility.PUBLIC,
                    generics="",
                    super_traits=(),
                    items=items,
                    attributes=(),
                    doc=doc,
                    span=span_from_node(node),
                )
            )

        if classification in ("namespace", "both"):
            # Also record as ModuleNode
            ast.modules.append(
                ModuleNode(
                    name=name,
                    visibility=Visibility.PUBLIC,
                    inline=True,
                    span=span_from_node(node),
                )
            )

        # Extract constants from module body
        self._extract_body_constants(node, src, ast)

        # Recurse into nested classes/modules
        self._walk_nested(node, src, ast)

    def _classify_module(self, node: Node, _src: bytes) -> str:
        """Classify module as 'mixin', 'namespace', or 'both'."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return "namespace"

        has_methods = False
        has_nested = False
        for child in body.children:
            if not child.is_named:
                continue
            if child.type in ("method", "singleton_method"):
                has_methods = True
            elif child.type in ("class", "module"):
                has_nested = True

        if has_methods and has_nested:
            return "both"
        if has_methods:
            return "mixin"
        return "namespace"

    def _extract_trait_items(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[TraitItemNode, ...]:
        """Extract method items from a module body for TraitNode."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return ()

        items: list[TraitItemNode] = []
        for child in body.children:
            if not child.is_named:
                continue
            if child.type in ("method", "singleton_method"):
                name = _get_method_name(child, src)
                params = _extract_parameters(child, src)
                doc = _extract_doc_comment(child, src)
                items.append(
                    TraitItemNode(
                        kind=TraitItemKind.DEFAULT_METHOD,
                        name=name,
                        is_async=False,
                        params=params,
                        return_type="",
                        doc=doc,
                        span=span_from_node(child),
                    )
                )
        return tuple(items)

    def _extract_body_constants(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract constant assignments from a module/class body."""
        body = _child_by_type(node, "body_statement")
        if body is None:
            return
        for child in body.children:
            if child.type == "assignment":
                self._handle_assignment(child, src, ast)

    # ------------------------------------------------------------------
    # Top-level function
    # ------------------------------------------------------------------

    def _extract_top_function(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract a top-level def as FunctionNode."""
        name = _get_method_name(node, src)
        params = _extract_parameters(node, src)
        doc = _extract_doc_comment(node, src)

        ast.functions.append(
            FunctionNode(
                name=name,
                visibility=Visibility.PUBLIC,
                is_async=False,
                is_unsafe=False,
                generics="",
                params=params,
                return_type="",
                where_clause="",
                attributes=(),
                doc=doc,
                span=span_from_node(node),
            )
        )

    # ------------------------------------------------------------------
    # Top-level call handling (require, etc.)
    # ------------------------------------------------------------------

    def _handle_top_call(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle top-level call nodes (require, require_relative)."""
        method_name = _get_call_method_name(node, src)

        if method_name in ("require", "require_relative"):
            stmt = _node_text(node, src)
            ast.uses.append(stmt)

    # ------------------------------------------------------------------
    # Assignment handling (constants, type aliases, Struct.new)
    # ------------------------------------------------------------------

    def _handle_assignment(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle assignment nodes for constants, type aliases, Struct.new."""
        lhs = node.named_children[0] if node.named_children else None
        if lhs is None or lhs.type != "constant":
            return

        name = _node_text(lhs, src)

        # Check for Struct.new(...) on RHS
        rhs = node.named_children[1] if len(node.named_children) > 1 else None
        if rhs is not None and self._is_struct_new(rhs, src):
            self._handle_struct_new(name, rhs, src, node, ast)
            return

        # UPPER_CASE → ConstantNode
        if _CONSTANT_NAME_RE.match(name):
            raw = _node_text(node, src)[:_MAX_RAW_CONSTANT_LENGTH]
            ast.constants.append(
                ConstantNode(
                    name=name,
                    visibility=Visibility.PUBLIC,
                    raw=raw,
                    span=span_from_node(node),
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                )
            )
            return

        # CamelCase → TypeAliasNode (heuristic)
        if name and name[0].isupper():
            rhs_text = _node_text(rhs, src) if rhs else ""
            ast.type_aliases.append(
                TypeAliasNode(
                    name=name,
                    aliased_to=rhs_text,
                    visibility=Visibility.PUBLIC,
                    span=span_from_node(node),
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED,
                )
            )

    def _is_struct_new(self, node: Node, src: bytes) -> bool:
        """Check if an expression is Struct.new(...) or Data.define(...)."""
        if node.type != "call":
            return False
        text = _node_text(node, src)
        return text.startswith(("Struct.new", "Data.define"))

    def _handle_struct_new(
        self,
        name: str,
        call_node: Node,
        src: bytes,
        assign_node: Node,
        ast: FileAST,
    ) -> None:
        """Extract ``Point = Struct.new(:x, :y)`` as StructNode with fields."""
        fields: list[FieldNode] = []
        for arg in _get_call_arguments(call_node):
            sym_name = _extract_symbol_name(arg, src)
            if sym_name:
                fields.append(
                    FieldNode(
                        name=sym_name,
                        type="",
                        visibility=Visibility.PUBLIC,
                    )
                )

        ast.structs.append(
            StructNode(
                name=name,
                visibility=Visibility.PUBLIC,
                generics="",
                fields=tuple(fields),
                attributes=(),
                doc="",
                span=span_from_node(assign_node),
            )
        )

    # ------------------------------------------------------------------
    # Manifest parsing (Gemfile)
    # ------------------------------------------------------------------

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``Gemfile`` manifest file.

        Args:
            manifest_path: Absolute path to ``Gemfile``.

        Returns:
            CrateModel with name, language, dependencies extracted.
        """
        try:
            text = manifest_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Failed to read Gemfile: %s", manifest_path)
            return CrateModel(
                name=manifest_path.parent.name,
                language="ruby",
                manifest_path=str(manifest_path),
            )

        name = manifest_path.parent.name
        deps: list[CrateDependency] = []
        in_dev_group = False
        group_depth = 0

        for line in text.splitlines():
            stripped = line.strip()

            # Track group blocks
            if _GROUP_RE.match(stripped):
                group_names = _extract_group_names(stripped)
                in_dev_group = bool(
                    {"development", "test"} & group_names
                )
                group_depth += 1
                continue
            if stripped == "end" and group_depth > 0:
                group_depth -= 1
                if group_depth == 0:
                    in_dev_group = False
                continue

            # Match gem declarations
            match = _GEM_RE.match(stripped)
            if match:
                gem_name = match.group(1)
                version = match.group(2) or ""
                deps.append(
                    CrateDependency(
                        name=gem_name,
                        version=version.strip(),
                        is_dev=in_dev_group,
                    )
                )

        # Augment declared gems with resolved pins + transitive gems from a
        # sibling ``Gemfile.lock``.
        from ast_intel.core.lockfile_parser import augment_with_lockfile

        merged = augment_with_lockfile(manifest_path.parent, "ruby", deps)

        return CrateModel(
            name=name,
            version="",
            manifest_path=str(manifest_path),
            language="ruby",
            dependencies=merged,
        )


# endregion: --- RubyExtractor class


# ---------------------------------------------------------------------------
# region:    --- Module-level helpers
# ---------------------------------------------------------------------------


def _extract_symbol_name(node: Node, src: bytes) -> str:
    """Extract a Ruby symbol name like ``:foo`` → ``"foo"``."""
    if node.type == "simple_symbol":
        text = _node_text(node, src)
        return text.lstrip(":")
    if node.type == "symbol":
        text = _node_text(node, src)
        return text.lstrip(":")
    return ""


def _extract_group_names(line: str) -> set[str]:
    """Extract group names from ``group :dev, :test do``."""
    return {m.group(1) for m in re.finditer(r":(\w+)", line)}


def _update_visibility(state: _VisibilityState, modifier: str) -> None:
    """Update visibility state from a bare modifier call."""
    state.current = _modifier_to_visibility(modifier)


def _modifier_to_visibility(modifier: str) -> Visibility:
    """Map a visibility modifier name to a Visibility enum value."""
    if modifier == "private":
        return Visibility.PRIVATE
    if modifier == "protected":
        return Visibility.PROTECTED
    return Visibility.PUBLIC


def _build_method_node(
    child: Node,
    src: bytes,
    visibility: Visibility,
    *,
    is_static: bool,
) -> MethodNode:
    """Build a MethodNode from a method/singleton_method AST node."""
    return MethodNode(
        name=_get_method_name(child, src),
        visibility=visibility,
        is_async=False,
        is_unsafe=False,
        is_static=is_static,
        params=_extract_parameters(child, src),
        return_type="",
        doc=_extract_doc_comment(child, src),
        span=span_from_node(child),
    )


def _process_visibility_call(
    child: Node,
    src: bytes,
    vis_state: _VisibilityState,
    targeted_vis: dict[str, Visibility],
) -> None:
    """Handle a call node that might be a visibility modifier."""
    method_name = _get_call_method_name(child, src)
    if method_name not in _VISIBILITY_MODIFIERS:
        return
    args = _get_call_arguments(child)
    if not args:
        _update_visibility(vis_state, method_name)
    else:
        for arg in args:
            sym = _extract_symbol_name(arg, src)
            if sym:
                targeted_vis[sym] = _modifier_to_visibility(method_name)


def _apply_targeted_visibility(
    methods: list[MethodNode],
    targeted_vis: dict[str, Visibility],
) -> list[MethodNode]:
    """Apply targeted visibility overrides to methods."""
    return [
        MethodNode(
            name=m.name,
            visibility=targeted_vis[m.name],
            is_async=m.is_async,
            is_unsafe=m.is_unsafe,
            is_static=m.is_static,
            params=m.params,
            return_type=m.return_type,
            doc=m.doc,
            span=m.span,
        )
        if m.name in targeted_vis
        else m
        for m in methods
    ]


# endregion: --- Module-level helpers
