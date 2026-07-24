# AST_INTEL — Phase 3: Cross-Service Architecture Intelligence

> Generated 2026-04-30 · Architect Document  
> Prerequisite: Phase 1 (Spans, Call Graph, Confidence, Rationale, Graph Output, Analysis) and Phase 2 features are complete.

---

## Table of Contents

1. [Feature 15 — Graph Merge (Multi-Repo Stitching)](#feature-15--graph-merge-multi-repo-stitching)
2. [Feature 16 — HTTP Route & Endpoint Extraction](#feature-16--http-route--endpoint-extraction)
3. [Feature 17 — HTTP Client Call Detection](#feature-17--http-client-call-detection)
4. [Feature 18 — Cross-Service Edge Resolution](#feature-18--cross-service-edge-resolution)
5. [Feature 19 — API Contract Parsing (OpenAPI / gRPC / GraphQL)](#feature-19--api-contract-parsing-openapi--grpc--graphql)
6. [Feature 20 — Service Topology Manifest](#feature-20--service-topology-manifest)
7. [Feature 21 — Frontend ↔ Backend Linking](#feature-21--frontend--backend-linking)
8. [Dependency Graph Between Features](#dependency-graph-between-features)
9. [Suggested Implementation Order](#suggested-implementation-order)

---

## The Problem

Modern systems are not a single repo. A typical architecture looks like:

```
┌─────────────────┐     HTTP/gRPC      ┌──────────────────┐     HTTP/gRPC      ┌──────────────────┐
│  frontend        │ ─────────────────→ │  api-gateway      │ ─────────────────→ │  user-service     │
│  (React/Next.js) │                    │  (Express/Go)     │                    │  (Python/FastAPI) │
└─────────────────┘                    └──────────────────┘                    └──────────────────┘
                                              │                                        │
                                              │ HTTP                                   │ gRPC
                                              ▼                                        ▼
                                       ┌──────────────────┐                    ┌──────────────────┐
                                       │  order-service    │                    │  auth-service     │
                                       │  (Java/Spring)    │                    │  (Rust/Actix)     │
                                       └──────────────────┘                    └──────────────────┘
```

Today, `ast-intel scan` produces one `CodeGraph` per repo. The HTTP calls between services are **invisible** — they appear as string arguments to `requests.get()` or `fetch()`, not as edges in the graph. This phase adds the ability to:

1. Merge multiple repo graphs into one unified architecture graph
2. Detect HTTP route definitions (server side) and HTTP client calls (client side)
3. Match them to create cross-service `CALLS_HTTP` edges
4. Parse API contracts (OpenAPI, gRPC, GraphQL) as first-class nodes
5. Declare service topology for deterministic linking

---

## Feature 15 — Graph Merge (Multi-Repo Stitching)

### What

A new `ast-intel merge` CLI command that combines two or more `graph.json` files into a single unified graph. Node IDs are prefixed with the service name to avoid collisions.

### Why

This is the **foundational feature** — everything else in this phase builds on having a single graph that spans multiple repos. Without merge, each repo is an island. With merge, all existing tools (`explain`, `impact`, `community`, MCP server) work across service boundaries.

**Current state**: If you run `ast-intel scan` on 5 repos, you get 5 separate `graph.json` files. There is no way to combine them. The `impact` command can only show blast radius within a single repo, even though changing a user-service endpoint breaks the order-service too.

### How It Works

#### Node ID Namespacing

Current scheme: `"src/user.py::UserService"`

Merged scheme: `"user-service::src/user.py::UserService"`

The service name prefix comes from either:
- The crate/package name in the graph's `meta` field
- An explicit `--name` flag: `ast-intel merge --name user-service graph1.json ...`
- The directory name of the graph's `workspace_root`

#### Merge Algorithm

```
Input:  graph_A.json, graph_B.json, graph_C.json
Output: merged.json

1. For each input graph:
   a. Read graph and determine service name (meta.crate or --name or dirname)
   b. Prefix all node IDs with "{service}::"
   c. Prefix edge source/target IDs with "{service}::"
   d. Add a SERVICE node: id="{service}", kind=SERVICE, label="{service}"
   e. Rewrite all FILE node IDs to include service prefix

2. Combine all nodes and edges into one CodeGraph
3. Add DEPENDS_ON edges between SERVICE nodes (from manifest deps if detectable)
4. Deduplicate: if the same external dep appears in multiple graphs, create one shared node
5. Write merged graph to output
```

### Design Decision: Prefix vs Namespace Node

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Prefix all IDs with `service::` | Simple, deterministic, no collisions | Longer IDs, breaks direct comparison with original graph | **Chosen** |
| B) Add a `service` field to `GraphNode` | Cleaner IDs | Every tool must filter by service, collision risk remains | Rejected |
| C) Separate graphs with virtual edges | No ID changes | Two data structures to query, complex | Rejected |

### Phased Implementation

#### Phase 15A — Model Extension

**Files**: `ast_intel/models/graph_model.py`

1. Add `SERVICE = "service"` to `NodeKind` enum
2. Add `service: str = ""` field to `GraphNode` — carries the original service name for filtering
3. Add `origin_id: str = ""` field to `GraphNode` — the original pre-merge node ID (for mapping back)

#### Phase 15B — Merge Engine

**Files**: new `ast_intel/core/_merge.py`

```python
def merge_graphs(
    graphs: list[tuple[str, CodeGraph]],  # (service_name, graph) pairs
) -> CodeGraph:
    """Merge multiple CodeGraphs into one with namespaced IDs."""
```

Key implementation details:
- Remap all node IDs: `id → "{service}::{id}"`
- Remap all edge source/target: same prefix
- Create one `SERVICE` node per input graph
- Each file node gets a `BELONGS_TO` edge → its service node
- Preserve all original edges (they now reference prefixed IDs)
- Merge `meta` fields: combine crate lists, sum file counts

#### Phase 15C — CLI Command

**Files**: `ast_intel/cli.py`

```bash
ast-intel merge \
  --name user-service ~/user-service/ast_output/graph.json \
  --name order-service ~/order-service/ast_output/graph.json \
  --name frontend ~/frontend/ast_output/graph.json \
  --out ~/architecture/merged-graph.json
```

Alternative simpler syntax (auto-detect names from graph meta):
```bash
ast-intel merge \
  ~/user-service/ast_output/graph.json \
  ~/order-service/ast_output/graph.json \
  --out merged.json
```

#### Phase 15D — MCP + Query Integration

**Files**: `ast_intel/core/_query_engine.py`, `ast_intel/mcp_server.py`

1. `QueryEngine` already works on any `CodeGraph` — merged graphs work out of the box
2. Add optional `--service` filter to `search`, `files`, `community` commands
3. MCP: `search_symbols` gets an optional `service` parameter

#### Phase 15E — Tests

**Files**: new `tests/test_merge.py`

1. Merge two synthetic graphs → assert node count = sum, all IDs prefixed
2. Assert SERVICE nodes created for each input
3. Assert edges reference prefixed IDs correctly
4. Assert `QueryEngine.search()` works on merged graph
5. Merge with duplicate node labels across services → no collision
6. Round-trip: merge → write JSON → read JSON → assert identical

**Effort estimate**: ~3-4 days  
**Depends on**: Nothing — can start immediately

---

## Feature 16 — HTTP Route & Endpoint Extraction

### What

Detect HTTP route definitions (server-side endpoints) during AST extraction and expose them as first-class nodes in the graph. This covers all major web frameworks across our supported languages.

### Why

Route definitions are the **public API surface** of a microservice. They are the nodes that client calls need to connect to. Without extracting routes, cross-service linking is impossible because we can't answer "what does this service expose?"

**Current state**: A `@app.route("/api/users")` decorator appears in our AST as a decorator string on a `FunctionNode`. The URL path `/api/users` and HTTP method `GET` are not structured data — they're buried inside decorator arguments.

### How It Works

#### Frameworks to Detect

| Language | Framework | Route Pattern | tree-sitter node structure |
|-|-|-|-|
| **Python** | FastAPI | `@app.get("/users")` | `decorated_definition` → `decorator` → `call_expression` with method + string arg |
| **Python** | Flask | `@app.route("/users", methods=["GET"])` | Same pattern, method in `keyword_argument` |
| **Python** | Django | `path("users/", views.user_list)` in `urls.py` | `call_expression` with string + attribute arg |
| **TypeScript** | Express | `router.get("/users", handler)` | `call_expression` → `member_expression` (method) + `string` arg |
| **TypeScript** | Next.js | `export default function GET(req)` in `app/api/users/route.ts` | File-based routing: path from filesystem, method from export name |
| **TypeScript** | NestJS | `@Get("/users")` decorator | `decorator` → `call_expression` |
| **Java** | Spring | `@GetMapping("/users")` / `@RequestMapping(...)` | `annotation` with method + value |
| **Go** | net/http | `http.HandleFunc("/users", handler)` | `call_expression` |
| **Go** | Gin | `r.GET("/users", handler)` | `call_expression` → `selector_expression` |
| **Rust** | Actix | `#[get("/users")]` | `attribute_item` |
| **C#** | ASP.NET | `[HttpGet("users")]` / `[Route("api/users")]` | `attribute` |

#### New AST Node

```python
@dataclass(frozen=True, slots=True)
class RouteNode:
    """An HTTP endpoint definition.

    Attributes:
        path: URL path pattern (e.g., "/api/users/{id}").
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, or "*" for all).
        handler: Name of the handler function/method.
        framework: Framework that defines this route (e.g., "fastapi", "express").
        span: Source location of the route definition.
    """
    path: str
    method: str          # "GET", "POST", "PUT", "DELETE", "PATCH", "*"
    handler: str         # Name of handler function
    framework: str       # "fastapi", "flask", "express", "spring", etc.
    span: Span | None = None
```

#### New Graph Entities

- `NodeKind.ROUTE = "route"` — an HTTP endpoint node
- `EdgeRelation.HANDLES = "handles"` — Route → Function (handler)
- `EdgeRelation.EXPOSES = "exposes"` — Service/File → Route

### Design Decision: Route Extraction Strategy

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Detect via decorator/annotation pattern matching in each extractor | High accuracy per framework, uses existing tree-sitter infrastructure | Must implement per framework (~10 patterns) | **Chosen** |
| B) Regex on raw source code | Simple, language-agnostic | Fragile, can't resolve handler references, false positives | Rejected |
| C) Run frameworks and introspect route tables | Perfect accuracy | Requires framework installed + app runnable — totally impractical | Rejected |

### Phased Implementation

#### Phase 16A — Model Changes

**Files**: `ast_intel/models/ast_node.py`, `ast_intel/models/graph_model.py`

1. Add `RouteNode` dataclass to `ast_node.py`
2. Add `routes: list[RouteNode] = field(default_factory=list)` to `FileAST`
3. Add `ROUTE = "route"` to `NodeKind`
4. Add `HANDLES = "handles"` and `EXPOSES = "exposes"` to `EdgeRelation`

#### Phase 16B — Route Extraction Module

**Files**: new `ast_intel/extractors/_routes.py`

A shared module that detects route patterns across languages:

```python
# Framework-specific detectors
def detect_fastapi_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_flask_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_express_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_spring_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_gin_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_actix_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_aspnet_routes(node: Node, src: bytes) -> list[RouteNode]: ...
def detect_nextjs_routes(file_path: str) -> list[RouteNode]: ...  # file-based routing
```

Each detector inspects the tree-sitter AST for framework-specific patterns:
- Decorator/annotation name matches (`@app.get`, `@GetMapping`, `#[get(...)]`)
- First string argument = URL path
- Handler = decorated function name or second argument

#### Phase 16C — Wire Into Extractors

**Files**: `python.py`, `typescript.py`, `java.py`, `go.py`, `rust.py`, `csharp.py`

In each extractor's `extract()` method, after normal extraction, call the route detectors:

```python
# At end of extract():
routes = detect_fastapi_routes(root, source) + detect_flask_routes(root, source)
file_ast.routes = routes
```

#### Phase 16D — Graph Builder Integration

**Files**: `ast_intel/core/graph_builder.py`

For each `RouteNode` in `FileAST.routes`:
1. Create a `GraphNode(kind=NodeKind.ROUTE, label="GET /api/users", ...)`
2. Emit `HANDLES` edge: Route → handler function node
3. Emit `EXPOSES` edge: File → Route
4. Store `path`, `method`, and `framework` in `properties`

#### Phase 16E — Tests

**Files**: new `tests/test_route_extraction.py`, new fixture files

1. Python fixtures: FastAPI app with 5+ routes, Flask app with 3+ routes
2. TypeScript fixtures: Express router, Next.js API routes
3. Java fixture: Spring `@RestController` with `@GetMapping`/`@PostMapping`
4. Assert `RouteNode` fields populated correctly
5. Assert graph contains `ROUTE` nodes and `HANDLES`/`EXPOSES` edges

**Effort estimate**: ~4-5 days  
**Depends on**: Nothing — can start immediately (but pairs with Feature 15 for cross-service value)

---

## Feature 17 — HTTP Client Call Detection

### What

Detect HTTP client calls (outgoing requests to other services) during AST extraction and expose them as structured data. This is the **client-side** counterpart to Feature 16 (server-side routes).

### Why

To build cross-service edges, we need both ends: the server defines `GET /api/users`, the client calls `requests.get("http://user-service/api/users")`. Feature 16 gives us the server side. This feature gives us the client side.

**Current state**: `requests.get(url)` is captured as a `CallEdge` with callee `"requests::get"`, but the URL argument — which identifies the target service — is not extracted.

### How It Works

#### Libraries to Detect

| Language | Library/Pattern | Call Pattern |
|-|-|-|
| **Python** | `requests` | `requests.get(url)`, `requests.post(url, ...)` |
| **Python** | `httpx` | `httpx.get(url)`, `client.get(url)` |
| **Python** | `aiohttp` | `session.get(url)` |
| **TypeScript** | `fetch` | `fetch("/api/users")`, `fetch(url, {method: "POST"})` |
| **TypeScript** | `axios` | `axios.get("/api/users")`, `axios.post(url)` |
| **TypeScript** | `ky` / `got` | `ky.get(url)` |
| **Java** | `HttpClient` | `HttpRequest.newBuilder(URI.create(url))` |
| **Java** | `RestTemplate` | `restTemplate.getForObject(url, ...)` |
| **Java** | `WebClient` | `webClient.get().uri(url)` |
| **Go** | `net/http` | `http.Get(url)`, `http.NewRequest("GET", url, ...)` |
| **Go** | `resty` | `client.R().Get(url)` |
| **Rust** | `reqwest` | `reqwest::get(url)`, `client.get(url)` |
| **C#** | `HttpClient` | `httpClient.GetAsync(url)` |

#### New AST Node

```python
@dataclass(frozen=True, slots=True)
class HttpCallNode:
    """An outgoing HTTP client call.

    Attributes:
        url: URL string or pattern (may contain variables: "/api/users/{id}").
        method: HTTP method ("GET", "POST", etc.) or "UNKNOWN".
        library: Client library name ("requests", "fetch", "axios", etc.).
        caller: Name of the enclosing function/method.
        span: Source location.
    """
    url: str
    method: str
    library: str
    caller: str
    span: Span | None = None
```

#### New Graph Entities

- `NodeKind.HTTP_CALL = "http_call"` — an outgoing HTTP client call node
- `EdgeRelation.CALLS_HTTP = "calls_http"` — Function → HTTP_CALL (initiates request)

### Design Decision: URL Extraction Depth

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Extract only string literal URLs | High confidence — the URL is right there in the source | Misses dynamic URLs (`f"/api/{resource}"`, template strings) | Too limited |
| B) Extract string literals + f-string/template patterns | Captures most URLs, can normalize variables to `{param}` | More complex parsing | **Chosen** |
| C) Trace variable assignments to resolve URLs | Most complete | Requires dataflow analysis — massive scope creep | Rejected for v1 |

For f-strings and template literals:
- `f"/api/users/{user_id}"` → extracted as `/api/users/{param}`
- `` `/api/users/${userId}` `` → extracted as `/api/users/{param}`
- Concatenation (`"/api/" + "users"`) → extracted as `/api/users` (best-effort)

### Phased Implementation

#### Phase 17A — Model Changes

**Files**: `ast_intel/models/ast_node.py`, `ast_intel/models/graph_model.py`

1. Add `HttpCallNode` dataclass
2. Add `http_calls: list[HttpCallNode] = field(default_factory=list)` to `FileAST`
3. Add `HTTP_CALL = "http_call"` to `NodeKind`
4. Add `CALLS_HTTP = "calls_http"` to `EdgeRelation`

#### Phase 17B — HTTP Call Detection Module

**Files**: new `ast_intel/extractors/_http_calls.py`

```python
def detect_http_calls(
    root: Node,
    source: bytes,
    language: str,
) -> list[HttpCallNode]:
    """Walk the AST for HTTP client call patterns.
    
    Detects library-specific patterns (requests.get, fetch, axios, etc.)
    and extracts the URL argument and HTTP method.
    """
```

URL extraction strategy:
1. Find `call_expression` nodes where the function matches known HTTP client patterns
2. Extract the first argument (the URL)
3. If it's a string literal → use directly
4. If it's an f-string → replace interpolations with `{param}`
5. If it's a template literal → same normalization
6. If it's a variable → store `"<dynamic>"` with `confidence=AMBIGUOUS`
7. Determine HTTP method from the function name (`get`, `post`, `put`, `delete`)

#### Phase 17C — Wire Into Extractors

**Files**: each language extractor

Same pattern as routes: call `detect_http_calls(root, source, "python")` at end of extraction.

#### Phase 17D — Graph Builder Integration

**Files**: `ast_intel/core/graph_builder.py`

For each `HttpCallNode`:
1. Create `GraphNode(kind=NodeKind.HTTP_CALL, label="GET /api/users", ...)`
2. Emit `CALLS_HTTP` edge: caller function → HTTP_CALL node
3. Store `url`, `method`, `library` in `properties`

#### Phase 17E — Tests

**Files**: new `tests/test_http_calls.py`, fixture files

1. Python fixtures with `requests.get()`, `httpx.post()`, aiohttp
2. TypeScript fixtures with `fetch()`, `axios.get()`
3. Assert URL extraction from literals, f-strings, template literals
4. Assert HTTP method detection
5. Assert `<dynamic>` for unresolvable URLs

**Effort estimate**: ~4-5 days  
**Depends on**: Nothing (but designed to pair with Feature 16)

---

## Feature 18 — Cross-Service Edge Resolution

### What

A resolution engine that matches `HTTP_CALL` nodes (client calls, Feature 17) against `ROUTE` nodes (server endpoints, Feature 16) across service boundaries in a merged graph (Feature 15), emitting `CALLS_SERVICE` edges.

### Why

This is the **bridge** — the feature that actually creates visible cross-service relationships in the graph. Features 15-17 build the raw data; Feature 18 connects it.

Without this: you have routes and calls as isolated nodes in the graph.  
With this: `ast-intel explain "OrderService"` shows "called by frontend::OrderList via GET /api/orders".

### How It Works

#### Matching Algorithm

```
Input:  merged CodeGraph with ROUTE nodes and HTTP_CALL nodes

For each HTTP_CALL node:
  1. Normalize the URL: strip base URL, extract path pattern
     "/api/users/123"     → "/api/users/{param}"
     "http://user-svc:8080/api/users" → "/api/users"
  
  2. Find ROUTE nodes whose path matches:
     - Exact match: "/api/users" == "/api/users"
     - Parameter match: "/api/users/{id}" ≈ "/api/users/{param}"
     - Prefix match: "/api/users/123/orders" starts with "/api/users"
  
  3. Also match on HTTP method: GET→GET, POST→POST, *→any
  
  4. Emit CALLS_SERVICE edge:
     Source: the function containing the HTTP_CALL
     Target: the function handling the ROUTE
     Properties: { url, method, confidence, client_service, server_service }
```

#### URL Normalization

| Raw URL in Code | Normalized Pattern |
|-|-|
| `"http://user-service:8080/api/users"` | `/api/users` |
| `"https://api.example.com/v2/users"` | `/v2/users` |
| `f"/api/users/{user_id}"` | `/api/users/{param}` |
| `"/api/users/" + id` | `/api/users/{param}` |
| `process.env.USER_SERVICE_URL + "/users"` | `/users` |
| `"/api/users?page=1"` | `/api/users` |

#### Confidence Scoring

| Match Quality | Confidence | Score |
|-|-|-|
| Exact path + exact method | `EXTRACTED` | 1.0 |
| Path with parameter wildcards + exact method | `INFERRED` | 0.85 |
| Path match, method unknown | `INFERRED` | 0.7 |
| Prefix match only | `AMBIGUOUS` | 0.5 |
| Dynamic URL, guessed from service name | `AMBIGUOUS` | 0.3 |

### Design Decision: Matching Precision

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Exact path matching only | Zero false positives | Misses parameterized routes and normalized URLs | Too strict |
| B) Normalized path + parameter wildcards | Good balance of precision and recall | Some false positives on similar paths | **Chosen** |
| C) NLP-based semantic URL matching | Handles synonyms ("users" ≈ "accounts") | Overengineered, introduces LLM dependency | Rejected |

### Phased Implementation

#### Phase 18A — New Edge Types

**Files**: `ast_intel/models/graph_model.py`

1. Add `CALLS_SERVICE = "calls_service"` to `EdgeRelation`
   - Docstring: "Cross-service call: HTTP client → HTTP route handler (resolved by URL matching)"

#### Phase 18B — URL Normalizer

**Files**: new `ast_intel/core/_url_matcher.py`

```python
def normalize_url(raw_url: str) -> str:
    """Strip scheme, host, port, query params. Normalize path params to {param}."""

def match_route(
    call_url: str,
    call_method: str,
    route_path: str,
    route_method: str,
) -> tuple[bool, float]:
    """Return (matches, confidence_score) for a call-route pair."""

def resolve_cross_service_edges(
    graph: CodeGraph,
) -> list[GraphEdge]:
    """Find all HTTP_CALL → ROUTE matches and emit CALLS_SERVICE edges."""
```

#### Phase 18C — Integration into Merge

**Files**: `ast_intel/core/_merge.py`

After merging nodes/edges, call `resolve_cross_service_edges(merged_graph)` and append the resulting edges.

#### Phase 18D — Tests

1. Exact match: `fetch("/api/users")` ↔ `@app.get("/api/users")` → confidence 1.0
2. Param match: `fetch("/api/users/123")` ↔ `@app.get("/api/users/{id}")` → confidence 0.85
3. No match: `fetch("/api/orders")` against a graph with no order routes → no edge
4. Method mismatch: `requests.post("/api/users")` ↔ `@app.get("/api/users")` → no edge
5. Multiple matches: same URL, different services → emit edges to all with note

**Effort estimate**: ~3-4 days  
**Depends on**: Feature 15 (merge), Feature 16 (routes), Feature 17 (HTTP calls)

---

## Feature 19 — API Contract Parsing (OpenAPI / gRPC / GraphQL)

### What

Parse API contract files as first-class sources — extract operations, types, and schemas as `ROUTE` nodes and `STRUCT` nodes. This provides **high-confidence** endpoint definitions without needing to detect framework-specific patterns.

### Why

Contracts are the **canonical truth** of a service's API. A Spring Boot service with 50 routes might not all be detectable from annotation patterns alone, but the `openapi.yaml` describes every single one. gRPC `.proto` files define exact service interfaces. GraphQL schemas describe the query surface.

If a team maintains API contracts (many enterprise teams mandate this), we can produce near-perfect cross-service edges without relying on fuzzy URL matching.

### How It Works

#### File Types to Parse

| Contract Type | Files | What We Extract |
|-|-|-|
| **OpenAPI / Swagger** | `openapi.yaml`, `openapi.json`, `swagger.yaml`, `swagger.json` | Operations: `(path, method, operationId, request/response types)` |
| **gRPC / Protobuf** | `*.proto` | Services, RPCs: `(service_name, rpc_name, request_type, response_type)` |
| **GraphQL** | `schema.graphql`, `*.graphql` | Queries, Mutations, Subscriptions: `(operation_name, args, return_type)` |

#### Mapping to Graph Entities

| Contract Element | GraphNode Kind | Example |
|-|-|-|
| OpenAPI operation | `ROUTE` | `GET /api/users` → RouteNode |
| OpenAPI schema | `STRUCT` | `User` schema → StructNode with fields |
| gRPC service | `TRAIT` | `UserService` → TraitNode |
| gRPC rpc | `ROUTE` | `GetUser` → RouteNode (method="GRPC") |
| gRPC message | `STRUCT` | `GetUserRequest` → StructNode |
| GraphQL query | `ROUTE` | `users` query → RouteNode (method="QUERY") |
| GraphQL type | `STRUCT` | `User` type → StructNode |

### Design Decision: Parsing Approach

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) YAML/JSON parsing (stdlib) for OpenAPI, regex for proto/graphql | Zero new deps, Python stdlib handles YAML/JSON | Fragile for proto/graphql syntax | **Chosen for OpenAPI** |
| B) `openapi-spec-validator` + `grpcio-tools` + `graphql-core` | Schema validation, accurate parsing | Three new deps | **Chosen for gRPC/GraphQL** (optional extras) |
| C) tree-sitter grammars for proto/graphql | Consistent with our approach | tree-sitter-protobuf and tree-sitter-graphql are immature | Rejected |

