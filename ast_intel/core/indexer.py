"""Indexer — builds global cross-reference indexes after extraction.

After all files have been parsed by the dispatcher, the indexer walks every
``FileAST`` and builds the global lookup tables that enable O(1) queries:

- **Pass 1**: ``struct_index``, ``enum_index``, ``trait_index``
- **Pass 2**: ``trait_implementations`` + ``impl_map``
- **Pass 3**: ``function_file_index``
- **Pass 4**: ``package_method_index``
- **Pass 5**: ``inter_crate_deps``
- **Pass 6**: Cross-file call-edge resolution

Each pass is a separate method for testability and clarity. All passes
operate on the same ``CrossReferences`` instance, writing disjoint fields.
"""

from __future__ import annotations

import logging

from ast_intel.models.ast_node import (
    SCORE_AMBIGUOUS,
    SCORE_INFERRED_CROSS_FILE,
    CallEdge,
    Confidence,
    FileAST,
)
from ast_intel.models.workspace_model import (
    CrateModel,
    CrossReferences,
    FunctionIndexEntry,
    ImplMapEntry,
    WorkspaceAST,
)

__all__: list[str] = ["Indexer"]

logger = logging.getLogger(__name__)


class Indexer:
    """Build cross-reference indexes from a fully extracted workspace.

    The indexer is stateless — it operates on the immutable collection of
    ``FileAST`` entries and produces a new ``CrossReferences`` instance.

    Six sequential passes build disjoint sections of the index:

    1. Type indexes (struct, enum, trait) — name → qualified module paths
    2. Trait implementations + impl map — trait → implementors
    3. Function file index — function/method name → definition locations
    4. Package method index — ``pkg::Type::method`` → calling files
    5. Inter-crate dependency graph — crate → dependency crate names
    6. Cross-file call resolution — resolve unresolved ``CallEdge`` targets
    """

    def build_cross_references(self, workspace: WorkspaceAST) -> WorkspaceAST:
        """Build all cross-reference indexes and attach them to the workspace.

        Args:
            workspace: A ``WorkspaceAST`` with fully populated ``FileAST`` entries.

        Returns:
            The same ``WorkspaceAST`` with ``cross_references`` populated.
        """
        logger.info("Building cross-reference indexes...")

        xref = CrossReferences()

        # Collect all (crate_name, file_ast) pairs for iteration
        file_pairs = _collect_file_pairs(workspace)

        # Pass 1: type indexes
        _build_type_indexes(xref, file_pairs)

        # Pass 2: trait implementations + impl map
        _build_trait_implementations(xref, file_pairs)

        # Pass 3: function file index
        _build_function_file_index(xref, file_pairs)

        # Pass 4: package method index
        _build_package_method_index(xref, file_pairs)

        # Pass 5: inter-crate dependencies
        _build_inter_crate_deps(xref, workspace.crates)

        # Pass 6: cross-file call resolution
        _resolve_cross_file_calls(xref, file_pairs)

        workspace.cross_references = xref

        logger.info(
            "Cross-references built: %d structs, %d enums, %d traits, "
            "%d functions, %d package methods, %d inter-crate deps",
            len(xref.struct_index),
            len(xref.enum_index),
            len(xref.trait_index),
            len(xref.function_file_index),
            len(xref.package_method_index),
            len(xref.inter_crate_deps),
        )

        return workspace


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _collect_file_pairs(
    workspace: WorkspaceAST,
) -> list[tuple[str, FileAST]]:
    """Flatten workspace crates into (crate_name, file_ast) pairs."""
    pairs: list[tuple[str, FileAST]] = []
    for crate_name, crate in workspace.crates.items():
        pairs.extend((crate_name, f) for f in crate.files)
    return pairs


def _module_path_or_file(file_ast: FileAST) -> str:
    """Return the module_path if set, otherwise the file path."""
    return file_ast.module_path or file_ast.file


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Pass 1: Type Indexes
# ---------------------------------------------------------------------------


