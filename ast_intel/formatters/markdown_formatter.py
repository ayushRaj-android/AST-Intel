"""Markdown formatter — serializes WorkspaceAST to a human/LLM-readable summary.

Produces ``summary.md`` with:
- Workspace overview table (crate counts, file counts, statistics)
- Inter-crate dependency graph
- Per-crate sections: structs, enums, traits, trait impls, public functions
- Global cross-reference section (trait impl map, type indexes)

The output is designed to be used as a system prompt prefix for coding agents.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from ast_intel.models.ast_node import (
    CallEdge,
    Confidence,
    EnumNode,
    FunctionNode,
    ImplBlockNode,
    RationaleNode,
    StructNode,
    TraitNode,
    Visibility,
)
from ast_intel.models.workspace_model import (
    CrateModel,
    CrossReferences,
    WorkspaceAST,
    WorkspaceMeta,
)

__all__: list[str] = ["MarkdownFormatter"]

logger = logging.getLogger(__name__)

# Type alias for the line-appender callable (``list.append`` bound to a list)
_LineAppender = Callable[[str], None]

# Maximum items to display before truncation
_MAX_FIELDS_SHOWN = 5
_MAX_VARIANTS_SHOWN = 8
_MAX_METHODS_SHOWN = 6
_MAX_PUB_FNS_PER_CRATE = 30
_MAX_TRAIT_IMPL_ROWS = 60
_MAX_IMPLEMENTORS_SHOWN = 5


class MarkdownFormatter:
    """Serialize a ``WorkspaceAST`` to a Markdown summary file.

    The output is a structured document that coding agents can consume
    as context, replacing the need to read individual source files.
    """

    def write(self, workspace: WorkspaceAST, output_path: Path) -> None:
        """Write the workspace AST summary to a Markdown file.

        Args:
            workspace: The fully indexed workspace AST.
            output_path: Absolute path to write ``summary.md``.
        """
        logger.info("Writing Markdown summary to %s", output_path)

        lines = self._render(workspace)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.1f KB)", output_path.name, size_kb)

    @staticmethod
    def _render(workspace: WorkspaceAST) -> list[str]:
        """Render the workspace AST as Markdown lines.

        Args:
            workspace: The workspace AST to render.

        Returns:
            List of Markdown lines.
        """
        lines: list[str] = []
        a = lines.append

        _render_header(a, workspace.meta)
        _render_inter_crate_deps(a, workspace.cross_references)
        _render_crate_reference(a, workspace.crates)
        _render_global_cross_refs(a, workspace.cross_references)

        return lines


# ---------------------------------------------------------------------------
# region:    --- Section Renderers
# ---------------------------------------------------------------------------


def _render_header(a: _LineAppender, meta: WorkspaceMeta) -> None:
    """Render the document header and overview table."""
    a("# AST Intel — Workspace Summary")
    a("")
    a("Generated from source files and manifests. This document is the structural")
    a("skeleton of the codebase: types, traits, functions, and their relationships.")
    a("Designed for coding agents to navigate the codebase without full-text search.")
    a("")
    a("## Workspace Overview")
    a("")
    a("| Metric | Count |")
    a("|--------|-------|")
    a(f"| Schema Version | {meta.schema_version} |")
    a(f"| Tool Version | {meta.tool_version} |")
    a(f"| Generated At | {meta.generated_at} |")
    a(f"| Workspace Root | `{meta.workspace_root}` |")
    a(f"| Crates | {meta.total_crates} |")
    a(f"| Files | {meta.total_files} |")
    a(f"| Structs | {meta.total_structs} |")
    a(f"| Enums | {meta.total_enums} |")
    a(f"| Traits | {meta.total_traits} |")
    a(f"| Free Functions | {meta.total_functions} |")
    a(f"| Impl Blocks | {meta.total_impl_blocks} |")
    a(f"| Self Methods | {meta.total_self_methods} |")
    a(f"| Pkg Method Call Sites | {meta.total_pkg_method_call_sites} |")
    a(f"| Call Edges | {meta.total_call_edges} |")
    a(f"| Rationale Comments | {meta.total_rationale_comments} |")
    a(f"| Parse Errors | {meta.total_errors} |")
    a("")


def _render_inter_crate_deps(a: _LineAppender, xref: CrossReferences) -> None:
    """Render the inter-crate dependency graph."""
    inter = xref.inter_crate_deps
    if not inter:
        return

    a("## Inter-Crate Dependency Graph")
    a("")
    a("```")
    for crate_name in sorted(inter):
        deps = inter[crate_name]
        if deps:
            a(crate_name)
            for dep in deps:
                a(f"  └─ {dep}")
    a("```")
    a("")


def _render_crate_reference(
    a: _LineAppender,
    crates: dict[str, CrateModel],
) -> None:
    """Render per-crate sections."""
    a("## Crate Reference")
    a("")

    for crate_name, crate in sorted(crates.items()):
        _render_single_crate(a, crate_name, crate)


def _render_single_crate(
    a: _LineAppender,
    crate_name: str,
    crate: CrateModel,
) -> None:
    """Render one crate's section."""
    # Crate heading with stats
    stats = _compute_crate_stats(crate)
    a(f"### `{crate_name}`")
    a("")
    if crate.manifest_path:
        a(f"**Manifest**: `{crate.manifest_path}`  ")
    a(
        f"**Files**: {stats.files} | "
        f"**Structs**: {stats.structs} | "
        f"**Enums**: {stats.enums} | "
        f"**Traits**: {stats.traits} | "
        f"**Fns**: {stats.functions} | "
        f"**Impl blocks**: {stats.impl_blocks}"
    )
    a("")

    # Workspace dependencies
    ws_deps = [
        d.name for d in crate.dependencies
        if d.path or d.is_workspace
    ]
    if ws_deps:
        a(f"**Workspace deps**: {', '.join(f'`{d}`' for d in sorted(ws_deps))}")
        a("")

    # Module file tree
    _render_module_tree(a, crate)

    # Collect all items across files
    items = _collect_crate_items(crate)

    # Structs table
    if items.structs:
        _render_structs_table(a, items.structs)

    # Enums table
    if items.enums:
        _render_enums_table(a, items.enums)

    # Detailed trait rendering
    if items.traits:
        _render_traits_section(a, items.traits)

    # Trait implementations table
    trait_impls = [
        (mod, im) for mod, im in items.impl_blocks if im.trait_type
    ]
    if trait_impls:
        _render_trait_impls_table(a, trait_impls)

    # Public functions
    pub_fns = [
        (mod, fn) for mod, fn in items.functions
        if fn.visibility == Visibility.PUBLIC
    ]
    if pub_fns:
        _render_pub_fns(a, pub_fns)

    # Rationale comments
    if items.rationale:
        _render_rationale_section(a, items.rationale)

    # Call graph
    if items.call_edges:
        _render_call_graph_section(a, items.call_edges)

    a("---")
    a("")


