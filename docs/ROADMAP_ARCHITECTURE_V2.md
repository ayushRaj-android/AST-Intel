# AST_INTEL — Phase 2 Feature Roadmap & Architecture Plan

> Generated 2026-04-27 · Architect Document  
> Benchmark reference: [Graphify v5](https://github.com/safishamsi/graphify/tree/v5)  
> Prerequisite: All Phase 1 features (Spans, Call Graph, Confidence, Rationale, Graph Output, Graph Analysis) are **complete** and tested (1 090 tests passing).

---

## Table of Contents

1. [Feature 7 — Java Extractor](#feature-7--java-extractor)
2. [Feature 8 — Project-Level Ignore File (`.ast-intel-ignore`)](#feature-8--project-level-ignore-file-ast-intel-ignore)
3. [Feature 9 — Semantic Similarity Edges](#feature-9--semantic-similarity-edges)
4. [Feature 10 — Interactive HTML Visualization](#feature-10--interactive-html-visualization)
5. [Feature 11 — Graph Query CLI Commands](#feature-11--graph-query-cli-commands)
6. [Feature 12 — Neo4j / GraphML / Cypher Export](#feature-12--neo4j--graphml--cypher-export)
7. [Feature 13 — MCP Server (Tool Exposure)](#feature-13--mcp-server-tool-exposure)
8. [Feature 14 — Tier 2+ Language Extractors (Ruby, Kotlin, PHP, Swift)](#feature-14--tier-2-language-extractors-ruby-kotlin-php-swift)
9. [Dependency Graph Between Features](#dependency-graph-between-features)
10. [Suggested Implementation Order](#suggested-implementation-order)

---

## Feature 7 — Java Extractor

### What

A full tree-sitter-based Java extractor on par with the existing Python, TypeScript, Rust, C#, Go, and C/C++ extractors. It must support: classes, interfaces, enums, methods, fields, annotations, inheritance/implements, imports, constants, abstract classes, generics, and Maven/Gradle manifest parsing.

### Why (The Gap)

Java is the **only language** whose infrastructure is already fully wired — `pyproject.toml` declares `tree-sitter-java` deps, `SupportedLanguage.JAVA` exists in the CLI, `_ALL_SOURCE_EXTENSIONS` includes `.java`, `_LANGUAGE_EXTENSIONS` maps `"java"` — yet **no extractor code exists**. Running `ast-intel /some/java-repo --lang java` today silently produces zero nodes because the dispatcher has no Java registration. Graphify supports Java through its generic tree-sitter grammar pipeline. This is a critical gap for enterprise adoption where Java dominates.

### How It Works

Follow the established extractor pattern (see `CSharpExtractor` as closest analog — both are C-family OOP languages with annotations, generics, and `implements`/`extends`).

**Grammar**: `tree-sitter-java` provides node types like `class_declaration`, `interface_declaration`, `enum_declaration`, `method_declaration`, `field_declaration`, `import_declaration`, `annotation`, `constructor_declaration`.

**Manifest parsing**: Support `pom.xml` (Maven) and `build.gradle` / `build.gradle.kts` (Gradle) in `manifest_parser.py` for `groupId:artifactId:version` dependency extraction.

### Design Decision: Mirror C# Extractor Pattern

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Generic grammar auto-extractor (like Graphify) | One extractor handles all languages | Loses semantic depth — no Java-specific generics, annotations, visibility | Rejected — our differentiator is depth |
| B) Dedicated `JavaExtractor(ExtractorBase)` | Full semantic extraction, matches existing quality bar, Java-specific handling | More code (~800-1000 lines) | **Chosen** |
| C) Java-only via external tool (javaparser) | More accurate Java semantics | New dependency, breaks tree-sitter uniformity | Rejected |

### Phased Implementation

#### Phase 7A — Test Fixtures

**Files**: `tests/fixtures/java/`

1. Create fixture files covering all Java constructs:
   - `classes.java` — classes with fields, methods, constructors, generics, visibility
   - `interfaces.java` — interfaces with default methods, generic bounds
   - `enums.java` — enums with values, fields, methods
   - `inheritance.java` — extends, implements, abstract classes
   - `annotations.java` — annotation declarations, @Override, @Deprecated, custom
   - `imports.java` — single imports, wildcard, static imports
   - `constants.java` — static final fields, enum constants
   - `comprehensive.java` — large file exercising all constructs together
   - `sample_pom.xml` — Maven POM with dependencies

#### Phase 7B — Extractor Core

**Files**: new `ast_intel/extractors/java.py`

Implement `JavaExtractor(ExtractorBase)` with:

| Component | tree-sitter node types | AST_INTEL model |
|-|-|-|
| Classes | `class_declaration`, `record_declaration` | `StructNode` (with `is_abstract` in `raw`) |
| Interfaces | `interface_declaration` | `TraitNode` |
| Enums | `enum_declaration` | `EnumNode` + `EnumVariantNode` per constant |
| Methods | `method_declaration`, `constructor_declaration` | `MethodNode` (with visibility, static, abstract, return type) |
| Functions | Static methods extracted as `FunctionNode` when at top-level | `FunctionNode` |
| Fields | `field_declaration` | `FieldNode` |
| Imports | `import_declaration` | `ImportNode` |
| Constants | `field_declaration` with `static` + `final` modifiers | `ConstantNode` |
| Annotations | `annotation` on declarations | Stored in `properties` dict |
| Generics | `type_parameters`, `type_arguments` | Stored in `generics` field |
| Inheritance | `superclass`, `super_interfaces` | Populate `bases` list |
| Module | `package_declaration` | `ModuleNode` |

Key implementation details:
- `language_id = "java"`
- `file_extensions = frozenset({".java"})`
- Visibility: `public`, `protected`, `private`, package-private (default)
- Use `_span_from_node()` from `base.py` for all spans
- Walk call expressions in method bodies for `_call_graph.py` integration
- Extract rationale comments via `_rationale.py`

#### Phase 7C — Manifest Parser (Maven & Gradle)

**Files**: `ast_intel/core/manifest_parser.py`

1. Add `"pom.xml"` and `"build.gradle"` / `"build.gradle.kts"` to `MANIFEST_FILENAMES`
2. `parse_pom_xml(path)`: Parse `<dependency>` → `groupId:artifactId:version` using `defusedxml` (already a deps for C# `.csproj` parsing — reuse the safe XML parser)
3. `parse_gradle_build(path)`: Regex-based extraction of `implementation "group:artifact:version"` and `api(...)` declarations (no full Groovy/Kotlin DSL parsing — match the pragmatic approach used for other manifests)

#### Phase 7D — Dispatcher Registration

**Files**: `ast_intel/core/dispatcher.py`

Add Java registration block (matching existing pattern):

```python
# Java
try:
    from ast_intel.extractors.java import JavaExtractor
    for suffix in JavaExtractor.file_extensions:
        registry[suffix] = JavaExtractor
except ImportError:
    logger.debug("Java extractor not available (missing tree-sitter-java)")
```

#### Phase 7E — Tests

**Files**: new `tests/test_java_extractor.py`

1. Test each construct type against fixtures (classes, interfaces, enums, methods, fields, imports, constants, inheritance, annotations, generics)
2. Assert spans are populated on all nodes
3. Assert call edges are extracted from method bodies
4. Assert rationale comments are captured
5. Test Maven POM parsing with `sample_pom.xml` fixture
6. Test Gradle build file parsing
7. Integration test: full pipeline end-to-end with `--lang java`

**Total effort estimate**: ~3-4 days  
**Depends on**: Nothing (all infrastructure is wired)

---

## Feature 8 — Project-Level Ignore File (`.ast-intel-ignore`)

### What

Support a `.ast-intel-ignore` file in the repository root that follows the same glob syntax as `.gitignore`. Patterns in this file cause matching files and directories to be excluded from the AST extraction pipeline, **in addition to** `.gitignore` rules and the built-in `_DEFAULT_EXCLUDES` set.

### Why (The Gap)

Currently, excluding paths requires `--exclude` CLI flags on every invocation. There is no way to commit project-specific exclusion rules to the repository. This means:

- Generated code directories (e.g., `proto/gen/`, `graphql/__generated__/`) are parsed on every run
- Large vendored directories that aren't in `.gitignore` (e.g., `third_party/`) waste parse time
- Test fixture code or mock files inflate the graph with noise
- Teams cannot share exclusion rules without wrapper scripts

**Graphify comparison**: Graphify supports `.graphifyignore` with gitignore-compatible syntax. This is table-stakes for project adoption.

### How It Works

We already use `pathspec` to compile and match `.gitignore` patterns in `workspace.py` (`_load_gitignore`). The change is minimal: load a second `PathSpec` from `.ast-intel-ignore` and combine both specs during the walk.

### Design Decision: Additive PathSpec + gitignore Syntax

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) YAML/TOML config file with `ignore:` section | Richer config, could hold other settings | Overengineered for exclusions, unfamiliar syntax | Rejected |
| B) `.ast-intel-ignore` with gitignore glob syntax | Zero learning curve, pathspec already handles it, aligns with Graphify | Yet another dotfile | **Chosen** |
| C) `[tool.ast-intel]` section in `pyproject.toml` | No new file, Python-native | Only works for Python projects, can't be used in Rust/Go repos | Rejected |

### Phased Implementation

#### Phase 8A — Ignore File Loader

**Files**: `ast_intel/core/workspace.py`

1. Add `_AST_INTEL_IGNORE_FILENAME = ".ast-intel-ignore"` constant
2. Add `_load_ast_intel_ignore(repo_root: Path) -> pathspec.PathSpec | None` function (mirror `_load_gitignore`)
3. In `WorkspaceDiscovery.__init__`, load the ignore spec alongside gitignore
4. In the walk loop, match against **both** specs — a file is excluded if **either** spec matches

Implementation detail: combine via a helper:
```python
def _is_ignored(rel_path: str, gitignore: PathSpec | None, project_ignore: PathSpec | None) -> bool:
    if gitignore and gitignore.match_file(rel_path):
        return True
    if project_ignore and project_ignore.match_file(rel_path):
        return True
    return False
```

#### Phase 8B — CLI Documentation

**Files**: `ast_intel/cli.py`, `README.md`

1. Add a note in the `--exclude` help text: "See also: .ast-intel-ignore file"
2. Document the ignore file in README under a new "Configuration" section

#### Phase 8C — Tests

**Files**: `tests/test_workspace.py`

1. Create a temp repo with a `.ast-intel-ignore` containing `generated/**` and `vendor/*.py`
2. Place matching files in the temp repo
3. Assert `WorkspaceDiscovery` skips ignored files
4. Assert `.gitignore` and `.ast-intel-ignore` rules combine (not override)
5. Assert the file is optional — missing `.ast-intel-ignore` doesn't error
6. Assert patterns support negation (`!important.py`) just like gitignore

**Total effort estimate**: ~1 day  
**Depends on**: Nothing

---

## Feature 9 — Semantic Similarity Edges

### What

Add `SIMILAR_TO` edges between symbols that share structural or naming similarity, **without any LLM or embedding model**. These edges surface "conceptually related" symbols that have no explicit import/call/inheritance relationship — the hidden coupling in a codebase.

### Why (The Gap)

Our current graph captures only **explicit** relationships: imports, calls, inheritance, containment. Two classes that do nearly the same thing but have no import path between them are invisible to each other. This is valuable for:

| Use Case | How Similarity Edges Help |
|-|-|
| Duplicate detection | "UserValidator and AccountValidator are 82% similar — consolidate?" |
| Refactoring suggestions | Find all classes structurally similar to a target |
| Impact analysis | "Changing this pattern? Here are 5 similar implementations" |
| God-node alternatives | Find less-connected nodes with similar structure |

**Graphify comparison**: Graphify uses OpenAI embeddings + cosine similarity. We take a **zero-dependency heuristic approach** using Jaccard similarity on structural features — no API keys, no latency, deterministic results.

### How It Works

For each pair of nodes (same `NodeKind`, e.g., struct-struct or function-function):

1. **Build a feature set** for each node:
   - Method names (for structs/classes): `{"get_user", "validate", "save"}`
   - Parameter types (for functions): `{"str", "int", "User"}`
   - Field types (for structs): `{"String", "Vec<u8>", "Option<i32>"}`
   - Import dependencies (for files): `{"os", "pathlib", "json"}`
   - Called functions (from call edges): `{"parse", "validate", "emit"}`

2. **Compute Jaccard similarity**: $J(A, B) = \frac{|A \cap B|}{|A \cup B|}$

3. **Threshold**: Only emit a `SIMILAR_TO` edge when $J \geq 0.4$ (configurable)

4. **Optimization**: Only compare nodes of the same `NodeKind` (no struct↔function). For $N$ nodes of a kind, that's $O(N^2/2)$ comparisons. For codebases with < 10 000 symbols per kind, this completes in < 1 second.

### Design Decision: Jaccard Heuristic vs Embeddings

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) LLM embeddings (OpenAI/local) | High quality, semantic understanding | API cost, latency, non-deterministic, requires key | Rejected for v1 |
| B) Jaccard similarity on structural features | Zero deps, deterministic, fast, explainable | Miss semantic nuance (synonyms, renamed patterns) | **Chosen** |
| C) TF-IDF on source code tokens | Captures naming patterns | Requires tokenizer, fragile to style differences | Rejected — too noisy |
| D) MinHash for approximate Jaccard | Scales to 100K+ nodes | Adds complexity, only needed at massive scale | Deferred to later if needed |

### Phased Implementation

#### Phase 9A — Model Extension

**Files**: `ast_intel/models/graph_model.py`

1. Add `SIMILAR_TO = "similar_to"` to `EdgeRelation` enum with docstring: "Structurally similar symbols (Jaccard heuristic)"
2. Add `RELATION_SIMILAR_TO` module-level alias

#### Phase 9B — Feature Extraction

**Files**: new `ast_intel/core/_similarity.py`

Create a module that extracts feature sets from `CodeGraph` nodes:

```python
def build_feature_sets(graph: CodeGraph) -> dict[str, frozenset[str]]:
    """Build structural feature sets for each node ID.
    
    Returns:
        Mapping of node_id → frozenset of feature strings.
    """
```

Feature composition per `NodeKind`:
- **STRUCT / ENUM**: method names (from METHOD_OF edges) + field types (from HAS_FIELD edges) + base classes (from INHERITS edges)
- **FUNCTION / METHOD**: parameter types (from properties) + return type + called functions (from CALLS edges)
- **FILE**: contained symbol names (from CONTAINS edges) + import targets (from IMPORTS edges)
- **TRAIT**: method signatures + implementing types (from IMPLEMENTS edges, reversed)

#### Phase 9C — Jaccard Computation + Edge Emission

**Files**: `ast_intel/core/_similarity.py`

```python
def compute_similarity_edges(
    graph: CodeGraph,
    threshold: float = 0.4,
    max_edges_per_node: int = 5,
) -> list[GraphEdge]:
    """Compute SIMILAR_TO edges between structurally similar nodes."""
```

Key details:
- Group nodes by `NodeKind` — only compare within same kind
- Skip nodes with fewer than 2 features (not enough signal)
- Cap at `max_edges_per_node` to prevent massive fan-out
- Set `confidence = Confidence.INFERRED`, `confidence_score = jaccard_value`
- Edges are bidirectional by convention but stored as single directed edge (lower ID → higher ID)

#### Phase 9D — Integration into GraphBuilder

**Files**: `ast_intel/core/graph_builder.py`

1. After building all explicit edges, call `compute_similarity_edges(graph)`
2. Append returned edges to `graph.edges`
3. Gate behind a flag: `GraphBuilder.__init__(*, similarity: bool = False)`
4. Wire to CLI: `--similarity` flag (or include in `--analyze`)

#### Phase 9E — Analyzer Integration

**Files**: `ast_intel/core/analyzer.py`

1. Include `SIMILAR_TO` edges in community detection (they strengthen intra-community bonds)
2. Flag cross-community `SIMILAR_TO` edges as surprising connections
3. Add a "Similarity Clusters" section to the analysis output

#### Phase 9F — Tests

**Files**: new `tests/test_similarity.py`

1. Build a synthetic `CodeGraph` with two structs sharing 3/4 method names → assert `SIMILAR_TO` edge emitted
2. Two structs sharing 0/5 method names → assert no edge
3. Assert threshold filtering works (0.3 vs 0.5 thresholds)
4. Assert `max_edges_per_node` cap is respected
5. Assert edges only connect same `NodeKind`

**Total effort estimate**: ~3-4 days  
**Depends on**: Feature 5 (CodeGraph) — already complete

---

## Feature 10 — Interactive HTML Visualization

### What

A `--format html` output option that generates a self-contained `graph.html` file with an interactive force-directed graph visualization. Nodes are color-coded by `NodeKind`, edges show relationship types on hover, and users can search/filter/zoom.

### Why (The Gap)

All our current output formats are static: JSON for machines, Markdown for reading, DOT for Graphviz rendering (requires external tool), Mermaid for GitHub README embedding. None provide **interactive exploration** — the ability to click a node and see its neighbors, search for a symbol, or collapse file-level nodes to see only the module structure.

**Graphify comparison**: Graphify generates an interactive `graph_visualization.html` using vis.js with ~2000 lines of embedded JavaScript. It's their most visually impressive feature and a key selling point for non-CLI users.

### How It Works

Generate a single HTML file with:
- **vis.js** (CDN or inlined) for force-directed graph layout
- Embedded JSON data from `CodeGraph`
- Controls: search box, node-kind filter checkboxes, zoom, physics toggle
- Color scheme: one color per `NodeKind` (16 kinds → 16 colors)
- Edge styling: different dash patterns per `EdgeRelation`
- Sidebar: click a node to see its properties, span, and connected edges

### Design Decision: vis.js Single-File HTML

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) vis.js embedded in single HTML | Zero external deps, works offline, one file to share | Large file (~300KB with vis.js inlined), no server needed | **Chosen** |
| B) D3.js custom force layout | More control over rendering | Much more JS code to write and maintain | Rejected — vis.js handles this out of the box |
| C) Cytoscape.js | Powerful graph library, supports compound nodes | Heavier, more complex API | Rejected — overkill for visualization |
| D) React app with separate build step | Rich UI, component-based | Requires Node.js build, not a single file | Rejected — deployment complexity |