### Phased Implementation

#### Phase 19A — OpenAPI Parser

**Files**: new `ast_intel/extractors/_openapi.py`

```python
def parse_openapi(file_path: Path) -> list[RouteNode]:
    """Parse an OpenAPI/Swagger spec and return route definitions."""
```

Uses `yaml.safe_load()` (stdlib) to parse. Walks `paths` → `operations` → extracts path, method, operationId, parameters, request/response schemas.

#### Phase 19B — Protobuf Parser

**Files**: new `ast_intel/extractors/_proto.py`

```python
def parse_proto(file_path: Path) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse a .proto file and return services/rpcs as routes and messages as structs."""
```

Regex-based extraction of `service`, `rpc`, and `message` blocks. gRPC RPCs become `RouteNode` with `method="GRPC"`.

#### Phase 19C — GraphQL Schema Parser

**Files**: new `ast_intel/extractors/_graphql.py`

```python
def parse_graphql_schema(file_path: Path) -> tuple[list[RouteNode], list[StructNode]]:
    """Parse a .graphql schema and return queries/mutations as routes and types as structs."""
```

#### Phase 19D — Workspace Integration

**Files**: `ast_intel/core/workspace.py`, `ast_intel/core/manifest_parser.py`

1. Add `.yaml`, `.json`, `.proto`, `.graphql` to recognized contract extensions (separate from source extensions)
2. Detect contract files during workspace discovery
3. Feed to contract parsers instead of language extractors

