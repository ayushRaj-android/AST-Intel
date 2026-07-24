# AST Intel

A language-agnostic CLI tool that parses a codebase and produces a structured semantic index —
an AST JSON and human-readable summary — capturing every type, function, trait, import, and
package method call, organized by file, module, and crate, with cross-reference indexes built
on top.

```bash
ast-intel /home/user/my-project --include src/ --output ./analysis
# Produces: ast.json + summary.md in ~8 seconds for 232 files
```

---

## Table of Contents

1. [The Problem](#the-problem)
2. [What It Does](#what-it-does)
3. [Output Schema](#output-schema)
4. [How It Helps — Use Cases](#how-it-helps--use-cases)
5. [Business & Cost Impact](#business--cost-impact)
6. [Who Uses It](#who-uses-it)
7. [CLI Interface](#cli-interface)
8. [Architecture](#architecture)
9. [Non-Functional Requirements](#non-functional-requirements)
10. [Known Limitations](#known-limitations)
11. [Testing Strategy](#testing-strategy)
12. [Competitive Landscape](#competitive-landscape)
13. [Privacy & Security](#privacy--security)
14. [Roadmap & Phases](#roadmap--phases)

---

## The Problem

Coding agents (GitHub Copilot, Claude, Codex, Cursor) work by:

1. Receiving a task
2. **Text-searching source files** to understand the codebase
3. Reading full file contents to find types, signatures, and relationships
4. Generating code based on partially understood context

This is expensive, slow, and error-prone:

```
Agent reads 15 files averaging 300 lines each to answer:
"Where is StorageHelper implemented?"
= ~45,000 tokens consumed just for navigation
```

**AST Intel replaces file reading with index lookups.**

---

## What It Does

### Core Output: `ast.json`

Machine-readable semantic index containing:

| Data | Purpose |
|---|---|
| Every struct / class / type with fields | Know data shapes without reading files |
| Every function with signature, visibility, async/sync | Know APIs without reading implementations |
| `self_methods[]` per file | Know what a file implements |
| `imported_package_methods{}` per file | Know what external packages a file uses and how |
| Trait → implementors map | Know contract relationships |
| Function → file index | Find any function in O(1) |
| Package method → files index | Find CVE blast radius in O(1) |
| Inter-crate / inter-module dependency graph | Know module coupling |

### Core Output: `summary.md`

Human and LLM readable reference. A pre-digested context document an agent uses as a system
prompt prefix instead of reading source files. For a 232-file Rust workspace this is ~129 KB
and ~2,000 lines.

### Code Knowledge Graph: `graph.json`

A typed code graph with nodes (functions, structs, traits, files, modules, routes, HTTP calls,
cloud resources, K8s deployments, Helm charts, Docker images, CI/CD pipelines, Terraform
resources) and edges (`CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`, `CONTAINS`, `ROUTES_TO`,
`USES_RESOURCE`, `BACKED_BY`, `DEPLOYS`, …). Powers all graph queries, the MCP server, and
the visualizers.

### Interactive Graph Viewer: `graph.html`

A self-contained vis.js interactive visualization of the code knowledge graph. Nodes colored
by kind, edges by relationship type. Click, zoom, search, filter by kind. Works offline with
`--offline` flag.

### Service Architecture Viewer: `architecture.html`

A hybrid vis.js + Mermaid viewer with 6 tabs for understanding service architecture at a
glance:

| Tab | Engine | Shows |
|---|---|---|
| **Overview / Service map** | vis.js | Modules sized by symbols, inter-module edges, External node |
| **API (UML)** | Mermaid classDiagram | Controllers as classes, routes as methods, inheritance |
| **Cross-service sequence** | Mermaid sequenceDiagram | `ModuleA ->> ModuleB: METHOD /path` |
| **Request flow** | Mermaid flowchart | Route → handler → call chain (depth 3) with cross-module boundaries |
| **Cloud Infra** | Mermaid flowchart | Cloud resources (DB, cache, queue, storage, secret) per module with caller attribution |
| **Deployment** | Mermaid flowchart | Service → image → K8s deployment → service/ingress |

### Cloud Infrastructure Inventory: `resources.json`

Cloud infrastructure resources detected from **both** application code (SDK client instantiation)
and IaC (Terraform, Bicep, ARM templates), classified by `(provider, service, category)` with
caller function attribution. Categories: database, cache, queue, topic, stream, storage, secret.

```bash
# CLI query
ast-intel resources /path/to/repo --category database --provider azure -f json

# MCP tool (for AI agents)
get_cloud_resources(category="database", provider="azure")
```

### MCP Server

Exposes the full code graph as 34 tools + 1 resource to any MCP-compatible editor
(VS Code + Copilot, Cursor, Windsurf, Claude Desktop). Includes structural queries,
history/ownership, decision context, IaC overview, cloud infrastructure inventory, and
the Minimalism Protocol tools.

### Minimalism Protocol (Graph-Powered Code Reuse)

A built-in system that ensures AI agents **reuse existing code instead of rewriting it**,
powered by the structural code graph. Inspired by research showing that agent behavior
shaping can reduce generated code by 54% while maintaining 100% safety — but unlike
prompt-only approaches, AST-Intel's protocol is backed by structural proof (the graph
definitively answers "does this already exist?").

**MCP tools:**

| Tool | Purpose |
|------|---------|
| `find_reusable` | Before writing new code, search the graph for existing symbols that already do what you need. Returns ranked reuse candidates. |
| `audit_codebase` | Structural over-engineering detection: dead abstractions, thin wrappers, duplicates, single-implementor interfaces, bloat hotspots. |
| `track_minimalism` | Log reuse/create/defer decisions to a JSONL ledger for measuring reuse rates over time. |
| `review_minimalism_ledger` | Read back the decision ledger with aggregate stats. |

**Passive signals:** `get_context`, `explain_symbol`, and `get_dependencies` responses
automatically include `reuse_signals` — structurally similar symbols, thin-wrapper
warnings, and community peers. Controlled via `ASTINTEL_REUSE_SIGNALS=on|off`.

**Agent instructions:** Ships with `.github/copilot-instructions.md`, `AGENTS.md`, and
`.cursor/rules/` that tell agents to query the graph before writing anything new.

```
The Minimalism Protocol:
1. STOP  — Does this need to exist?
2. QUERY — search_symbols + find_similar (graph is authoritative)
3. CHECK — get_dependencies (does a dep already handle this?)
4. CHECK — stdlib coverage
5. ASSESS — get_impact (smallest correct diff)
6. Only then: write the minimum that works
```

### Languages Supported

| Tier | Languages | Grammar |
|---|---|---|
| **Tier 1** — production ready | Rust, Python, TypeScript / JavaScript, Go, C#, Java | `tree-sitter-*` |
| **Tier 2** — community | C / C++, Ruby, Kotlin, Swift, PHP | `tree-sitter-*` |

All languages map to a **normalized schema** so cross-language queries work uniformly:

| Language Concept | Normalized To |
|---|---|
| Rust `trait`, Go `interface`, TS `interface`, Java `interface`, C# `interface` | `trait` |
| Rust `struct`, Go `struct`, Python `class`, TS `class`, Java `class`, C# `class` | `struct` |
| Rust `impl Trait for Type`, Go receiver method, TS `implements`, Java `implements` | `impl_block` |
| Rust `enum`, TS `enum`, Java `enum`, C# `enum` | `enum` |

---

## Output Schema

`ast.json` follows a versioned schema. Consumers should check `meta.schema_version` before
parsing. Backward-incompatible changes bump the major version.

### Top-level Structure

```json
{
  "meta": {
    "schema_version": "1.0.0",
    "tool_version": "0.1.0",
    "generated_at": "2026-04-23T07:39:00Z",
    "workspace_root": "/home/user/project",
    "total_crates": 13,
    "total_rs_files": 232,
    "total_structs": 200,
    "total_enums": 34,
    "total_traits": 19,
    "total_functions": 623,
    "total_impl_blocks": 272,
    "total_self_methods": 1301,
    "total_pkg_method_call_sites": 523
  },
  "workspace": { },
  "crates": { },
  "cross_references": { }
}
```

### Per-File AST Entry

```json
{
  "file": "src/crates/services/approval-engine/src/components/async_component.rs",
  "module_path": "approval_engine::components::async_component",
  "is_test": false,
  "ast": {
    "uses": ["use bytes::{Buf, BufMut, Bytes, BytesMut}", "..."],
    "modules": [{ "name": "sub_mod", "visibility": "pub", "inline": false }],
    "structs": [{
      "name": "AsyncComponent",
      "visibility": "pub",
      "generics": "<S, T, U, V>",
      "fields": [
        { "name": "scanner_client", "type": "Arc<S>", "visibility": "private" }
      ],
      "attributes": ["#[derive(Debug, Clone)]"],
      "doc": "Manages async scanning jobs"
    }],
    "enums": [{ "name": "JobStatus", "variants": [{ "name": "Pending", "kind": "unit" }] }],
    "traits": [{
      "name": "AsyncComponentTrait",
      "visibility": "pub",
      "items": [
        { "kind": "required_method", "name": "create_async_job", "is_async": true,
          "return_type": "Result<JobCreationResponse, Error>" }
      ]
    }],
    "functions": [{
      "name": "helper_fn",
      "visibility": "pub",
      "is_async": true,
      "params": [{ "name": "input", "type": "&str" }],
      "return_type": "Result<String>"
    }],
    "impl_blocks": [{
      "self_type": "AsyncComponent<S, T, U, V>",
      "trait_type": "AsyncComponentTrait",
      "methods": [
        { "kind": "method", "name": "create_async_job", "visibility": "pub",
          "is_async": true, "return_type": "Result<JobCreationResponse, Error>" }
      ]
    }],
    "type_aliases": [{ "name": "Result", "aliased_to": "core::result::Result<T, Error>" }],
    "constants": [{ "name": "MAX_RETRIES", "visibility": "pub", "raw": "const MAX_RETRIES: u32 = 3" }],
    "self_methods": [
      { "name": "create_async_job", "visibility": "pub", "is_async": true,
        "return_type": "Result<JobCreationResponse, Error>",
        "context": "impl:AsyncComponentTrait for AsyncComponent<S, T, U, V>" },
      { "name": "helper_fn", "visibility": "pub", "is_async": true,
        "return_type": "Result<String>", "context": "free" }
    ],
    "imported_package_methods": {
      "bytes::BytesMut": ["with_capacity", "freeze"],
      "uuid::Uuid": ["new_v4"],
      "serde_json": ["from_slice", "to_string"]
    },
    "errors": []
  }
}
```

### Cross-References Structure

```json
{
  "cross_references": {
    "struct_index":            { "Configuration": ["approval_engine::config::Configuration"] },
    "enum_index":              { "Error": ["approval_engine::error::Error"] },
    "trait_index":             { "StorageHelper": ["lib_storage_service::...::StorageHelper"] },
    "trait_implementations":   { "StorageHelper": ["...::DiskStorageService", "...::RedisStorageService"] },
    "function_file_index":     { "poll_async_jobs": [
      { "file": "src/.../custom_scanner_client.rs", "module_path": "...",
        "visibility": "pub", "is_async": true, "return_type": "Result<...>",
        "context": "impl:CustomScannerClientTrait for CustomScannerClient" }
    ]},
    "package_method_index":    { "bytes::BytesMut::with_capacity": ["src/.../telegram.rs"] },
    "inter_crate_deps":        { "approval-engine": ["lib-common", "lib-storage-service"] },
    "impl_map": [
      { "trait": "StorageHelper", "for": "DiskStorageService",
        "in_module": "lib_storage_service::...", "methods": ["retrieve_telegrams", "store_telegrams"] }
    ]
  }
}
```

---

## How It Helps — Three Use Cases

### 1. Coding Agent Context (Primary)

The `summary.md` becomes a **system prompt prefix**. One-time cost of ~30,000 tokens gives
the agent full semantic awareness of the entire codebase. No per-query file reading.

```
Before: Agent reads 15 files = 45,000 tokens to answer "where is X?"
After:  Agent queries ast.json = 0 file reads, dict lookup
```

### 2. Security & CVE Blast Radius

```python
# RUSTSEC-2026-0007: Which files use BytesMut::with_capacity?
xref["package_method_index"]["bytes::BytesMut::with_capacity"]
# → ["lib-common/.../telegram.rs", "lib-common/.../messages.rs"]
# Instant. Zero grep. Zero file reads.
```

Before: Engineer manually grep-searches 232 files, reads each result for context.  
After: One dict lookup returns exact file list with crate context.

### 3. Impact Analysis Before Code Changes

```python
# "I'm changing StorageHelper trait — what breaks?"
xref["trait_implementations"]["StorageHelper"]
# → DiskStorageService, RedisStorageService, InMemoryStorageService,
#    MockStorageService, ClusterStorageService
# 5 files need updating — identified in 0 seconds
```

### 4. Function Lookup (Reverse Index)

```python
# "Where is poll_async_jobs implemented?"
xref["function_file_index"]["poll_async_jobs"]
# → 4 entries: exact file path, module path, visibility, context, return type
# Agent opens the right file on first try — zero wasted reads
```

---

## Business & Cost Impact

### Token Cost Reduction

| Task | Without AST Intel | With AST Intel | Reduction |
|---|---|---|---|
| Find where X is implemented | ~8,000 tokens | ~50 tokens | **99%** |
| Understand service dependencies | ~15,000 tokens | ~200 tokens | **98%** |
| CVE blast radius analysis | ~40,000 tokens | ~500 tokens | **98%** |
| Feature implementation + impact analysis | ~80,000 tokens | ~5,000 tokens | **94%** |

### Real Dollar Cost (GPT-4o pricing as reference)

```
Typical agent session on a 200-file codebase:
  Without AST Intel:  ~120,000 tokens  =  $0.36 / session
  With AST Intel:       ~8,000 tokens  =  $0.024 / session

At 100 developer sessions / day:
  Without AST Intel:  $36 / day   =  $13,140 / year
  With AST Intel:     $2.40 / day =  $876 / year
  Annual savings:  $12,264 / year per 100-developer team

At 1,000 autonomous agent runs / day (CI pipelines, PR reviews, security scans):
  Without AST Intel:  $360 / day   =  $131,400 / year
  With AST Intel:      $24 / day   =    $8,760 / year
  Annual savings:  $122,640 / year
```

### Latency Impact

```
Agent response time today:
  File search + read:   8–15 seconds
  LLM generation:        3–5 seconds
  Total:                11–20 seconds

With AST Intel:
  Index lookup:          <50 ms
  LLM generation:        3–5 seconds
  Total:                 3–5 seconds   →  4x faster

At 50 interactions/day × 200 developers = 10,000 interactions/day
Developer time saved: ~1,500 hours/year across the team
```

### Hallucination Reduction

| Hallucination Type | Without AST Intel | With AST Intel |
|---|---|---|
| Wrong function signature | Common — agent guesses | Eliminated — signature in index |
| Invented struct fields | Common — agent invents | Eliminated — fields in index |
| Wrong dependency version | Occasional | Eliminated — manifest data in AST |
| Adding forbidden crate (e.g. `rustls`) | Possible | Eliminated — workspace lint rules in AST |
| Wrong trait implementation | Common | Eliminated — implementors in index |

**Cost of one hallucination**: 1 code review cycle + fix ≈ 30 min developer time ≈ $25 at
$50/hr burdened cost. At 5 hallucinations/day prevented across a team:
**$125/day = $45,625/year saved**.

---

## Who Uses It

| User | How |
|---|---|
| **Developer + Copilot / Cursor** | Drop `ast.json` into project root; agent auto-uses it as context |
| **CI/CD pipeline** | Run `ast-intel` on PR; attach output to agent security scanner |
| **Security engineer** | Query `package_method_index` for any CVE package in seconds |
| **Onboarding engineer** | Read `summary.md` to understand codebase in 20 min instead of 2 days |
| **Architect** | Use `inter_crate_deps` to visualize module coupling without reading code |

---

## CLI Interface

```
ast-intel [OPTIONS] COMMAND [ARGS]

Commands:
  scan           Analyze a codebase → ast.json + summary.md + graph.json + viewers
  query          Search the code graph for symbols matching a pattern
  explain        Explain a symbol's structural role
  context        Unified single-shot context (deps + dependents + file)
  deps           Forward dependencies of a symbol
  dependents     Reverse dependents of a symbol
  usages         All incoming references to a symbol
  impact         Blast radius of changing a symbol
  path           Shortest path between two symbols
  implementors   Find implementations of a trait/interface
  similar        Find structurally similar symbols
  community      Code community/cluster detection
  files          List scanned files with symbol counts
  routes         List HTTP routes/endpoints
  resources      List cloud infrastructure resources (DB, cache, queue, etc.)
  serve          Start MCP server for editor integration
  merge          Merge multiple graph.json files into a unified graph
  install        Configure an AI assistant to use ast-intel's MCP server
  uninstall      Remove ast-intel configuration from an AI assistant
  hook           Manage git hooks for automatic graph rebuild
  history        Git commit history of a file:line-range
  ownership      Per-author ownership scores for a file:line-range

scan options:
  REPO_PATH                          Path to repo root
  --include    -i  PATH              Include specific paths (repeatable)
  --exclude    -e  PATH              Exclude specific paths (repeatable)
  --lang       -l  LANGUAGE          Languages: rust, python, go, typescript, csharp,
                                     java, cpp, ruby, kotlin, scala, swift, php
  --output     -o  DIR               Output directory (default: <repo>/ast_output/)
  --format     -f  FORMAT            json, md, both, graph-json, dot, mermaid,
                                     html, arch, or all (default: both)
  --analyze                          Run graph analysis (communities, god nodes)
  --similarity                       Compute SIMILAR_TO edges
  --offline                          Inline vis.js/mermaid in HTML (self-contained)
  --infra-scan                       Emit resources.json (cloud infrastructure inventory)
  --workers    -w  INT               Parallel workers (default: CPU count)
  --no-methods                       Skip package method call extraction
  --no-cache                         Force full re-parse
  --no-iac                           Skip IaC scanning
  --quiet      -q                    Suppress progress output
  --debug                            Verbose logging to stderr
  --version                          Print tool version
```

**Output formats:**

| Format | Output file | Description |
|---|---|---|
| `json` | `ast.json` | Machine-readable semantic index |
| `md` | `summary.md` | Human/LLM-readable reference |
| `both` | `ast.json` + `summary.md` | Default |
| `graph-json` | `graph.json` | Code knowledge graph (nodes + edges) |
| `dot` | `graph.dot` | Graphviz DOT format |
| `mermaid` | `graph.mermaid.md` | Mermaid diagram |
| `html` | `graph.html` | Interactive vis.js graph viewer |
| `arch` | `architecture.html` | Service architecture viewer (6 tabs) |
| `all` | All of the above | Full output suite |

**Exit codes**: `0` success | `1` partial failure | `2` fatal error

Examples:

```bash
# Full repo, auto-detect language
ast-intel /home/user/my-project

# Specific paths only, exclude tests and build output
ast-intel /home/user/my-project \
  --include src/crates/libs \
  --include src/crates/services \
  --exclude "*/tests/*" \
  --exclude "*/target/*"

# Only Rust and Go, JSON output for CI
ast-intel /home/user/polyglot-repo --lang rust --lang go --format json --quiet
```

---

## Architecture

### Project Structure

```
ast-intel/
├── pyproject.toml                  # PEP 517, entry point, optional deps per language
├── README.md
│
├── ast_intel/
│   ├── __init__.py                 # SCHEMA_VERSION = "1.0.0"
│   ├── cli.py                      # Entry point — typer CLI
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── workspace.py            # Repo discovery, .gitignore, path resolution
│   │   ├── manifest_parser.py      # Cargo.toml, package.json, go.mod → crate metadata
│   │   ├── dispatcher.py           # Routes files to correct extractor, parallel execution
│   │   ├── indexer.py              # Builds cross-references after all files parsed
│   │   └── emitter.py              # Orchestrates output writing
│   │
│   ├── extractors/                 # One extractor per language — all implement ExtractorBase
│   │   ├── __init__.py
│   │   ├── base.py                 # Abstract base class: ExtractorBase
│   │   ├── rust.py
│   │   ├── python.py
│   │   ├── typescript.py
│   │   ├── go.py
│   │   ├── csharp.py
│   │   └── java.py
│   │
│   ├── models/                     # Pure dataclasses — no logic
│   │   ├── __init__.py
│   │   ├── ast_node.py             # FileAST, StructNode, FunctionNode, MethodNode, ImplNode
│   │   └── workspace_model.py      # WorkspaceAST, CrateModel, CrossReferences
│   │
│   └── formatters/
│       ├── __init__.py
│       ├── json_formatter.py       # WorkspaceAST → ast.json (with custom encoder)
│       └── markdown_formatter.py   # WorkspaceAST → summary.md
│
└── tests/
    ├── conftest.py                 # Shared fixtures, temp directory management
    ├── fixtures/                   # Minimal source snippets per language
    │   ├── rust/
    │   ├── python/
    │   └── typescript/
    ├── test_rust_extractor.py
    ├── test_python_extractor.py
    ├── test_typescript_extractor.py
    ├── test_indexer.py
    ├── test_dispatcher.py
    ├── test_workspace.py
    ├── test_json_formatter.py
    └── integration/
        └── test_end_to_end.py      # Full pipeline: repo → ast.json, verify contents
```

### Code Flow

```
CLI invocation
     │
     ▼
┌─ cli.py ─────────────────────────────────────────────────────────┐
│  Parse args → validate paths → resolve output dir                │
│                                                                   │
│  Step 1: Discover                                                 │
│  ├─→ workspace.py::discover()                                     │
│  │     Walk file tree, filter by --include / --exclude            │
│  │     Respect .gitignore via pathspec                            │
│  │     Detect manifests: Cargo.toml, package.json, go.mod, ...   │
│  │     Group files under nearest parent manifest                  │
│  │     Returns → WorkspaceModel { crates, files_per_crate }      │
│  │                                                                │
│  ├─→ manifest_parser.py::parse_manifests()                        │
│  │     Cargo.toml → workspace deps, crate deps, features         │
│  │     package.json → dependencies, devDependencies               │
│  │     go.mod → module name, require list                         │
│  │     Returns → dependency metadata per crate                    │
│  │                                                                │
│  Step 2: Extract                                                  │
│  ├─→ dispatcher.py::dispatch()                                    │
│  │     For each file → detect language by extension               │
│  │     → pick extractor → call extractor.extract(file)            │
│  │     Runs in parallel via ThreadPoolExecutor(--workers)         │
│  │     Parse errors → logged, file marked with errors[]           │
│  │     Returns → WorkspaceAST (all files populated)               │
│  │                                                                │
│  Step 3: Index                                                    │
│  ├─→ indexer.py::build_cross_references()                         │
│  │     Pass 1: Symbol tables (struct, enum, trait, fn indexes)    │
│  │     Pass 2: Relationships (trait impls, pkg method calls)      │
│  │     Pass 3: Inter-crate deps (from manifest metadata)          │
│  │     Returns → WorkspaceAST.cross_references populated          │
│  │                                                                │
│  Step 4: Emit                                                     │
│  └─→ emitter.py::emit()                                           │
│        json_formatter  → ast.json  (if --format json|both)        │
│        markdown_formatter → summary.md (if --format md|both)      │
└───────────────────────────────────────────────────────────────────┘
```

### Extractor Contract

Every language extractor implements this interface. The dispatcher calls `extract()` — it
never knows which language it is dealing with.

```python
class ExtractorBase(ABC):
    language_id: str             # "rust" | "python" | "go" | "typescript" | ...
    file_extensions: list[str]   # [".rs"] | [".py"] | [".go"] | [".ts", ".tsx"]

    @abstractmethod
    def extract(self, file_path: Path) -> FileAST:
        """Parse a single file and return its AST representation."""

    # Each extract() internally calls:
    #   extract_structs()      → StructNode[]
    #   extract_enums()        → EnumNode[]
    #   extract_traits()       → TraitNode[]     (interfaces in TS/Go/Java/C#)
    #   extract_functions()    → FunctionNode[]
    #   extract_impl_blocks()  → ImplNode[]
    #   extract_imports()      → str[]
    #   extract_self_methods() → MethodNode[]    (all fns defined in this file)
    #   extract_pkg_methods()  → dict[str, list[str]]  (Type::method scoped calls)

    @abstractmethod
    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Parse a manifest file (Cargo.toml, package.json, etc.)."""
```

### Packaging & Installation

```toml
# pyproject.toml — install only what you need
[project.optional-dependencies]
rust       = ["tree-sitter-rust"]
python     = ["tree-sitter-python"]
go         = ["tree-sitter-go"]
typescript = ["tree-sitter-typescript"]
all        = ["tree-sitter-rust", "tree-sitter-python",
              "tree-sitter-go", "tree-sitter-typescript",
              "tree-sitter-c-sharp", "tree-sitter-java"]
```

```bash
pipx install ast-intel[rust]       # Rust only
pipx install ast-intel[all]        # All languages
```

---

## Non-Functional Requirements

| Requirement | Target |
|---|---|
| **Max file size** | Gracefully skip files > 10 MB with a warning (likely generated code) |
| **Encoding** | UTF-8 only. Non-UTF-8 files logged to stderr and skipped |
| **Symlinks** | Follow symlinks, detect infinite loops. Skip circular symlinks with warning |
| **Memory** | < 2 GB RSS for 10,000-file repos. Stream files, don't hold all ASTs in memory during extraction |
| **Binary files** | Auto-detect via null byte check in first 8 KB. Skip silently |
| **Empty files** | Produce a valid FileAST with empty arrays. Never error on empty files |
| **Determinism** | Same input always produces byte-identical output (sorted keys, stable ordering) |
| **Error tolerance** | A parse error on one file never aborts the entire run. Error count reflected in exit code |

---

## Known Limitations

These are inherent to AST-level (not compiler-level) analysis and are **not planned to be fixed**:

| Limitation | Why | Workaround |
|---|---|---|
| Instance method calls not captured | `buf.reserve(n)` — type of `buf` unknown without compiler type inference | Scoped calls (`BytesMut::with_capacity(n)`) are captured. Use `package_method_index` to narrow files, then grep for instance calls |
| Macro-expanded code invisible | `derive` macros, `macro_rules!` generate code at compile time | Macro definitions are captured; their expansions are not |
| Conditional compilation (`cfg`) ignored | `#[cfg(test)]` items are captured equally with normal items | All items in source appear regardless of cfg flags |
| No data flow or lifetime analysis | Cannot determine ownership, borrowing, or value flow | Out of scope — this is a structural index, not a compiler |
| Cross-file type resolution limited | If a function returns `T` from another crate, we store the text `T`, not the resolved type | The `struct_index` and `trait_index` help resolve type names manually |

---

## Testing Strategy

### Unit Tests

Each extractor has a dedicated test file using fixture source files:

```
tests/fixtures/rust/struct_with_fields.rs     → test struct extraction
tests/fixtures/rust/trait_with_methods.rs      → test trait extraction
tests/fixtures/rust/impl_block.rs              → test impl extraction
tests/fixtures/python/class_with_init.py       → test Python class → struct
tests/fixtures/typescript/interface.ts          → test TS interface → trait
```

Each fixture is a **minimal, self-contained source snippet** designed to test one extractor
method. Fixtures are never modified after creation — new tests get new fixtures.

### Integration Tests

`tests/integration/test_end_to_end.py`:

- Creates a temporary directory with a synthetic multi-crate repo
- Runs the full CLI pipeline
- Asserts on the output `ast.json` structure and cross-references
- Verifies determinism: two runs produce identical output

### CI Pipeline

```yaml
# Runs on every PR
- ruff check ast_intel/
- mypy ast_intel/
- pytest tests/ --cov=ast_intel --cov-report=term-missing
- ast-intel tests/fixtures/ --format json --quiet  # self-test on fixtures
```

Coverage target: **> 90%** on extractors, **> 80%** overall.

---

## Competitive Landscape

| Tool | What It Does | Gap Filled by AST Intel |
|---|---|---|
| **GitHub Copilot workspace** | Reads files on demand | Still does file reads, token-expensive per query |
| **Sourcegraph** | Text-based code search | Search, not pre-computed semantic index. Not agent-consumable JSON |
| **ctags / LSP** | Symbol navigation for humans | Not serializable to JSON. IDE-specific, not agent-consumable |
| **ast-grep** | AST-based find/replace (linting) | Pattern matching, not whole-codebase indexing. No cross-references |
| **CodeQL** | Security-focused semantic analysis | Heavy setup (database build), not designed for agent context |
| **rust-analyzer** | Full Rust compiler integration | Rust-only. Not serializable for agents. Requires full cargo project |
| **AST Intel** | Pre-computed multi-language semantic index for agents | Lightweight, JSON-native, cross-language, agent-optimized |

---

## Privacy & Security

`ast.json` contains **code structure only, not source code**.

| Included | NOT Included |
|---|---|
| Function names and signatures | Function body implementations |
| Struct / class field names and types | Field values or constants' actual values (truncated) |
| Import paths | File contents beyond `use` / `import` statements |
| Trait / interface method signatures | Method body logic |
| Module hierarchy and file paths | Environment variables, secrets, credentials |

This makes `ast.json` safe to share with external tools, CI services, or LLM providers without
exposing proprietary business logic. Only the skeleton is exposed.

---

## Roadmap & Phases

Each phase is a shippable milestone. A phase is **complete** only when every verification
checkpoint listed under it passes.

### Phase Summary

| Phase | Name | Depends On | Effort |
|---|---|---|---|
| **0** | Foundation & Project Scaffold | — | 2 days |
| **1** | Rust Extractor (reference implementation) | 0 | 3 days |
| **2** | Indexer & Cross-References | 1 | 2 days |
| **3** | Emitters (JSON + Markdown) | 2 | 2 days |
| **4** | Workspace Discovery & Dispatcher | 0 | 2 days |
| **5** | Go Extractor | 1, 4 | 3 days |
| **6** | Python Extractor | 1, 4 | 3 days |
| **7** | TypeScript / JavaScript Extractor | 1, 4 | 3 days |
| **8** | C# / .NET Extractor | 1, 4 | 3 days |
| **9** | C / C++ Extractor | 1, 4 | 3 days |
| **10** | Packaging, CLI Polish & PyPI Release | 3, 5–9 | 2 days |
| **11** | Incremental Mode & Performance | 3 | 3 days |
| **12** | GitHub Action & CI Integration | 10 | 2 days |
| **13** | VS Code Extension | 10 | 5 days |

### Phase Dependency Graph

```
Phase 0 ──────────────────────────────┐
   │                                   │
   ├──→ Phase 1 (Rust extractor)       ├──→ Phase 4 (Discovery & Dispatcher)
   │         │                         │
   │         │  ┌──────────────────────┘
   │         │  │
   │         ▼  ▼
   │    ┌─────────────────────────────────────┐
   │    │  Language Extractors (parallel)       │
   │    │  Phase 5  — Go                        │
   │    │  Phase 6  — Python                    │
   │    │  Phase 7  — TypeScript / JavaScript   │
   │    │  Phase 8  — C# / .NET                 │
   │    │  Phase 9  — C / C++                   │
   │    └──────────────┬──────────────────────┘
   │                   │
   │    Phase 2 (Indexer) ◄── Phase 1
   │         │
   │    Phase 3 (Emitters) ◄── Phase 2
   │         │
   │         ├─── Phase 11 (Incremental)
   |         |
   |         ├─── Phase 14 (Deployment AST — Helm + Ansible)
   │         │
   │    Phase 10 (Packaging) ◄── Phase 3 + Phases 5–9
   │         │
   │    ┌────┴────┐
   │  Phase 12  Phase 13
   │  (GH Action)(VS Code)
```

**Key constraint**: Phases 5–9 (all language extractors) can be developed **in parallel** with
each other and in parallel with Phases 2–3 since all follow the extractor contract from Phase 1.

---

### Phase 0 — Foundation & Project Scaffold
**Goal**: Repo exists, dependencies resolved, and a no-op CLI invocation succeeds.

#### Deliverables
- `pyproject.toml` with `[project.scripts]` entry point and optional dependency groups
- Package skeleton: `ast_intel/cli.py`, `core/`, `extractors/`, `models/`, `formatters/`
- `ExtractorBase` abstract class defined in `extractors/base.py`
- `FileAST`, `StructNode`, `FunctionNode`, `MethodNode`, `ImplNode` dataclasses in `models/ast_node.py`
- `WorkspaceAST` and `CrossReferences` dataclasses in `models/workspace_model.py`
- `typer` CLI wired up with all flags (`--include`, `--exclude`, `--lang`, `--output`, `--format`, `--workers`)
- `pytest` + `ruff` + `mypy` configured

#### Verification Checkpoints
- [ ] `pip install -e ".[rust]"` installs without errors
- [ ] `ast-intel --help` prints full usage with all options
- [ ] `ast-intel /tmp/empty-dir` exits cleanly with `"0 files processed"` message
- [ ] `from ast_intel.models.ast_node import FileAST, StructNode` imports cleanly
- [ ] `from ast_intel.extractors.base import ExtractorBase` imports and is abstract
- [ ] `pytest tests/` passes (empty test suite)
- [ ] `mypy ast_intel/` — zero errors
- [ ] `ruff check ast_intel/` — zero violations

---

### Phase 1 — Rust Extractor (Reference Implementation)
**Goal**: Full accurate AST extraction for Rust files. This is the reference implementation
all other language extractors will mirror.

**Depends on**: Phase 0

#### Language-Specific Concepts

| Rust Concept | Normalized To | Notes |
|---|---|---|
| `struct` | `StructNode` | Fields with visibility, generics |
| `enum` | `EnumNode` | Variants: unit, tuple, struct kinds |
| `trait` | `TraitNode` | Super-traits, required/default methods, associated types |
| `impl Trait for Type` | `ImplNode` | `trait_type` + `self_type` + methods |
| `impl Type` | `ImplNode` | `trait_type: null`, inherent methods |
| `fn` (free function) | `FunctionNode` | async, unsafe, generics, where clause |
| `use a::b::{C, D}` | `imports[]` | Raw statements |
| `Type::method()` | `imported_package_methods` | Scoped calls resolved via import map |
| `type Alias = Original` | `TypeAlias` | |
| `const` / `static` | `ConstantNode` | |
| `mod` declaration | `ModuleNode` | Inline vs file-backed |

#### Deliverables
- `extractors/rust.py` implementing all `ExtractorBase` methods:
  - `extract_structs()` — name, visibility, generics, fields (name + type + visibility)
  - `extract_enums()` — name, visibility, generics, variants (unit / tuple / struct kind)
  - `extract_traits()` — name, visibility, super-traits, required methods, default methods, associated types
  - `extract_functions()` — name, visibility, async, unsafe, params (name + type), return type, where clause
  - `extract_impl_blocks()` — self type, trait type, generics, all methods with full signatures
  - `extract_imports()` — raw `use` statements as strings
  - `extract_self_methods()` — flat list of every fn defined in the file, with `context` field
  - `extract_pkg_methods()` — `Type::method()` scoped calls grouped by resolved qualified type
- `build_import_map()` utility: parses `use bytes::{Buf, BytesMut}` into `{"Buf": "bytes::Buf", "BytesMut": "bytes::BytesMut"}`
- `parse_manifest()` for `Cargo.toml`: crate name, version, deps (workspace flag, path, features)
- Fixture files in `tests/fixtures/rust/`
- Unit tests in `tests/test_rust_extractor.py`

#### Verification Checkpoints
- [ ] Fixture with a struct having 4 fields → extracts all 4 with correct types
- [ ] Fixture with `trait` → extracts required and default methods with correct signatures
- [ ] Fixture with 2 impl blocks → `self_methods` lists methods from both, each with correct `context`
- [ ] Fixture with `BytesMut::with_capacity` → `imported_package_methods` includes `bytes::BytesMut: ["with_capacity"]`
- [ ] Fixture with no scoped calls → `imported_package_methods` is `{}`
- [ ] Fixture with `use a::{B, C}` → `build_import_map` returns `{"B": "a::B", "C": "a::C"}`
- [ ] Running on Safeguard `src/`: 232 files, 0 parse errors, Structs ≥ 200, Enums ≥ 34, Traits ≥ 19, Functions ≥ 623, Impl blocks ≥ 272
- [ ] `pytest tests/test_rust_extractor.py` — all pass

---

### Phase 2 — Indexer & Cross-References
**Goal**: After all files are parsed, build the global lookup indexes that make O(1) queries
possible.

**Depends on**: Phase 1

#### Deliverables
- `core/indexer.py` with five sequential passes:
  - Pass 1: `struct_index`, `enum_index`, `trait_index` — `name → [qualified_module_path]`
  - Pass 2: `trait_implementations` — `trait → [module::Type]` + `impl_map` list
  - Pass 3: `function_file_index` — `fn_name → [{file, module_path, visibility, is_async, return_type, context}]`
  - Pass 4: `package_method_index` — `pkg::Type::method → [files_that_call_it]`
  - Pass 5: `inter_crate_deps` — `crate → [dep_crates]` from manifest metadata

#### Verification Checkpoints
- [ ] `xref["function_file_index"]["poll_async_jobs"]` returns 4 entries on Safeguard repo
- [ ] `xref["package_method_index"]["bytes::BytesMut::with_capacity"]` returns 2 files
- [ ] `xref["trait_implementations"]["StorageHelper"]` lists all 5+ implementors
- [ ] `xref["inter_crate_deps"]["approval-engine"]` contains `lib-common`, `lib-storage-service`, `lib-http-service-client`
- [ ] Generic name `new` has one `function_file_index` entry per definition site, not per call site
- [ ] `pytest tests/test_indexer.py` — all pass

---

### Phase 3 — Emitters (JSON + Markdown)
**Goal**: `WorkspaceAST` serializes cleanly to both output formats, deterministically.

**Depends on**: Phase 2

#### Deliverables
- `formatters/json_formatter.py`
  - Serializes full `WorkspaceAST` to `ast.json`
  - Custom JSON encoder handles `set` → sorted `list`, `Path` → `str`, dataclasses → `dict`
  - Writes `meta` block: `schema_version`, `tool_version`, `generated_at`, all totals
  - Keys sorted for deterministic output
- `formatters/markdown_formatter.py`
  - Workspace overview table, dependency list, inter-crate graph
  - Per-crate: module tree, structs, enums, traits, trait impls, public functions
  - Global cross-reference section
- `core/emitter.py` wires both, respects `--format json|md|both`

#### Verification Checkpoints
- [ ] `ast.json` is valid JSON (`python -m json.tool ast.json` exits 0)
- [ ] `ast.json` contains `meta.schema_version` and `meta.generated_at`
- [ ] `ast.json` round-trips: `json.loads(json.dumps(ast))` identical
- [ ] `summary.md` renders without broken Markdown tables
- [ ] `--format json` produces only `ast.json`, `--format md` produces only `summary.md`
- [ ] Two runs on identical input produce **byte-identical** output
- [ ] `pytest tests/test_json_formatter.py` — all pass

---

### Phase 4 — Workspace Discovery & Dispatcher
**Goal**: correctly walk any repo, detect languages, group files into logical modules, and
route each file to the right extractor in parallel.

**Depends on**: Phase 0. Can develop in parallel with Phase 1.

#### Deliverables
- `core/workspace.py`
  - Walk repo from `REPO_PATH`, apply `--include` / `--exclude` glob filters
  - Respect `.gitignore` via `pathspec`
  - Detect manifests: `Cargo.toml`, `package.json`, `go.mod`, `pyproject.toml`, `*.csproj`, `CMakeLists.txt`, `Makefile`
  - Group files under nearest parent manifest; ungrouped files go into `ungrouped` module
  - Skip binary files (null byte in first 8 KB), files > 10 MB, non-UTF-8 files
- `core/dispatcher.py`
  - Extension → extractor map: `.rs`, `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.cs`, `.c`, `.h`, `.cpp`, `.hpp`, `.cc`
  - `ThreadPoolExecutor` with `--workers` control
  - Per-file error handling: parse failures logged, do not abort
  - Progress bar via `rich` (suppressed with `--quiet`)

#### Verification Checkpoints
- [ ] `--include src/crates/libs --include src/crates/services` processes only those paths
- [ ] `--exclude "*/tests/*"` skips all test files
- [ ] A `.gitignore`d file is not processed
- [ ] A repo with no manifest files produces output under `ungrouped`
- [ ] `--workers 1` and `--workers 8` produce identical output
- [ ] One corrupt file does not abort the run; `errors[]` populated, exit code `1`
- [ ] Binary file and > 10 MB file are silently skipped

---

### Phase 5 — Go Extractor
**Goal**: Full AST extraction for Go files following the `ExtractorBase` contract.

**Depends on**: Phase 1 (contract), Phase 4 (dispatcher)

**Grammar**: `tree-sitter-go`

#### Language-Specific Concepts

| Go Concept | Normalized To | Notes |
|---|---|---|
| `type X struct { ... }` | `StructNode` | Fields with exported/unexported visibility (Capital = pub) |
| `type X interface { ... }` | `TraitNode` | Method signatures as required methods |
| `func (s *X) Method()` | `ImplNode` | Receiver method → `self_type: X`, one method per impl block |
| `func Foo()` | `FunctionNode` | Free function |
| `import (...)` | `imports[]` | Package paths |
| `pkg.Function()` | `imported_package_methods` | Resolved via import path alias |
| `type Alias = Original` | `TypeAlias` | |
| `const` / `var` | `ConstantNode` | Block declarations expanded to individual entries |

#### Deliverables
- `extractors/go.py` implementing all `ExtractorBase` methods
- Visibility inference: exported (uppercase first letter) = `pub`, unexported = `private`
- Receiver method grouping: all `func (s *DiskStorage) ...` methods → single logical `ImplNode` for `DiskStorage`
- `parse_manifest()` for `go.mod`: module name, Go version, require list
- Multi-return types captured in `return_type` as `"([]byte, error)"`
- Fixtures in `tests/fixtures/go/`
- Unit tests in `tests/test_go_extractor.py`

#### Verification Checkpoints
- [ ] `type Config struct { Name string; Port int }` → struct with 2 fields, both `pub` (capitalized)
- [ ] `type config struct { name string }` → struct with 1 field, field visibility `private` (lowercase)
- [ ] `type StorageHelper interface { Retrieve() ([]byte, error) }` → trait with 1 required method, return type `([]byte, error)`
- [ ] `func (s *DiskStorage) Retrieve() ([]byte, error)` → impl block on `DiskStorage` with method `Retrieve`
- [ ] `func (s *DiskStorage) Store(data []byte) error` → same `DiskStorage` impl block with 2 methods
- [ ] `func NewStorage(cfg Config) *DiskStorage` → free function with params and return type
- [ ] `import "fmt"; fmt.Println(...)` → `imported_package_methods: {"fmt": ["Println"]}`
- [ ] `import alias "github.com/pkg/errors"; alias.New(...)` → `imported_package_methods: {"github.com/pkg/errors": ["New"]}`
- [ ] `go.mod` parsed: module name, Go version, require list extracted
- [ ] Go-only repo produces valid `ast.json` with correct module paths
- [ ] `pytest tests/test_go_extractor.py` — all pass

---

### Phase 6 — Python Extractor
**Goal**: Full AST extraction for Python files following the `ExtractorBase` contract.

**Depends on**: Phase 1 (contract), Phase 4 (dispatcher)

**Grammar**: `tree-sitter-python`

#### Language-Specific Concepts

| Python Concept | Normalized To | Notes |
|---|---|---|
| `class Foo:` | `StructNode` | Fields from `__init__` type annotations or `@dataclass` |
| `class Foo(ABC):` with `@abstractmethod` | `TraitNode` | Abstract base class → interface equivalent |
| `class Bar(Foo):` | `ImplNode` | Inheritance → `trait_type: Foo` |
| `@dataclass class X:` | `StructNode` | Fields directly from class body annotations |
| `def foo()` | `FunctionNode` | `async def` → `is_async: true`, decorators as attributes |
| Class method `def m(self)` | method in `ImplNode` | `context: "impl:ClassName"` |
| `import X` / `from X import Y` | `imports[]` | |
| `X.method()` | `imported_package_methods` | When `X` is an imported module/class |
| `Type[X]`, `Optional[X]` | Return type captured as-is | Type hints preserved as text |

#### Deliverables
- `extractors/python.py` implementing all `ExtractorBase` methods
- `__init__` field extraction: parse `self.name: str = name` and typed params `def __init__(self, name: str)`
- `@dataclass` field extraction: parse `name: str` annotations in class body
- Decorator capture: `@staticmethod`, `@classmethod`, `@abstractmethod`, `@property` as attributes
- ABC detection: class with `ABC`/`ABCMeta` base + `@abstractmethod` methods → `TraitNode`
- `parse_manifest()` for `pyproject.toml` and `setup.py/setup.cfg`: package name, deps
- Fixtures in `tests/fixtures/python/`
- Unit tests in `tests/test_python_extractor.py`

#### Verification Checkpoints
- [ ] `class Foo: def __init__(self, name: str, value: int)` → struct with 2 fields, types `str` and `int`
- [ ] `@dataclass class Config: name: str; port: int = 8080` → struct with 2 fields
- [ ] `class ABCStorage(ABC): @abstractmethod async def retrieve(self)` → trait with 1 required method, `is_async: true`
- [ ] `class DiskStorage(ABCStorage): def retrieve(self)` → impl block with `trait_type: ABCStorage`
- [ ] `async def fetch(url: str) -> dict` → function with `is_async: true`, return type `dict`
- [ ] `@staticmethod def helper()` → method with attribute `@staticmethod`
- [ ] `from pathlib import Path; Path.home()` → `imported_package_methods: {"pathlib::Path": ["home"]}`
- [ ] `import os; os.path.join(...)` → `imported_package_methods: {"os": ["path"]}`
- [ ] `pyproject.toml` parsed: name, version, dependencies list
- [ ] Python-only repo produces valid `ast.json`
- [ ] `pytest tests/test_python_extractor.py` — all pass

---

### Phase 7 — TypeScript / JavaScript Extractor
**Goal**: Full AST extraction for TypeScript and JavaScript files.

**Depends on**: Phase 1 (contract), Phase 4 (dispatcher)

**Grammar**: `tree-sitter-typescript` (covers `.ts`, `.tsx`), `tree-sitter-javascript` (covers `.js`, `.jsx`)

#### Language-Specific Concepts

| TS/JS Concept | Normalized To | Notes |
|---|---|---|
| `interface Foo { ... }` | `TraitNode` | Method signatures as required methods |
| `type Foo = { ... }` | `StructNode` or `TypeAlias` | Object type → struct, primitive alias → type alias |
| `class Foo { ... }` | `StructNode` | Fields from constructor params + property declarations |
| `class Foo implements IBar` | `ImplNode` | `trait_type: IBar` |
| `class Foo extends Bar` | `ImplNode` | `trait_type: Bar` (inheritance = impl for index purposes) |
| `function foo()` / `const foo = () =>` | `FunctionNode` | Arrow functions captured, `async` flag |
| Class method | method in `ImplNode` | `context: "impl:ClassName"` |
| `import { X } from 'pkg'` | `imports[]` | |
| `X.method()` | `imported_package_methods` | When `X` is an imported name |
| `enum Foo { ... }` | `EnumNode` | String/numeric/auto variants |
| `export` keyword | `visibility: "pub"` | `export` = pub, no export = private |

#### Deliverables
- `extractors/typescript.py` implementing all `ExtractorBase` methods
- Handle both `.ts`/`.tsx` and `.js`/`.jsx` files (separate grammar but same extractor logic)
- `export` detection for visibility (`export class` = pub, `class` = private)
- Constructor field extraction: `constructor(private name: string, public age: number)` → 2 fields
- Property declaration fields: `private name: string;` in class body
- `import { X } from 'pkg'` and `import X from 'pkg'` and `const X = require('pkg')` → `imports[]`
- `parse_manifest()` for `package.json`: name, version, dependencies, devDependencies
- Fixtures in `tests/fixtures/typescript/` and `tests/fixtures/javascript/`
- Unit tests in `tests/test_typescript_extractor.py`

#### Verification Checkpoints
- [ ] `interface StorageHelper { retrieve(): Promise<Data> }` → trait with 1 required method
- [ ] `type Config = { name: string; port: number }` → struct with 2 fields
- [ ] `class DiskStorage implements StorageHelper` → impl block with `trait_type: StorageHelper`
- [ ] `class Base { }; class Child extends Base` → impl block with `trait_type: Base`
- [ ] `export class Foo` → visibility `pub`; `class Bar` → visibility `private`
- [ ] `constructor(private name: string, public age: number)` → struct with 2 fields, correct visibility
- [ ] `async function fetch(): Promise<Data>` → function with `is_async: true`, return type `Promise<Data>`
- [ ] `const handler = async () => {}` → function with `is_async: true`
- [ ] `import { Router } from 'express'; Router()` → `imported_package_methods: {"express::Router": ["Router"]}`
- [ ] `enum Status { Active, Inactive }` → enum with 2 variants
- [ ] `.js` file with `const X = require('pkg')` → import captured
- [ ] `package.json` parsed: name, version, dependencies
- [ ] Mixed TS + JS repo produces single unified crate entry
- [ ] `pytest tests/test_typescript_extractor.py` — all pass

---

### Phase 8 — C# / .NET Extractor
**Goal**: Full AST extraction for C# files, covering .NET project structures.

**Depends on**: Phase 1 (contract), Phase 4 (dispatcher)

**Grammar**: `tree-sitter-c-sharp`

#### Language-Specific Concepts

| C# Concept | Normalized To | Notes |
|---|---|---|
| `class Foo { ... }` | `StructNode` | Properties + fields. `public` = pub, `private`/`internal` = private |
| `record Foo(...)` | `StructNode` | Positional parameters → fields |
| `struct Foo { ... }` | `StructNode` | Value type struct, same extraction as class |
| `interface IFoo { ... }` | `TraitNode` | Method signatures as required methods |
| `class Foo : IBar, IBaz` | `ImplNode` | Multiple `trait_type` entries (one impl block per interface) |
| `abstract class` with `abstract` methods | `TraitNode` | Treated as interface equivalent |
| Method | `FunctionNode` / method in `ImplNode` | `async`, `static`, `virtual`, `override` as flags |
| Property `{ get; set; }` | Field in `StructNode` | Type + visibility extracted |
| `namespace Foo.Bar` | Module path prefix | Replaces file-path based module path |
| `using X;` | `imports[]` | |
| `X.Method()` | `imported_package_methods` | Scoped calls on imported types |
| `enum Foo { ... }` | `EnumNode` | Integer-backed enums |
| `[Attribute]` | `attributes[]` | `[Serializable]`, `[HttpGet]`, etc. |

#### Deliverables
- `extractors/csharp.py` implementing all `ExtractorBase` methods
- Namespace tracking: `namespace Foo.Bar` → module path becomes `Foo.Bar.ClassName`
- Property extraction: `public string Name { get; set; }` → field with type `string`
- Record parameter extraction: `record Foo(string Name, int Age)` → struct with 2 fields
- Abstract class detection: class with `abstract` keyword + abstract methods → `TraitNode`
- Multiple interface implementation: `class X : IA, IB` → 2 separate `ImplNode` entries
- Attribute capture: `[HttpGet]`, `[Authorize]`, `[Serializable]` → `attributes[]`
- `parse_manifest()` for `*.csproj`: project name, target framework, PackageReference deps
- Fixtures in `tests/fixtures/csharp/`
- Unit tests in `tests/test_csharp_extractor.py`

#### Verification Checkpoints
- [ ] `public class Config { public string Name { get; set; } public int Port { get; set; } }` → struct with 2 fields
- [ ] `record UserDto(string Name, int Age)` → struct with 2 fields
- [ ] `interface IStorageHelper { Task<byte[]> RetrieveAsync(); }` → trait with 1 required method
- [ ] `class DiskStorage : IStorageHelper` → impl block with `trait_type: IStorageHelper`
- [ ] `class MyService : IFoo, IBar` → 2 impl blocks, one per interface
- [ ] `abstract class BaseHandler { abstract Task Handle(); }` → trait with 1 required method
- [ ] `public async Task<Result> ProcessAsync(Request req)` → method with `is_async: true`, return type `Task<Result>`
- [ ] `[HttpGet("api/items")] public IActionResult Get()` → method with attribute `[HttpGet("api/items")]`
- [ ] `namespace Company.Project.Feature` → module path reflects namespace, not file path
- [ ] `using System.Collections.Generic; List<T>.Sort()` → captured in `imported_package_methods`
- [ ] `.csproj` parsed: project name, target framework (`net8.0`), PackageReference list
- [ ] `enum HttpMethod { Get, Post, Put, Delete }` → enum with 4 variants
- [ ] C#-only repo produces valid `ast.json`
- [ ] `pytest tests/test_csharp_extractor.py` — all pass

---

### Phase 9 — C / C++ Extractor
**Goal**: AST extraction for C and C++ files, covering structs, classes, functions, and
header relationships.

**Depends on**: Phase 1 (contract), Phase 4 (dispatcher)

**Grammar**: `tree-sitter-c` (`.c`, `.h`), `tree-sitter-cpp` (`.cpp`, `.hpp`, `.cc`, `.hh`, `.cxx`)

#### Language-Specific Concepts

| C/C++ Concept | Normalized To | Notes |
|---|---|---|
| `struct Foo { ... };` | `StructNode` | Fields with types. C has no visibility; C++ has `public`/`private`/`protected` |
| `class Foo { ... };` (C++) | `StructNode` | Default `private`. Sections tracked for visibility |
| `class Foo : public IBar` (C++) | `ImplNode` | Inheritance → `trait_type: IBar` |
| Pure virtual class (C++) | `TraitNode` | Class where all methods are `= 0` (pure virtual) |
| Free function `void foo()` | `FunctionNode` | |
| Class method `void Foo::bar()` (C++) | method in `ImplNode` | Out-of-class definitions linked to class |
| `#include "x.h"` / `#include <y>` | `imports[]` | Both local and system includes |
| `namespace foo { }` (C++) | Module path prefix | |
| `typedef` / `using` (C++) | `TypeAlias` | |
| `enum` / `enum class` (C++) | `EnumNode` | Scoped vs unscoped |
| `#define MACRO ...` | `MacroNode` | Name captured, body not expanded |
| `template<typename T>` (C++) | `generics` field | Captured as text |

#### Deliverables
- `extractors/cpp.py` implementing all `ExtractorBase` methods (handles both C and C++)
- Separate grammar loading: `tree-sitter-c` for `.c`/`.h`, `tree-sitter-cpp` for `.cpp`/`.hpp`/`.cc`
- C++ access specifier tracking: `public:`, `private:`, `protected:` sections → per-field visibility
- Pure virtual class detection: all methods `= 0` → `TraitNode`
- Template capture: `template<typename T, typename U>` → `generics: "<typename T, typename U>"`
- Out-of-class method definitions: `void Foo::bar() { }` → linked to `Foo` impl block
- Include capture: `#include "local.h"` and `#include <system>` → `imports[]`
- Namespace tracking: `namespace a::b` → module path prefix
- `parse_manifest()` for `CMakeLists.txt`: project name, `find_package()` deps (best-effort)
- Fixtures in `tests/fixtures/c/` and `tests/fixtures/cpp/`
- Unit tests in `tests/test_cpp_extractor.py`

#### Verification Checkpoints
- [ ] C: `struct Config { char* name; int port; };` → struct with 2 fields, both `pub` (C has no visibility)
- [ ] C: `void process(int* data, size_t len)` → function with 2 params
- [ ] C: `#include "config.h"` and `#include <stdio.h>` → both in `imports[]`
- [ ] C: `typedef unsigned int uint32;` → type alias
- [ ] C++: `class Storage { public: void store(); private: int data_; };` → struct with 1 pub method, 1 private field
- [ ] C++: `class IHandler { public: virtual void handle() = 0; };` → trait (pure virtual)
- [ ] C++: `class Worker : public IHandler { void handle() override; };` → impl block with `trait_type: IHandler`
- [ ] C++: `void Worker::handle() { }` (out-of-class) → linked to `Worker` impl block
- [ ] C++: `template<typename T> class Container { T data; };` → struct with `generics: "<typename T>"`
- [ ] C++: `namespace project::core { class Foo {}; }` → module path `project::core::Foo`
- [ ] C++: `enum class Color { Red, Green, Blue };` → enum with 3 variants
- [ ] C: `#define MAX_SIZE 1024` → macro with name `MAX_SIZE`
- [ ] `.h` file with both C structs and function declarations → all captured
- [ ] Mixed C + C++ project produces valid `ast.json`
- [ ] `pytest tests/test_cpp_extractor.py` — all pass

---

### Phase 10 — Packaging, CLI Polish & PyPI Release
**Goal**: Tool is installable by anyone in one command and behaves correctly on real-world repos.

**Depends on**: Phase 3 + Phases 5–9 (at least Rust + one other language verified)

#### Deliverables
- `pyproject.toml` finalized: version, description, license, classifiers, entry point
- Optional dependency groups: `[rust]`, `[go]`, `[python]`, `[typescript]`, `[cpp]`, `[csharp]`, `[all]`
- `--version` flag prints tool version
- Structured error messages: file not found, unsupported language, permission denied
- Missing grammar hint: `"tree-sitter-go not installed. Run: pip install ast-intel[go]"`
- `--quiet` suppresses progress output (for CI)
- `--debug` enables verbose per-file logging to stderr
- Exit codes: `0` success, `1` partial failure, `2` fatal error
- `README.md` install + quickstart section

#### Verification Checkpoints
- [ ] `pipx install ast-intel[all]` installs cleanly on Ubuntu 22.04, macOS 14, Windows WSL2
- [ ] `ast-intel --version` prints `ast-intel x.y.z`
- [ ] `ast-intel /nonexistent` exits with code `2` and clear error message
- [ ] `ast-intel /repo --lang go` without `tree-sitter-go` prints actionable install hint
- [ ] `ast-intel --quiet /repo` produces no stdout, only output files
- [ ] Published to PyPI test index: `pip install -i https://test.pypi.org/simple ast-intel` works
- [ ] Polyglot repo (Rust + Go + Python + TS + C# + C++) produces unified `ast.json`
- [ ] `ast-intel` on 3 different open-source repos completes without crash

---

### Phase 11 — Incremental Mode & Performance
**Goal**: Re-running on an unchanged repo is instant. Large repos (5,000+ files) complete in
under 60 seconds.

**Depends on**: Phase 3

#### Deliverables
- File hash (SHA-256) stored per file entry in `ast.json` under `meta.file_hashes`
- On re-run: load existing `ast.json`, compare hashes, skip unchanged files, re-parse only changed/new files, remove deleted files
- Cross-references rebuilt fully (operates on in-memory data, not files — fast)
- Cache stored at `{output_dir}/.ast-intel-cache.json`
- `--no-cache` flag: force full re-parse
- Benchmark script: `tests/benchmark.py` measures parse time on synthetic repos

#### Verification Checkpoints
- [ ] First run on Safeguard: ≤ 15 seconds
- [ ] Second run (no changes): ≤ 1 second
- [ ] Modify one file: only that file re-parsed (verify via `--debug` log: `"1 changed, N cached"`)
- [ ] Delete one file: its entries removed from `ast.json` on next run
- [ ] Add one new file: it appears in `ast.json` on next run
- [ ] Incremental output is **identical** to fresh output
- [ ] `--no-cache` ignores cache and re-parses everything
- [ ] `tests/benchmark.py` passes: 5,000 files in ≤ 60 seconds on 4-core machine

---

### Phase 12 — GitHub Action & CI Integration
**Goal**: `ast.json` is automatically generated on every PR with a diff summary.

**Depends on**: Phase 10

#### Deliverables
- `.github/actions/ast-intel/action.yml` — composite action wrapping `ast-intel` CLI
- Inputs: `repo-path`, `include`, `exclude`, `lang`, `output-path`, `fail-on-error`
- Outputs: `ast-json-path`, `summary-md-path`, `file-count`, `error-count`
- Example workflow: run on push to main, upload artifacts
- PR diff mode: compare `ast.json` from base vs PR branch, post summary as PR comment

#### Verification Checkpoints
- [ ] Action runs on `ubuntu-latest` runner without manual setup
- [ ] `ast.json` artifact uploaded and downloadable from Actions run
- [ ] PR comment posted: files changed, functions added/removed, types added/removed
- [ ] Action completes in ≤ 30 seconds for a 200-file repo
- [ ] `fail-on-error: true` causes non-zero exit when `error-count > 0`

---

### Phase 13 — VS Code Extension (Query Interface)
**Goal**: Developer can query `ast.json` from inside VS Code without leaving the editor.

**Depends on**: Phase 10

#### Deliverables
- VS Code extension: `ast-intel-vscode`
- Commands:
  - `AST Intel: Find Function` — fuzzy search `function_file_index`, opens file at definition
  - `AST Intel: Show Implementors` — cursor on trait name, shows all implementors
  - `AST Intel: Package Method Callers` — cursor on import, shows `package_method_index` hits
  - `AST Intel: CVE Blast Radius` — prompt for `package::Type::method`, list all files
  - `AST Intel: Regenerate Index` — runs CLI in terminal
- Status bar: `ast.json` freshness timestamp
- Auto-reload when `ast.json` changes on disk

#### Verification Checkpoints
- [ ] `Find Function poll_async_jobs` opens correct file
- [ ] `Show Implementors` on `StorageHelper` lists all 5+ implementors
- [ ] `CVE Blast Radius` for `bytes::BytesMut::with_capacity` returns 2 files
- [ ] Extension activates in ≤ 500 ms on 2.5 MB `ast.json`
- [ ] Degrades gracefully if `ast.json` missing: shows `"No AST index found. Run ast-intel."`
- [ ] Published to VS Code Marketplace

---

### Phase 14 — Deployment AST (Helm + Ansible)
**Goal**: Parse Helm charts and Ansible playbooks into a deployment AST with a resource
dependency graph, so that renaming a ConfigMap, Secret, or Service automatically surfaces
every downstream manifest that references it.

**Depends on**: Phase 3 (emitters). No tree-sitter needed — YAML parser + regex.

#### Why This Matters

Helm templates and Ansible playbooks have **invisible cross-dependencies** that no compiler
catches. Renaming `safeguard-redis-config` in one template silently breaks three Deployments
that reference it via `configMapKeyRef.name`. The deployment AST makes these edges explicit.

#### Deployment-Specific Concepts

| Source | Concept | Extracted As |
|---|---|---|
| `Chart.yaml` | Chart name, version, dependencies | `DeploymentCrate` metadata |
| `values.yaml` | Default values tree | `ValuesSchema` — key → type, default, path |
| `values/*.yaml` | Per-environment overrides | `ValuesOverride` — key → value, source file |
| `templates/*.yaml` | K8s resources | `Resource` — kind, name, labels, references |
| `{{ .Values.x.y }}` | Value binding | `ValueBinding` — template file → values key |
| `configMapKeyRef.name` | ConfigMap dependency | `ResourceEdge` — Deployment → ConfigMap |
| `secretKeyRef.name` | Secret dependency | `ResourceEdge` — Deployment → Secret |
| `volumeMounts` + `volumes` | PVC dependency | `ResourceEdge` — Deployment → PVC |
| `serviceName` in Ingress/NetworkPolicy | Service dependency | `ResourceEdge` — resource → Service |
| Ansible `*.yml` playbooks | Plays, tasks, modules | `PlaybookNode` — hosts, tasks, variables |
| Ansible `group_vars/` | Variable definitions | `VariableSource` — var name → file, environment |
| Ansible `helm` task | Chart deployment | `AnsibleHelmBridge` — playbook → chart + values file |

#### Cross-References Produced

```json
{
  "deployment_cross_references": {
    "resource_dependency_graph": {
      "Deployment:file-reconstruction": {
        "depends_on": [
          "ConfigMap:safeguard-redis-config",
          "Secret:safeguard-tls-secret",
          "Service:redis-master",
          "PVC:safeguard-data-pvc"
        ],
        "defined_in": "helm-charts/safeguard/templates/deployment-file-recon.yaml",
        "values_used": ["redis.clusterNodes", "image.repository", "image.tag"]
      },
      "ConfigMap:safeguard-redis-config": {
        "depended_by": [
          "Deployment:file-reconstruction",
          "Deployment:message-reconstruction",
          "Deployment:approval-engine"
        ],
        "defined_in": "helm-charts/safeguard/templates/configmap.yaml"
      }
    },

    "values_impact_index": {
      "redis.clusterNodes": {
        "defined_in": "helm-charts/safeguard/values.yaml",
        "overridden_in": [
          "helm-charts/safeguard/values/dev.yaml",
          "helm-charts/safeguard/values/prod.yaml"
        ],
        "consumed_by": [
          "templates/configmap.yaml -> ConfigMap:safeguard-redis-config",
          "templates/deployment.yaml -> env:REDIS_CLUSTER_NODES"
        ]
      }
    },

    "ansible_to_helm_bridge": {
      "playbooks/install-safeguard.yml": {
        "deploys_charts": ["safeguard", "safeguard-cert-management"],
        "values_overrides": ["helm-charts/safeguard/values/{{ env }}.yaml"]
      },
      "playbooks/k8s-redis-upgrade.yml": {
        "affects_resources": ["ConfigMap:safeguard-redis-config", "StatefulSet:redis"],
        "downstream_impact": [
          "Deployment:file-reconstruction",
          "Deployment:message-reconstruction",
          "Deployment:approval-engine"
        ]
      }
    },

    "port_conflict_index": {
      "8080": ["Deployment:file-reconstruction", "Deployment:approval-engine"],
      "9090": ["Deployment:telegram-release", "Service:prometheus"]
    },

    "secret_usage_index": {
      "safeguard-tls-secret": {
        "created_by": "helm-charts/safeguard-cert-management/templates/certificate.yaml",
        "consumed_by": [
          "Deployment:file-reconstruction",
          "Deployment:approval-engine",
          "Deployment:telegram-release"
        ]
      }
    }
  }
}
```

#### What Is Reliably Parseable vs What To Skip

| Parseable (v1) | Parser | Reliability |
|---|---|---|
| `values.yaml` full tree | YAML | ✓ Exact |
| `Chart.yaml` metadata + dependencies | YAML | ✓ Exact |
| `{{ .Values.x.y }}` references | Regex `\.Values\.[\w.]+` | ✓ High |
| `configMapKeyRef.name`, `secretKeyRef.name` | YAML path query | ✓ Exact |
| `volumes[].persistentVolumeClaim.claimName` | YAML path query | ✓ Exact |
| Ansible task `module` + `params` | YAML | ✓ Exact |
| Ansible `group_vars/*.yml` variables | YAML | ✓ Exact |

| Skip (unreliable without engine) | Why |
|---|---|
| `{{ include "helper" . }}` | Requires Helm template engine |
| `{{ if .Values.feature.enabled }}` | Conditional resources — runtime dependent |
| Ansible `when:` conditions | Runtime conditionals |
| Ansible `with_items` / `loop` | Dynamic resource generation |
| Jinja2 filters `{{ var \| b64encode }}` | Need Jinja engine |

#### Deliverables
- `extractors/helm.py` — parses `Chart.yaml`, `values.yaml`, `values/*.yaml`, `templates/*.yaml`
  - Resource extraction: kind, name, namespace, labels, annotations
  - Reference extraction: `configMapKeyRef`, `secretKeyRef`, `volumeMounts`, `serviceName`
  - Values binding: regex scan for `{{ .Values.* }}` → maps template file to values keys
  - Chart dependency extraction from `Chart.yaml` `dependencies:` block
- `extractors/ansible.py` — parses playbooks, inventories, `group_vars/`
  - Play extraction: name, hosts, roles, tasks
  - Task extraction: name, module, params, `when` condition (as text, not evaluated)
  - Variable source mapping: which `group_vars` file defines which variable
  - Helm bridge: tasks using `kubernetes.core.helm` module → chart + values file links
- `core/indexer.py` extended with deployment cross-reference passes:
  - Pass D1: `resource_dependency_graph` — resource → resources it depends on / depended by
  - Pass D2: `values_impact_index` — values key → defined in, overridden in, consumed by
  - Pass D3: `ansible_to_helm_bridge` — playbook → charts, values files, affected resources
  - Pass D4: `port_conflict_index` — port → all resources exposing that port
  - Pass D5: `secret_usage_index` — secret → created by, consumed by
- Fixtures in `tests/fixtures/helm/` and `tests/fixtures/ansible/`
- Unit tests in `tests/test_helm_extractor.py` and `tests/test_ansible_extractor.py`

#### Verification Checkpoints
- [ ] `Chart.yaml` parsed: name `safeguard`, version, dependencies list
- [ ] `values.yaml` produces full key tree: `redis.clusterNodes`, `image.repository`, etc.
- [ ] `values/dev.yaml` detected as override: keys mapped to base `values.yaml` keys
- [ ] Template with `{{ .Values.redis.clusterNodes }}` → `values_impact_index` entry for `redis.clusterNodes`
- [ ] Template with `configMapKeyRef.name: safeguard-redis-config` → Deployment depends on that ConfigMap
- [ ] Template with `secretKeyRef.name: safeguard-tls-secret` → Deployment depends on that Secret
- [ ] Template with `persistentVolumeClaim.claimName: data-pvc` → Deployment depends on that PVC
- [ ] `resource_dependency_graph["ConfigMap:safeguard-redis-config"]["depended_by"]` lists 3+ Deployments
- [ ] Renaming a ConfigMap in one template → `depended_by` index immediately shows all affected Deployments
- [ ] Ansible `install-safeguard.yml` with `kubernetes.core.helm` task → bridge links to `safeguard` chart
- [ ] Ansible `group_vars/all.yml` with `redis_password: x` → variable source correctly mapped
- [ ] `port_conflict_index` detects two resources on same port
- [ ] `secret_usage_index["safeguard-tls-secret"]["created_by"]` points to cert-management chart
- [ ] Safeguard `deploy/` directory: all 4 Helm charts parsed, all playbooks parsed, zero errors
- [ ] `pytest tests/test_helm_extractor.py` — all pass
- [ ] `pytest tests/test_ansible_extractor.py` — all pass