### Phased Implementation

#### Phase 10A — HTML Template

**Files**: new `ast_intel/formatters/_html_template.py`

1. Create a Python module containing the HTML/CSS/JS template as a multi-line string
2. Template uses `{graph_json}` placeholder for the serialized CodeGraph data
3. Include vis.js from CDN with integrity hash (`<script src="https://unpkg.com/vis-network@9/dist/vis-network.min.js" integrity="sha384-..." crossorigin>`)
4. Alternatively, inline a minified copy for fully offline usage (configurable)

Template structure:
```html
<!DOCTYPE html>
<html>
<head>
  <title>AST Intel — Code Graph</title>
  <style>/* ~200 lines: layout, sidebar, search box, node colors */</style>
</head>
<body>
  <div id="controls">
    <input id="search" placeholder="Search symbols...">
    <div id="filters"><!-- checkbox per NodeKind --></div>
  </div>
  <div id="graph-container"></div>
  <div id="sidebar"><!-- node details on click --></div>
  <script src="vis-network CDN or inline"></script>
  <script>
    const graphData = {graph_json};
    // ~300 lines: build vis.js DataSet, configure physics, 
    // wire search, wire filters, wire click handler
  </script>
</body>
</html>
```

#### Phase 10B — HTML Formatter

**Files**: new `ast_intel/formatters/graph_html_formatter.py`