def _render_module_tree(a: _LineAppender, crate: CrateModel) -> None:
    """Render a module file tree for the crate."""
    non_test_files = [f for f in crate.files if not f.is_test]
    if not non_test_files:
        return

    a("#### Module File Tree")
    a("")
    a("```")
    for file_ast in sorted(non_test_files, key=lambda f: f.module_path or f.file):
        mod = file_ast.module_path or file_ast.file
        # Indent based on module depth
        depth = mod.count("::") if "::" in mod else 0
        indent = "  " * depth
        a(f"{indent}{mod}")
        a(f"{indent}  └ {file_ast.file}")
    a("```")
    a("")


def _render_structs_table(
    a: _LineAppender,
    all_structs: list[tuple[str, StructNode]],
) -> None:
    """Render structs as a Markdown table."""
    a("#### Structs")
    a("")
    a("| Name | Module | Visibility | Line | Fields |")
    a("|------|--------|------------|------|--------|")
    for mod, s in all_structs:
        fields_str = ", ".join(
            f"`{f.name}: {f.type}`" for f in s.fields[:_MAX_FIELDS_SHOWN]
        )
        if len(s.fields) > _MAX_FIELDS_SHOWN:
            fields_str += f" +{len(s.fields) - _MAX_FIELDS_SHOWN} more"
        gen = s.generics or ""
        line = str(s.span.start_line) if s.span else ""
        a(f"| `{s.name}{gen}` | `{mod}` | {s.visibility} | {line} | {fields_str} |")
    a("")