#### Phase 19E — Tests

1. Parse a real-world OpenAPI spec (petstore) → assert correct route count and paths
2. Parse a `.proto` file with 2 services → assert service + rpc nodes
3. Parse a GraphQL schema → assert query/mutation/type nodes
4. End-to-end: contract routes link to HTTP client calls via Feature 18

**Effort estimate**: ~4-5 days  
**Depends on**: Feature 16 (RouteNode model) for consistent representation

---

## Feature 20 — Service Topology Manifest

### What

Support a `.ast-intel-services.yaml` file that declares the microservice topology — which repos exist, their service names, base URLs, and communication patterns. This enables **deterministic** cross-service linking without fuzzy URL matching.

### Why

URL matching (Feature 18) works well for many cases but fails when:
- URLs are constructed dynamically from environment variables
- Internal service names differ from URL paths
- Services communicate through a gateway that rewrites paths
- gRPC/message-queue communication has no URL at all

A topology manifest provides the **ground truth** that can't be inferred from code alone.

### How It Works

#### File Format

```yaml
# .ast-intel-services.yaml
version: 1

services:
  user-service:
    repo: ./user-service              # relative path to repo root
    base_url: /api/users              # base URL path this service owns
    aliases:                          # alternative hostnames used in client code
      - user-svc
      - user-service.internal
      - localhost:8001
    contracts:                        # optional: contract files relative to repo
      - docs/openapi.yaml
    language: python                  # optional: restrict language detection

  order-service:
    repo: ./order-service
    base_url: /api/orders
    aliases:
      - order-svc
    depends_on:                       # explicit dependency declarations
      - user-service                  # "I call user-service"
      - payment-service

  frontend:
    repo: ./web-app
    type: frontend                    # changes how client calls are interpreted
    api_base: /api                    # all fetch() calls are relative to this

  payment-service:
    repo: ./payment-service
    base_url: /api/payments
    protocol: grpc                    # non-HTTP communication
```

