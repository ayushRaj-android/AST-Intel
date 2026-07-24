"""Python language extractor — tree-sitter based AST extraction.

Parses ``.py`` / ``.pyi`` files using ``tree-sitter-python`` and maps
Python-specific constructs to the normalized AST schema.

Python → Normalized mapping:
    =============================  =================
    Python Concept                 Normalized To
    =============================  =================
    ``class``                      StructNode
    ``class(ABC)`` / ``Protocol``  TraitNode
    ``class(Parent)``              ImplBlockNode
    ``@dataclass class``           StructNode (fields)
    ``def`` (module-level)         FunctionNode
    ``def`` (in class)             MethodNode
    ``import`` / ``from…import``   uses (raw str)
    ``Type.method()``              imported_package_methods
    ``TypeAlias = …``              TypeAliasNode
    ``CONST: type = val``          ConstantNode
    ``@decorator``                 attributes
    =============================  =================

Also parses ``pyproject.toml`` manifests via :meth:`parse_manifest`.
"""

from __future__ import annotations

import logging
import re
import tomllib
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
    ParamNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
    Visibility,
)
from ast_intel.models.workspace_model import CrateDependency, CrateModel

__all__: list[str] = ["PythonExtractor"]

logger = logging.getLogger(__name__)

# Truncation limits for raw text previews in extracted nodes.
_MAX_RAW_CONSTANT_LENGTH = 120
_MAX_ERROR_PREVIEW_LENGTH = 60

# Base classes that mark a class as abstract (→ TraitNode).
_ABSTRACT_BASES: frozenset[str] = frozenset({
    "ABC",
    "ABCMeta",
    "Protocol",
})

# Decorator names that make a method abstract.
_ABSTRACT_DECORATORS: frozenset[str] = frozenset({
    "abstractmethod",
    "abstractproperty",
    "abstractclassmethod",
    "abstractstaticmethod",
})