def _render_enums_table(
    a: _LineAppender,
    all_enums: list[tuple[str, EnumNode]],
) -> None:
    """Render enums as a Markdown table."""
    a("#### Enums")
    a("")
    a("| Name | Module | Line | Variants |")
    a("|------|--------|------|----------|")
    for mod, e in all_enums:
        variants_str = ", ".join(
            f"`{v.name}`" for v in e.variants[:_MAX_VARIANTS_SHOWN]
        )
        if len(e.variants) > _MAX_VARIANTS_SHOWN:
            variants_str += f" +{len(e.variants) - _MAX_VARIANTS_SHOWN} more"
        line = str(e.span.start_line) if e.span else ""
        a(f"| `{e.name}` | `{mod}` | {line} | {variants_str} |")
    a("")


def _render_traits_section(
    a: _LineAppender,
    all_traits: list[tuple[str, TraitNode]],
) -> None:
    """Render traits with detailed method signatures."""
    a("#### Traits")
    a("")
    for mod, t in all_traits:
        super_str = (
            " : " + " + ".join(t.super_traits) if t.super_traits else ""
        )
        line_hint = f" (L{t.span.start_line})" if t.span else ""
        a(f"##### `{t.name}{super_str}` — `{mod}`{line_hint}")
        a("")
        if t.doc:
            a(f"*{t.doc}*")
            a("")

        # Categorize items
        required = [i for i in t.items if i.kind == "required_method"]
        default = [i for i in t.items if i.kind == "default_method"]
        assoc_types = [i for i in t.items if i.kind == "associated_type"]

        if assoc_types:
            assoc_str = ", ".join(f"`{i.name}`" for i in assoc_types)
            a(f"Associated types: {assoc_str}")
            a("")
        if required:
            a("Required methods:")
            for m in required:
                params = ", ".join(p.name for p in m.params)
                ret = f" -> `{m.return_type}`" if m.return_type else ""
                async_pfx = "async " if m.is_async else ""
                a(f"- `{async_pfx}{m.name}({params})`{ret}")
            a("")
        if default:
            a("Default methods:")
            for m in default:
                params = ", ".join(p.name for p in m.params)
                a(f"- `{m.name}({params})`")
            a("")


def _render_trait_impls_table(
    a: _LineAppender,
    trait_impls: list[tuple[str, ImplBlockNode]],
) -> None:
    """Render trait implementation blocks as a table."""
    a("#### Trait Implementations")
    a("")
    a("| Trait | For | Module | Line | Methods | Confidence |")
    a("|-------|-----|--------|------|---------|------------|")
    for mod, im in trait_impls:
        methods_str = ", ".join(
            f"`{m.name}`" for m in im.methods[:_MAX_METHODS_SHOWN]
        )
        if len(im.methods) > _MAX_METHODS_SHOWN:
            methods_str += f" +{len(im.methods) - _MAX_METHODS_SHOWN} more"
        line = str(im.span.start_line) if im.span else ""
        conf = _confidence_tag(im.confidence)
        a(
            f"| `{im.trait_type}` | `{im.self_type}` | `{mod}` "
            f"| {line} | {methods_str} | {conf} |"
        )
    a("")