#### What the Manifest Enables

| Without Manifest | With Manifest |
|-|-|
| `requests.get("http://user-svc:8001/users")` → URL doesn't match any route because host is stripped | `user-svc` alias maps to `user-service` → `CALLS_SERVICE` edge emitted with confidence 0.95 |
| gRPC calls are invisible (no URL to match) | `depends_on: [user-service]` + `protocol: grpc` → `CALLS_SERVICE` edge at service level |
| Frontend `fetch("/api/users")` could go to any service | `frontend.api_base: /api` + `user-service.base_url: /api/users` → deterministic match |

### Design Decision: Manifest vs Auto-Detection

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Manifest only (user-declared topology) | Deterministic, handles all edge cases | Requires user effort to write and maintain | **Chosen as primary** |
| B) Auto-detect from docker-compose / k8s manifests | Zero user effort | Fragile, many deployment patterns, complex parsing | Deferred (future enhancement) |
| C) Require both | Maximum accuracy | Too much friction for adoption | Rejected |

### Phased Implementation

#### Phase 20A — Manifest Parser

**Files**: new `ast_intel/core/_service_manifest.py`

```python
@dataclass
class ServiceDefinition:
    name: str
    repo_path: Path
    base_url: str
    aliases: list[str]
    contracts: list[str]
    depends_on: list[str]
    protocol: str           # "http", "grpc", "graphql"
    service_type: str       # "backend", "frontend", "gateway"
    language: str | None

def load_service_manifest(manifest_path: Path) -> list[ServiceDefinition]:
    """Parse .ast-intel-services.yaml and return service definitions."""
```