```python
class GraphHtmlFormatter:
    """Render a CodeGraph as an interactive HTML visualization."""
    
    @staticmethod
    def format(graph: CodeGraph) -> str:
        """Return a complete HTML document string."""
```

Implementation:
1. Serialize `CodeGraph` nodes/edges into vis.js-compatible JSON format:
   - Nodes: `{id, label, group: kind, title: hover_text, ...}`
   - Edges: `{from, to, label: relation, arrows: "to", dashes: ...}`
2. Inject into template via `template.replace("{graph_json}", json_data)`
3. Apply node-kind → color mapping
4. Apply edge-relation → style mapping (solid for CONTAINS, dashed for CALLS, dotted for SIMILAR_TO, etc.)

#### Phase 10C — Emitter + CLI Integration

**Files**: `ast_intel/core/emitter.py`, `ast_intel/cli.py`

1. Add `"html"` to `_VALID_FORMATS` and `_GRAPH_FORMATS` in emitter
2. Add `HTML = "html"` to `OutputFormat` enum in `cli.py`
3. Wire `GraphHtmlFormatter` in `Emitter.emit()` → writes `graph.html`
4. Output filename: `graph.html`

#### Phase 10D — Large Graph Handling

**Files**: `ast_intel/formatters/graph_html_formatter.py`