def _render_pub_fns(
    a: _LineAppender,
    pub_fns: list[tuple[str, FunctionNode]],
) -> None:
    """Render public function list."""
    a("#### Public Functions")
    a("")
    for mod, fn in pub_fns[:_MAX_PUB_FNS_PER_CRATE]:
        params = ", ".join(p.name for p in fn.params)
        ret = f" -> `{fn.return_type}`" if fn.return_type else ""
        async_pfx = "async " if fn.is_async else ""
        line_hint = f" (L{fn.span.start_line})" if fn.span else ""
        a(f"- `{async_pfx}{fn.name}({params})`{ret} — `{mod}`{line_hint}")
    if len(pub_fns) > _MAX_PUB_FNS_PER_CRATE:
        a(f"- ... and {len(pub_fns) - _MAX_PUB_FNS_PER_CRATE} more")
    a("")


def _render_rationale_section(
    a: _LineAppender,
    rationale: list[tuple[str, RationaleNode]],
) -> None:
    """Render rationale comments as a Markdown table."""
    a("#### Rationale Comments")
    a("")
    a("| Kind | Text | Module | Line | Scope |")
    a("|------|------|--------|------|-------|")
    for mod, r in rationale:
        line = str(r.span.start_line) if r.span else ""
        # Truncate long text to keep the table readable
        text = r.text if len(r.text) <= 80 else r.text[:77] + "..."  # noqa: PLR2004
        # Escape pipes in text to avoid breaking the Markdown table
        text = text.replace("|", "\\|")
        a(f"| {r.kind} | {text} | `{mod}` | {line} | `{r.parent}` |")
    a("")


_MAX_CALL_EDGES_SHOWN = 50


def _render_call_graph_section(
    a: _LineAppender,
    call_edges: list[tuple[str, CallEdge]],
) -> None:
    """Render call-graph edges as a Markdown table."""
    a("#### Call Graph")
    a("")
    a("| Caller | Callee | Line | Resolved Target | Method? | Confidence |")
    a("|--------|--------|------|-----------------|---------|------------|")
    for _mod, ce in call_edges[:_MAX_CALL_EDGES_SHOWN]:
        line = str(ce.call_site.start_line) if ce.call_site else ""
        resolved = f"`{ce.resolved_target}`" if ce.resolved_target else "—"
        method = "yes" if ce.is_method_call else ""
        conf = _confidence_tag(ce.confidence)
        a(
            f"| `{ce.caller}` | `{ce.callee}` | {line} "
            f"| {resolved} | {method} | {conf} |"
        )
    if len(call_edges) > _MAX_CALL_EDGES_SHOWN:
        a(
            f"| ... | +{len(call_edges) - _MAX_CALL_EDGES_SHOWN} more"
            f" edges | | | | |"
        )
    a("")


def _render_global_cross_refs(a: _LineAppender, xref: CrossReferences) -> None:
    """Render the global cross-reference section at the end of the document."""
    a("## Global Cross-Reference Index")
    a("")

    # Trait Implementation Map
    _render_trait_impl_map(a, xref)

    # Struct Index
    if xref.struct_index:
        a("### Type Index (Structs)")
        a("")
        a("| Struct Name | Defined In |")
        a("|-------------|-----------|")
        for name in sorted(xref.struct_index):
            paths = xref.struct_index[name]
            a(f"| `{name}` | {', '.join(f'`{p}`' for p in paths)} |")
        a("")

    # Enum Index
    if xref.enum_index:
        a("### Type Index (Enums)")
        a("")
        a("| Enum Name | Defined In |")
        a("|-----------|-----------|")
        for name in sorted(xref.enum_index):
            paths = xref.enum_index[name]
            a(f"| `{name}` | {', '.join(f'`{p}`' for p in paths)} |")
        a("")

    # Trait Index
    if xref.trait_index:
        a("### Trait Index")
        a("")
        a("| Trait | Defined In |")
        a("|-------|-----------|")
        for name in sorted(xref.trait_index):
            paths = xref.trait_index[name]
            a(f"| `{name}` | {', '.join(f'`{p}`' for p in paths)} |")
        a("")