Uses `yaml.safe_load()` (stdlib). Validates structure, resolves relative paths.

#### Phase 20B — Multi-Repo Scan Command

**Files**: `ast_intel/cli.py`

```bash
ast-intel scan-services .ast-intel-services.yaml \
  --format graph-json \
  --analyze \
  --out ~/architecture/
```

This command:
1. Reads the service manifest
2. Runs `ast-intel scan` on each service's repo
3. Merges the graphs (Feature 15)
4. Resolves cross-service edges using alias mapping (Feature 18 enhanced)
5. Adds explicit `DEPENDS_ON` edges from `depends_on` declarations
6. Writes the merged graph

#### Phase 20C — Enhanced URL Matching

**Files**: `ast_intel/core/_url_matcher.py`

Enhance the matcher from Feature 18 to use service manifest data:
- Map hostnames/aliases to service names before URL matching
- Use `base_url` to scope which routes belong to which service
- Honor `protocol` field for non-HTTP services

#### Phase 20D — Tests

1. Parse a valid manifest → assert correct `ServiceDefinition` objects
2. Invalid manifest (missing required field) → assert helpful error
3. Resolve alias-based URL: `fetch("http://user-svc/users")` with alias config → match
4. `depends_on` creates SERVICE→SERVICE edges even without detectable calls
5. End-to-end: 3 fixture repos + manifest → merged graph with cross-service edges