For graphs with > 500 nodes, add:
1. **Clustering**: Collapse file-level nodes by default, expand on click
2. **Level-of-detail**: Hide method/field nodes until their parent is expanded
3. **Performance**: Use vis.js `barnesHut` physics solver (default) for O(N log N) layout
4. **Warning**: If nodes > 5000, include a banner: "Large graph — consider filtering with --include"

#### Phase 10E — Tests

**Files**: new `tests/test_html_formatter.py`

1. Generate HTML from a small `CodeGraph` → assert valid HTML structure
2. Assert `graph_json` is embedded and parseable
3. Assert all `NodeKind` values appear in the color map
4. Assert all `EdgeRelation` values appear in the edge style map
5. Assert search input element exists
6. Assert no external resource URLs that could break offline usage (if inline mode)

**Total effort estimate**: ~3-4 days  
**Depends on**: Feature 5 (CodeGraph) — already complete

---

## Feature 11 — Graph Query CLI Commands

### What

Three new CLI subcommands that let users interactively query the code graph without writing code:

```bash
ast-intel query "UserService" /path/to/repo       # Find nodes matching a pattern
ast-intel path "AuthController" "Database" /repo   # Shortest path between two symbols
ast-intel explain "UserService" /repo              # Why is this node important?
```

### Why (The Gap)

Our current CLI is **batch-only**: run extraction, get files. There's no way to ask ad-hoc questions about the graph without loading `ast.json` into a script. For agent workflows, being able to query the graph interactively (or from a script) is critical.

**Graphify comparison**: Graphify offers `query`, `path`, and `explain` commands. Their `explain` command generates a natural-language summary of a node's role (using the graph structure, not an LLM). This is highly useful for onboarding and code review.

### How It Works

All three commands:
1. Load the existing `ast.json` (or run extraction if not cached)
2. Build a `CodeGraph` from the `WorkspaceAST`
3. Execute the query against the graph
4. Pretty-print results to the terminal using `rich`

### Design Decision: Subcommands vs Flags

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Subcommands (`ast-intel query ...`) | Clean UX, each command has own args, composable | Breaking change to current single-command CLI | **Chosen** — add subcommands alongside existing `main` |
| B) Flags on main command (`--query "..."`) | No CLI restructure | Cluttered, hard to document, mixing concerns | Rejected |
| C) Separate binary (`ast-intel-query`) | Clean separation | Fragmented installation, harder to discover | Rejected |

CLI restructure approach: Keep the existing `ast-intel /path/to/repo` command as the default (extraction). Add `ast-intel query`, `ast-intel path`, and `ast-intel explain` as additional Typer subcommands. Use `typer.Typer()` with `invoke_without_command=True` so the existing behavior is preserved.

### Phased Implementation

#### Phase 11A — Graph Loader Utility

**Files**: new `ast_intel/core/_graph_loader.py`

Create a helper that constructs a queryable `CodeGraph`:

```python
def load_or_build_graph(repo_path: Path, output_dir: Path) -> CodeGraph:
    """Load graph from cached ast.json or build fresh."""
```

1. Check if `ast.json` exists and is newer than all source files → load + build graph
2. Otherwise, run the full pipeline (discover → extract → index → build graph)
3. Return `CodeGraph` ready for querying

#### Phase 11B — Query Engine

**Files**: new `ast_intel/core/_query_engine.py`

```python
class QueryEngine:
    """Execute structured queries against a CodeGraph."""
    
    def __init__(self, graph: CodeGraph) -> None:
        self._graph = graph
        self._nx = self._to_networkx()  # networkx DiGraph for path queries
        self._index = self._build_search_index()  # label → node_id
    
    def search(self, pattern: str, kind: NodeKind | None = None) -> list[GraphNode]:
        """Find nodes matching a pattern (substring or regex)."""
    
    def shortest_path(self, source: str, target: str) -> list[GraphNode] | None:
        """Find shortest path between two symbols (by label or ID)."""
    
    def explain(self, symbol: str) -> NodeExplanation:
        """Generate a structural explanation of a symbol's role."""
```

`NodeExplanation` dataclass:
```python
@dataclass
class NodeExplanation:
    node: GraphNode
    degree: int                    # Total edges
    in_degree: int                 # Incoming edges
    out_degree: int                # Outgoing edges
    callers: list[str]             # Who calls this
    callees: list[str]             # What this calls
    community: str | None          # Community label if analysis was run
    role: str                      # "hub" | "leaf" | "bridge" | "isolated"
    summary: str                   # Human-readable summary paragraph
```