def _render_trait_impl_map(a: _LineAppender, xref: CrossReferences) -> None:
    """Render the trait → implementors cross-ref table."""
    if not xref.impl_map:
        return

    a("### Trait Implementation Map")
    a("")
    a("Shows which types implement which traits across the workspace.")
    a("")
    a("| Trait | Implemented By |")
    a("|-------|----------------|")

    # Group by trait from impl_map
    by_trait: dict[str, list[str]] = defaultdict(list)
    for entry in xref.impl_map:
        by_trait[entry.trait_name].append(entry.for_type)

    for trait_name, implementors in sorted(by_trait.items())[:_MAX_TRAIT_IMPL_ROWS]:
        impls_str = ", ".join(
            f"`{i}`" for i in implementors[:_MAX_IMPLEMENTORS_SHOWN]
        )
        if len(implementors) > _MAX_IMPLEMENTORS_SHOWN:
            impls_str += f" +{len(implementors) - _MAX_IMPLEMENTORS_SHOWN} more"
        a(f"| `{trait_name}` | {impls_str} |")
    a("")


# endregion: --- Section Renderers


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _confidence_tag(confidence: Confidence | str) -> str:
    """Return a Markdown annotation for non-EXTRACTED confidence levels.

    EXTRACTED items are the norm and need no annotation.
    INFERRED and AMBIGUOUS items get a visible tag so consumers can
    filter or weight them differently.  Unknown future variants
    fall through to a generic ``[value]`` label.

    Accepts both ``Confidence`` enum instances and plain strings
    (e.g., from cache-restored SimpleNamespace objects).
    """
    if isinstance(confidence, str):
        if confidence == "extracted":
            return ""
        return f"[{confidence}]"
    if confidence == Confidence.EXTRACTED:
        return ""
    return f"[{confidence.value}]"


def _compute_crate_stats(crate: CrateModel) -> _CrateStats:
    """Compute per-crate statistics."""
    return _CrateStats(
        files=len(crate.files),
        structs=sum(len(f.structs) for f in crate.files),
        enums=sum(len(f.enums) for f in crate.files),
        traits=sum(len(f.traits) for f in crate.files),
        functions=sum(len(f.functions) for f in crate.files),
        impl_blocks=sum(len(f.impl_blocks) for f in crate.files),
    )


class _CrateItems(NamedTuple):
    """Collected AST items across all files in a crate."""

    structs: list[tuple[str, StructNode]]
    enums: list[tuple[str, EnumNode]]
    traits: list[tuple[str, TraitNode]]
    functions: list[tuple[str, FunctionNode]]
    impl_blocks: list[tuple[str, ImplBlockNode]]
    call_edges: list[tuple[str, CallEdge]]
    rationale: list[tuple[str, RationaleNode]]


class _CrateStats(NamedTuple):
    """Per-crate statistics."""

    files: int
    structs: int
    enums: int
    traits: int
    functions: int
    impl_blocks: int


def _collect_crate_items(crate: CrateModel) -> _CrateItems:
    """Collect all AST items from all files in a crate."""
    all_structs: list[tuple[str, StructNode]] = []
    all_enums: list[tuple[str, EnumNode]] = []
    all_traits: list[tuple[str, TraitNode]] = []
    all_fns: list[tuple[str, FunctionNode]] = []
    all_impls: list[tuple[str, ImplBlockNode]] = []
    all_call_edges: list[tuple[str, CallEdge]] = []
    all_rationale: list[tuple[str, RationaleNode]] = []
    for file_ast in crate.files:
        mod = file_ast.module_path or file_ast.file
        all_structs.extend((mod, s) for s in file_ast.structs)
        all_enums.extend((mod, e) for e in file_ast.enums)
        all_traits.extend((mod, t) for t in file_ast.traits)
        all_fns.extend((mod, fn) for fn in file_ast.functions)
        all_impls.extend((mod, im) for im in file_ast.impl_blocks)
        all_call_edges.extend((mod, ce) for ce in file_ast.call_edges)
        all_rationale.extend((mod, r) for r in file_ast.rationale_comments)
    return _CrateItems(
        all_structs, all_enums, all_traits, all_fns, all_impls,
        all_call_edges, all_rationale,
    )


# endregion: --- Helpers