**Effort estimate**: ~3-4 days  
**Depends on**: Feature 15 (merge), Feature 18 (edge resolution)

---

## Feature 21 — Frontend ↔ Backend Linking

### What

Specialized detection for frontend→backend communication patterns. This is the most common and most detectable cross-service boundary — the frontend always calls the backend via known client-side patterns.

### Why

Frontend→backend is often the **first** cross-service boundary teams want to visualize. It's also the **easiest** to detect because:
- Frontend code uses a small set of client libraries (`fetch`, `axios`, SWR, React Query, Angular HttpClient)
- URL paths are usually string literals (not dynamically constructed)
- The API surface is often typed (TypeScript types matching backend DTOs)

The dedicated handling here goes beyond generic HTTP call detection (Feature 17) to capture frontend-specific patterns like React hooks, API client modules, and generated types.

### How It Works

#### Frontend-Specific Patterns

| Pattern | Example | What We Extract |
|-|-|-|
| **`fetch()` calls** | `fetch("/api/users")` | `HttpCallNode` with URL |
| **axios instances** | `api.get("/users")` where `api = axios.create({baseURL: "/api"})` | Resolve `baseURL` + path |
| **React Query / SWR** | `useQuery({queryKey: ["users"], queryFn: () => fetch("/api/users")})` | URL from the fetch inside queryFn |
| **API client modules** | `apiClient.getUsers()` where `apiClient.ts` defines `getUsers = () => fetch("/api/users")` | Follow one level of indirection |
| **Generated API clients** | `import { UsersApi } from './generated/api'` | OpenAPI-generated clients map to contract |
| **Angular HttpClient** | `this.http.get<User[]>("/api/users")` | `HttpCallNode` with URL + response type |