**Role classification**:
- **Hub**: in_degree + out_degree > 2× median → "central coordination point"
- **Bridge**: member of multiple communities (or connects two) → "bridges module X and module Y"
- **Leaf**: out_degree=0 or in_degree=0 → "endpoint with no dependencies" / "no dependents"
- **Isolated**: degree=0 → "disconnected from the graph"

**Summary generation** (no LLM):
```
"UserService is a struct in src/services/user.rs (L42-L180) with 12 methods.
It is called by 8 symbols across 3 files, making it a hub in the Auth community.
Its primary callers are AuthController and SessionManager."
```

#### Phase 11C — CLI Subcommands

**Files**: `ast_intel/cli.py`

1. Restructure `app` to support subcommands:
   ```python
   # Keep existing main command as default
   @app.command(name="scan")  # or keep as default with invoke_without_command
   def main(...): ...
   
   @app.command()
   def query(pattern: str, repo_path: Path, kind: NodeKind | None = None): ...
   
   @app.command()
   def path(source: str, target: str, repo_path: Path): ...
   
   @app.command()
   def explain(symbol: str, repo_path: Path): ...
   ```

2. **query** output: Rich table with columns `[Node ID | Kind | File | Span]`
3. **path** output: Arrow chain `A → (calls) → B → (imports) → C → (contains) → D`
4. **explain** output: Rich panel with the `NodeExplanation` summary

#### Phase 11D — Tests

**Files**: new `tests/test_query_engine.py`, update `tests/test_cli.py`

1. Build a synthetic `CodeGraph` → test `search()` with exact match, substring, regex
2. Test `shortest_path()` on a known graph → assert correct path
3. Test `shortest_path()` with no path → assert `None`
4. Test `explain()` → assert correct role classification
5. Test `explain()` → assert summary contains node name and file
6. CLI integration: invoke `query`, `path`, `explain` via `typer.testing.CliRunner`

**Total effort estimate**: ~4-5 days  
**Depends on**: Feature 5 (CodeGraph), Feature 6 (GraphAnalyzer for community info in `explain`)

---

## Feature 12 — Neo4j / GraphML / Cypher Export

### What

Three new export formats for the code graph:
- **GraphML** (`.graphml`): XML-based graph format supported by yEd, Gephi, NetworkX, and most graph databases
- **Cypher** (`.cypher`): Neo4j query language script for importing the graph into Neo4j
- **Neo4j CSV** (`.neo4j/`): Node and relationship CSVs compatible with `neo4j-admin import`

### Why (The Gap)

Our current graph outputs (JSON, DOT, Mermaid) are great for visualization but incompatible with **graph databases** and **professional graph analysis tools**. Teams using Neo4j for architecture governance, or Gephi for large-scale graph analysis, cannot consume our output without custom ETL scripts.

**Graphify comparison**: Graphify supports Neo4j export and GraphML. This is a key differentiator for enterprise teams that need to integrate code intelligence into their existing graph infrastructure.

### How It Works

All three formats are straightforward serializations of the existing `CodeGraph` model:

| Format | Node Representation | Edge Representation | Tools |
|-|-|-|-|
| **GraphML** | `<node id="..."><data key="kind">struct</data>...</node>` | `<edge source="..." target="..."><data key="relation">calls</data>...</edge>` | yEd, Gephi, NetworkX |
| **Cypher** | `CREATE (n:Struct {id: "...", label: "...", file: "..."})` | `CREATE (a)-[:CALLS {confidence: 0.9}]->(b)` | Neo4j Browser, Aura |
| **Neo4j CSV** | `nodes.csv`: id, label, kind, file, span | `relationships.csv`: start_id, end_id, type, confidence | `neo4j-admin import` |

### Design Decision: Three Separate Formatters

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Single "graph-export" command with sub-format flag | Fewer CLI options | Still need 3 serializers internally | Rejected — match existing pattern of one formatter per format |
| B) Three formatters + three CLI format values | Consistent with existing pattern (graph-json, dot, mermaid) | More format options in --format | **Chosen** |
| C) Export only GraphML (most universal) | Simplest | Misses Neo4j users who need Cypher directly | Rejected — both audiences matter |

### Phased Implementation

#### Phase 12A — GraphML Formatter

**Files**: new `ast_intel/formatters/graph_graphml_formatter.py`

1. Use `xml.etree.ElementTree` for XML generation (stdlib, no new deps)
2. Define `<key>` elements for all node properties (`kind`, `file`, `span_start`, `span_end`, `visibility`, etc.)
3. Define `<key>` elements for edge properties (`relation`, `confidence`, `confidence_score`)
4. Emit one `<node>` per `GraphNode`, one `<edge>` per `GraphEdge`
5. Use proper XML escaping for all string values (prevent injection)
6. Output filename: `graph.graphml`

#### Phase 12B — Cypher Formatter

**Files**: new `ast_intel/formatters/graph_cypher_formatter.py`

1. Emit `CREATE` statements for all nodes, using `NodeKind` as the Neo4j label:
   ```cypher
   CREATE (n_0:Struct {id: "src/user.rs::User", label: "User", file: "src/user.rs", start_line: 10, end_line: 42})
   ```
2. Emit relationship `CREATE` statements using `EdgeRelation` as relationship type:
   ```cypher
   CREATE (n_0)-[:CALLS {confidence: "EXTRACTED", score: 0.95, file: "src/main.rs"}]->(n_5)
   ```
3. Wrap in a `BEGIN` / `COMMIT` transaction block for safe import
4. Sanitize all string values to prevent Cypher injection (escape backslashes, single quotes)
5. Output filename: `graph.cypher`

#### Phase 12C — Neo4j CSV Formatter

**Files**: new `ast_intel/formatters/graph_neo4j_csv_formatter.py`

1. Generate `nodes.csv` with headers: `id:ID,label,kind:LABEL,file,start_line:int,end_line:int`
2. Generate `relationships.csv` with headers: `:START_ID,:END_ID,:TYPE,confidence,confidence_score:float,file`
3. Use `csv.writer` with proper quoting to handle commas/newlines in labels
4. Output directory: `neo4j_export/` containing both CSVs
5. Include a `README.md` in the export dir with the `neo4j-admin import` command

#### Phase 12D — Emitter + CLI Integration

**Files**: `ast_intel/core/emitter.py`, `ast_intel/cli.py`

1. Add `"graphml"`, `"cypher"`, `"neo4j-csv"` to `_VALID_FORMATS` and `_GRAPH_FORMATS`
2. Add corresponding `OutputFormat` enum values
3. Wire formatters in `Emitter.emit()`

#### Phase 12E — Tests