# Pattern to detect a module-level constant: ``UPPER_CASE`` identifier.
_CONSTANT_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Pattern for Python dependency version specifiers (to distinguish from
# type alias assignments).
_DEP_VERSION_RE: re.Pattern[str] = re.compile(r"[><=!~]+")


class _ClassInfo(NamedTuple):
    """Parsed metadata for a Python class, reducing argument passing."""

    name: str
    bases: tuple[str, ...]
    decorators: tuple[str, ...]
    doc: str
    vis: Visibility
    is_abstract: bool
    is_dataclass: bool


# ---------------------------------------------------------------------------
# region:    --- Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, src: bytes) -> str:
    """Extract UTF-8 text for a tree-sitter node."""
    return src[node.start_byte : node.end_byte].decode(
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


def _get_identifier(node: Node, src: bytes) -> str:
    """Extract the first ``identifier`` child text."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, src)
    return ""


def _get_visibility(name: str) -> Visibility:
    """Determine visibility from a Python name.

    - Names starting with ``_`` (but **not** ``__dunder__``) are private.
    - Everything else is public.
    """
    if name.startswith("__") and name.endswith("__"):
        return Visibility.PUBLIC  # dunder
    if name.startswith("_"):
        return Visibility.PRIVATE
    return Visibility.PUBLIC


def _extract_decorators(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@decorator`` text from a ``decorated_definition`` parent.

    In tree-sitter-python, decorators are siblings inside the
    ``decorated_definition`` node.
    """
    if node.parent is not None and node.parent.type == "decorated_definition":
        return tuple(
            _node_text(c, src)
            for c in node.parent.children
            if c.type == "decorator"
        )
    return ()


def _extract_decorators_from_decorated(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract ``@decorator`` text directly from a ``decorated_definition`` node."""
    return tuple(
        _node_text(c, src)
        for c in node.children
        if c.type == "decorator"
    )


def _extract_docstring(node: Node, src: bytes) -> str:
    """Extract a docstring from the first ``expression_statement`` in a block.

    In Python, the docstring is the first statement in the class/function body
    if it is a string literal.
    """
    body = _child_by_type(node, "block")
    if body is None:
        return ""
    for child in body.children:
        if not child.is_named:
            continue
        if child.type == "expression_statement":
            inner = child.named_children[0] if child.named_children else None
            if inner is not None and inner.type == "string":
                raw = _node_text(inner, src)
                # Strip triple-quote delimiters
                for delim in ('"""', "'''"):
                    if raw.startswith(delim) and raw.endswith(delim):
                        return raw[3:-3].strip()
                # Single-quoted string used as docstring
                return raw.strip("\"'").strip()
        break  # Only the very first statement can be a docstring
    return ""


def _extract_return_type(node: Node, src: bytes) -> str:
    """Extract return type annotation from a function_definition.

    The return type in tree-sitter-python is a ``type`` child node that
    appears after the ``parameters`` node.
    """
    found_params = False
    for child in node.children:
        if child.type == "parameters":
            found_params = True
            continue
        if found_params and child.type == "type":
            return _node_text(child, src)
    return ""


def _extract_parameters(
    params_node: Node | None,
    src: bytes,
) -> tuple[ParamNode, ...]:
    """Parse function parameters into ``ParamNode`` tuples.

    Skips ``self`` and ``cls`` parameters.
    """
    if params_node is None:
        return ()
    params: list[ParamNode] = []
    for child in params_node.named_children:
        param = _parse_single_param(child, src)
        if param is not None:
            params.append(param)
    return tuple(params)


_SELF_CLS: frozenset[str] = frozenset({"self", "cls"})


def _parse_single_param(child: Node, src: bytes) -> ParamNode | None:
    """Parse a single parameter node into a ParamNode, or ``None`` to skip."""
    ntype = child.type
    if ntype == "identifier":
        name = _node_text(child, src)
        return None if name in _SELF_CLS else ParamNode(name=name)
    if ntype == "typed_parameter":
        name, ptype = _parse_typed_param(child, src)
        return None if name in _SELF_CLS else ParamNode(name=name, type=ptype)
    if ntype in ("typed_default_parameter", "default_parameter"):
        return _parse_default_param(child, src, typed=ntype == "typed_default_parameter")
    if ntype in ("list_splat_pattern", "dictionary_splat_pattern"):
        prefix = "**" if ntype == "dictionary_splat_pattern" else "*"
        fallback = "**kwargs" if prefix == "**" else "*args"
        return _parse_splat_param(child, src, prefix=prefix, fallback=fallback)
    return None


def _parse_default_param(
    child: Node,
    src: bytes,
    *,
    typed: bool,
) -> ParamNode | None:
    """Parse a ``default_parameter`` or ``typed_default_parameter``."""
    name_node = _child_by_type(child, "identifier")
    name = _node_text(name_node, src) if name_node else ""
    if name in _SELF_CLS:
        return None
    ptype = ""
    if typed:
        type_node = _child_by_type(child, "type")
        ptype = _node_text(type_node, src) if type_node else ""
    return ParamNode(name=name, type=ptype)


def _parse_splat_param(
    child: Node,
    src: bytes,
    *,
    prefix: str,
    fallback: str,
) -> ParamNode:
    """Parse a ``list_splat_pattern`` or ``dictionary_splat_pattern``."""
    name_node = _child_by_type(child, "identifier")
    name = f"{prefix}{_node_text(name_node, src)}" if name_node else fallback
    return ParamNode(name=name)


def _parse_typed_param(node: Node, src: bytes) -> tuple[str, str]:
    """Parse a ``typed_parameter`` node, handling ``*args: T`` and ``**kwargs: T``.

    Returns:
        Tuple of (name, type).
    """
    type_node = _child_by_type(node, "type")
    ptype = _node_text(type_node, src) if type_node else ""

    # Direct identifier child (normal param)
    id_node = _child_by_type(node, "identifier")
    if id_node is not None:
        return (_node_text(id_node, src), ptype)

    # *args: type
    splat = _child_by_type(node, "list_splat_pattern")
    if splat is not None:
        inner = _child_by_type(splat, "identifier")
        name = f"*{_node_text(inner, src)}" if inner else "*args"
        return (name, ptype)

    # **kwargs: type
    dsplat = _child_by_type(node, "dictionary_splat_pattern")
    if dsplat is not None:
        inner = _child_by_type(dsplat, "identifier")
        name = f"**{_node_text(inner, src)}" if inner else "**kwargs"
        return (name, ptype)

    return ("", ptype)


def _is_async_function(node: Node) -> bool:
    """Check if a function_definition is async.

    In tree-sitter-python, async functions have their first non-named child
    be the ``async`` keyword.
    """
    for child in node.children:
        if child.type == "async":
            return True
        if child.type in ("def", "identifier", "parameters"):
            break
    return False


def _has_self_param(node: Node, src: bytes) -> bool:
    """Check if a function has ``self`` as first parameter."""
    params = _child_by_type(node, "parameters")
    if params is None:
        return False
    for child in params.named_children:
        if child.type == "identifier" and _node_text(child, src) == "self":
            return True
        # typed_parameter whose first identifier is self
        if child.type in ("typed_parameter", "typed_default_parameter"):
            first_id = _child_by_type(child, "identifier")
            if first_id and _node_text(first_id, src) == "self":
                return True
        break  # Only check first param
    return False


def _has_cls_param(node: Node, src: bytes) -> bool:
    """Check if a function has ``cls`` as first parameter."""
    params = _child_by_type(node, "parameters")
    if params is None:
        return False
    for child in params.named_children:
        if child.type == "identifier" and _node_text(child, src) == "cls":
            return True
        if child.type in ("typed_parameter", "typed_default_parameter"):
            first_id = _child_by_type(child, "identifier")
            if first_id and _node_text(first_id, src) == "cls":
                return True
        break
    return False


def _get_decorator_names(node: Node, src: bytes) -> frozenset[str]:
    """Get simple decorator names (without arguments) from a node.

    If the node is a ``class_definition`` or ``function_definition`` that
    is wrapped in a ``decorated_definition``, returns the decorator names.
    """
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return frozenset()
    names: set[str] = set()
    for child in parent.children:
        if child.type == "decorator":
            # Could be @name or @name(args)
            for inner in child.children:
                if inner.type == "identifier":
                    names.add(_node_text(inner, src))
                elif inner.type == "call":
                    fn_name = _child_by_type(inner, "identifier")
                    if fn_name:
                        names.add(_node_text(fn_name, src))
    return frozenset(names)


def _extract_base_classes(node: Node, src: bytes) -> tuple[str, ...]:
    """Extract base class names from a ``class_definition`` argument_list."""
    arg_list = _child_by_type(node, "argument_list")
    if arg_list is None:
        return ()
    bases: list[str] = []
    for child in arg_list.children:
        if child.type in ("identifier", "attribute"):
            bases.append(_node_text(child, src))
        elif child.type == "keyword_argument":
            # e.g., metaclass=ABCMeta — skip keyword args
            continue
        elif child.is_named:
            # subscript like Generic[T] etc.
            bases.append(_node_text(child, src))
    return tuple(bases)


def _is_abstract_class(
    bases: tuple[str, ...],
    decorators: frozenset[str],
) -> bool:
    """Determine if a class is abstract (ABC / Protocol).

    A class is abstract if:
    - Any base class is in ``_ABSTRACT_BASES``.
    - The decorator ``runtime_checkable`` is present (Protocol pattern).
    """
    for base in bases:
        # Handle dotted names like abc.ABC
        simple = base.rsplit(".", maxsplit=1)[-1]
        if simple in _ABSTRACT_BASES:
            return True
    return "runtime_checkable" in decorators


def _extract_class_annotations(
    node: Node,
    src: bytes,
) -> tuple[FieldNode, ...]:
    """Extract typed annotations from a class body (non-__init__).

    These are the ``name: type`` statements at class body level, used for
    both ``@dataclass`` field definitions and class attribute declarations.
    """
    body = _child_by_type(node, "block")
    if body is None:
        return ()
    fields: list[FieldNode] = []
    for child in body.children:
        if not child.is_named:
            continue
        if child.type == "expression_statement":
            inner = child.named_children[0] if child.named_children else None
            if inner is not None and inner.type == "assignment":
                _try_extract_field_from_assignment(inner, src, fields)
    return tuple(fields)


def _try_extract_field_from_assignment(
    assign_node: Node,
    src: bytes,
    fields: list[FieldNode],
) -> None:
    """Try to extract a field from an assignment node with a type annotation.

    Matches patterns like:
    - ``name: str``
    - ``name: str = "default"``
    """
    name_node = _child_by_type(assign_node, "identifier")
    type_node = _child_by_type(assign_node, "type")
    if name_node is None or type_node is None:
        return
    name = _node_text(name_node, src)
    ftype = _node_text(type_node, src)

    # Skip ClassVar annotations — they are not instance fields
    if ftype.startswith("ClassVar"):
        return

    vis = _get_visibility(name)
    fields.append(FieldNode(name=name, type=ftype, visibility=vis))


def _merge_class_and_init_fields(
    node: Node,
    src: bytes,
) -> tuple[FieldNode, ...]:
    """Combine class-level annotations with ``__init__`` self-assignments."""
    annotation_fields = _extract_class_annotations(node, src)
    init_fields = _extract_init_fields(node, src)
    seen = {f.name for f in annotation_fields}
    merged = list(annotation_fields)
    for f in init_fields:
        if f.name not in seen:
            merged.append(f)
            seen.add(f.name)
    return tuple(merged)


def _extract_init_fields(
    node: Node,
    src: bytes,
) -> tuple[FieldNode, ...]:
    """Extract fields from ``__init__`` self-assignments.

    Walks the ``__init__`` method body looking for ``self.name = …``
    assignments. Types are inferred from parameter annotations when
    available.
    """
    init_fn = _find_init_method(node, src)
    if init_fn is None:
        return ()

    param_types = _build_param_type_map(init_fn, src)
    return _collect_self_assignments(init_fn, src, param_types)


def _find_init_method(node: Node, src: bytes) -> Node | None:
    """Locate the ``__init__`` method in a class body."""
    body = _child_by_type(node, "block")
    if body is None:
        return None
    for child in body.children:
        if not child.is_named:
            continue
        fn: Node = child
        if child.type == "decorated_definition":
            inner = _child_by_type(child, "function_definition")
            if inner is None:
                continue
            fn = inner
        if fn.type == "function_definition" and _get_identifier(fn, src) == "__init__":
            return fn
    return None


def _build_param_type_map(init_fn: Node, src: bytes) -> dict[str, str]:
    """Build parameter name → type annotation map from __init__ signature."""
    param_types: dict[str, str] = {}
    params_node = _child_by_type(init_fn, "parameters")
    if params_node is not None:
        for child in params_node.named_children:
            if child.type in ("typed_parameter", "typed_default_parameter"):
                id_node = _child_by_type(child, "identifier")
                tp_node = _child_by_type(child, "type")
                if id_node and tp_node:
                    param_types[_node_text(id_node, src)] = _node_text(tp_node, src)
    return param_types


def _collect_self_assignments(
    init_fn: Node,
    src: bytes,
    param_types: dict[str, str],
) -> tuple[FieldNode, ...]:
    """Walk __init__ body for ``self.x = ...`` assignments."""
    init_body = _child_by_type(init_fn, "block")
    if init_body is None:
        return ()
    fields: list[FieldNode] = []
    seen: set[str] = set()
    for stmt in init_body.children:
        if not stmt.is_named:
            continue
        if stmt.type == "expression_statement":
            inner = stmt.named_children[0] if stmt.named_children else None
            if inner is not None and inner.type == "assignment":
                _try_extract_self_assignment(inner, src, param_types, fields, seen)
    return tuple(fields)


def _try_extract_self_assignment(
    assign_node: Node,
    src: bytes,
    param_types: dict[str, str],
    fields: list[FieldNode],
    seen: set[str],
) -> None:
    """Extract a field from ``self.name = value`` assignment."""
    # LHS must be an attribute access: self.name
    lhs = assign_node.named_children[0] if assign_node.named_children else None
    if lhs is None or lhs.type != "attribute":
        return
    parts = _children_by_type(lhs, "identifier")
    if len(parts) < 2 or _node_text(parts[0], src) != "self":  # noqa: PLR2004
        return
    name = _node_text(parts[1], src)
    if name in seen:
        return
    seen.add(name)
    ftype = param_types.get(name, "")
    vis = _get_visibility(name)
    fields.append(FieldNode(name=name, type=ftype, visibility=vis))


def _is_type_alias_assignment(
    name: str,
    node: Node,
    src: bytes,
) -> bool:
    """Heuristic to determine if an assignment is a type alias.

    An assignment ``Name = expr`` is treated as a type alias if:
    - The name is CamelCase (first letter uppercase, not ALL_CAPS).
    - OR the assignment has an explicit ``TypeAlias`` annotation.
    """
    # Explicit TypeAlias annotation
    if _has_explicit_type_alias_annotation(node, src):
        return True

    # CamelCase heuristic (uppercase start, not ALL_CAPS, not _private)
    return bool(
        name and name[0].isupper() and not _CONSTANT_NAME_RE.match(name)
    )


def _has_explicit_type_alias_annotation(node: Node, src: bytes) -> bool:
    """Return True if the assignment has an explicit ``TypeAlias`` annotation.

    Checks for ``name: TypeAlias = expr`` syntax.
    """
    type_node = _child_by_type(node, "type")
    if type_node is not None:
        ttext = _node_text(type_node, src)
        if "TypeAlias" in ttext:
            return True
    return False


# endregion: --- Tree-sitter helpers


# ---------------------------------------------------------------------------
# region:    --- Import map & scoped method calls
# ---------------------------------------------------------------------------


def build_import_map(use_statements: list[str]) -> dict[str, str]:
    """Parse Python import statements into a short_name → qualified path map.

    Handles:
    - ``import os`` → ``{"os": "os"}``
    - ``from pathlib import Path`` → ``{"Path": "pathlib.Path"}``
    - ``import importlib as il`` → ``{"il": "importlib"}``
    - ``from os.path import join`` → ``{"join": "os.path.join"}``

    Args:
        use_statements: Raw import statement strings from the file.

    Returns:
        Dict mapping short names to fully qualified paths.
    """
    import_map: dict[str, str] = {}
    for stmt in use_statements:
        cleaned = stmt.strip()
        if cleaned.startswith("from "):
            _parse_from_import(cleaned, import_map)
        elif cleaned.startswith("import "):
            _parse_plain_import(cleaned, import_map)
    return import_map


def _parse_from_import(stmt: str, result: dict[str, str]) -> None:
    """Parse ``from X import A, B as C`` into the import map."""
    parts = stmt.split(" import ", maxsplit=1)
    if len(parts) < 2:  # noqa: PLR2004
        return
    module = parts[0].removeprefix("from ").strip()
    # Handle parenthesized imports
    imports_str = parts[1].strip().strip("()")
    for raw_item in imports_str.split(","):
        entry = raw_item.strip()
        if not entry:
            continue
        if " as " in entry:
            original, alias = entry.split(" as ", maxsplit=1)
            result[alias.strip()] = f"{module}.{original.strip()}"
        else:
            result[entry] = f"{module}.{entry}"


def _parse_plain_import(stmt: str, result: dict[str, str]) -> None:
    """Parse ``import X`` or ``import X as Y`` into the import map."""
    rest = stmt.removeprefix("import ").strip()
    for raw_item in rest.split(","):
        entry = raw_item.strip()
        if not entry:
            continue
        if " as " in entry:
            original, alias = entry.split(" as ", maxsplit=1)
            result[alias.strip()] = original.strip()
        else:
            # Short name is the first component
            short = entry.split(".")[0]
            result[short] = entry


def _extract_scoped_method_calls(
    root: Node,
    src: bytes,
    import_map: dict[str, str],
) -> dict[str, list[str]]:
    """Walk the AST to find ``Type.method()`` scoped call expressions.

    Captures calls where the object is a known imported name, e.g.,
    ``Path.cwd()`` or ``logging.getLogger()``.

    Args:
        root: Tree-sitter root node.
        src: Source bytes.
        import_map: Short name → qualified path map.

    Returns:
        Dict of ``{"qualified.module": ["method1", "method2"]}``.
    """
    results: dict[str, set[str]] = {}

    stack: list[Node] = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "call":
            fn_node = _child_by_type(node, "attribute")
            if fn_node is not None:
                _resolve_attr_call(fn_node, src, import_map, results)
        stack.extend(node.children)
    return {k: sorted(v) for k, v in results.items()}


def _resolve_attr_call(
    node: Node,
    src: bytes,
    import_map: dict[str, str],
    results: dict[str, set[str]],
) -> None:
    """Resolve ``obj.method(...)`` into results if obj is in import_map."""
    ids = _children_by_type(node, "identifier")
    if len(ids) < 2:  # noqa: PLR2004
        return
    obj_name = _node_text(ids[0], src)
    method_name = _node_text(ids[-1], src)
    qualified = import_map.get(obj_name)
    if qualified is not None:
        results.setdefault(qualified, set()).add(method_name)


# endregion: --- Import map & scoped method calls


# ---------------------------------------------------------------------------
# region:    --- self_methods builder
# ---------------------------------------------------------------------------


def _collect_self_methods(
    functions: list[FunctionNode],
    impl_blocks: list[ImplBlockNode],
) -> list[MethodNode]:
    """Build flat list of every function/method defined in a file.

    Mirrors the Rust extractor pattern: free functions get ``context="free"``,
    impl block methods get ``context="impl:ClassName"`` or
    ``context="impl:Trait for ClassName"``.
    """
    methods: list[MethodNode] = []

    # Free functions
    methods.extend(
        MethodNode(
            name=fn.name,
            visibility=fn.visibility,
            is_async=fn.is_async,
            is_unsafe=False,
            params=fn.params,
            return_type=fn.return_type,
            context="free",
            attributes=fn.attributes,
            doc=fn.doc,
            span=fn.span,
        )
        for fn in functions
    )

    # Class methods (from impl blocks)
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
# region:    --- PythonExtractor class
# ---------------------------------------------------------------------------


class PythonExtractor(ExtractorBase):
    """Python language extractor using ``tree-sitter-python``.

    Parses ``.py`` / ``.pyi`` source files and ``pyproject.toml`` manifests
    into the normalized AST schema.
    """

    language_id: str = "python"
    file_extensions: list[str] = [".py", ".pyi"]  # noqa: RUF012

    # Base class names that map the class to a TraitNode.
    # (Uses module-level ``_ABSTRACT_BASES`` frozenset directly.)

    def __init__(self) -> None:
        """Initialize the Python parser with tree-sitter-python grammar."""
        import tree_sitter_python

        self._language = Language(tree_sitter_python.language())
        self._parser = Parser(self._language)

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a single ``.py`` file into a FileAST.

        Args:
            file_path: Absolute path to the ``.py`` file.
            source: Raw file bytes (UTF-8).

        Returns:
            Populated FileAST with all Python constructs extracted.
        """
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

        # Post-pass 5: HTTP route extraction
        from ast_intel.extractors._routes import (
            detect_fastapi_routes,
            detect_flask_routes,
        )
        ast.routes = detect_fastapi_routes(ast.functions, ast.self_methods)
        ast.routes += detect_flask_routes(ast.functions, ast.self_methods)

        # Post-pass 6: HTTP client call detection
        from ast_intel.extractors._http_calls import (
            detect_python_http_calls,
        )
        ast.http_calls = detect_python_http_calls(tree.root_node, source)

        # Post-pass 7: Cloud SDK client detection
        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_python,
        )

        ast.cloud_resources = detect_cloud_clients_python(tree.root_node, source)

        # Post-pass 8: Third-party SDK egress detection
        from ast_intel.extractors._sdk_calls import (
            detect_python_sdk_calls,
        )

        ast.sdk_calls = detect_python_sdk_calls(tree.root_node, source, ast.uses)

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

        if ntype in ("import_statement", "import_from_statement"):
            ast.uses.append(_node_text(node, src))

        elif ntype == "class_definition":
            self._handle_class(node, src, ast)

        elif ntype == "function_definition":
            self._extract_top_function(node, src, ast)

        elif ntype == "decorated_definition":
            self._handle_decorated(node, src, ast)

        elif ntype == "expression_statement":
            self._handle_expression_stmt(node, src, ast)

        elif ntype == "ERROR":
            ast.errors.append(
                f"parse error at byte {node.start_byte}: "
                f"{_node_text(node, src)[:_MAX_ERROR_PREVIEW_LENGTH]}"
            )

    def _handle_decorated(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Unwrap a ``decorated_definition`` and dispatch the inner item."""
        inner_class = _child_by_type(node, "class_definition")
        if inner_class is not None:
            self._handle_class(inner_class, src, ast)
            return
        inner_fn = _child_by_type(node, "function_definition")
        if inner_fn is not None:
            self._extract_top_function(inner_fn, src, ast)

    def _handle_class(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Dispatch class extraction — abstract classes become TraitNode, rest StructNode."""
        name = _get_identifier(node, src)
        bases = _extract_base_classes(node, src)
        decorator_names = _get_decorator_names(node, src)

        info = _ClassInfo(
            name=name,
            bases=bases,
            decorators=_extract_decorators(node, src),
            doc=_extract_docstring(node, src),
            vis=_get_visibility(name),
            is_abstract=_is_abstract_class(bases, decorator_names),
            is_dataclass="dataclass" in decorator_names,
        )

        if info.is_abstract:
            self._extract_trait(node, src, info, ast)
        else:
            self._extract_struct(node, src, info, ast)

        # Create ImplBlockNode for each non-abstract base class.
        # Python has no explicit "implements" keyword — base class
        # inheritance is a heuristic mapping to trait implementation.
        non_abstract_bases = tuple(
            b for b in bases
            if b.rsplit(".", maxsplit=1)[-1] not in _ABSTRACT_BASES
        )
        if non_abstract_bases:
            methods = self._extract_class_methods(node, src)
            for base in non_abstract_bases:
                ast.impl_blocks.append(
                    ImplBlockNode(
                        self_type=name,
                        trait_type=base,
                        generics="",
                        methods=methods,
                        span=span_from_node(node),
                        confidence=Confidence.INFERRED,
                        confidence_score=SCORE_INFERRED,
                    )
                )
        else:
            # Inherent impl block — classes with methods get one
            methods = self._extract_class_methods(node, src)
            if methods:
                ast.impl_blocks.append(
                    ImplBlockNode(
                        self_type=name,
                        trait_type="",
                        generics="",
                        methods=methods,
                        span=span_from_node(node),
                    )
                )

    def _extract_trait(
        self,
        node: Node,
        src: bytes,
        info: _ClassInfo,
        ast: FileAST,
    ) -> None:
        """Extract an abstract class / Protocol as a TraitNode."""
        super_traits = tuple(
            b for b in info.bases
            if b.rsplit(".", maxsplit=1)[-1] in _ABSTRACT_BASES
        )

        items = self._extract_trait_items(node, src)

        ast.traits.append(
            TraitNode(
                name=info.name,
                visibility=info.vis,
                generics="",
                super_traits=super_traits,
                items=items,
                attributes=info.decorators,
                doc=info.doc,
                span=span_from_node(node),
            )
        )

    def _extract_trait_items(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[TraitItemNode, ...]:
        """Extract trait-level items (methods) from a class body."""
        body = _child_by_type(node, "block")
        if body is None:
            return ()
        items: list[TraitItemNode] = []
        for child in body.children:
            if not child.is_named:
                continue
            fn_node: Node | None = None
            child_decorators: frozenset[str] = frozenset()

            if child.type == "function_definition":
                fn_node = child
            elif child.type == "decorated_definition":
                fn_node = _child_by_type(child, "function_definition")
                child_decorators = frozenset(
                    _node_text(d, src).lstrip("@").split("(")[0]
                    for d in _children_by_type(child, "decorator")
                )

            if fn_node is None:
                continue

            fname = _get_identifier(fn_node, src)
            is_abstract = bool(child_decorators & _ABSTRACT_DECORATORS)
            kind = (
                TraitItemKind.REQUIRED_METHOD
                if is_abstract
                else TraitItemKind.DEFAULT_METHOD
            )
            params = _extract_parameters(
                _child_by_type(fn_node, "parameters"), src,
            )
            rtype = _extract_return_type(fn_node, src)
            is_async = _is_async_function(fn_node)
            fdoc = _extract_docstring(fn_node, src)

            items.append(
                TraitItemNode(
                    kind=kind,
                    name=fname,
                    is_async=is_async,
                    params=params,
                    return_type=rtype,
                    doc=fdoc,
                    span=span_from_node(fn_node),
                )
            )
        return tuple(items)

    def _extract_struct(
        self,
        node: Node,
        src: bytes,
        info: _ClassInfo,
        ast: FileAST,
    ) -> None:
        """Extract a regular or dataclass as a StructNode."""
        if info.is_dataclass:
            fields = _extract_class_annotations(node, src)
        else:
            fields = _merge_class_and_init_fields(node, src)

        ast.structs.append(
            StructNode(
                name=info.name,
                visibility=info.vis,
                generics="",
                fields=fields,
                attributes=info.decorators,
                doc=info.doc,
                span=span_from_node(node),
            )
        )

    def _extract_class_methods(
        self,
        node: Node,
        src: bytes,
    ) -> tuple[MethodNode, ...]:
        """Extract methods from a class body as MethodNode tuples."""
        body = _child_by_type(node, "block")
        if body is None:
            return ()
        methods: list[MethodNode] = []
        for child in body.children:
            if not child.is_named:
                continue
            fn_node: Node | None = None
            decorators: tuple[str, ...] = ()

            if child.type == "function_definition":
                fn_node = child
            elif child.type == "decorated_definition":
                fn_node = _child_by_type(child, "function_definition")
                decorators = _extract_decorators_from_decorated(child, src)

            if fn_node is None:
                continue

            fname = _get_identifier(fn_node, src)
            params = _extract_parameters(
                _child_by_type(fn_node, "parameters"), src,
            )
            rtype = _extract_return_type(fn_node, src)
            is_async = _is_async_function(fn_node)
            fvis = _get_visibility(fname)

            # Detect static methods (no self/cls param)
            is_static = not _has_self_param(fn_node, src) and not _has_cls_param(
                fn_node, src,
            )

            fdoc = _extract_docstring(fn_node, src)

            methods.append(
                MethodNode(
                    name=fname,
                    visibility=fvis,
                    is_async=is_async,
                    is_unsafe=False,
                    is_static=is_static,
                    params=params,
                    return_type=rtype,
                    attributes=decorators,
                    doc=fdoc,
                    span=span_from_node(fn_node),
                )
            )
        return tuple(methods)

    def _extract_top_function(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Extract a top-level function_definition into a FunctionNode."""
        name = _get_identifier(node, src)
        decorators = _extract_decorators(node, src)
        params = _extract_parameters(_child_by_type(node, "parameters"), src)
        rtype = _extract_return_type(node, src)
        is_async = _is_async_function(node)
        vis = _get_visibility(name)
        doc = _extract_docstring(node, src)

        ast.functions.append(
            FunctionNode(
                name=name,
                visibility=vis,
                is_async=is_async,
                is_unsafe=False,
                generics="",
                params=params,
                return_type=rtype,
                where_clause="",
                attributes=decorators,
                doc=doc,
                span=span_from_node(node),
            )
        )

    def _handle_expression_stmt(
        self,
        node: Node,
        src: bytes,
        ast: FileAST,
    ) -> None:
        """Handle module-level expression statements (constants, type aliases)."""
        inner = node.named_children[0] if node.named_children else None
        if inner is None or inner.type != "assignment":
            return

        name_node = _child_by_type(inner, "identifier")
        if name_node is None:
            return
        name = _node_text(name_node, src)

        # Type alias detection
        if _is_type_alias_assignment(name, inner, src):
            # Explicit TypeAlias annotation → EXTRACTED;
            # CamelCase heuristic only → INFERRED.
            has_explicit_annotation = _has_explicit_type_alias_annotation(
                inner, src,
            )
            confidence = (
                Confidence.EXTRACTED
                if has_explicit_annotation
                else Confidence.INFERRED
            )
            score = SCORE_EXTRACTED if has_explicit_annotation else SCORE_INFERRED
            rhs = self._extract_assignment_rhs(inner, src)
            ast.type_aliases.append(
                TypeAliasNode(
                    name=name,
                    aliased_to=rhs,
                    visibility=_get_visibility(name),
                    span=span_from_node(node),
                    confidence=confidence,
                    confidence_score=score,
                )
            )
            return

        # Constants: UPPER_CASE or has type annotation
        type_node = _child_by_type(inner, "type")
        has_type = type_node is not None
        is_upper = _CONSTANT_NAME_RE.match(name) is not None
        if has_type or is_upper:
            # Typed annotation → EXTRACTED; UPPER_CASE heuristic → INFERRED
            confidence = (
                Confidence.EXTRACTED if has_type else Confidence.INFERRED
            )
            score = SCORE_EXTRACTED if has_type else SCORE_INFERRED
            raw = _node_text(inner, src)[:_MAX_RAW_CONSTANT_LENGTH]
            ast.constants.append(
                ConstantNode(
                    name=name,
                    visibility=_get_visibility(name),
                    raw=raw,
                    span=span_from_node(node),
                    confidence=confidence,
                    confidence_score=score,
                )
            )

    @staticmethod
    def _extract_assignment_rhs(node: Node, src: bytes) -> str:
        """Extract the right-hand side text of an assignment.

        Skips the identifier and optional type annotation to get the value.
        """
        # For typed assignment: the RHS is the last named child that isn't
        # the identifier or type
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq and child.is_named:
                return _node_text(child, src)
        # Fallback: if it's name: TypeAlias = rhs, grab after type
        type_node = _child_by_type(node, "type")
        if type_node is not None:
            # return text after the type annotation
            remaining = node.named_children
            for i, c in enumerate(remaining):
                if c == type_node and i + 1 < len(remaining):
                    return _node_text(remaining[i + 1], src)
        return ""

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a ``pyproject.toml`` manifest file.

        Args:
            manifest_path: Absolute path to ``pyproject.toml``.

        Returns:
            CrateModel with name, version, language, dependencies extracted.
        """
        try:
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            logger.warning("Failed to parse pyproject.toml: %s", manifest_path)
            return CrateModel(
                name=manifest_path.parent.name,
                language="python",
                manifest_path=str(manifest_path),
            )

        project = data.get("project", {})
        name = project.get("name", manifest_path.parent.name)
        version = project.get("version", "")

        deps = self._parse_python_deps(
            project.get("dependencies", []),
            is_dev=False,
        )
        # Aggregate optional-dependencies as dev deps
        opt_deps_raw = project.get("optional-dependencies", {})
        dev_deps: list[CrateDependency] = []
        for pkgs in opt_deps_raw.values():
            if isinstance(pkgs, list):
                dev_deps.extend(
                    self._parse_python_deps(pkgs, is_dev=True),
                )

        return CrateModel(
            name=name,
            version=str(version) if version else "",
            manifest_path=str(manifest_path),
            language="python",
            dependencies=deps + dev_deps,
        )

    @staticmethod
    def _parse_python_deps(
        deps_raw: list[str],
        *,
        is_dev: bool,
    ) -> list[CrateDependency]:
        """Parse PEP 508 dependency strings into CrateDependency list.

        Each entry is like ``"requests>=2.28.0"`` or ``"click"``.
        """
        deps: list[CrateDependency] = []
        for raw_entry in deps_raw:
            if not isinstance(raw_entry, str):
                continue
            entry = raw_entry.strip()
            if not entry:
                continue
            # Split on version specifier
            match = _DEP_VERSION_RE.search(entry)
            if match:
                name = entry[: match.start()].strip()
                version = entry[match.start() :].strip()
            else:
                # Might have extras: name[extra1,extra2]
                bracket_idx = entry.find("[")
                if bracket_idx > 0:
                    name = entry[:bracket_idx].strip()
                    version = ""
                else:
                    name = entry
                    version = ""
            deps.append(
                CrateDependency(
                    name=name,
                    version=version,
                    is_dev=is_dev,
                )
            )
        return deps


# endregion: --- PythonExtractor class