#### Type Matching (Bonus Signal)

If both frontend and backend use TypeScript/Python types:
- Backend defines `class User { name: str; email: str }` and returns it from `GET /api/users`
- Frontend has `interface User { name: string; email: string }` used to type the response

We can emit a `SIMILAR_TO` edge between these types (they share field names), strengthening the frontend↔backend connection in the graph.

### Design Decision: Depth of Frontend Analysis

| Option | Pros | Cons | Decision |
|-|-|-|-|
| A) Detect direct `fetch`/`axios` calls only | Simple, high confidence | Misses abstracted API clients | Good enough for v1 |
| B) A + resolve one level of API client indirection | Catches `apiClient.getUsers()` → `fetch("/api/users")` | Requires simple dataflow (follow variable assignment) | **Chosen** |
| C) Full interprocedural analysis of frontend API layer | Complete coverage | Immense complexity, type inference needed | Rejected |

### Phased Implementation

#### Phase 21A — Frontend Detection in TypeScript Extractor

**Files**: `ast_intel/extractors/typescript.py`, `ast_intel/extractors/_http_calls.py`

1. Detect `fetch()`, `axios.*()`, `ky.*()` calls with URL extraction
2. Detect `axios.create({ baseURL })` and resolve when the instance is used
3. Detect React Query / SWR hooks and extract URL from the query function
4. Detect Angular `HttpClient` calls

#### Phase 21B — API Client Module Detection

**Files**: `ast_intel/extractors/_http_calls.py`

```python
def detect_api_client_module(
    file_ast: FileAST,
    all_files: dict[str, FileAST],
) -> list[HttpCallNode]:
    """Detect patterns like:
    
    // api/client.ts
    export const getUsers = () => fetch("/api/users")
    
    // pages/users.tsx  
    import { getUsers } from "../api/client"
    getUsers()  // → resolve to fetch("/api/users")
    """
```

This runs during indexing (cross-file), not during extraction.

#### Phase 21C — Type Similarity Between Frontend/Backend

**Files**: `ast_intel/core/_similarity.py`

In a merged graph, if a frontend `STRUCT` and a backend `STRUCT` share ≥60% of field names:
- Emit `SIMILAR_TO` edge with `properties: { "cross_boundary": "true" }`
- This shows up in the `find_similar` MCP tool

#### Phase 21D — Tests

1. React app fixture with `fetch()` calls → assert `HttpCallNode` extracted
2. API client module with `axios.create()` → assert base URL resolved
3. Merged frontend+backend graph → assert `CALLS_SERVICE` edges
4. Frontend `User` type similar to backend `User` struct → assert `SIMILAR_TO` edge

**Effort estimate**: ~3-4 days  
**Depends on**: Feature 17 (HTTP call detection), Feature 15 (merge)

---

## Dependency Graph Between Features

