# AST_INTEL — Feature Roadmap & Architecture Plan

> Generated 2026-04-27 · Architect Document  
> Benchmark reference: [Graphify v5](https://github.com/safishamsi/graphify/tree/v5)

---

## Table of Contents

1. [Feature 1 — Source Line Numbers / Spans](#feature-1--source-line-numbers--spans)
2. [Feature 2 — Intra-File Call-Graph Extraction](#feature-2--intra-file-call-graph-extraction)
3. [Feature 3 — Confidence Tagging](#feature-3--confidence-tagging)
4. [Feature 4 — Rationale Comment Extraction](#feature-4--rationale-comment-extraction)
5. [Feature 5 — Graph Output Mode](#feature-5--graph-output-mode)
6. [Feature 6 — Graph Analysis (God Nodes, Communities, Surprises)](#feature-6--graph-analysis-god-nodes-communities-surprises)
7. [Dependency Graph Between Features](#dependency-graph-between-features)
8. [Suggested Implementation Order](#suggested-implementation-order)

---

## Feature 1 — Source Line Numbers / Spans

### Why This Matters (The Gap)

Every node in our current model (`StructNode`, `FunctionNode`, `MethodNode`, `EnumNode`, `TraitNode`, `ConstantNode`, `TypeAliasNode`, `MacroNode`, `ModuleNode`) carries **zero positional information**. You know a function named `parse` exists in `src/parser.py`, but you don't know if it starts at line 42 or line 742.

This single missing field blocks:

| Blocked Capability | How Lines Would Unblock It |
|-|-|
| IDE go-to-definition | Need `(start_line, start_col)` to jump to the right place |
| Git-blame / PR-diff integration | "Did this PR change any function in the auth module?" requires knowing line ranges |
| Vulnerability pinpointing | Security tools need byte-precise locations |
| Graph `source_location` edges | Feature 5 (graph output) needs `L42` annotations on every node — Graphify has this on every single node/edge |
| Call-graph edge annotations | Feature 2 (call graph) needs call-site line numbers |
| Markdown summary improvements | "function `parse` (L42-L89)" is far more useful than just "function `parse`" |

**Graphify comparison**: Every node has `source_location: "L42"`. Every edge has `source_file` + `source_location`. This is the single most impactful data-quality gap between us and them.

### How It Works

Tree-sitter already provides `node.start_point` (row, col) and `node.end_point` (row, col) on every AST node. We already call `node.start_byte` for text extraction in every extractor. We simply aren't recording the positional data.

### Design Decision: `Span` Dataclass

```python
@dataclass(frozen=True, slots=True)
class Span:
    """Source location span for any extracted symbol.
    
    Line numbers are 1-based (matching editor conventions).
    Column numbers are 0-based (matching tree-sitter convention).
    """
    start_line: int    # 1-based
    start_col: int     # 0-based
    end_line: int      # 1-based
    end_col: int       # 0-based
```

**Trade-off analysis:**

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Single `line: int` field on each node | Minimal schema change, mirrors Graphify | Loses end position and column info | Rejected — too little data for diff/blame |
| B) `Span` dataclass (start + end) | Full range, enables diff integration, precise | Slightly larger schema, more fields to populate | **Chosen** |
| C) Byte offsets (`start_byte`, `end_byte`) | Most precise, cheapest to extract | Useless without the source file open; editors use lines | Rejected — bad UX |

### Phased Implementation

#### Phase 1A — Model Changes (models/ast_node.py)

**Files**: `ast_intel/models/ast_node.py`

1. Add `Span` dataclass to `ast_node.py` with `__all__` export
2. Add `span: Span | None = None` field to every node dataclass:
   - `StructNode`, `EnumNode`, `EnumVariantNode`, `FunctionNode`, `MethodNode`
   - `TraitNode`, `TraitItemNode`, `ImplBlockNode`
   - `TypeAliasNode`, `ConstantNode`, `ModuleNode`, `MacroNode`
   - `FieldNode` (for struct/enum fields, optional — lower priority)
3. Keep it `None`-defaulted so all existing code continues to work
4. Update `FileAST` docstring to document the new field

**Risk**: Frozen dataclasses mean every construction site must pass `span=`. Mitigated by the `None` default.

#### Phase 1B — Extractor Helper (extractors/base.py or shared util)

**Files**: `ast_intel/extractors/base.py` or new `ast_intel/extractors/_span.py`

1. Add a shared helper function:
   ```python
   def _span_from_node(node: Node) -> Span:
       """Convert tree-sitter start_point/end_point to a Span."""
       r0, c0 = node.start_point
       r1, c1 = node.end_point
       return Span(start_line=r0 + 1, start_col=c0, end_line=r1 + 1, end_col=c1)
   ```
2. This is the **only** place that does the `+1` conversion (tree-sitter is 0-based rows, we output 1-based lines)

#### Phase 1C — Wire Into Each Extractor

**Files**: `python.py`, `typescript.py`, `rust.py`, `csharp.py`

For each extractor, at every point where a node dataclass is constructed, add `span=_span_from_node(ts_node)`:

- **Python**: `_extract_class` → `StructNode(span=...)`, `_extract_function` → `FunctionNode(span=...)`, etc.
- **TypeScript**: same pattern
- **Rust**: same pattern
- **C#**: same pattern

Estimated touch-points: ~15-20 per extractor, ~60-80 total edits across 4 files.

#### Phase 1D — Formatter Updates

**Files**: `json_formatter.py`, `markdown_formatter.py`

1. **JSON**: `Span` serializes automatically via the custom encoder (it's a dataclass, already handled). Verify `span` appears in output.
2. **Markdown**: Append `(L{start_line})` or `(L{start_line}-L{end_line})` to function/struct names in tables.

#### Phase 1E — Tests

**Files**: every `test_*_extractor.py`, `test_models.py`, `test_formatters.py`

1. Assert `span is not None` on every extracted node in existing test fixtures
2. Assert specific line numbers for at least 3-4 well-known fixtures per language
3. Add a round-trip test: extract → JSON → parse back → spans match

**Total effort estimate**: ~2-3 days

---

## Feature 2 — Intra-File Call-Graph Extraction

### Why This Matters (The Gap)

Our `imported_package_methods` tracks `Type.method()` calls where the type is explicitly scoped via an import. But we completely miss:

```python
# We capture:
response = requests.get(url)        # ✅ scoped: requests::get

# We do NOT capture:
def process(data):
    result = validate(data)          # ❌ free-function-to-free-function call 
    return transform(result)         # ❌ also missed
```

**Graphify's approach**: After extracting all function/class nodes, they do a second `walk_calls()` pass over every function body. For each `call_expression`, they resolve the callee name against a `label_to_nid` lookup table of all functions defined in the file. Unresolved calls are saved in `raw_calls` for cross-file resolution later.

This is their **biggest extraction-quality advantage over us**. Their graph has `calls` edges; ours has none.

### Design Decision: `CallEdge` Model

```python
@dataclass(frozen=True, slots=True)
class CallEdge:
    """A call-site from one function/method to another.
    
    The caller is known precisely (it's the enclosing function).
    The callee may be resolved (known target) or unresolved (just a name).
    """
    caller: str             # Qualified name of the calling function
    callee: str             # Name of the called function/method
    call_site: Span         # Where the call happens (requires Feature 1)
    resolved_target: str    # Qualified name if resolved, empty if not
    is_method_call: bool    # True if obj.method() style
    confidence: str         # "EXTRACTED" if resolved intra-file, "INFERRED" cross-file
```

**Trade-off analysis:**

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Flat list of `(caller, callee)` string pairs | Simple, like Graphify | Loses call-site info, no resolution status | Rejected |
| B) `CallEdge` dataclass with span + resolution | Full info, enables confidence tagging, graph edges | More complex model, more extraction work | **Chosen** |
| C) Full type-inferred call graph | Accurate method dispatch resolution | Requires type inference engine — massive scope creep | Rejected for now |

### Phased Implementation

#### Phase 2A — Model (ast_node.py)

**Files**: `ast_intel/models/ast_node.py`

1. Add `CallEdge` dataclass
2. Add `call_edges: list[CallEdge] = field(default_factory=list)` to `FileAST`
3. Export in `__all__`

#### Phase 2B — Extractor Infrastructure (extractors/base.py or new _call_graph.py)

**Files**: new `ast_intel/extractors/_call_graph.py`

Create a language-agnostic call-extraction helper:

```python
def extract_intra_file_calls(
    root_node: Node,
    source: bytes,
    function_bodies: list[tuple[str, Node, Span]],  # (qualified_name, body_node, fn_span)
    defined_symbols: dict[str, str],                  # lower_name → qualified_name
    call_node_types: frozenset[str],                   # e.g. {"call", "call_expression"}
    function_boundary_types: frozenset[str],            # stop recursion types
    resolve_callee_fn: Callable,                       # language-specific callee resolver
) -> list[CallEdge]:
    ...
```

This mirrors Graphify's `walk_calls` but returns typed `CallEdge` objects instead of dict mutations.

#### Phase 2C — Wire Into Python Extractor First (extractors/python.py)

**Files**: `ast_intel/extractors/python.py`

1. After the main walk, collect all `(func_name, body_node)` pairs from functions + methods
2. Build a `defined_symbols` map from all `FunctionNode.name` and `MethodNode.name` in the file
3. Walk each function body looking for `call` nodes (tree-sitter type: `call`)
4. For `identifier` callees → look up in `defined_symbols`
5. For `attribute` callees (e.g. `self.method()`) → look up in class scope
6. Unresolved calls → `CallEdge(resolved_target="", confidence="UNRESOLVED")`
7. Resolved calls → `CallEdge(resolved_target=qualified, confidence="EXTRACTED")`

**Python-specific call node types**:
- `call` → function call
- First child = `identifier` (plain call) or `attribute` (method call)

#### Phase 2D — Wire Into Remaining Extractors

**Files**: `typescript.py`, `rust.py`, `csharp.py`

Each extractor needs:
- A `resolve_callee` function that understands its language's call syntax
- TypeScript: `call_expression` → `identifier` | `member_expression`
- Rust: `call_expression` | `macro_invocation` → `identifier` | `field_expression` | `scoped_identifier`
- C#: `invocation_expression` → `identifier` | `member_access_expression`

#### Phase 2E — Cross-File Call Resolution (Indexer)

**Files**: `ast_intel/core/indexer.py`

New **Pass 6**: Cross-file call resolution.

1. Collect all unresolved `CallEdge` entries across all files
2. Build a global `name → qualified_name` map from the function_file_index
3. For each unresolved call, try to match callee name against the global map
4. If matched: update `resolved_target` and set `confidence = "INFERRED"`
5. If ambiguous (multiple matches): leave unresolved or mark `confidence = "AMBIGUOUS"`

#### Phase 2F — Tests

1. Fixture files with known call chains
2. Assert resolved intra-file calls have correct caller/callee
3. Assert unresolved calls are captured with empty `resolved_target`
4. Cross-file resolution tests with multi-file fixtures

**Total effort estimate**: ~4-5 days  
**Depends on**: Feature 1 (Spans) — call-site locations require `Span`

---

## Feature 3 — Confidence Tagging

### Why This Matters (The Gap)

Graphify tags every edge as `EXTRACTED`, `INFERRED`, or `AMBIGUOUS` with a numeric `confidence_score` (0.0–1.0). This is critical because:

1. **Consumers can filter by trustworthiness** — "show me only EXTRACTED edges" for security audits, vs "include INFERRED for exploration"
2. **It's honest about uncertainty** — our `imported_package_methods` resolution is heuristic (import map + string matching), but we present it the same as a guaranteed `use` statement
3. **Graph analysis needs it** — Feature 6 (god nodes, communities) should weight EXTRACTED edges more heavily than INFERRED

### Current Implicit Confidence Levels in AST_INTEL

| Data Point | Actual Confidence | Currently Tagged? |
|-|-|-|
| `uses` (raw import strings) | EXTRACTED — directly from AST | No |
| `StructNode`, `FunctionNode`, etc. | EXTRACTED — direct tree-sitter parse | No |
| `imported_package_methods` | INFERRED — import map heuristic, may false-positive | No |
| `ImplBlockNode` from base class detection | EXTRACTED for explicit syntax, INFERRED for Python heuristic `class Foo(Bar)` | No |
| Cross-file call resolution (Feature 2E) | INFERRED — name-based matching | No (doesn't exist yet) |
| `ConstantNode` from UPPER_CASE heuristic | INFERRED — naming convention, not syntax | No |
| `TypeAliasNode` from CamelCase heuristic (Python) | INFERRED — fragile heuristic | No |

### Design Decision: `Confidence` Enum + Per-Edge Tagging

```python
class Confidence(StrEnum):
    """Confidence level for extracted data."""
    EXTRACTED = "extracted"   # Directly found in source AST — guaranteed correct
    INFERRED = "inferred"     # Reasonable deduction from heuristics — usually correct
    AMBIGUOUS = "ambiguous"   # Uncertain — flagged for review
```

**Where to apply it:**

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Tag every node (Struct, Fn, etc.) | Complete provenance | Overkill — AST nodes are always EXTRACTED | Rejected |
| B) Tag only relationships/edges | Targets the uncertainty | Requires relationship model | **Chosen** |
| C) Tag both nodes and edges | Full Graphify parity | Excessive for deterministic parser | Rejected |

Edges that need tagging: `imported_package_methods`, `CallEdge`, `ImplBlockNode` (Python base class inference), `ConstantNode` (UPPER_CASE heuristic), `TypeAliasNode` (CamelCase heuristic), cross-file indexer resolutions.

### Phased Implementation

#### Phase 3A — Model (ast_node.py)

**Files**: `ast_intel/models/ast_node.py`

1. Add `Confidence` enum to `ast_node.py`
2. Add `confidence: Confidence = Confidence.EXTRACTED` to:
   - `CallEdge` (Feature 2)
   - `ConstantNode` (UPPER_CASE heuristic → `INFERRED`)
   - `TypeAliasNode` (CamelCase heuristic → `INFERRED` for Python)
   - `ImplBlockNode` (for Python base class detection → `INFERRED` when heuristic)
3. Add `confidence_score: float = 1.0` for numeric confidence (1.0 for EXTRACTED, 0.7-0.9 for INFERRED)

#### Phase 3B — Tag Existing Heuristics in Extractors

**Files**: `python.py`, `typescript.py`, `rust.py`, `csharp.py`

1. **Python constants** (UPPER_CASE): set `confidence=Confidence.INFERRED, confidence_score=0.8`
2. **Python type aliases** (CamelCase + `TypeAlias`): `INFERRED` when name-heuristic, `EXTRACTED` when explicit `TypeAlias` annotation
3. **Python base class → ImplBlockNode**: `EXTRACTED` when explicit syntax, `INFERRED` when inferred from naming
4. **TypeScript/C#/Rust**: all direct AST extraction → `EXTRACTED` (default, no changes needed)

#### Phase 3C — Tag imported_package_methods

**Files**: all extractors, `workspace_model.py`

Change `imported_package_methods: dict[str, list[str]]` to carry confidence:

```python
@dataclass(frozen=True, slots=True)
class PackageMethodCall:
    method: str
    confidence: Confidence = Confidence.INFERRED
    confidence_score: float = 0.8

# In FileAST:
imported_package_methods: dict[str, list[PackageMethodCall]]
```

#### Phase 3D — Tag Cross-File Indexer Resolutions

**Files**: `ast_intel/core/indexer.py`

When cross-file call resolution (Feature 2E) resolves a call by name matching:
- Single match → `INFERRED, 0.85`  
- Multiple matches → `AMBIGUOUS, 0.5`

#### Phase 3E — Formatter + Test Updates

1. JSON: serialize `confidence` and `confidence_score` fields
2. Markdown: append `[inferred]` or `[ambiguous]` annotation to uncertain items
3. Tests: verify confidence tags on known heuristic extractions

**Total effort estimate**: ~2 days  
**Depends on**: Feature 2 (CallEdge model for call confidence)

---

## Feature 4 — Rationale Comment Extraction

### Why This Matters (The Gap)

Codebases contain critical design rationale in special comments:

```python
# NOTE: We use a custom allocator here because the default one fragments memory
# HACK: Temporary workaround for upstream bug #1234, remove after v3.0
# WHY: This serialization format was chosen for backward compat with v1 clients
# TODO: Replace with async implementation when tokio 2.0 is stable
# FIXME: Race condition under high concurrency — needs lock refactor
# IMPORTANT: This function must be called before any I/O operations
# SAFETY: The raw pointer is guaranteed valid for 'static by the arena allocator
```

Graphify extracts these as **separate graph nodes** with `rationale_for` edges connecting them to the enclosing function/class. This means an AI assistant can answer "why was this written this way?" without reading the entire file.

We currently extract `doc` comments (docstrings, `///`, `/** */`) but completely ignore rationale comments.

### Design Decision: `RationaleNode` Dataclass

```python
@dataclass(frozen=True, slots=True)
class RationaleNode:
    """A rationale comment extracted from source code.
    
    Captures design decisions, known issues, and important notes
    from specially-prefixed comments.
    """
    kind: str          # "NOTE", "HACK", "WHY", "TODO", "FIXME", "IMPORTANT", "SAFETY"
    text: str          # The comment text (without the prefix)
    span: Span         # Source location (requires Feature 1)
    parent: str        # Name of the enclosing function/class/module, or file-level
```

**Trade-off analysis:**

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Append to existing `doc` field | No schema change | Mixes docstrings with rationale; loses prefix-kind metadata | Rejected |
| B) Separate `RationaleNode` list on `FileAST` | Clean separation, queryable by kind, preserves parent context | New model, new field, extraction work | **Chosen** |
| C) Inline within each node's `attributes` | Reuses existing field | Wrong semantics — attributes are decorators, not comments | Rejected |

### Recognized Prefixes (Cross-Language)

| Prefix | Languages | Meaning |
|-|-|-|
| `# NOTE:` / `// NOTE:` / `/* NOTE:` | All | Important context |
| `# HACK:` / `// HACK:` | All | Known workaround |
| `# WHY:` / `// WHY:` | All | Design rationale |
| `# TODO:` / `// TODO:` | All | Planned work |
| `# FIXME:` / `// FIXME:` | All | Known bug |
| `# IMPORTANT:` / `// IMPORTANT:` | All | Critical constraint |
| `# SAFETY:` / `// SAFETY:` | Rust, C, C++ | Unsafe-code justification |
| `# RATIONALE:` / `// RATIONALE:` | All | Explicit design decision |
| `# PERF:` / `// PERF:` | All | Performance consideration |

### Phased Implementation

#### Phase 4A — Model (ast_node.py)

**Files**: `ast_intel/models/ast_node.py`

1. Add `RationaleNode` dataclass
2. Add `rationale_comments: list[RationaleNode] = field(default_factory=list)` to `FileAST`
3. Export in `__all__`

#### Phase 4B — Shared Rationale Extractor (extractors/_rationale.py)

**Files**: new `ast_intel/extractors/_rationale.py`

Create a language-agnostic extractor since the logic is almost identical across languages — only the comment token (`#` vs `//` vs `/* */`) differs:

```python
RATIONALE_PREFIXES = {
    "NOTE": re.compile(r"^(?:#|//)\s*NOTE:\s*(.+)", re.IGNORECASE),
    "HACK": re.compile(r"^(?:#|//)\s*HACK:\s*(.+)", re.IGNORECASE),
    "WHY": re.compile(r"^(?:#|//)\s*WHY:\s*(.+)", re.IGNORECASE),
    "TODO": re.compile(r"^(?:#|//)\s*TODO:\s*(.+)", re.IGNORECASE),
    "FIXME": re.compile(r"^(?:#|//)\s*FIXME:\s*(.+)", re.IGNORECASE),
    "IMPORTANT": re.compile(r"^(?:#|//)\s*IMPORTANT:\s*(.+)", re.IGNORECASE),
    "SAFETY": re.compile(r"^(?:#|//)\s*SAFETY:\s*(.+)", re.IGNORECASE),
    "RATIONALE": re.compile(r"^(?:#|//)\s*RATIONALE:\s*(.+)", re.IGNORECASE),
    "PERF": re.compile(r"^(?:#|//)\s*PERF:\s*(.+)", re.IGNORECASE),
}

def extract_rationale_comments(
    root_node: Node,
    source: bytes,
    comment_node_type: str,      # "comment" for all languages via tree-sitter
    enclosing_scope_fn: Callable, # resolves parent scope for a given line
) -> list[RationaleNode]:
    """Walk the tree-sitter AST and extract rationale comments."""
```

**Approach**: Walk all `comment` nodes in the AST (tree-sitter exposes them). For each, check if the text matches a rationale prefix. If so, determine the enclosing scope (nearest function/class/module ancestor) and emit a `RationaleNode`.

Tree-sitter already parses comments as nodes (type `comment` for Python/Rust/TS/C#), so we don't need line-by-line regex scanning — we can walk the AST.

#### Phase 4C — Wire Into Each Extractor

**Files**: `python.py`, `typescript.py`, `rust.py`, `csharp.py`

At the end of each `extract()` method, call the shared rationale extractor:

```python
file_ast.rationale_comments = extract_rationale_comments(
    root_node=tree.root_node,
    source=source,
    comment_node_type="comment",
    enclosing_scope_fn=self._find_enclosing_scope,
)
```

Each extractor implements `_find_enclosing_scope(node)` which walks up the tree to find the nearest `function_definition` / `class_definition` / module.

#### Phase 4D — Formatter + Indexer Updates

**Files**: `json_formatter.py`, `markdown_formatter.py`, `indexer.py`

1. **JSON**: auto-serialized via dataclass encoder
2. **Markdown**: new section per crate: "### Design Rationale" with a table of `| Kind | Text | Location | Scope |`
3. **Indexer**: optional Pass 7 — build a `rationale_index: dict[str, list[RationaleNode]]` mapping symbol names → rationale comments about them

#### Phase 4E — Tests

1. Add rationale comments to existing fixture files
2. Assert correct extraction of each prefix kind
3. Assert correct parent scope resolution
4. Assert multi-line rationale comment merging (when consecutive `# NOTE:` lines)

**Total effort estimate**: ~2-3 days  
**Depends on**: Feature 1 (Spans) — rationale comments need `Span` for location

---

## Feature 5 — Graph Output Mode

### Why This Matters (The Gap)

Our current output formats (`ast.json` and `summary.md`) are excellent for **LLM context** and **human reading**, but they don't model **relationships as first-class edges**. Consumers who want to:

- Visualize dependency graphs
- Run graph algorithms (shortest path, PageRank, community detection)
- Import into Neo4j, Gephi, or VS Code extensions
- Build MCP servers over the graph

…have to reverse-engineer edges from our flat index tables. Graphify outputs `graph.json` (nodes + edges), `graph.html` (interactive vis.js), GraphML, SVG, Neo4j Cypher, and Obsidian vaults.

### Design Decision: Explicit Graph Model

```python
@dataclass(frozen=True, slots=True)
class GraphNode:
    """A node in the code knowledge graph."""
    id: str                     # Unique, stable identifier (e.g., "src/parser.py::Parser")
    label: str                  # Human-readable display name
    kind: str                   # "struct", "function", "trait", "enum", "file", "rationale", etc.
    file: str                   # Source file
    span: Span | None           # Source location (from Feature 1)
    properties: dict[str, str]  # Extra metadata (visibility, async, generics, etc.)

@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed edge in the code knowledge graph."""
    source: str                 # Source node ID
    target: str                 # Target node ID
    relation: str               # "contains", "calls", "imports", "implements", "inherits", 
                                # "method_of", "uses", "rationale_for", etc.
    confidence: Confidence      # From Feature 3
    confidence_score: float     # 0.0-1.0
    file: str                   # Where this relationship was found
    span: Span | None           # Call-site or import line

@dataclass(slots=True)
class CodeGraph:
    """The full code knowledge graph for a workspace."""
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    meta: WorkspaceMeta
```

### Edge Types We Can Extract Today (+ Features 1-4)

| Edge Type | Source → Target | Confidence | Source of Data |
|-|-|-|-|
| `contains` | File → Struct/Function/Enum/Trait | EXTRACTED | FileAST membership |
| `method_of` | Method → ImplBlock/Class | EXTRACTED | ImplBlockNode.methods |
| `implements` | Type → Trait | EXTRACTED | ImplBlockNode.trait_type |
| `inherits` | Subclass → Base class | EXTRACTED/INFERRED | ImplBlockNode (Python heuristic) |
| `imports` | File → Module/Package | EXTRACTED | FileAST.uses |
| `calls` | Function → Function | EXTRACTED/INFERRED | Feature 2 CallEdge |
| `uses_method` | Function → Package Method | INFERRED | imported_package_methods |
| `depends_on` | Crate → Crate | EXTRACTED | inter_crate_deps |
| `super_trait` | Trait → Parent Trait | EXTRACTED | TraitNode.super_traits |
| `rationale_for` | RationaleComment → Symbol | EXTRACTED | Feature 4 RationaleNode |
| `has_field` | Struct → FieldType (if it's a known struct) | INFERRED | FieldNode.type resolution |

### Output Formats

| Format | File | Use Case |
|-|-|-|
| **JSON graph** | `graph.json` | Programmatic consumption, MCP servers, further analysis |
| **DOT** | `graph.dot` | Graphviz rendering → SVG/PNG |
| **Mermaid** | `graph.mermaid.md` | Embeddable in GitHub READMEs and docs |

### Phased Implementation

#### Phase 5A — Graph Model (new models/graph_model.py)

**Files**: new `ast_intel/models/graph_model.py`

1. Define `GraphNode`, `GraphEdge`, `CodeGraph` dataclasses
2. Define `RELATION_*` constants for edge types
3. Export in `models/__init__.py`

#### Phase 5B — Graph Builder (new core/graph_builder.py)

**Files**: new `ast_intel/core/graph_builder.py`

The graph builder is a **transformer** that converts `WorkspaceAST` → `CodeGraph`:

```python
class GraphBuilder:
    """Transform a WorkspaceAST into a CodeGraph.
    
    Walks all crates, files, and cross-references to emit
    nodes and edges with full provenance.
    """
    def build(self, workspace: WorkspaceAST) -> CodeGraph:
        nodes, edges = [], []
        
        for crate_name, crate in workspace.crates.items():
            for file_ast in crate.files:
                self._emit_file_nodes(file_ast, nodes, edges)
        
        self._emit_cross_ref_edges(workspace.cross_references, edges)
        return CodeGraph(nodes=nodes, edges=edges, meta=workspace.meta)
```

Node ID scheme: `"{file_path}::{symbol_name}"` — deterministic, human-readable, stable across runs.

#### Phase 5C — Graph Formatters (new formatters/)

**Files**: new `ast_intel/formatters/graph_json_formatter.py`, `graph_dot_formatter.py`, `graph_mermaid_formatter.py`

1. **JSON**: `{"nodes": [...], "edges": [...], "meta": {...}}` — compatible with vis.js/D3/Cytoscape
2. **DOT**: `digraph { ... }` with node shapes by kind, edge labels by relation
3. **Mermaid**: `graph TD` with subgraphs per crate/file

#### Phase 5D — CLI + Emitter Integration

**Files**: `cli.py`, `core/emitter.py`

1. Add `--format graph-json` / `--format dot` / `--format mermaid` to CLI
2. Emitter dispatches to graph builder + graph formatter
3. Default `--format both` unchanged; graph output is opt-in

#### Phase 5E — Tests

1. Build a `CodeGraph` from test fixtures, assert node/edge counts
2. Assert node ID stability (same input → same IDs)
3. Assert DOT output is valid Graphviz syntax
4. Assert Mermaid output renders correctly

**Total effort estimate**: ~4-5 days  
**Depends on**: Features 1 (spans on nodes), 2 (call edges), 3 (confidence tags), 4 (rationale nodes)

---

## Feature 6 — Graph Analysis (God Nodes, Communities, Surprises)

### Why This Matters (The Gap)

Graphify's `GRAPH_REPORT.md` contains **analytical insights**, not just data:

- **God nodes**: The 5-10 highest-degree concepts that everything connects through (e.g., `Response`, `Config`, `AuthManager`)
- **Surprising connections**: Ranked cross-file or cross-modal edges that humans wouldn't expect, each with a plain-English "why"
- **Community detection**: Leiden algorithm clusters the graph into topological communities — modules that are tightly connected internally
- **Suggested questions**: 4-5 questions the graph is uniquely positioned to answer
- **Semantic similarity edges**: Cross-file conceptual links (two functions solving the same problem without calling each other)
- **Hyperedges**: Group relationships connecting 3+ nodes (all classes implementing a protocol, all functions in an auth flow)

Our `summary.md` is a **catalog** ("here's what exists"). Their `GRAPH_REPORT.md` is an **analysis** ("here's what's interesting"). That's the difference between a phone book and an intelligence briefing.

### Design Decision: Analysis Module

This is the most complex feature. We decompose it into sub-components:

| Component | Algorithm | Dependency | LLM Required? |
|-|-|-|-|
| God nodes | Degree centrality on CodeGraph | Feature 5 | No |
| Community detection | Leiden algorithm (graspologic) | Feature 5 | No |
| Surprising connections | Edge betweenness + cross-community edges | Feature 5 + communities | No |
| Suggested questions | Template-based from god nodes + communities | God nodes + communities | No (templates) |
| Semantic similarity | Embedding cosine similarity | Feature 5 | Yes (embeddings) or No (Jaccard on shared dependencies) |
| Hyperedges | Group detection from shared trait implementations | Feature 5 + cross-refs | No |

**Critical trade-off: LLM dependency**

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Pure graph-algorithmic (no LLM) | Deterministic, free, fast, zero API cost | No embedding-based similarity, no generated "why" text | **Chosen for Phase 1** |
| B) LLM-assisted (Claude/GPT for similarity + explanations) | Richer insights, natural language "why" | API cost, non-deterministic, requires API key | Deferred to Phase 2 |

### Phased Implementation

#### Phase 6A — Core Analysis Algorithms (new core/analyzer.py)

**Files**: new `ast_intel/core/analyzer.py`

```python
@dataclass(frozen=True, slots=True)
class GodNode:
    """A high-centrality node in the code graph."""
    node_id: str
    label: str
    degree: int              # Total edges (in + out)
    in_degree: int
    out_degree: int
    community: int           # Which community it belongs to
    connected_communities: int  # How many communities it bridges

@dataclass(frozen=True, slots=True)
class SurprisingConnection:
    """An edge connecting nodes from different communities."""
    source_id: str
    target_id: str
    relation: str
    source_community: int
    target_community: int
    surprise_score: float    # Higher = more surprising (cross-community, low common neighbors)
    why: str                 # Template-generated explanation

@dataclass(frozen=True, slots=True)
class Community:
    """A cluster of tightly-connected nodes."""
    id: int
    label: str               # Auto-generated from god node(s) in this community
    node_count: int
    internal_edge_count: int
    key_nodes: tuple[str, ...]  # Top-5 nodes by internal degree

@dataclass(frozen=True, slots=True)
class Hyperedge:
    """A group relationship connecting 3+ nodes."""
    label: str               # e.g., "All implementors of Serializable"
    kind: str                # "shared_trait", "shared_import", "call_chain"
    members: tuple[str, ...]
    
@dataclass(slots=True)
class GraphAnalysis:
    """Full analysis results."""
    god_nodes: list[GodNode]
    communities: list[Community]
    surprising_connections: list[SurprisingConnection]
    suggested_questions: list[str]
    hyperedges: list[Hyperedge]
```

#### Phase 6B — God Node Detection

**Algorithm**: Degree centrality on the `CodeGraph`.

1. Count `in_degree` and `out_degree` for every node
2. Sort by total degree descending
3. Top N nodes (configurable, default 10) are god nodes
4. Also compute betweenness centrality for bridge-detection

**No external dependency needed** — pure Python dict counting on our edge list.

#### Phase 6C — Community Detection (Leiden Algorithm)

**New dependency**: `graspologic>=3.0` (Microsoft's graph statistics library, contains Leiden implementation)  
**Alternative**: `leidenalg` (C extension, faster) or `networkx` + `community` module

**Algorithm**:
1. Convert `CodeGraph` edges to an adjacency matrix (or NetworkX graph)
2. Run Leiden community detection (resolution parameter configurable)
3. Assign `community: int` label to each node
4. Compute modularity score

**Trade-off**:

| Option | Pros | Cons | Decision |
|-|-|-|-|
| graspologic | Pure Python, Microsoft-backed, no C deps | Slower on very large graphs | **Chosen** — simplicity, matches Graphify's approach |
| leidenalg (igraph) | Fastest Leiden implementation | C dependency, harder to install | Alternative if perf needed |
| NetworkX Louvain | Built into NetworkX, no extra dep | Louvain not Leiden (less stable) | Fallback option |

#### Phase 6D — Surprising Connection Detection

**Algorithm**: For each edge crossing community boundaries:

1. Compute `surprise_score = 1.0 - jaccard_similarity(neighbors(source), neighbors(target))`
2. Boost score for cross-community edges (multiply by `1.5`)
3. Boost score for cross-file edges (multiply by `1.2`)
4. Rank by `surprise_score` descending, take top-20
5. Generate template "why" text:
   - `"{source.label} ({source_kind} in {source_community_label}) connects to {target.label} ({target_kind} in {target_community_label}) via {relation} — these communities share only {N} other connections"`

#### Phase 6E — Suggested Question Generation (Template-Based)

Templates driven by graph structure:

1. **God nodes**: `"What role does {god_node.label} play in the architecture? It connects to {degree} other symbols across {connected_communities} modules."`
2. **Surprising connections**: `"Why does {source.label} depend on {target.label}? They appear in different architectural modules."`
3. **Orphan communities**: `"Community {id} ({label}) has no external dependencies — is it a self-contained module or accidentally isolated?"`
4. **High fan-out functions**: `"Function {fn.label} calls {out_degree} other functions — is it doing too much?"`

#### Phase 6F — Hyperedge Detection

**Algorithm**: Group nodes that share identical relationship patterns:

1. **Shared trait implementations**: All types implementing the same trait → hyperedge `"Implementors of {trait}"`
2. **Shared callers**: All functions called by the same set of callers → hyperedge `"Utility functions used by {callers}"`
3. **Shared imports**: All files importing the same module → hyperedge `"Consumers of {module}"`

#### Phase 6G — Analysis Report Formatter (new formatters/report_formatter.py)

**Files**: new `ast_intel/formatters/report_formatter.py`

Generates `GRAPH_REPORT.md`:

```markdown
# AST Intel — Graph Analysis Report

## God Nodes (Highest Connectivity)
| Rank | Symbol | Kind | Degree | Communities Bridged |
|------|--------|------|--------|---------------------|
| 1 | Response | struct | 47 | 5 |
| 2 | Config | struct | 38 | 4 |
...

## Communities (Leiden Clusters)
| ID | Label | Nodes | Key Symbols |
|----|-------|-------|-------------|
| 0 | HTTP Layer | 23 | Response, Request, Client |
| 1 | Auth Module | 15 | DigestAuth, Token, Session |
...

## Surprising Connections
| # | Connection | Score | Why |
|---|-----------|-------|-----|
| 1 | DigestAuth → CacheManager | 0.92 | These symbols are in different communities (Auth vs Cache) and share only 1 common neighbor |
...

## Suggested Questions
1. What role does `Response` play? It connects 47 symbols across 5 modules.
2. Why does `DigestAuth` depend on `CacheManager`?
...
```

#### Phase 6H — CLI + Emitter Integration

Add `--analyze` flag to CLI:

```
ast-intel /path/to/repo --analyze              # runs graph analysis after extraction
ast-intel /path/to/repo --format graph-json --analyze  # outputs graph + analysis
```

New output files:
- `GRAPH_REPORT.md` — human/LLM-readable analysis report
- `analysis.json` — machine-readable analysis results

#### Phase 6I — Tests

1. Build a known small graph, assert correct god nodes by degree
2. Assert Leiden produces expected communities on a graph with clear clusters
3. Assert surprising connections are cross-community
4. Assert suggested questions contain god node names

**Total effort estimate**: ~5-7 days  
**Depends on**: Feature 5 (CodeGraph model). Features 1-4 improve quality but aren't hard blockers.

---

## Dependency Graph Between Features

```
Feature 1 (Spans) ◄────────────────────────────────────────────┐
    │                                                           │
    ├── Feature 2 (Call Graph) ◄── needs Span for call sites    │
    │       │                                                   │
    │       ├── Feature 3 (Confidence) ◄── tags CallEdge        │
    │       │                                                   │
    │       └────────────────┐                                  │
    │                        │                                  │
    ├── Feature 4 (Rationale) ◄── needs Span for comment loc   │
    │                        │                                  │
    └────────────────────────┼──────────────────────────────────┘
                             │
                             ▼
                    Feature 5 (Graph Output) ◄── needs all edges + nodes
                             │
                             ▼
                    Feature 6 (Graph Analysis) ◄── needs CodeGraph
```

**Critical path**: 1 → 2 → 3 → 5 → 6  
**Parallel track**: 4 (rationale) can run alongside 2-3 after Feature 1 is done

---

## Suggested Implementation Order

| Sprint | Feature | Est. Days | Cumulative |
|--------|---------|-----------|------------|
| **Sprint 1** | Feature 1: Spans (1A-1E) | 2-3 | 2-3 |
| **Sprint 2** | Feature 4: Rationale (4A-4E) ‖ Feature 3: Confidence (3A-3B) | 3-4 | 5-7 |
| **Sprint 3** | Feature 2: Call Graph (2A-2F) + Feature 3 remaining (3C-3E) | 4-5 | 9-12 |
| **Sprint 4** | Feature 5: Graph Output (5A-5E) | 4-5 | 13-17 |
| **Sprint 5** | Feature 6: Graph Analysis (6A-6I) | 5-7 | 18-24 |

**Total estimated effort**: ~18-24 working days (4-5 weeks)

### New Dependencies to Add to pyproject.toml

| Package | Feature | Purpose | Required? |
|---------|---------|---------|-----------|
| `graspologic>=3.0` | Feature 6C | Leiden community detection | Optional (`[analysis]` extra) |
| `networkx>=3.0` | Feature 5-6 | Graph data structure (if needed) | Optional (`[analysis]` extra) |

### New Files Created

| File | Feature | Purpose |
|------|---------|---------|
| `ast_intel/models/graph_model.py` | 5A | GraphNode, GraphEdge, CodeGraph |
| `ast_intel/extractors/_call_graph.py` | 2B | Shared call-graph extraction |
| `ast_intel/extractors/_rationale.py` | 4B | Shared rationale comment extraction |
| `ast_intel/core/graph_builder.py` | 5B | WorkspaceAST → CodeGraph transformer |
| `ast_intel/core/analyzer.py` | 6A | Graph analysis algorithms |
| `ast_intel/formatters/graph_json_formatter.py` | 5C | Graph JSON serializer |
| `ast_intel/formatters/graph_dot_formatter.py` | 5C | Graphviz DOT serializer |
| `ast_intel/formatters/graph_mermaid_formatter.py` | 5C | Mermaid diagram serializer |
| `ast_intel/formatters/report_formatter.py` | 6G | GRAPH_REPORT.md generator |

### Modified Files

| File | Features | Changes |
|------|----------|---------|
| `ast_intel/models/ast_node.py` | 1, 2, 3, 4 | Span, CallEdge, Confidence, RationaleNode |
| `ast_intel/models/workspace_model.py` | 3 | PackageMethodCall |
| `ast_intel/extractors/base.py` | 1 | _span_from_node helper |
| `ast_intel/extractors/python.py` | 1, 2, 3, 4 | Spans, calls, confidence, rationale |
| `ast_intel/extractors/typescript.py` | 1, 2, 3, 4 | Same |
| `ast_intel/extractors/rust.py` | 1, 2, 3, 4 | Same |
| `ast_intel/extractors/csharp.py` | 1, 2, 3, 4 | Same |
| `ast_intel/core/indexer.py` | 2, 3 | Pass 6 (cross-file calls), confidence on resolutions |
| `ast_intel/core/emitter.py` | 5, 6 | Graph + analysis output dispatch |
| `ast_intel/cli.py` | 5, 6 | --format graph-json/dot/mermaid, --analyze flag |
| `ast_intel/formatters/json_formatter.py` | 1, 3 | Serialize Span, Confidence |
| `ast_intel/formatters/markdown_formatter.py` | 1, 4 | Line numbers in tables, rationale section |
| `pyproject.toml` | 6 | graspologic optional dep |

---

*This document is the single source of truth for the AST_INTEL enhancement roadmap. Update it as phases complete.*