**Files**: new `tests/test_graph_export_formatters.py`

1. **GraphML**: Parse output with `xml.etree.ElementTree` → assert valid XML, correct node/edge counts, key definitions present
2. **Cypher**: Assert valid Cypher syntax (regex check for `CREATE` statements), assert node count matches, assert relationship types are valid `EdgeRelation` values
3. **Neo4j CSV**: Parse with `csv.reader` → assert header format, row counts match graph, no unescaped commas
4. String injection test: node with label `'; DROP TABLE nodes; --` → assert properly escaped in Cypher output
5. Round-trip test: GraphML → NetworkX → assert same node/edge count

**Total effort estimate**: ~3-4 days  
**Depends on**: Feature 5 (CodeGraph) — already complete

---

## Feature 13 — MCP Server (Tool Exposure)

### What

An MCP (Model Context Protocol) server that exposes AST_INTEL's code graph as tools callable by AI coding agents (GitHub Copilot, Cursor, Claude, etc.). The server runs locally and serves the code graph over the MCP standard transport (stdio or HTTP+SSE).

### Why (The Gap)

AI coding agents are the primary consumers of AST_INTEL output. Currently, agents must:
1. Run the CLI + read AST output files (batch mode only)
2. Parse JSON themselves
3. Re-run extraction when the codebase changes

An MCP server lets agents **call tools directly** — `search_symbols`, `get_dependencies`, `find_path`, `explain_symbol` — getting structured responses instantly withing their tool-use workflows.

**Graphify comparison**: Graphify includes a full MCP server with tools for code graph queries. This is their primary AI-agent integration story and a significant usability advantage.

### How It Works

The MCP server wraps the existing `QueryEngine` (Feature 11) and `GraphBuilder` in an MCP-compliant server. Tools correspond to query operations.

**Transport**: stdio (for local editor integration — VS Code, Cursor) or HTTP+SSE (for remote agents).

**Proposed tools**:

| Tool Name | Parameters | Returns | Maps To |
|-|-|-|-|
| `search_symbols` | `pattern: str`, `kind?: str` | `list[{id, label, kind, file, span}]` | `QueryEngine.search()` |
| `get_node` | `id: str` | `{node, edges, neighbors}` | Direct graph lookup |
| `find_path` | `source: str`, `target: str` | `list[{node, edge}]` | `QueryEngine.shortest_path()` |
| `explain_symbol` | `symbol: str` | `{explanation, summary}` | `QueryEngine.explain()` |
| `list_files` | `pattern?: str` | `list[{file, symbol_count}]` | Graph file-node scan |
| `get_analysis` | — | `{god_nodes, communities, hyperedges}` | `GraphAnalyzer.analyze()` |

### Design Decision: MCP SDK vs Custom Implementation

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) `mcp` Python SDK (`pip install mcp`) | Standard-compliant, handles protocol details, maintained | New dependency | **Chosen** — correctness and maintenance trump dep count |
| B) Custom stdio JSON-RPC server | No new deps | Must implement MCP protocol manually, error-prone, maintenance burden | Rejected |
| C) HTTP REST API (non-MCP) | Simpler protocol | Not compatible with MCP-aware agents (Copilot, Cursor) | Rejected — defeats the purpose |

### Phased Implementation

#### Phase 13A — MCP Dependency + Server Skeleton

**Files**: `pyproject.toml`, new `ast_intel/mcp_server.py`

1. Add `mcp = ["mcp>=1.0,<2.0"]` to `[project.optional-dependencies]`
2. Create `mcp_server.py` with the server skeleton:
   ```python
   from mcp.server import Server
   from mcp.server.stdio import stdio_server
   
   server = Server("ast-intel")
   
   @server.list_tools()
   async def list_tools() -> list[Tool]: ...
   
   @server.call_tool()
   async def call_tool(name: str, arguments: dict) -> list[TextContent]: ...
   ```

#### Phase 13B — Tool Implementations

**Files**: `ast_intel/mcp_server.py`

Wire each tool to the existing `QueryEngine`:
1. `search_symbols` → `engine.search(pattern, kind)`
2. `get_node` → lookup + neighbor collection
3. `find_path` → `engine.shortest_path(source, target)`
4. `explain_symbol` → `engine.explain(symbol)`
5. `list_files` → scan graph for FILE nodes
6. `get_analysis` → run or load cached `GraphAnalyzer` results

Each tool returns structured JSON as `TextContent` (MCP standard).

**Input validation**: Validate all tool parameters before graph operations. Pattern strings are treated as literal substrings by default; regex mode is opt-in via a `regex: bool` parameter.

#### Phase 13C — CLI Integration

**Files**: `ast_intel/cli.py`

Add a `serve` subcommand:

```bash
ast-intel serve /path/to/repo                 # stdio transport (default)
ast-intel serve /path/to/repo --transport sse  # HTTP+SSE transport
ast-intel serve /path/to/repo --port 8080      # custom port for SSE
```

Implementation:
```python
@app.command()
def serve(
    repo_path: Path,
    transport: str = "stdio",
    port: int = 3000,
) -> None:
    """Start an MCP server exposing the code graph as tools."""
```

#### Phase 13D — MCP Configuration Helper

**Files**: `README.md`

Document how to add AST_INTEL to agent configurations:

```json
// VS Code settings.json / .vscode/mcp.json
{
  "servers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/path/to/repo"]
    }
  }
}
```

#### Phase 13E — Tests

**Files**: new `tests/test_mcp_server.py`

1. Unit test each tool handler with a mock `CodeGraph`
2. Test `search_symbols` with pattern matching
3. Test `find_path` with known shortest path
4. Test `explain_symbol` summary generation
5. Test invalid tool parameters → proper MCP error response
6. Integration test: start server, send MCP `tools/list` → assert all 6 tools listed

**Total effort estimate**: ~4-5 days  
**Depends on**: Feature 11 (QueryEngine for tool implementations)

---

## Feature 14 — Tier 2+ Language Extractors (Ruby, Kotlin, PHP, Swift)

### What

Four additional language extractors that expand AST_INTEL toward Graphify's 25-language coverage. These are lower-priority than Java (Feature 7) but important for multi-language enterprise codebases.

### Why (The Gap)

Each additional language directly expands the addressable market. Priority is based on tree-sitter grammar maturity + language popularity:

| Language | tree-sitter Grammar | Popularity (TIOBE) | Enterprise Relevance | Priority |
|-|-|-|-|-|
| **Kotlin** | `tree-sitter-kotlin` (stable) | #15 | Android, Spring Boot | High |
| **Ruby** | `tree-sitter-ruby` (mature) | #17 | Rails, DevOps | Medium |
| **PHP** | `tree-sitter-php` (stable) | #8 | WordPress, Laravel | Medium |
| **Swift** | `tree-sitter-swift` (beta) | #12 | iOS/macOS | Lower |

**Graphify comparison**: Graphify handles all 25 via a generic grammar extractor. We extract at higher semantic depth, but we must expand language count to remain competitive. Each new extractor follows the established `ExtractorBase` pattern and takes approximately 1-2 days once the pattern is familiar.

### How It Works

Each extractor follows the identical architecture:
1. Subclass `ExtractorBase`
2. Configure `language_id`, `file_extensions`, tree-sitter `Language`
3. Implement `_extract_*` methods for each construct type
4. Register in `dispatcher.py`
5. Add fixtures + tests
6. Add manifest parser support (if applicable: `Gemfile`, `composer.json`, `Package.swift`, `build.gradle.kts`)

### Design Decision: Dedicated Extractors (Same as All Others)

We do not adopt a generic/shallow extractor strategy. Each language gets a dedicated, deep extractor. The rationale is unchanged from Phase 1: our competitive advantage is extraction depth (spans, call edges, confidence, rationale), and a generic extractor cannot provide this.

### Phased Implementation

#### Phase 14A — Kotlin Extractor

**Files**: new `ast_intel/extractors/kotlin.py`, `tests/test_kotlin_extractor.py`, `tests/fixtures/kotlin/`

Kotlin constructs:
- `class_declaration` → `StructNode` (with `data class`, `open`, `sealed` in properties)
- `object_declaration` → `StructNode` (singleton)
- `interface_declaration` → `TraitNode`
- `function_declaration` → `FunctionNode` / `MethodNode`
- `property_declaration` → `FieldNode` / `ConstantNode` (if `val` + top-level or `companion object`)
- `enum_class` → `EnumNode`
- `annotation` → properties dict
- `import` → `ImportNode`
- Manifest: `build.gradle.kts` `implementation("...")` → already partially handled by Java Phase 7C

**pyproject.toml**: Add `kotlin = ["tree-sitter>=0.23.0,<1.0", "tree-sitter-kotlin>=0.23.0,<1.0"]`

#### Phase 14B — Ruby Extractor

**Files**: new `ast_intel/extractors/ruby.py`, `tests/test_ruby_extractor.py`, `tests/fixtures/ruby/`

Ruby constructs:
- `class` → `StructNode` (with `< SuperClass` in `bases`)
- `module` → `ModuleNode`
- `method` / `singleton_method` → `MethodNode` / `FunctionNode`
- `constant_assignment` → `ConstantNode`
- `require` / `require_relative` → `ImportNode`
- Mixins: `include ModuleName` → `IMPLEMENTS` edge
- Manifest: `Gemfile` → regex-based `gem "name", "~> version"` extraction

**pyproject.toml**: Add `ruby = ["tree-sitter>=0.23.0,<1.0", "tree-sitter-ruby>=0.23.0,<1.0"]`

#### Phase 14C — PHP Extractor

**Files**: new `ast_intel/extractors/php.py`, `tests/test_php_extractor.py`, `tests/fixtures/php/`

PHP constructs:
- `class_declaration` → `StructNode`
- `interface_declaration` → `TraitNode`
- `trait_declaration` → `TraitNode` (PHP traits ≈ mixins)
- `function_definition` → `FunctionNode`
- `method_declaration` → `MethodNode`
- `enum_declaration` (PHP 8.1+) → `EnumNode`
- `namespace_definition` → `ModuleNode`
- `use_declaration` → `ImportNode`
- `const_declaration` → `ConstantNode`
- Manifest: `composer.json` `require` section → JSON parsing

**pyproject.toml**: Add `php = ["tree-sitter>=0.23.0,<1.0", "tree-sitter-php>=0.23.0,<1.0"]`

#### Phase 14D — Swift Extractor

**Files**: new `ast_intel/extractors/swift.py`, `tests/test_swift_extractor.py`, `tests/fixtures/swift/`

Swift constructs:
- `class_declaration` → `StructNode`
- `struct_declaration` → `StructNode`  
- `protocol_declaration` → `TraitNode`
- `enum_declaration` → `EnumNode`
- `function_declaration` → `FunctionNode`
- `subscript_declaration` → `MethodNode`
- `import_declaration` → `ImportNode`
- `typealias_declaration` → `TypeAliasNode`
- `extension_declaration` → `ImplBlockNode`
- Manifest: `Package.swift` → regex-based `.package(name:url:from:)` extraction

**pyproject.toml**: Add `swift = ["tree-sitter>=0.23.0,<1.0", "tree-sitter-swift>=0.23.0,<1.0"]`

#### Phase 14E — Dispatcher + CLI Registration

**Files**: `ast_intel/core/dispatcher.py`, `ast_intel/cli.py`, `ast_intel/core/workspace.py`

For each new language:
1. Add to dispatcher registry (try/except import pattern)
2. Add to `SupportedLanguage` enum
3. Add extensions to `_ALL_SOURCE_EXTENSIONS` and `_LANGUAGE_EXTENSIONS`
4. Update `[all]` optional-dependencies to include new grammars

#### Phase 14F — Tests

Each language gets its own test file following the established pattern:
- Test extraction of all construct types from fixture files
- Assert spans, call edges, rationale comments, confidence values
- Assert manifest parsing
- Integration test: full pipeline end-to-end with `--lang <language>`

**Total effort estimate**: ~2 days per language, ~8 days total  
**Depends on**: Nothing (each language is independent)

---

## Dependency Graph Between Features

```
Feature 7 (Java)                    Feature 8 (.ast-intel-ignore)
    │                                    │
    │ (independent)                      │ (independent)
    │                                    │
Feature 14 (Tier 2 Languages)           │
    │                                    │
    │ (independent)                      │
    │                                    │
    └──────────────┐    ┌────────────────┘
                   │    │
                   ▼    ▼
          Feature 9 (Semantic Similarity)
                   │
                   │ (enriches graph)
                   │
                   ▼
          Feature 10 (HTML Visualization) ◄── renders the graph
                   │
                   │
                   ▼
          Feature 11 (Query CLI) ◄── queries the graph
                   │
                   ├── Feature 12 (Neo4j/GraphML Export)  (parallel)
                   │
                   ▼
          Feature 13 (MCP Server) ◄── wraps QueryEngine as tools
```