```
Feature 15 (Graph Merge) ◄────────── Foundation for everything
    │
    │
    ├──── Feature 16 (Route Extraction)
    │         │
    │         │     Feature 17 (HTTP Client Calls)
    │         │         │
    │         └────┬────┘
    │              │
    │              ▼
    │     Feature 18 (Cross-Service Edge Resolution) ◄── The bridge
    │              │
    │              │
    │     Feature 19 (API Contract Parsing)
    │         │    │
    │         │    │   Enriches routes with contract data
    │         │    │
    │         └────┤
    │              │
    │     Feature 20 (Service Topology Manifest)
    │              │
    │              │   Provides ground-truth for matching
    │              │
    │              ▼
    │     Feature 21 (Frontend ↔ Backend Linking)
    │              │
    │              │   Specialized patterns + type similarity
    │              │
    └──────────────┘
```

**Critical path**: 15 → (16 + 17 parallel) → 18 → 20  
**Parallel tracks**: 16 and 17 are independent. 19 and 21 can run in parallel after 18.

---

## Suggested Implementation Order

| Sprint | Feature | Est. Days | Cumulative | Milestone |
|--------|---------|-----------|------------|-----------|
| **Sprint 1** | Feature 15: Graph Merge | 3-4 | 3-4 | `ast-intel merge` command works |
| **Sprint 1** | Feature 16: Route Extraction | 4-5 | 7-9 | Routes appear as ROUTE nodes in graph |
| **Sprint 2** | Feature 17: HTTP Client Calls | 4-5 | 11-14 | HTTP calls captured with URLs |
| **Sprint 2** | Feature 18: Cross-Service Resolution | 3-4 | 14-18 | `CALLS_SERVICE` edges in merged graph |
| **Sprint 3** | Feature 19: API Contract Parsing | 4-5 | 18-23 | OpenAPI/proto/GraphQL as first-class nodes |
| **Sprint 3** | Feature 20: Service Topology Manifest | 3-4 | 21-27 | `.ast-intel-services.yaml` + `scan-services` |
| **Sprint 4** | Feature 21: Frontend ↔ Backend | 3-4 | 24-31 | Full frontend→backend linking |

**Total estimated effort**: ~24-31 working days (5-7 weeks)

### New Files Created

| File | Feature | Purpose |
|------|---------|---------|
| `ast_intel/core/_merge.py` | 15 | Graph merge engine |
| `ast_intel/extractors/_routes.py` | 16 | Route detection (all frameworks) |
| `ast_intel/extractors/_http_calls.py` | 17 | HTTP client call detection |
| `ast_intel/core/_url_matcher.py` | 18 | URL normalization + route matching |
| `ast_intel/extractors/_openapi.py` | 19 | OpenAPI/Swagger parser |
| `ast_intel/extractors/_proto.py` | 19 | Protobuf service parser |
| `ast_intel/extractors/_graphql.py` | 19 | GraphQL schema parser |
| `ast_intel/core/_service_manifest.py` | 20 | `.ast-intel-services.yaml` parser |
| `tests/test_merge.py` | 15 | Merge tests |
| `tests/test_route_extraction.py` | 16 | Route detection tests |
| `tests/test_http_calls.py` | 17 | HTTP client call tests |
| `tests/test_url_matcher.py` | 18 | URL matching tests |
| `tests/test_contract_parsers.py` | 19 | Contract parsing tests |
| `tests/test_service_manifest.py` | 20 | Service manifest tests |
| `tests/test_frontend_backend.py` | 21 | Frontend↔backend linking tests |

### Modified Files

| File | Features | Changes |
|------|----------|---------|
| `ast_intel/models/ast_node.py` | 16, 17 | `RouteNode`, `HttpCallNode`, fields on `FileAST` |
| `ast_intel/models/graph_model.py` | 15, 16, 17, 18 | `SERVICE`, `ROUTE`, `HTTP_CALL` node kinds; `HANDLES`, `EXPOSES`, `CALLS_HTTP`, `CALLS_SERVICE` edge relations |
| `ast_intel/core/graph_builder.py` | 16, 17 | Build route/http_call graph nodes + edges |
| `ast_intel/extractors/python.py` | 16, 17 | Route + HTTP call detection for Flask/FastAPI/requests/httpx |
| `ast_intel/extractors/typescript.py` | 16, 17, 21 | Route + HTTP call detection for Express/fetch/axios + frontend patterns |
| `ast_intel/extractors/java.py` | 16, 17 | Spring annotations + HttpClient calls |
| `ast_intel/extractors/go.py` | 16, 17 | Gin/net-http routes + http.Get calls |
| `ast_intel/extractors/rust.py` | 16, 17 | Actix routes + reqwest calls |
| `ast_intel/extractors/csharp.py` | 16, 17 | ASP.NET routes + HttpClient calls |
| `ast_intel/cli.py` | 15, 20 | `merge` and `scan-services` subcommands |
| `ast_intel/core/workspace.py` | 19 | Detect contract files during discovery |
| `ast_intel/core/_similarity.py` | 21 | Cross-boundary type similarity |

---

*This document is the roadmap for cross-service architecture intelligence. It builds on the existing single-repo graph and extends AST_INTEL to handle the most common distributed system pattern: microservices communicating over HTTP/gRPC.*
