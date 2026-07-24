"""Graph builder — transforms a WorkspaceAST into a CodeGraph.

The builder walks all crates, files, and cross-reference indexes
to emit nodes and edges with full provenance.  It is the bridge
between the flat ``WorkspaceAST`` representation and the explicit
graph model consumed by graph formatters.

Node ID scheme: ``"{file_path}::{symbol_name}"`` — deterministic,
human-readable, and stable across runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath

from ast_intel.extractors._thirdparty_registry import (
    classify_host,
    extract_host,
    is_external_host,
)
from ast_intel.models.ast_node import (
    SCORE_EXTRACTED,
    SCORE_INFERRED,
    CallEdge,
    Confidence,
    ConstantNode,
    EnumNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MacroNode,
    MethodNode,
    ModuleNode,
    PayloadField,
    RationaleNode,
    StructNode,
    TraitNode,
    TypeAliasNode,
)
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.workspace_model import (
    CrossReferences,
    WorkspaceAST,
)

__all__: list[str] = ["GraphBuilder"]

logger = logging.getLogger(__name__)

# Primitive / standard-library type names that should not produce
# ``has_field`` edges.  Hoisted to module level to avoid repeated
# allocation on every ``_extract_base_type`` call.
_PRIMITIVES: frozenset[str] = frozenset({
    # Rust
    "bool", "char", "str", "String",
    "i8", "i16", "i32", "i64", "i128", "isize",
    "u8", "u16", "u32", "u64", "u128", "usize",
    "f32", "f64",
    # Python
    "int", "float", "complex", "bytes", "bytearray", "None",
    # TypeScript / JavaScript
    "number", "string", "boolean", "any", "void",
    "object", "undefined", "null", "symbol", "never", "bigint",
    # Go
    "nil", "byte", "rune", "error",
    "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    "float32", "float64", "complex64", "complex128",
    # Java
    "short", "long", "double",
    "Integer", "Long", "Double", "Float", "Short", "Byte",
    "Boolean", "Character", "Void",
    # C# value types
    "decimal", "nint", "nuint",
    # C / C++
    "size_t", "ptrdiff_t", "wchar_t", "char8_t", "char16_t", "char32_t",
})

# Wrapper types that use ``<>`` generics — unwrap to get the inner type.
_ANGLE_WRAPPERS: frozenset[str] = frozenset({
    # Rust smart pointers / containers
    "Arc", "Rc", "Box", "Cell", "RefCell", "Mutex", "RwLock",
    "Cow", "Pin", "Option", "Result",
    "Vec", "HashSet", "BTreeSet", "VecDeque", "LinkedList",
    "HashMap", "BTreeMap",
    # C++ smart pointers / containers
    "shared_ptr", "unique_ptr", "weak_ptr", "auto_ptr",
    "vector", "optional", "variant",
    # Java / C# / TypeScript generics
    "List", "ArrayList", "Set", "Map", "Queue", "Deque",
    "Promise", "Observable", "Array",
    "Task", "Lazy", "Nullable",
    "IEnumerable", "IList", "ICollection",
})

# Python wrapper types that use ``[]`` bracket generics.
_SQUARE_WRAPPERS: frozenset[str] = frozenset({
    "Optional", "List", "Set", "FrozenSet", "Dict",
    "Tuple", "Union", "Sequence", "Iterable", "Iterator",
    "Deque", "DefaultDict", "OrderedDict", "Counter",
    "Awaitable", "Coroutine", "AsyncIterator",
})


# ---------------------------------------------------------------------------
# region:    --- Node ID helpers
# ---------------------------------------------------------------------------


def _node_id(file: str, name: str) -> str:
    """Build a stable, deterministic node ID.

    Format: ``"{file_path}::{symbol_name}"``.
    """
    return f"{file}::{name}"


def _collect_first_party_roots(workspace: WorkspaceAST) -> set[str]:
    """Top-level package/module names owned by the workspace (first-party).

    Used to exclude heuristic ``unknown``-vendor SDK calls that actually
    target the repository's own modules rather than an external service.
    """
    roots: set[str] = set()
    for crate in workspace.crates.values():
        for file_ast in crate.files:
            fp = file_ast.file
            if not fp:
                continue
            parts = PurePosixPath(fp).parts
            roots.add(parts[0] if len(parts) > 1 else PurePosixPath(fp).stem)
    return roots


def _payload_summary(
    payload: tuple[PayloadField, ...], confidence: Confidence,
) -> dict[str, str]:
    """Summarize an egress payload as string graph-node properties."""
    names = [f.name or "<pos>" for f in payload]
    sources = sorted({f.source_kind for f in payload})
    return {
        "payload_count": str(len(payload)),
        "payload_fields": ",".join(names),
        "payload_sources": ",".join(sources),
        "has_secrets": "true" if any(f.redacted for f in payload) else "false",
        "payload_confidence": str(confidence),
        "payload_json": json.dumps(
            [
                {
                    "name": f.name,
                    "source_kind": f.source_kind,
                    "value": f.value,
                    "redacted": f.redacted,
                }
                for f in payload
            ],
            ensure_ascii=False,
        ),
    }


def _crate_node_id(crate_name: str) -> str:
    """Build a stable node ID for a crate / package."""
    return f"crate::{crate_name}"


def _import_target_id(use_stmt: str) -> str:
    """Build a stable node ID for an import target.

    The import string is used verbatim as the label, with a
    ``"import::"`` prefix for uniqueness.
    """
    return f"import::{use_stmt}"


# endregion: --- Node ID helpers


# ---------------------------------------------------------------------------
# region:    --- GraphBuilder
# ---------------------------------------------------------------------------


class GraphBuilder:
    """Transform a ``WorkspaceAST`` into a ``CodeGraph``.

    Walks all crates, files, and cross-references to emit nodes and
    edges with full provenance (confidence, span, file).

    Build state (``_nodes``, ``_edges``, ``_seen``) is held on the
    instance for the duration of a single :meth:`build` call to avoid
    threading many lists through every helper method.

    Args:
        similarity: When ``True``, emit ``SIMILAR_TO`` edges between
            structurally similar symbols after all explicit edges.
        similarity_threshold: Minimum Jaccard similarity (0.0–1.0)
            to emit a ``SIMILAR_TO`` edge.  Default ``0.4``.
    """

    def __init__(
        self,
        *,
        similarity: bool = False,
        similarity_threshold: float = 0.4,
    ) -> None:
        self._nodes: list[GraphNode] = []
        self._edges: list[GraphEdge] = []
        self._seen: set[str] = set()
        self._similarity = similarity
        self._similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, workspace: WorkspaceAST) -> CodeGraph:
        """Build the full code knowledge graph.

        Args:
            workspace: A fully indexed ``WorkspaceAST``.

        Returns:
            A ``CodeGraph`` with all nodes and edges.
        """
        self._nodes = []
        self._edges = []
        self._seen = set()
        self._first_party_roots = _collect_first_party_roots(workspace)

        for crate_name, crate in sorted(workspace.crates.items()):
            crate_id = _crate_node_id(crate_name)
            self._add(
                GraphNode(
                    id=crate_id,
                    label=crate_name,
                    kind=NodeKind.CRATE,
                    file=crate.manifest_path,
                    properties={
                        "version": crate.version,
                        "language": crate.language,
                    },
                ),
            )

            for file_ast in crate.files:
                self._emit_file_nodes(file_ast, crate_id)

        self._emit_cross_ref_edges(workspace.cross_references)

        # Post-processing: resolve synthetic type:: references to
        # actual struct/trait/enum nodes now that all files are emitted.
        symbol_table = self._build_symbol_table()
        self._resolve_type_references(symbol_table)

        # Post-processing: resolve import nodes to their actual
        # definitions across crate boundaries (Feature 16).
        self._resolve_imports(workspace)

        # Similarity edges (opt-in) — must run after all explicit
        # edges so feature extraction sees the full graph.
        if self._similarity:
            self._emit_similarity_edges()

        logger.info(
            "Graph built: %d nodes, %d edges",
            len(self._nodes),
            len(self._edges),
        )

        return CodeGraph(
            nodes=self._nodes, edges=self._edges, meta=workspace.meta,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_symbol_table(self) -> dict[str, list[str]]:
        """Build a mapping of short type names to full node IDs.

        Only includes STRUCT, TRAIT, ENUM, and TYPE_ALIAS nodes —
        these are the kinds that ``type::`` references target.

        Returns:
            A dict mapping ``node.label`` → list of matching node IDs.
        """
        type_kinds = {NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ENUM, NodeKind.TYPE_ALIAS}
        table: dict[str, list[str]] = {}
        for node in self._nodes:
            if node.kind in type_kinds:
                table.setdefault(node.label, []).append(node.id)
        return table

    def _resolve_type_references(
        self, symbol_table: dict[str, list[str]],
    ) -> None:
        """Rewrite synthetic ``type::X`` edge targets to real node IDs.

        Three emitters create edges targeting ``type::{name}`` which is
        a synthetic ID that doesn't correspond to any real node.  This
        post-pass resolves them using the *symbol_table* built from all
        emitted nodes.

        Resolution strategy:
        - **1 candidate**: unambiguous — rewrite the edge target.
        - **>1 candidates**: prefer candidates whose file path shares
          the longest common prefix with the edge source (same-crate
          heuristic).  If still ambiguous, leave unchanged.
        - **0 candidates**: leave as-is (external / framework type).
        """
        resolved = 0
        ambiguous_resolved = 0
        unresolved = 0

        for i, edge in enumerate(self._edges):
            if not edge.target.startswith("type::"):
                continue

            short_name = edge.target.removeprefix("type::")
            candidates = symbol_table.get(short_name, [])

            if len(candidates) == 1:
                self._edges[i] = GraphEdge(
                    source=edge.source,
                    target=candidates[0],
                    relation=edge.relation,
                    confidence=edge.confidence,
                    confidence_score=edge.confidence_score,
                    file=edge.file,
                    span=edge.span,
                )
                resolved += 1
            elif len(candidates) > 1:
                best = self._disambiguate(edge.source, candidates)
                if best is not None:
                    self._edges[i] = GraphEdge(
                        source=edge.source,
                        target=best,
                        relation=edge.relation,
                        confidence=edge.confidence,
                        confidence_score=edge.confidence_score,
                        file=edge.file,
                        span=edge.span,
                    )
                    ambiguous_resolved += 1
                else:
                    logger.debug(
                        "Ambiguous type::%s — %d candidates, no same-crate match",
                        short_name,
                        len(candidates),
                    )
                    unresolved += 1
            else:
                unresolved += 1

        total = resolved + ambiguous_resolved + unresolved
        if total:
            logger.info(
                "Type references: %d resolved, %d disambiguated, "
                "%d unresolved (of %d total)",
                resolved,
                ambiguous_resolved,
                unresolved,
                total,
            )

    @staticmethod
    def _disambiguate(
        source_id: str, candidates: list[str],
    ) -> str | None:
        """Pick the best candidate by longest shared path prefix.

        If the source node and a candidate share a common directory
        prefix (e.g. both live under ``src/crates/libs/lib-storage/``)
        that candidate is preferred.  Returns ``None`` when no single
        winner can be determined.
        """
        # Extract directory from source (everything before ::)
        source_dir = source_id.rsplit("::", maxsplit=1)[0] if "::" in source_id else ""
        if not source_dir:
            return None

        best_len = 0
        best_candidate: str | None = None
        tied = False

        for cand in candidates:
            cand_dir = cand.rsplit("::", maxsplit=1)[0] if "::" in cand else ""
            # Find common prefix length
            prefix_len = len(source_dir) if cand_dir.startswith(source_dir) else 0
            if prefix_len == 0:
                # Try token-wise common prefix
                s_parts = source_dir.split("/")
                c_parts = cand_dir.split("/")
                common = 0
                for s, c in zip(s_parts, c_parts, strict=False):
                    if s == c:
                        common += len(s) + 1
                    else:
                        break
                prefix_len = common

            if prefix_len > best_len:
                best_len = prefix_len
                best_candidate = cand
                tied = False
            elif prefix_len == best_len and prefix_len > 0:
                tied = True

        if tied:
            return None
        return best_candidate

    # ------------------------------------------------------------------
    # Import resolution (Feature 16)
    # ------------------------------------------------------------------

    def _build_full_symbol_table(self) -> dict[str, list[str]]:
        """Build a mapping of symbol labels to node IDs for import resolution.

        Unlike ``_build_symbol_table()`` (which covers only types),
        this includes *all* resolvable symbol kinds: structs, traits,
        enums, functions, methods, constants, macros, type aliases,
        and modules.
        """
        skip_kinds = frozenset({
            NodeKind.FILE,
            NodeKind.CRATE,
            NodeKind.IMPORT,
            NodeKind.EXTERNAL_METHOD,
            NodeKind.RATIONALE,
            NodeKind.IMPL_BLOCK,
        })
        table: dict[str, list[str]] = {}
        for node in self._nodes:
            if node.kind not in skip_kinds:
                table.setdefault(node.label, []).append(node.id)
        return table

    @staticmethod
    def _build_crate_name_index(
        workspace: WorkspaceAST,
    ) -> dict[str, str]:
        """Map underscore-normalised crate names to their real names.

        Rust ``use`` paths replace dashes with underscores::

            use lib_storage_service::StorageHelper
            # ^^^ crate name is actually "lib-storage-service"

        Returns:
            ``{"lib_storage_service": "lib-storage-service", ...}``
        """
        index: dict[str, str] = {}
        for crate_name in workspace.crates:
            normalised = crate_name.replace("-", "_")
            index[normalised] = crate_name
        return index

    def _resolve_imports(self, workspace: WorkspaceAST) -> None:
        """Add ``RESOLVES_TO`` edges from import nodes to real definitions.

        For each ``NodeKind.IMPORT`` node whose label is a ``use``
        statement, parse out the imported symbol names, look each up
        in the full symbol table, and — if matched — emit a
        ``RESOLVES_TO`` edge from the import node to the definition.

        Disambiguation strategy when multiple candidates share the
        same label:

        1. Extract the *crate hint* from the import path (first ``::``
           segment) and normalise dashes/underscores.
        2. Keep only candidates whose file path belongs to that crate.
        3. If still ambiguous, fall back to ``_disambiguate()``
           (longest shared path prefix).
        """
        full_table = self._build_full_symbol_table()
        crate_index = self._build_crate_name_index(workspace)

        # Reverse map: file_path → crate_name  (for ``use crate::`` resolution)
        file_to_crate: dict[str, str] = {}
        for crate_name, crate in workspace.crates.items():
            for file_ast in crate.files:
                file_to_crate[file_ast.file] = crate_name

        resolved = 0
        skipped_external = 0

        for node in self._nodes:
            if node.kind != NodeKind.IMPORT:
                continue

            parsed = _parse_import(node.label)
            if not parsed:
                continue

            for short_name, qualified_path in parsed:
                candidates = full_table.get(short_name, [])
                if not candidates:
                    skipped_external += 1
                    continue

                target = self._pick_import_target(
                    short_name,
                    qualified_path,
                    candidates,
                    crate_index,
                    file_to_crate.get(node.file, ""),
                    node.id,
                )
                if target is not None:
                    self._edge(
                        GraphEdge(
                            source=node.id,
                            target=target,
                            relation=EdgeRelation.RESOLVES_TO,
                            confidence=Confidence.INFERRED,
                            confidence_score=SCORE_INFERRED,
                            file=node.file,
                        ),
                    )
                    resolved += 1

        logger.info(
            "Import resolution: %d resolved, %d external (skipped)",
            resolved,
            skipped_external,
        )

    def _pick_import_target(
        self,
        _short_name: str,
        qualified_path: str,
        candidates: list[str],
        crate_index: dict[str, str],
        file_crate: str,
        import_node_id: str,
    ) -> str | None:
        """Select the best target node ID for a single imported symbol.

        Returns ``None`` when no confident match can be made.
        """
        if len(candidates) == 1:
            return candidates[0]

        # Extract crate hint from the qualified import path.
        crate_hint = _import_crate_hint(qualified_path, crate_index, file_crate)
        if crate_hint:
            # Filter to candidates living in the hinted crate's directory.
            # Crate files live under paths containing the crate name
            # (e.g. "src/crates/libs/lib-storage-service/...").
            narrowed = [
                c for c in candidates
                if f"/{crate_hint}/" in f"/{c}/"
                or c.startswith(f"{crate_hint}/")
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            if narrowed:
                candidates = narrowed

        # Fall back to path-prefix disambiguation.
        return self._disambiguate(import_node_id, candidates)

    def _emit_similarity_edges(self) -> None:
        """Emit ``SIMILAR_TO`` edges via Jaccard feature comparison."""
        from ast_intel.core._similarity import compute_similarity_edges

        # Build a temporary CodeGraph for feature extraction.
        tmp = CodeGraph(nodes=list(self._nodes), edges=list(self._edges))
        sim_edges = compute_similarity_edges(
            tmp,
            threshold=self._similarity_threshold,
        )
        self._edges.extend(sim_edges)

    def _add(self, node: GraphNode) -> None:
        """Add a node only if its ID hasn't been seen before."""
        if node.id not in self._seen:
            self._seen.add(node.id)
            self._nodes.append(node)

    def _edge(self, edge: GraphEdge) -> None:
        """Append an edge."""
        self._edges.append(edge)

    # ------------------------------------------------------------------
    # File-level node + edge emission
    # ------------------------------------------------------------------

    def _emit_file_nodes(self, file_ast: FileAST, crate_id: str) -> None:
        """Emit nodes and intra-file edges for a single file."""
        fp = file_ast.file

        file_id = _node_id(fp, "<file>")
        self._add(GraphNode(id=file_id, label=fp, kind=NodeKind.FILE, file=fp))

        self._edge(
            GraphEdge(
                source=crate_id,
                target=file_id,
                relation=EdgeRelation.CONTAINS,
                confidence=Confidence.EXTRACTED,
                confidence_score=SCORE_EXTRACTED,
                file=fp,
            ),
        )

        self._emit_symbol_nodes(file_ast, file_id, fp)
        self._emit_rationale_nodes(file_ast, fp)
        self._emit_call_edges(file_ast, fp)
        self._emit_import_edges(file_ast, file_id, fp)
        self._emit_uses_method_edges(file_ast, file_id, fp)
        self._emit_route_nodes(file_ast, file_id, fp)
        self._emit_http_call_nodes(file_ast, file_id, fp)
        self._emit_cloud_resource_nodes(file_ast, file_id, fp)
        self._emit_sdk_call_nodes(file_ast, file_id, fp)

    def _emit_symbol_nodes(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit struct/enum/trait/function/impl/alias/const/macro/module nodes."""
        for struct in file_ast.structs:
            sid = _node_id(fp, struct.name)
            self._add(_struct_node(struct, fp, sid))
            self._edge(_contains_edge(file_id, sid, fp))
            self._emit_has_field_edges(struct, sid, fp)

        for enum in file_ast.enums:
            eid = _node_id(fp, enum.name)
            self._add(_enum_node(enum, fp, eid))
            self._edge(_contains_edge(file_id, eid, fp))

        for trait in file_ast.traits:
            tid = _node_id(fp, trait.name)
            self._add(_trait_node(trait, fp, tid))
            self._edge(_contains_edge(file_id, tid, fp))
            self._emit_super_trait_edges(trait, tid, fp)

        for func in file_ast.functions:
            fid = _node_id(fp, func.name)
            self._add(_function_node(func, fp, fid))
            self._edge(_contains_edge(file_id, fid, fp))

        for impl_block in file_ast.impl_blocks:
            impl_id = _impl_block_id(fp, impl_block)
            self._add(_impl_block_node(impl_block, fp, impl_id))
            self._edge(_contains_edge(file_id, impl_id, fp))
            self._emit_impl_edges(impl_block, impl_id, fp)

        for alias in file_ast.type_aliases:
            aid = _node_id(fp, alias.name)
            self._add(_type_alias_node(alias, fp, aid))
            self._edge(_contains_edge(file_id, aid, fp))

        for const in file_ast.constants:
            cid = _node_id(fp, const.name)
            self._add(_constant_node(const, fp, cid))
            self._edge(_contains_edge(file_id, cid, fp))

        for macro in file_ast.macros:
            mid = _node_id(fp, macro.name)
            self._add(_macro_node(macro, fp, mid))
            self._edge(_contains_edge(file_id, mid, fp))

        for mod in file_ast.modules:
            mod_id = _node_id(fp, mod.name)
            self._add(_module_node(mod, fp, mod_id))
            self._edge(_contains_edge(file_id, mod_id, fp))

    # ------------------------------------------------------------------
    # Specialized edge emitters
    # ------------------------------------------------------------------

    def _emit_has_field_edges(
        self, struct: StructNode, struct_id: str, fp: str,
    ) -> None:
        """Emit ``has_field`` edges for struct fields whose type is a known struct."""
        for fld in struct.fields:
            for base_type in _extract_base_types(fld.type):
                target_id = f"type::{base_type}"
                self._edge(
                    GraphEdge(
                        source=struct_id,
                        target=target_id,
                        relation=EdgeRelation.HAS_FIELD,
                        confidence=Confidence.INFERRED,
                        confidence_score=SCORE_INFERRED,
                        file=fp,
                    ),
                )

    def _emit_super_trait_edges(
        self, trait: TraitNode, trait_id: str, fp: str,
    ) -> None:
        """Emit ``super_trait`` edges from trait to its parent traits."""
        for parent in trait.super_traits:
            target_id = f"type::{parent}"
            self._edge(
                GraphEdge(
                    source=trait_id,
                    target=target_id,
                    relation=EdgeRelation.SUPER_TRAIT,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=trait.span,
                ),
            )

    def _emit_impl_edges(
        self, impl_block: ImplBlockNode, impl_id: str, fp: str,
    ) -> None:
        """Emit method_of, implements, and inherits edges for an impl block."""
        for method in impl_block.methods:
            method_id = _node_id(fp, f"{impl_block.self_type}.{method.name}")
            self._add(_method_node(method, fp, method_id))
            self._edge(
                GraphEdge(
                    source=method_id,
                    target=impl_id,
                    relation=EdgeRelation.METHOD_OF,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=method.span,
                ),
            )

        if impl_block.trait_type:
            relation = (
                EdgeRelation.INHERITS
                if impl_block.confidence == Confidence.INFERRED
                else EdgeRelation.IMPLEMENTS
            )
            target_id = f"type::{impl_block.trait_type}"
            self._edge(
                GraphEdge(
                    source=impl_id,
                    target=target_id,
                    relation=relation,
                    confidence=impl_block.confidence,
                    confidence_score=impl_block.confidence_score,
                    file=fp,
                    span=impl_block.span,
                ),
            )

    def _emit_rationale_nodes(self, file_ast: FileAST, fp: str) -> None:
        """Emit rationale comment nodes and ``rationale_for`` edges."""
        for rationale in file_ast.rationale_comments:
            rid = _rationale_id(fp, rationale)
            self._add(_rationale_node(rationale, fp, rid))
            if rationale.parent == "<file>":
                target_id = _node_id(fp, "<file>")
            else:
                target_id = _node_id(fp, rationale.parent)
            self._edge(
                GraphEdge(
                    source=rid,
                    target=target_id,
                    relation=EdgeRelation.RATIONALE_FOR,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=rationale.span,
                ),
            )

    def _emit_call_edges(self, file_ast: FileAST, fp: str) -> None:
        """Emit ``calls`` edges from the file's call graph."""
        for call_edge in file_ast.call_edges:
            caller_id = _node_id(fp, call_edge.caller)
            if call_edge.resolved_target:
                callee_id = _resolve_call_target_id(call_edge, fp)
            else:
                callee_id = _node_id(fp, call_edge.callee)
            self._edge(
                GraphEdge(
                    source=caller_id,
                    target=callee_id,
                    relation=EdgeRelation.CALLS,
                    confidence=call_edge.confidence,
                    confidence_score=call_edge.confidence_score,
                    file=fp,
                    span=call_edge.call_site,
                ),
            )

    def _emit_import_edges(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``imports`` edges for each ``use`` / ``import`` statement."""
        for use_stmt in file_ast.uses:
            target_id = _import_target_id(use_stmt)
            self._add(
                GraphNode(
                    id=target_id,
                    label=use_stmt,
                    kind=NodeKind.IMPORT,
                    file=fp,
                ),
            )
            self._edge(
                GraphEdge(
                    source=file_id,
                    target=target_id,
                    relation=EdgeRelation.IMPORTS,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                ),
            )

    def _emit_uses_method_edges(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``uses_method`` edges for imported package methods."""
        for pkg_type, methods in file_ast.imported_package_methods.items():
            for method_name in methods:
                target_label = f"{pkg_type}::{method_name}"
                target_id = f"pkg_method::{target_label}"
                self._add(
                    GraphNode(
                        id=target_id,
                        label=target_label,
                        kind=NodeKind.EXTERNAL_METHOD,
                        file=fp,
                    ),
                )
                self._edge(
                    GraphEdge(
                        source=file_id,
                        target=target_id,
                        relation=EdgeRelation.USES_METHOD,
                        confidence=Confidence.INFERRED,
                        confidence_score=SCORE_INFERRED,
                        file=fp,
                    ),
                )

    def _emit_route_nodes(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``ROUTE`` nodes and ``HANDLES``/``EXPOSES`` edges."""
        for route in file_ast.routes:
            route_id = _node_id(
                fp, f"route:{route.method}:{route.path}",
            )
            self._add(
                GraphNode(
                    id=route_id,
                    label=f"{route.method} {route.path}",
                    kind=NodeKind.ROUTE,
                    file=fp,
                    span=route.span,
                    properties={
                        "path": route.path,
                        "method": route.method,
                        "framework": route.framework,
                        "handler": route.handler,
                    },
                ),
            )
            # File → Route (EXPOSES)
            self._edge(
                GraphEdge(
                    source=file_id,
                    target=route_id,
                    relation=EdgeRelation.EXPOSES,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=route.span,
                ),
            )
            # Route → handler function (HANDLES)
            handler_id = _node_id(fp, route.handler)
            self._edge(
                GraphEdge(
                    source=route_id,
                    target=handler_id,
                    relation=EdgeRelation.HANDLES,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=route.span,
                ),
            )

    def _emit_http_call_nodes(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``HTTP_CALL`` nodes and ``CALLS_HTTP`` edges."""
        for call in file_ast.http_calls:
            call_id = _node_id(
                fp, f"http_call:{call.method}:{call.url}",
            )
            # Confidence depends on URL quality.
            if call.url == "<dynamic>":
                conf = Confidence.AMBIGUOUS
                score = 0.5
            elif "{param}" in call.url:
                conf = Confidence.INFERRED
                score = 0.85
            else:
                conf = Confidence.EXTRACTED
                score = SCORE_EXTRACTED

            properties = {
                "url": call.url,
                "method": call.method,
                "library": call.library,
                "caller": call.caller,
            }
            info = classify_host(call.url)
            if info is not None:
                properties["third_party"] = "true"
                properties["vendor"] = info.vendor
                properties["category"] = info.category
                properties["direction"] = info.direction
            elif is_external_host(extract_host(call.url)):
                properties["third_party"] = "true"
                properties["vendor"] = "unknown"
                properties["category"] = "unknown"
            else:
                properties["third_party"] = "false"
            properties.update(
                _payload_summary(call.payload, call.payload_confidence),
            )

            self._add(
                GraphNode(
                    id=call_id,
                    label=f"{call.method} {call.url}",
                    kind=NodeKind.HTTP_CALL,
                    file=fp,
                    span=call.span,
                    properties=properties,
                ),
            )
            # File → HTTP_CALL (CONTAINS)
            self._edge(
                GraphEdge(
                    source=file_id,
                    target=call_id,
                    relation=EdgeRelation.CONTAINS,
                    confidence=conf,
                    confidence_score=score,
                    file=fp,
                    span=call.span,
                ),
            )
            # Caller function → HTTP_CALL (CALLS_HTTP)
            caller_id = _node_id(fp, call.caller)
            self._edge(
                GraphEdge(
                    source=caller_id,
                    target=call_id,
                    relation=EdgeRelation.CALLS_HTTP,
                    confidence=conf,
                    confidence_score=score,
                    file=fp,
                    span=call.span,
                ),
            )

    def _emit_cloud_resource_nodes(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``CLOUD_RESOURCE`` nodes and ``USES_RESOURCE`` edges."""
        for res in file_ast.cloud_resources:
            res_id = _node_id(
                fp, f"cloud:{res.provider}:{res.category}:{res.client}:{res.caller}",
            )
            self._add(
                GraphNode(
                    id=res_id,
                    label=f"{res.service} ({res.client})",
                    kind=NodeKind.CLOUD_RESOURCE,
                    file=fp,
                    span=res.span,
                    properties={
                        "provider": res.provider,
                        "service": res.service,
                        "category": res.category,
                        "client": res.client,
                        "caller": res.caller,
                        "name": res.name,
                        "source": "code",
                    },
                ),
            )
            # File → CLOUD_RESOURCE (CONTAINS)
            self._edge(
                GraphEdge(
                    source=file_id,
                    target=res_id,
                    relation=EdgeRelation.CONTAINS,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=res.span,
                ),
            )
            # Caller function → CLOUD_RESOURCE (USES_RESOURCE)
            caller_id = _node_id(fp, res.caller)
            self._edge(
                GraphEdge(
                    source=caller_id,
                    target=res_id,
                    relation=EdgeRelation.USES_RESOURCE,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=res.span,
                ),
            )

    def _emit_sdk_call_nodes(
        self, file_ast: FileAST, file_id: str, fp: str,
    ) -> None:
        """Emit ``SDK_CALL`` nodes and ``SENDS_DATA`` edges."""
        for call in file_ast.sdk_calls:
            # Drop heuristic 'unknown' calls that target the repo's own modules.
            if call.vendor == "unknown" and call.sdk in self._first_party_roots:
                continue
            call_id = _node_id(
                fp, f"sdk_call:{call.vendor}:{call.method}:{call.caller}",
            )
            score = {
                Confidence.EXTRACTED: SCORE_EXTRACTED,
                Confidence.INFERRED: SCORE_INFERRED,
                Confidence.AMBIGUOUS: 0.5,
            }.get(call.payload_confidence, SCORE_EXTRACTED)

            properties = {
                "vendor": call.vendor,
                "category": call.category,
                "sdk": call.sdk,
                "method": call.method,
                "caller": call.caller,
                "third_party": "true",
                "heuristic": "true" if call.vendor == "unknown" else "false",
            }
            properties.update(
                _payload_summary(call.payload, call.payload_confidence),
            )

            self._add(
                GraphNode(
                    id=call_id,
                    label=f"{call.vendor}: {call.method}",
                    kind=NodeKind.SDK_CALL,
                    file=fp,
                    span=call.span,
                    properties=properties,
                ),
            )
            # File → SDK_CALL (CONTAINS)
            self._edge(
                GraphEdge(
                    source=file_id,
                    target=call_id,
                    relation=EdgeRelation.CONTAINS,
                    confidence=Confidence.EXTRACTED,
                    confidence_score=SCORE_EXTRACTED,
                    file=fp,
                    span=call.span,
                ),
            )
            # Caller function → SDK_CALL (SENDS_DATA)
            caller_id = _node_id(fp, call.caller)
            self._edge(
                GraphEdge(
                    source=caller_id,
                    target=call_id,
                    relation=EdgeRelation.SENDS_DATA,
                    confidence=call.payload_confidence,
                    confidence_score=score,
                    file=fp,
                    span=call.span,
                ),
            )

    # ------------------------------------------------------------------
    # Cross-reference edge emission
    # ------------------------------------------------------------------

    def _emit_cross_ref_edges(
        self, xref: CrossReferences,
    ) -> None:
        """Emit edges derived from global cross-reference indexes."""
        for crate_name, dep_names in xref.inter_crate_deps.items():
            source_id = _crate_node_id(crate_name)
            for dep_name in dep_names:
                target_id = _crate_node_id(dep_name)
                self._add(
                    GraphNode(
                        id=target_id,
                        label=dep_name,
                        kind=NodeKind.CRATE,
                    ),
                )
                self._edge(
                    GraphEdge(
                        source=source_id,
                        target=target_id,
                        relation=EdgeRelation.DEPENDS_ON,
                        confidence=Confidence.EXTRACTED,
                        confidence_score=SCORE_EXTRACTED,
                    ),
                )


# endregion: --- GraphBuilder


# ---------------------------------------------------------------------------
# region:    --- Helper Functions
# ---------------------------------------------------------------------------


def _contains_edge(source_id: str, target_id: str, fp: str) -> GraphEdge:
    """Create a ``contains`` edge (file → symbol)."""
    return GraphEdge(
        source=source_id,
        target=target_id,
        relation=EdgeRelation.CONTAINS,
        confidence=Confidence.EXTRACTED,
        confidence_score=SCORE_EXTRACTED,
        file=fp,
    )


def _impl_block_id(fp: str, impl_block: ImplBlockNode) -> str:
    """Build a deterministic ID for an impl block.

    Uses ``self_type`` and optionally ``trait_type`` for uniqueness:
    - Inherent impl: ``"file.rs::impl:MyType"``
    - Trait impl: ``"file.rs::impl:Trait for MyType"``
    """
    if impl_block.trait_type:
        label = f"impl:{impl_block.trait_type} for {impl_block.self_type}"
    else:
        label = f"impl:{impl_block.self_type}"
    return _node_id(fp, label)


def _rationale_id(fp: str, rationale: RationaleNode) -> str:
    """Build a deterministic ID for a rationale comment.

    Format: ``"file::rationale:KIND@LINE"`` — unique per file/kind/line.
    """
    return _node_id(
        fp,
        f"rationale:{rationale.kind}@{rationale.span.start_line}",
    )


def _resolve_call_target_id(call_edge: CallEdge, fp: str = "") -> str:
    """Resolve a call edge's target to a node ID.

    ``resolved_target`` has the format ``"file_path::function_name"``
    or ``"function_name"`` (same file). We normalize it to a proper node ID.

    Args:
        call_edge: The call edge to resolve.
        fp: Fallback file path for same-file resolution.
    """
    target = call_edge.resolved_target
    if "::" in target:
        return target
    # Fallback: same-file — prefix with file path for valid node ID
    return _node_id(fp, target) if fp else target


def _extract_base_type(type_str: str) -> str:
    """Legacy wrapper — returns the first unwrapped type or ``""``."""
    results = _extract_base_types(type_str)
    return results[0] if results else ""


def _extract_base_types(type_str: str) -> list[str]:
    """Extract base type names from a type annotation string.

    Unwraps generic wrappers (``Arc<X>``, ``Optional[X]``, Go ``*X``)
    recursively, collecting all non-primitive inner types.  Handles
    three syntax families across all supported languages:

    - Angle brackets ``<>`` — Rust, TypeScript, Java, C#, C++
    - Square brackets ``[]`` — Python
    - Prefix / inline — Go (``*T``, ``[]T``, ``map[K]V``, ``chan T``)

    Returns a (possibly empty) list of capitalised, non-primitive type names.

    Examples:
        ``"Arc<Config>"``           → ``["Config"]``
        ``"Option<Box<MyStruct>>"`` → ``["MyStruct"]``
        ``"HashMap<String, Job>"``  → ``["Job"]``
        ``"Optional[Config]"``      → ``["Config"]``
        ``"*StorageConfig"``        → ``["StorageConfig"]``
        ``"Dict[str, MyModel]"``    → ``["MyModel"]``
        ``"i32"``                    → ``[]``
    """
    results: list[str] = []
    _unwrap_type(type_str, results)
    return results


def _unwrap_type(raw: str, out: list[str]) -> None:
    """Recursively unwrap *raw* and append non-primitive types to *out*."""
    s = raw.strip()
    if not s:
        return

    # Strip Rust / C references & pointers
    for prefix in ("&mut ", "&", "*const ", "*mut "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    # Go prefix patterns (pointer, slice, chan, map)
    if _unwrap_go_prefix(s, out):
        return

    # Strip C++ / Rust namespace paths
    s = _strip_namespace(s)

    # Angle-bracket generics <...>
    if _unwrap_angle_bracket(s, out):
        return

    # Square-bracket generics [...] (Python)
    if _unwrap_square_bracket(s, out):
        return

    # Plain name (no brackets)
    _emit_if_valid(s, out)


def _unwrap_go_prefix(s: str, out: list[str]) -> bool:
    """Handle Go prefix patterns.  Returns ``True`` if consumed."""
    # Pointer:  *MyType
    if s.startswith("*") and len(s) > 1 and s[1] != " ":
        _unwrap_type(s[1:], out)
        return True
    # Slice / array:  []MyType
    if s.startswith("[]"):
        _unwrap_type(s[2:], out)
        return True
    # Channel:  chan MyType
    if s.startswith("chan "):
        _unwrap_type(s[4:], out)
        return True
    # Map:   map[K]V — extract K and V
    if s.startswith("map["):
        depth, i = 1, 4
        while i < len(s) and depth > 0:
            if s[i] == "[":
                depth += 1
            elif s[i] == "]":
                depth -= 1
            i += 1
        _unwrap_type(s[4 : i - 1], out)
        _unwrap_type(s[i:], out)
        return True
    return False


def _strip_namespace(s: str) -> str:
    """Strip ``::`` namespace prefix, preserving bracket suffixes."""
    if "::" not in s:
        return s
    bracket_pos = len(s)
    for ch in ("<", "["):
        p = s.find(ch)
        if p != -1 and p < bracket_pos:
            bracket_pos = p
    if bracket_pos < len(s):
        base_part = s[:bracket_pos]
        rest_part = s[bracket_pos:]
        if "::" in base_part:
            base_part = base_part.rsplit("::", maxsplit=1)[-1]
        return base_part + rest_part
    return s.rsplit("::", maxsplit=1)[-1]


def _unwrap_angle_bracket(s: str, out: list[str]) -> bool:
    """Handle ``<...>`` generics.  Returns ``True`` if consumed."""
    angle_pos = s.find("<")
    if angle_pos == -1:
        return False
    outer = s[:angle_pos].strip()
    inner = s[angle_pos + 1 :].removesuffix(">")
    if outer in _ANGLE_WRAPPERS:
        for part in _split_type_params(inner, "<", ">"):
            _unwrap_type(part, out)
    else:
        _emit_if_valid(outer, out)
    return True


def _unwrap_square_bracket(s: str, out: list[str]) -> bool:
    """Handle ``[...]`` generics (Python).  Returns ``True`` if consumed."""
    sq_pos = s.find("[")
    if sq_pos == -1:
        return False
    outer = s[:sq_pos].strip()
    inner = s[sq_pos + 1 :].removesuffix("]")
    if outer in _SQUARE_WRAPPERS:
        for part in _split_type_params(inner, "[", "]"):
            _unwrap_type(part, out)
    else:
        _emit_if_valid(outer, out)
    return True


def _emit_if_valid(name: str, out: list[str]) -> None:
    """Append *name* to *out* if it's a valid, non-primitive type name."""
    if not name or name in _PRIMITIVES:
        return
    if not name[0].isupper():
        return
    if name not in out:
        out.append(name)


def _split_type_params(
    inner: str, open_ch: str, close_ch: str,
) -> list[str]:
    """Split comma-separated type parameters respecting nested brackets.

    ``"String, Vec<u8>"``  →  ``["String", "Vec<u8>"]``
    """
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(inner):
        if ch in (open_ch, "[", "<"):
            depth += 1
        elif ch in (close_ch, "]", ">"):
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[start:i].strip())
            start = i + 1
    tail = inner[start:].strip()
    if tail:
        parts.append(tail)
    return parts


# ---------------------------------------------------------------------------
# Node factory helpers — keep the main builder methods clean
# ---------------------------------------------------------------------------


def _struct_node(s: StructNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": s.visibility.value}
    if s.generics:
        props["generics"] = s.generics
    return GraphNode(
        id=nid, label=s.name, kind=NodeKind.STRUCT, file=fp,
        span=s.span, properties=props,
    )


def _enum_node(e: EnumNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": e.visibility.value}
    if e.generics:
        props["generics"] = e.generics
    props["variants"] = str(len(e.variants))
    return GraphNode(
        id=nid, label=e.name, kind=NodeKind.ENUM, file=fp,
        span=e.span, properties=props,
    )


def _trait_node(t: TraitNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": t.visibility.value}
    if t.generics:
        props["generics"] = t.generics
    if t.super_traits:
        props["super_traits"] = ", ".join(t.super_traits)
    return GraphNode(
        id=nid, label=t.name, kind=NodeKind.TRAIT, file=fp,
        span=t.span, properties=props,
    )


def _function_node(f: FunctionNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": f.visibility.value}
    if f.is_async:
        props["async"] = "true"
    if f.return_type:
        props["return_type"] = f.return_type
    return GraphNode(
        id=nid, label=f.name, kind=NodeKind.FUNCTION, file=fp,
        span=f.span, properties=props,
    )


def _method_node(m: MethodNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": m.visibility.value}
    if m.is_async:
        props["async"] = "true"
    if m.is_static:
        props["static"] = "true"
    if m.return_type:
        props["return_type"] = m.return_type
    return GraphNode(
        id=nid, label=m.name, kind=NodeKind.METHOD, file=fp,
        span=m.span, properties=props,
    )


def _impl_block_node(
    ib: ImplBlockNode, fp: str, nid: str,
) -> GraphNode:
    label = ib.self_type
    if ib.trait_type:
        label = f"{ib.trait_type} for {ib.self_type}"
    props: dict[str, str] = {}
    if ib.generics:
        props["generics"] = ib.generics
    if ib.trait_type:
        props["trait_type"] = ib.trait_type
    props["self_type"] = ib.self_type
    return GraphNode(
        id=nid, label=label, kind=NodeKind.IMPL_BLOCK, file=fp,
        span=ib.span, properties=props,
    )


def _type_alias_node(ta: TypeAliasNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {
        "visibility": ta.visibility.value,
        "aliased_to": ta.aliased_to,
    }
    return GraphNode(
        id=nid, label=ta.name, kind=NodeKind.TYPE_ALIAS, file=fp,
        span=ta.span, properties=props,
    )


def _constant_node(c: ConstantNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": c.visibility.value}
    if c.raw:
        props["raw"] = c.raw
    return GraphNode(
        id=nid, label=c.name, kind=NodeKind.CONSTANT, file=fp,
        span=c.span, properties=props,
    )


def _macro_node(m: MacroNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": m.visibility.value}
    return GraphNode(
        id=nid, label=m.name, kind=NodeKind.MACRO, file=fp,
        span=m.span, properties=props,
    )


def _module_node(mod: ModuleNode, fp: str, nid: str) -> GraphNode:
    props: dict[str, str] = {"visibility": mod.visibility.value}
    if mod.inline:
        props["inline"] = "true"
    return GraphNode(
        id=nid, label=mod.name, kind=NodeKind.MODULE, file=fp,
        span=mod.span, properties=props,
    )


def _rationale_node(r: RationaleNode, fp: str, nid: str) -> GraphNode:
    return GraphNode(
        id=nid,
        label=f"{r.kind}: {r.text[:60]}",
        kind=NodeKind.RATIONALE,
        file=fp,
        span=r.span,
        properties={"kind": r.kind, "parent": r.parent},
    )


# ---------------------------------------------------------------------------
# Import parsing helpers (Feature 16)
# ---------------------------------------------------------------------------


def _parse_import(
    label: str,
) -> list[tuple[str, str]]:
    """Extract ``(short_name, qualified_path)`` pairs from a use statement.

    Returns an empty list for unparseable / glob (``*``) imports and
    for ``super::`` / ``self::`` relative paths.

    Examples::

        "use lib_b::MyTrait"
            → [("MyTrait", "lib_b::MyTrait")]

        "use lib_b::{Foo, Bar}"
            → [("Foo", "lib_b::Foo"), ("Bar", "lib_b::Bar")]

        "use crate::config::Settings as AppSettings"
            → [("AppSettings", "crate::config::Settings")]

        "use std::collections::*"
            → []
    """
    cleaned = label.strip().removeprefix("use ").rstrip(";")

    # Skip glob imports — cannot resolve to specific symbols.
    if cleaned.endswith("::*"):
        return []

    # Skip relative paths (super::, self::) — too ambiguous.
    if cleaned.startswith(("super::", "self::")):
        return []

    results: list[tuple[str, str]] = []

    if "::{" in cleaned:
        # Braced: use bytes::{Buf, BytesMut}
        base, rest = cleaned.split("::{", maxsplit=1)
        rest = rest.rstrip("}")
        for raw_item in rest.split(","):
            entry = raw_item.strip()
            if not entry or entry == "self":
                continue
            if " as " in entry:
                original = entry.split(" as ", maxsplit=1)[0].strip()
                alias = entry.split(" as ", maxsplit=1)[1].strip()
                full_path = f"{base}::{original}"
                results.append((alias, full_path))
            elif "::" in entry:
                short = entry.rsplit("::", maxsplit=1)[-1]
                full_path = f"{base}::{entry}"
                results.append((short, full_path))
            else:
                results.append((entry, f"{base}::{entry}"))

    elif " as " in cleaned:
        # Aliased: use foo::Bar as Baz
        original = cleaned.split(" as ", maxsplit=1)[0].strip()
        alias = cleaned.split(" as ", maxsplit=1)[1].strip()
        results.append((alias, original))

    elif "::" in cleaned:
        # Simple: use foo::bar::Baz
        short = cleaned.rsplit("::", maxsplit=1)[-1]
        results.append((short, cleaned))

    return results


def _import_crate_hint(
    qualified_path: str,
    crate_index: dict[str, str],
    file_crate: str,
) -> str:
    """Extract the target crate name from a qualified import path.

    ``"crate::config::Settings"`` → *file_crate* (the importing file's crate).
    ``"lib_storage_service::StorageHelper"`` → ``"lib-storage-service"``.

    Returns an empty string when the crate is not a workspace member
    (e.g. ``std``, ``tokio``).
    """
    if "::" not in qualified_path:
        return ""

    first_segment = qualified_path.split("::", maxsplit=1)[0]

    if first_segment == "crate":
        return file_crate

    return crate_index.get(first_segment, "")


# endregion: --- Helper Functions
