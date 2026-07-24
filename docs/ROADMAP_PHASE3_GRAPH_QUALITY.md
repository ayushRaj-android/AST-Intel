# AST_INTEL — Phase 3: Graph Quality & Resolution Roadmap

> Generated 2026-04-28 · Post-Mortem from Safeguard MCP Real-World Testing  
> Prerequisite: All Phase 1 + Phase 2 features complete (1 479 tests passing)  
> Reference: Tested against [Safeguard](https://dev.azure.com/) — 232 files, 3 626 nodes, 15 702 edges

---

## Table of Contents

1. [Background — How the Shortcomings Were Found](#background)
2. [Feature 15 — Type Reference Resolution](#feature-15--type-reference-resolution)
3. [Feature 16 — Cross-Crate Use Resolution](#feature-16--cross-crate-use-resolution)
4. [Feature 17 — Fuzzy Symbol Resolution](#feature-17--fuzzy-symbol-resolution)
5. [Feature 18 — Similarity Auto-Enable](#feature-18--similarity-auto-enable)
6. [Feature 19 — Field Type Unwrapping](#feature-19--field-type-unwrapping)
7. [Feature 20 — Graph Summary Resource](#feature-20--graph-summary-resource)
8. [Implementation Priority Matrix](#implementation-priority-matrix)
9. [File Change Summary](#file-change-summary)

---

## Background

During real-world MCP testing against the Safeguard repository (a production
Rust workspace with 14 crates, 232 source files), five concrete shortcomings
were identified despite overall success (96% average token reduction):

| # | Shortcoming | Symptom | Root Cause |
|---|-------------|---------|------------|
| 1 | `find_path` returns "no path" across services | `find_path("Configuration", "TelegramReleaseService")` → empty | IMPLEMENTS edges target synthetic `type::X` nodes that are disconnected from real trait/struct nodes |
| 2 | `get_impact` produces shallow results | Impact of `StorageHelper` only shows 1 hop; misses transitive dependents | Same `type::X` disconnect — graph is partitioned at trait boundaries |
| 3 | `get_implementors` always returns 0 | `get_implementors("StorageHelper")` → `[]` | IMPLEMENTS edges point to `type::StorageHelper`, not the real node `lib-storage-service/src/…::StorageHelper` |
| 4 | `find_similar` always returns empty | `find_similar("RedisClient")` → `[]` | Similarity edges are not computed unless `--similarity` is passed; MCP `serve` never sets it |
| 5 | Symbol resolution requires 2 calls | Agent must `query("approval")` first, then use the full ID in a second call | `_resolve()` has only 3 tiers (exact → label → substring) with no fuzzy/suffix matching |

These are **graph construction issues**, not query logic bugs — the query
engine is correct given the graph it receives, but the graph itself has
structural gaps.

---

## Feature 15 — Type Reference Resolution ✅

### Priority: **P0 — Critical** | Status: **COMPLETE**

### What

Add a post-processing pass in `GraphBuilder` that resolves synthetic
`type::X` node references to their real graph node IDs. After all files
have been emitted, scan every edge whose target starts with `type::` and
rewrite it to point at the actual struct/trait/enum node.

### Why (Root Cause)

In `graph_builder.py` lines ~355-370, `_emit_impl_edges` creates:

```python
target_id = f"type::{impl_block.trait_type}"
```

This generates edges like:

```
impl_block::StorageHelper → IMPLEMENTS → type::StorageHelper
```

But the actual trait node has ID:

```
src/crates/libs/lib-storage-service/src/abstractions/storage_helper.rs::StorageHelper
```

The `type::StorageHelper` node is never created — it's a dangling reference.
This severs the graph at every trait boundary, making cross-service traversal
impossible. Three of the five shortcomings (1, 2, 3) trace back to this
single issue.

### How

**Phase 15A — Build Global Symbol Table**

After all files have been emitted by `_emit_file_nodes` + `_emit_symbol_nodes`,
build a lookup mapping short type names to full node IDs:

```python
# In GraphBuilder.build(), after the per-file loop:
symbol_table: dict[str, list[str]] = {}
for node in self._nodes.values():
    if node.kind in {NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ENUM, NodeKind.TYPE_ALIAS}:
        symbol_table.setdefault(node.label, []).append(node.id)
```

**Phase 15B — Rewrite Type References**

New method `_resolve_type_references()` called at the end of `build()`:

```python
def _resolve_type_references(self, symbol_table: dict[str, list[str]]) -> None:
    resolved = 0
    for i, edge in enumerate(self._edges):
        if not edge.target.startswith("type::"):
            continue
        short_name = edge.target.removeprefix("type::")
        candidates = symbol_table.get(short_name, [])
        if len(candidates) == 1:
            # Unambiguous — rewrite edge target
            self._edges[i] = GraphEdge(
                source=edge.source, target=candidates[0],
                relation=edge.relation, confidence=edge.confidence,
                confidence_score=edge.confidence_score,
                file=edge.file, span=edge.span,
            )
            resolved += 1
        elif len(candidates) > 1:
            # Ambiguous — try same-crate heuristic
            source_crate = edge.source.split("/")[0] if "/" in edge.source else ""
            same_crate = [c for c in candidates if c.startswith(source_crate)]
            if len(same_crate) == 1:
                self._edges[i] = GraphEdge(
                    source=edge.source, target=same_crate[0],
                    relation=edge.relation, confidence=edge.confidence,
                    confidence_score=edge.confidence_score,
                    file=edge.file, span=edge.span,
                )
                resolved += 1
            else:
                logger.debug("Ambiguous type::%s → %d candidates", short_name, len(candidates))
    logger.info("Resolved %d type:: references", resolved)
```

**Phase 15C — Remove Dangling type:: Nodes**

After rewriting, remove any remaining `type::` entries from the node set
(these were phantom nodes with no definition):

```python
# Clean up phantom type:: nodes that were never real
self._nodes = {k: v for k, v in self._nodes.items() if not k.startswith("type::")}
```

### Impact

| Query | Before | After |
|-------|--------|-------|
| `get_implementors("StorageHelper")` | `[]` (0 results) | All impl blocks that implement the trait |
| `find_path("Configuration", "TelegramReleaseService")` | No path found | Path through shared traits |
| `get_impact("StorageHelper", depth=3)` | 1 direct dependent | Full transitive tree across crates |

### Files Modified

| File | Change |
|------|--------|
| `ast_intel/core/graph_builder.py` | Add `_resolve_type_references()`, call after emission loop |
| `tests/test_graph_builder.py` | Add tests for type resolution (unambiguous, ambiguous, same-crate heuristic) |

### Estimated Effort: 2-3 days

---

## Feature 16 — Cross-Crate Use Resolution

### Priority: **P2 — Important**

### What

Resolve Rust `use` import statements to their target nodes. Currently,
`_emit_import_edges` creates `import::use X` nodes that are dead-end
strings with no connection to the target module or symbol. This feature
adds a `RESOLVES_TO` edge from each import node to the actual
struct/function/trait it refers to.

### Why (Root Cause)

In `graph_builder.py`, `_emit_import_edges` creates edges like:

```
file.rs → IMPORTS → import::use lib_storage_service::StorageHelper
```

But `import::use lib_storage_service::StorageHelper` is a leaf node — it
doesn't connect to the real `StorageHelper` node in `lib-storage-service`.
This means the graph has no symbol-level cross-crate connectivity.

The existing `_emit_cross_ref_edges` only creates crate-level `DEPENDS_ON`
edges (crate A → DEPENDS_ON → crate B) from `Cargo.toml` manifest parsing,
which is too coarse for symbol-level queries.

### How

**Phase 16A — Module Path Index**

Build a mapping from Rust module paths to file paths:

```python
class GraphBuilder:
    def _build_module_index(self) -> dict[str, str]:
        """Map 'crate::module::symbol' paths to file node IDs."""
        index: dict[str, str] = {}
        for crate_name, crate_ast in self._ast.crates.items():
            for fp, file_ast in crate_ast.files.items():
                # Map file path to module path
                mod_path = self._file_to_module_path(crate_name, fp)
                for node in file_ast.all_symbols():
                    full_path = f"{mod_path}::{node.name}"
                    node_id = _node_id(fp, node.name)
                    index[full_path] = node_id
        return index
```

**Phase 16B — New Edge Relation**

Add `RESOLVES_TO` to `EdgeRelation` enum in `graph_model.py`:

```python
class EdgeRelation(str, Enum):
    ...
    RESOLVES_TO = "resolves_to"
```

**Phase 16C — Resolve Import Targets**

After building the module index, rewrite import nodes to add `RESOLVES_TO`
edges:

```python
def _resolve_imports(self, module_index: dict[str, str]) -> None:
    for node_id, node in list(self._nodes.items()):
        if node.kind != NodeKind.IMPORT:
            continue
        # Extract the use path from the import label
        use_path = node.label.removeprefix("use ")
        if use_path in module_index:
            self._edge(GraphEdge(
                source=node_id, target=module_index[use_path],
                relation=EdgeRelation.RESOLVES_TO,
                confidence=Confidence.INFERRED, confidence_score=SCORE_INFERRED,
                file=node.file, span=node.span,
            ))
```

**Phase 16D — Workspace Module Resolver** (new file)

Create `ast_intel/core/_module_resolver.py` to encapsulate the
file-path-to-module-path logic for Rust (and later Python/TypeScript):

- Parse `src/lib.rs`, `src/main.rs`, `mod.rs` declarations
- Handle `pub mod X;` → `src/X.rs` or `src/X/mod.rs`
- Handle `#[path = "..."]` attributes
- Handle workspace member crate names from `Cargo.toml`

### Impact

| Query | Before | After |
|-------|--------|-------|
| `find_path("RedisClient", "StorageHelper")` | No path (different crates) | Path via import → RESOLVES_TO → trait node |
| `find_usages("StorageHelper")` | Only usages within lib-storage-service | All files that `use` the trait |

### Files Modified / Created

| File | Change |
|------|--------|
| `ast_intel/models/graph_model.py` | Add `RESOLVES_TO` to `EdgeRelation` |
| `ast_intel/core/graph_builder.py` | Add `_build_module_index()`, `_resolve_imports()` |
| `ast_intel/core/_module_resolver.py` | **NEW** — Module path resolution logic |
| `ast_intel/core/workspace.py` | Expose module tree info for resolver |
| `tests/test_module_resolver.py` | **NEW** — Module resolution tests |
| `tests/test_graph_builder.py` | Add import resolution tests |

### Estimated Effort: 3-4 days

---

## Feature 17 — Fuzzy Symbol Resolution ✅

### Priority: **P1 — High** | Status: **COMPLETE**

### What

Upgrade the `_resolve()` method in `QueryEngine` to use a multi-tier
resolution strategy with fuzzy matching, suffix matching, and
disambiguation responses. This allows agents to find symbols without
knowing exact node IDs.

### Why (Root Cause)

The current `_resolve()` in `_query_engine.py` has only 3 tiers:

```python
def _resolve(self, name: str) -> list[str]:
    # Tier 1: Exact ID match
    if name in self._node_map:
        return [name]
    # Tier 2: Exact label match (case-insensitive)
    ids = self._label_index.get(name.lower(), [])
    if ids:
        return ids
    # Tier 3: Substring fallback
    lower = name.lower()
    return [n.id for n in self._graph.nodes if lower in n.label.lower()]
```

Problems:
- No suffix matching: `"StorageHelper"` doesn't match because the label
  index stores full labels, and the substring tier matches too broadly
- No disambiguation: if 5 nodes match, the caller gets all 5 with no
  guidance on which to pick
- No fuzzy matching: `"storag_helper"` (typo) returns nothing

### How

**Phase 17A — Enhanced Resolution Tiers**

Replace `_resolve()` with a 6-tier strategy:

```python
def _resolve(self, name: str) -> list[str]:
    # Tier 1: Exact ID
    if name in self._node_map:
        return [name]

    # Tier 2: Exact label (case-insensitive)
    ids = self._label_index.get(name.lower(), [])
    if ids:
        return ids

    # Tier 3: Suffix match — "StorageHelper" matches
    #         "lib-storage-service/.../storage_helper.rs::StorageHelper"
    lower = name.lower()
    suffix_matches = [
        n.id for n in self._graph.nodes
        if n.id.lower().endswith(f"::{lower}") or n.label.lower() == lower
    ]
    if suffix_matches:
        return suffix_matches

    # Tier 4: Token match — "storage helper" matches "StorageHelper"
    tokens = set(lower.replace("_", " ").replace("-", " ").split())
    token_matches = [
        n.id for n in self._graph.nodes
        if tokens <= set(n.label.lower().replace("_", " ").replace("-", " ").split())
    ]
    if token_matches:
        return token_matches

    # Tier 5: Substring (existing behavior)
    substr_matches = [n.id for n in self._graph.nodes if lower in n.label.lower()]
    if substr_matches:
        return substr_matches

    # Tier 6: Levenshtein fuzzy (optional)
    return self._fuzzy_resolve(name)

def _fuzzy_resolve(self, name: str, threshold: int = 3) -> list[str]:
    """Last resort — find nodes within edit distance threshold."""
    results: list[tuple[int, str]] = []
    lower = name.lower()
    for n in self._graph.nodes:
        dist = _levenshtein(lower, n.label.lower())
        if dist <= threshold:
            results.append((dist, n.id))
    results.sort(key=lambda x: x[0])
    return [r[1] for r in results[:10]]
```

**Phase 17B — Disambiguation Response**

When `_resolve()` returns multiple matches, the MCP tool should return a
disambiguation list instead of silently picking the first:

```python
# In mcp_server.py dispatch:
if len(ids) > 5:
    return [_text({
        "status": "ambiguous",
        "message": f"'{name}' matches {len(ids)} symbols. Narrow your query.",
        "top_matches": [
            {"id": n.id, "label": n.label, "kind": n.kind.value, "file": n.file}
            for n in (node_map[i] for i in ids[:10] if i in node_map)
        ]
    })]
```

### Impact

| Query | Before | After |
|-------|--------|-------|
| `query("StorageHelper")` | Needs exact label or substring | Matches via suffix tier |
| `query("storag_helper")` | No results | Fuzzy match within edit distance 2 |
| `query("config")` | Returns 50+ substring matches with no ranking | Top 10 ranked by relevance tier |

### Files Modified

| File | Change |
|------|--------|
| `ast_intel/core/_query_engine.py` | Replace `_resolve()` with 6-tier strategy, add `_fuzzy_resolve()` |
| `ast_intel/mcp_server.py` | Add disambiguation response when matches > threshold |
| `tests/test_query_engine.py` | Add resolution tier tests (suffix, token, fuzzy, disambiguation) |

### Estimated Effort: 1-2 days

---

## Feature 18 — Similarity Auto-Enable

### Priority: **P2 — Quality of Life**

### What

Automatically compute similarity edges in the MCP server if the graph
has no `SIMILAR_TO` edges. Currently, the `find_similar` tool always
returns empty unless the user passed `--similarity` during scan — but
the MCP `serve` command has no way to specify this.

### Why (Root Cause)

The `serve` subcommand in `cli.py` calls `load_or_build_graph()` without
the `similarity=True` flag. Even if the graph was previously built without
similarity edges, the MCP server has no way to add them after the fact.
The `find_similar` tool silently returns `[]` with no error message,
making the agent think there are simply no similar symbols.

### How

**Phase 18A — Lazy Similarity Computation**

When `find_similar` is called and zero `SIMILAR_TO` edges exist, compute
them on the fly:

```python
# In mcp_server.py, find_similar tool handler:
similar_edges = [e for e in graph.edges if e.relation == EdgeRelation.SIMILAR_TO]
if not similar_edges:
    try:
        from ast_intel.core._similarity import compute_similarity_edges
        new_edges = compute_similarity_edges(graph.nodes, threshold=0.3)
        graph.edges.extend(new_edges)
        # Rebuild DiGraph
        engine = QueryEngine(graph)  # Re-initialize with new edges
    except ImportError:
        return [_text({"error": "Similarity requires networkx. Install with: pip install ast-intel[analysis]"})]
```

**Phase 18B — Add `--similarity` Flag to `serve`**

Add a `--similarity` option to the `serve` subcommand so users can
pre-compute similarity edges at startup:

```python
@app.command()
def serve(
    repo_path: Path,
    similarity: bool = typer.Option(False, help="Pre-compute similarity edges"),
    ...
):
```

### Impact

| Query | Before | After |
|-------|--------|-------|
| `find_similar("RedisClient")` | `[]` (no SIMILAR_TO edges) | List of structurally similar symbols |
| MCP `serve` startup | No similarity option | `--similarity` flag pre-computes edges |

### Files Modified

| File | Change |
|------|--------|
| `ast_intel/mcp_server.py` | Add lazy similarity computation in `find_similar` handler |
| `ast_intel/cli.py` | Add `--similarity` flag to `serve` subcommand |
| `tests/test_mcp_server.py` | Add test for auto-similarity edge computation |

### Estimated Effort: 0.5 days

---

## Feature 19 — Field Type Unwrapping ✅

### Priority: **P1 — High** | Status: **COMPLETE**

### What

Fix `_extract_base_type()` in `graph_builder.py` to unwrap generic
wrappers (Arc, Box, Option, Vec, Rc, Mutex, RwLock) and extract the
**inner** type, not the wrapper itself. This dramatically increases
the density of `HAS_FIELD` edges in the graph.

### Why (Root Cause)

The current `_extract_base_type()` at `graph_builder.py` line ~558 strips
a type like `Arc<Configuration>` to just `Arc` — then discards it because
`Arc` starts with uppercase but is in `_PRIMITIVES`. Even if it weren't
filtered, the HAS_FIELD edge would point to `type::Arc` instead of
`type::Configuration`.

In the Safeguard codebase, nearly every struct field uses `Arc<T>`,
`Option<T>`, or `Vec<T>` wrappers. This means **most HAS_FIELD edges
are lost**, severing connectivity between structs and the types they
contain.

### How

Modify `_extract_base_type()` to unwrap known wrappers:

```python
_WRAPPERS: frozenset[str] = frozenset({
    "Arc", "Rc", "Box", "Option", "Vec", "Mutex", "RwLock",
    "RefCell", "Cell", "Weak", "Pin", "MutexGuard", "RwLockReadGuard",
    "RwLockWriteGuard", "Cow", "OnceCell", "OnceLock",
})

def _extract_base_type(type_str: str) -> str:
    s = type_str.strip()

    # Strip references and mutability
    for prefix in ("&mut ", "&", "*const ", "*mut "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    # Unwrap known wrappers to get the inner type
    # "Arc<Configuration>" → "Configuration"
    # "Option<Vec<String>>" → "Vec" (then "String" on next unwrap)
    # Keep unwrapping until we reach a non-wrapper type
    while "<" in s:
        outer = s[:s.index("<")].strip()
        if "::" in outer:
            outer = outer.rsplit("::", maxsplit=1)[-1]
        if outer in _WRAPPERS:
            # Extract inner: everything between first '<' and last '>'
            inner = s[s.index("<") + 1 : s.rindex(">")].strip()
            # Handle multiple type params: take the first non-primitive
            if "," in inner:
                parts = _split_type_params(inner)
                # Pick first non-primitive, non-wrapper type
                for part in parts:
                    candidate = _extract_base_type(part.strip())
                    if candidate:
                        return candidate
                return ""
            s = inner
        else:
            # Non-wrapper generic (e.g., HashMap<K,V>) — keep outer as type
            s = outer
            break

    # Strip path prefix
    if "::" in s:
        s = s.rsplit("::", maxsplit=1)[-1]

    if not s or s in _PRIMITIVES:
        return ""

    if not s[0].isupper():
        return ""

    return s
```

Add a helper to split generic type parameters respecting nesting:

```python
def _split_type_params(s: str) -> list[str]:
    """Split 'A, B<C, D>, E' into ['A', 'B<C, D>', 'E']."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts
```

### Impact

| Field Type | Before | After |
|------------|--------|-------|
| `Arc<Configuration>` | No HAS_FIELD edge (stripped to `Arc`, filtered) | `HAS_FIELD → type::Configuration` (resolved by Feature 15) |
| `Option<Box<MyStruct>>` | No edge (stripped to `Option`, filtered) | `HAS_FIELD → type::MyStruct` |
| `Vec<TelegramPacket>` | No edge (stripped to `Vec`, filtered) | `HAS_FIELD → type::TelegramPacket` |
| `HashMap<String, Value>` | No edge (stripped to `HashMap`) | `HAS_FIELD → type::HashMap` (kept as non-primitive) |

### Files Modified

| File | Change |
|------|--------|
| `ast_intel/core/graph_builder.py` | Rewrite `_extract_base_type()`, add `_WRAPPERS`, `_split_type_params()` |
| `tests/test_graph_builder.py` | Add unwrapping tests for all wrapper combinations |

### Estimated Effort: 1 day

---

## Feature 20 — Graph Summary Resource

### Priority: **P3 — Nice to Have**

### What

Add an MCP **resource** (not tool) that provides a codebase overview:
total nodes by kind, total edges by relation, list of crates/files,
top-connected symbols. This gives agents immediate situational awareness
without needing to call a tool first.

### Why

When an agent connects to the MCP server, it has no idea what's in the
graph. The first call is always a discovery query (`list_files`, `query`
with a broad term). A summary resource eliminates this cold-start problem
by giving the agent a bird's-eye view immediately.

### How

Expose an MCP resource at `ast-intel://summary`:

```python
@server.list_resources()
async def list_resources() -> list[Resource]:
    return [Resource(
        uri="ast-intel://summary",
        name="Codebase Summary",
        description="Overview of the analyzed codebase graph",
        mimeType="application/json",
    )]

@server.read_resource()
async def read_resource(uri: str) -> str:
    if uri != "ast-intel://summary":
        raise ValueError(f"Unknown resource: {uri}")
    return json.dumps({
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
        "nodes_by_kind": _count_by(graph.nodes, lambda n: n.kind.value),
        "edges_by_relation": _count_by(graph.edges, lambda e: e.relation.value),
        "crates": [n.label for n in graph.nodes if n.kind == NodeKind.CRATE],
        "files": len([n for n in graph.nodes if n.kind == NodeKind.FILE]),
        "top_connected": _top_connected(graph, 10),
    })
```

### Files Modified

| File | Change |
|------|--------|
| `ast_intel/mcp_server.py` | Add `list_resources()`, `read_resource()` handlers |
| `tests/test_mcp_server.py` | Add resource listing and reading tests |

### Estimated Effort: 1 day

---

## Implementation Priority Matrix

| Feature | Priority | Effort | Shortcomings Fixed | Dependencies | Status |
|---------|----------|--------|-------------------|--------------|--------|
| **15 — Type Reference Resolution** | **P0** | 2-3 days | #1, #2, #3 | None | ✅ Done |
| **19 — Field Type Unwrapping** | **P1** | 1 day | Amplifies #15 | Best after 15 | ✅ Done |
| **17 — Fuzzy Symbol Resolution** | **P1** | 1-2 days | #5 | None | ✅ Done |
| **16 — Cross-Crate Use Resolution** | **P2** | 3-4 days | #1 (fully) | Best after 15 | ✅ Done |
| **18 — Similarity Auto-Enable** | **P2** | 0.5 day | #4 | None | ✅ Done |
| **20 — Graph Summary Resource** | **P3** | 1 day | Cold-start UX | None | ✅ Done |

### Recommended Implementation Order

```
Feature 15 (P0)  ──→  Feature 19 (P1)  ──→  Feature 16 (P2)
                                                     │
Feature 17 (P1)  ──→  (independent)                  │
                                                     ▼
Feature 18 (P2)  ──→  (independent)            Feature 20 (P3)
```

**Sprint plan:**

| Sprint | Features | Cumulative Effect |
|--------|----------|-------------------|
| Sprint 1 | 15, 17 | `get_implementors` works, `find_path` connects across traits, agents can use natural names |
| Sprint 2 | 19, 18 | HAS_FIELD edges 5-10x denser, `find_similar` works in MCP |
| Sprint 3 | 16, 20 | Full cross-crate symbol resolution, agent cold-start eliminated |

---

## File Change Summary

### New Files

| File | Feature | Purpose |
|------|---------|---------|
| `ast_intel/core/_module_resolver.py` | 16 | Rust module path → file path resolution |
| `tests/test_module_resolver.py` | 16 | Module resolver tests |

### Modified Files

| File | Features | Changes |
|------|----------|---------|
| `ast_intel/core/graph_builder.py` | 15, 16, 19 | Type resolution pass, import resolution, `_extract_base_type` rewrite |
| `ast_intel/core/_query_engine.py` | 17 | 6-tier `_resolve()`, `_fuzzy_resolve()` |
| `ast_intel/models/graph_model.py` | 16 | Add `RESOLVES_TO` edge relation |
| `ast_intel/mcp_server.py` | 17, 18, 20 | Disambiguation responses, lazy similarity, summary resource |
| `ast_intel/cli.py` | 18 | `--similarity` flag on `serve` |
| `tests/test_graph_builder.py` | 15, 19 | Type resolution + unwrapping tests |
| `tests/test_query_engine.py` | 17 | Fuzzy resolution tests |
| `tests/test_mcp_server.py` | 18, 20 | Auto-similarity + resource tests |

---

## Validation Plan

After implementing all Phase 3 features, re-run the Safeguard MCP test
suite from the Phase 2 post-mortem and verify:

| Test | Expected Result |
|------|----------------|
| `get_implementors("StorageHelper")` | ≥ 3 impl blocks returned |
| `find_path("Configuration", "TelegramReleaseService")` | Valid path with ≤ 6 hops |
| `get_impact("StorageHelper", depth=3)` | ≥ 10 affected symbols across ≥ 3 crates |
| `find_similar("RedisClient")` | ≥ 2 similar symbols |
| `query("storage helper")` | Matches `StorageHelper` via token tier |
| MCP resource `ast-intel://summary` | Returns node/edge counts, crate list |

---

*This document is the single source of truth for the AST_INTEL Phase 3
graph quality improvements. Update it as features complete.*