def _build_type_indexes(
    xref: CrossReferences,
    file_pairs: list[tuple[str, FileAST]],
) -> None:
    """Pass 1: Build struct_index, enum_index, and trait_index.

    Maps each type name to a list of qualified module paths where it
    is defined. Multiple definitions with the same name in different
    modules result in multiple entries.

    Args:
        xref: CrossReferences to populate.
        file_pairs: List of (crate_name, file_ast) pairs.
    """
    for _crate, file_ast in file_pairs:
        mod = _module_path_or_file(file_ast)

        for struct_node in file_ast.structs:
            qualified = f"{mod}::{struct_node.name}"
            xref.struct_index.setdefault(struct_node.name, []).append(qualified)

        for enum_node in file_ast.enums:
            qualified = f"{mod}::{enum_node.name}"
            xref.enum_index.setdefault(enum_node.name, []).append(qualified)

        for trait_node in file_ast.traits:
            qualified = f"{mod}::{trait_node.name}"
            xref.trait_index.setdefault(trait_node.name, []).append(qualified)


# endregion: --- Pass 1: Type Indexes


# ---------------------------------------------------------------------------
# region:    --- Pass 2: Trait Implementations
# ---------------------------------------------------------------------------


def _build_trait_implementations(
    xref: CrossReferences,
    file_pairs: list[tuple[str, FileAST]],
) -> None:
    """Pass 2: Build trait_implementations and impl_map.

    For each ``impl Trait for Type`` block, records:
    - ``trait_implementations[trait_name]`` → list of ``module::Type`` strings
    - ``impl_map`` → list of ``ImplMapEntry`` with full metadata

    Inherent impls (no trait_type) are skipped.

    Args:
        xref: CrossReferences to populate.
        file_pairs: List of (crate_name, file_ast) pairs.
    """
    for _crate, file_ast in file_pairs:
        mod = _module_path_or_file(file_ast)

        for impl_block in file_ast.impl_blocks:
            if not impl_block.trait_type or not impl_block.self_type:
                continue

            # trait_implementations: trait_name → [module::Type]
            qualified_type = f"{mod}::{impl_block.self_type}"
            xref.trait_implementations.setdefault(
                impl_block.trait_type, [],
            ).append(qualified_type)

            # impl_map: full structured entry
            method_names = tuple(m.name for m in impl_block.methods)
            xref.impl_map.append(
                ImplMapEntry(
                    trait_name=impl_block.trait_type,
                    for_type=impl_block.self_type,
                    in_module=mod,
                    methods=method_names,
                )
            )


# endregion: --- Pass 2: Trait Implementations


# ---------------------------------------------------------------------------
# region:    --- Pass 3: Function File Index
# ---------------------------------------------------------------------------


def _build_function_file_index(
    xref: CrossReferences,
    file_pairs: list[tuple[str, FileAST]],
) -> None:
    """Pass 3: Build function_file_index from self_methods.

    Each ``self_methods`` entry becomes a ``FunctionIndexEntry`` mapping
    the function name to its definition location, visibility, async flag,
    return type, and context (e.g., ``"impl:TraitName for Type"``).

    ``FileAST.self_methods`` is the unified list produced by the extractor's
    ``_collect_self_methods`` post-pass — it already contains both free
    functions (from ``file_ast.functions``) and impl-block methods.  We
    therefore index only ``self_methods``, not ``functions`` separately.

    One entry per **definition site** — not per call site.

    Args:
        xref: CrossReferences to populate.
        file_pairs: List of (crate_name, file_ast) pairs.
    """
    for _crate, file_ast in file_pairs:
        mod = _module_path_or_file(file_ast)

        for method in file_ast.self_methods:
            entry = FunctionIndexEntry(
                file=file_ast.file,
                module_path=mod,
                visibility=str(method.visibility),
                is_async=method.is_async,
                return_type=method.return_type,
                context=method.context,
            )
            xref.function_file_index.setdefault(method.name, []).append(entry)


# endregion: --- Pass 3: Function File Index


# ---------------------------------------------------------------------------
# region:    --- Pass 4: Package Method Index
# ---------------------------------------------------------------------------


def _build_package_method_index(
    xref: CrossReferences,
    file_pairs: list[tuple[str, FileAST]],
) -> None:
    """Pass 4: Build package_method_index from imported_package_methods.

    Inverts the per-file ``imported_package_methods`` dict into a global
    index mapping ``qualified::Type::method`` → list of file paths.

    Args:
        xref: CrossReferences to populate.
        file_pairs: List of (crate_name, file_ast) pairs.
    """
    for _crate, file_ast in file_pairs:
        for qualified_type, methods in file_ast.imported_package_methods.items():
            for method in methods:
                key = f"{qualified_type}::{method}"
                bucket = xref.package_method_index.setdefault(key, [])
                # Avoid duplicate file paths when the same call appears
                # multiple times within a single file.
                if file_ast.file not in bucket:
                    bucket.append(file_ast.file)