**Key dependencies**:
- Feature 13 (MCP) **requires** Feature 11 (Query CLI) — the MCP tools wrap `QueryEngine`
- Feature 11 (Query CLI) **requires** `networkx` (already in `[analysis]` extra)
- Features 7, 8, 14 are **fully independent** — can start immediately
- Feature 9 (Similarity) is independent but enriches the graph for Features 10-13
- Features 10, 11, 12 can proceed in **parallel** (all consume `CodeGraph`)

---

## Suggested Implementation Order

| Sprint | Feature | Est. Days | Cumulative | Dependencies |
|--------|---------|-----------|------------|--------------|
| **Sprint 1** | Feature 7: Java Extractor (7A-7E) | 3-4 | 3-4 | None |
| **Sprint 1** | Feature 8: .ast-intel-ignore (8A-8C) | 1 | 4-5 | None |
| **Sprint 2** | Feature 9: Semantic Similarity (9A-9F) | 3-4 | 7-9 | Feature 5 (done) |
| **Sprint 2** | Feature 12: Neo4j/GraphML/Cypher (12A-12E) | 3-4 | 10-13 | Feature 5 (done) |
| **Sprint 3** | Feature 10: HTML Visualization (10A-10E) | 3-4 | 13-17 | Feature 5 (done) |
| **Sprint 3** | Feature 11: Query CLI (11A-11D) | 4-5 | 17-22 | Feature 6 (done) |
| **Sprint 4** | Feature 13: MCP Server (13A-13E) | 4-5 | 21-27 | Feature 11 |
| **Sprint 5** | Feature 14: Tier 2 Languages (14A-14F) | 8 | 29-35 | None (can run anytime) |

**Total estimated effort**: ~29-35 working days (6-7 weeks)

**Parallelization opportunities**:
- Sprint 1: Features 7 + 8 in parallel (different files entirely)
- Sprint 2: Features 9 + 12 in parallel (different formatters/modules)
- Sprint 3: Features 10 + 11 in parallel (HTML formatter vs query engine)
- Sprint 5: Each Tier 2 language is independent — can assign to different contributors

### New Dependencies to Add to pyproject.toml

| Package | Feature | Purpose | Required? |
|---------|---------|---------|-----------|
| `mcp>=1.0,<2.0` | Feature 13 | MCP server SDK | Optional (`[mcp]` extra) |
| `tree-sitter-kotlin` | Feature 14A | Kotlin grammar | Optional (`[kotlin]` extra) |
| `tree-sitter-ruby` | Feature 14B | Ruby grammar | Optional (`[ruby]` extra) |
| `tree-sitter-php` | Feature 14C | PHP grammar | Optional (`[php]` extra) |
| `tree-sitter-swift` | Feature 14D | Swift grammar | Optional (`[swift]` extra) |

### New Files Created

| File | Feature | Purpose |
|------|---------|---------|
| `ast_intel/extractors/java.py` | 7 | Java language extractor |
| `ast_intel/core/_similarity.py` | 9 | Jaccard similarity computation |
| `ast_intel/formatters/graph_html_formatter.py` | 10 | Interactive HTML visualization |
| `ast_intel/formatters/_html_template.py` | 10 | HTML/CSS/JS template |
| `ast_intel/core/_graph_loader.py` | 11 | Load/build graph from cache |
| `ast_intel/core/_query_engine.py` | 11 | Graph query operations |
| `ast_intel/formatters/graph_graphml_formatter.py` | 12 | GraphML serializer |
| `ast_intel/formatters/graph_cypher_formatter.py` | 12 | Neo4j Cypher serializer |
| `ast_intel/formatters/graph_neo4j_csv_formatter.py` | 12 | Neo4j CSV export |
| `ast_intel/mcp_server.py` | 13 | MCP server implementation |
| `ast_intel/extractors/kotlin.py` | 14A | Kotlin extractor |
| `ast_intel/extractors/ruby.py` | 14B | Ruby extractor |
| `ast_intel/extractors/php.py` | 14C | PHP extractor |
| `ast_intel/extractors/swift.py` | 14D | Swift extractor |
| `tests/fixtures/java/*.java` | 7 | Java test fixtures |
| `tests/fixtures/kotlin/` | 14A | Kotlin test fixtures |
| `tests/fixtures/ruby/` | 14B | Ruby test fixtures |
| `tests/fixtures/php/` | 14C | PHP test fixtures |
| `tests/fixtures/swift/` | 14D | Swift test fixtures |
| `tests/test_java_extractor.py` | 7 | Java tests |
| `tests/test_similarity.py` | 9 | Similarity edge tests |
| `tests/test_html_formatter.py` | 10 | HTML visualization tests |
| `tests/test_query_engine.py` | 11 | Query engine tests |
| `tests/test_graph_export_formatters.py` | 12 | Export formatter tests |
| `tests/test_mcp_server.py` | 13 | MCP server tests |
| `tests/test_kotlin_extractor.py` | 14A | Kotlin tests |
| `tests/test_ruby_extractor.py` | 14B | Ruby tests |
| `tests/test_php_extractor.py` | 14C | PHP tests |
| `tests/test_swift_extractor.py` | 14D | Swift tests |

### Modified Files

| File | Features | Changes |
|------|----------|---------|
| `ast_intel/models/graph_model.py` | 9 | Add `SIMILAR_TO` to `EdgeRelation` |
| `ast_intel/core/graph_builder.py` | 9 | Wire similarity edge computation |
| `ast_intel/core/analyzer.py` | 9 | Include similarity in community detection |
| `ast_intel/core/workspace.py` | 8 | Load `.ast-intel-ignore`, combine with gitignore |
| `ast_intel/core/dispatcher.py` | 7, 14 | Register Java, Kotlin, Ruby, PHP, Swift extractors |
| `ast_intel/core/emitter.py` | 10, 12 | Add html, graphml, cypher, neo4j-csv formats |
| `ast_intel/core/manifest_parser.py` | 7, 14 | Parse pom.xml, build.gradle, Gemfile, composer.json, Package.swift |
| `ast_intel/cli.py` | 10, 11, 12, 13, 14 | Add html/graphml/cypher formats, query/path/explain/serve subcommands, new languages |
| `pyproject.toml` | 13, 14 | Add mcp, kotlin, ruby, php, swift optional deps |

---

*This document is the single source of truth for the AST_INTEL Phase 2 enhancement roadmap. Update it as phases complete.*