# endregion: --- Pass 4: Package Method Index


# ---------------------------------------------------------------------------
# region:    --- Pass 5: Inter-Crate Dependencies
# ---------------------------------------------------------------------------


def _build_inter_crate_deps(
    xref: CrossReferences,
    crates: dict[str, CrateModel],
) -> None:
    """Pass 5: Build inter_crate_deps from manifest dependency metadata.

    For each crate, extracts the names of its dependencies that are also
    workspace members (have a ``path`` set), building a crate → [dep_crates]
    mapping.

    Only workspace-internal dependencies are included (path deps or
    workspace-inherited deps). External registry crates are excluded
    since they are not part of the analyzed workspace.

    Args:
        xref: CrossReferences to populate.
        crates: Crate name → CrateModel mapping from the workspace.
    """
    workspace_crate_names = frozenset(crates.keys())

    for crate_name, crate in crates.items():
        internal_deps = [
            dep.name
            for dep in crate.dependencies
            if dep.path or (dep.is_workspace and dep.name in workspace_crate_names)
        ]

        if internal_deps:
            xref.inter_crate_deps[crate_name] = sorted(set(internal_deps))


# endregion: --- Pass 5: Inter-Crate Dependencies


# ---------------------------------------------------------------------------
# region:    --- Pass 6: Cross-File Call Resolution
# ---------------------------------------------------------------------------


def _resolve_cross_file_calls(
    xref: CrossReferences,
    file_pairs: list[tuple[str, FileAST]],
) -> None:
    """Pass 6: Resolve unresolved call edges using the function file index.

    For each file's ``call_edges``, find edges that have an empty
    ``resolved_target``.  If the callee name matches exactly one entry
    in ``function_file_index``, resolve it to the qualified module path
    with ``INFERRED`` confidence.

    When multiple candidates exist (2+ matches), the edge is tagged
    ``AMBIGUOUS`` without resolving a target — the consumer can decide
    how to handle ambiguity.

    ``CallEdge`` is frozen, so resolved edges are replaced (the list
    reference on ``FileAST`` is reassigned).

    Only non-method calls (``is_method_call=False``) with an unresolved
    target are candidates — method calls require type information to
    resolve, which is beyond the scope of name-based resolution.

    Args:
        xref: CrossReferences (must have ``function_file_index`` populated).
        file_pairs: List of (crate_name, file_ast) pairs.
    """
    fn_index = xref.function_file_index

    for _crate, file_ast in file_pairs:
        if not file_ast.call_edges:
            continue

        new_edges: list[CallEdge] | None = None  # lazy — only allocate on change

        for i, edge in enumerate(file_ast.call_edges):
            if edge.resolved_target or edge.is_method_call:
                continue

            entries = fn_index.get(edge.callee)
            if not entries:
                # 0 matches → truly unknown; leave as-is
                continue

            if len(entries) == 1:
                # Single unambiguous match — resolve with INFERRED confidence
                entry = entries[0]
                resolved = (
                    f"{entry.module_path}::{edge.callee}"
                    if entry.module_path
                    else edge.callee
                )
                resolved_edge = CallEdge(
                    caller=edge.caller,
                    callee=edge.callee,
                    call_site=edge.call_site,
                    resolved_target=resolved,
                    is_method_call=edge.is_method_call,
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED_CROSS_FILE,
                )
            else:
                # 2+ matches → ambiguous; tag but leave unresolved
                resolved_edge = CallEdge(
                    caller=edge.caller,
                    callee=edge.callee,
                    call_site=edge.call_site,
                    resolved_target="",
                    is_method_call=edge.is_method_call,
                    confidence=Confidence.AMBIGUOUS,
                    confidence_score=SCORE_AMBIGUOUS,
                )

            if new_edges is None:
                # Copy existing edges up to this point
                new_edges = list(file_ast.call_edges)
            new_edges[i] = resolved_edge

        if new_edges is not None:
            file_ast.call_edges = new_edges


# endregion: --- Pass 6: Cross-File Call Resolution
