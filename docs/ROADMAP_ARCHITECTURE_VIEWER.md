# Plan: Service Architecture Viewer (new visualizer)

A new self-contained **`architecture.html`** (emitted via `-f arch`, never touching `graph.html`) that replaces the "hairball" problem with the right diagram per question. Since you chose **single-repo-first**, "services" are derived from **projects/crates** (e.g. `REM.DataPlane.Api`), auto-upgrading to real `SERVICE` nodes when a merged graph is detected. Render is **hybrid**: an interactive **vis.js module overview** + **Mermaid** for the UML/sequence/detail/deployment views. Mental model = the **C4 architecture model** (Context → Component → Dynamic → Deployment), which maps cleanly onto your existing graph data.

The core trick for testability: do **all** graph derivation in Python (a pure `ArchModel`), pre-build the Mermaid source strings + vis.js payload, embed as JSON, and keep the page's JS thin (just renders).

## Phase 1 — Architecture model (pure Python, the brains)

1. `ast_intel/formatters/_service_graph.py` → `build_arch_model(graph) -> ArchModel`: derive modules (crate nodes → folder fallback → `SERVICE` nodes if merged), assign every node to a module, aggregate inter-module dependency edges (`IMPORTS`/`CALLS`/`DEPENDS_ON` counts), collect per-module routes (grouped by `route_group` prefix), controllers (`route → HANDLES → handler → METHOD_OF → class`), outbound `HTTP_CALL`s, cross-service interactions (`CALLS_SERVICE` + unresolved → "External"), and deployment topology (IaC edges).

## Phase 2 — Mermaid builders (pure string fns) *(depends on Ph1)*

2. `ast_intel/formatters/_arch_mermaid.py` → `class_diagram(model, mod)` (UML), `detail_flowchart(model, mod)`, `cross_service_sequence(model)`, `deployment_topology(model)`, `service_map_mermaid(model)` (export). All bounded with caps + "+N more".

## Phase 3 — Formatter + HTML shell *(depends on Ph1–2)*

3. `graph_arch_html_formatter.py` → `ArchHtmlFormatter.write(graph, path, *, offline=False)` (matches existing formatter signature).
4. `_arch_html_template.py` → left rail (module picker + 5 view tabs), main panel renders the vis.js overview or a Mermaid diagram, click-a-module → detail. Mermaid via CDN, `--offline` inlines a vendored `_mermaid.min.js`.

## Phase 4 — Wire-in *(depends on Ph3; parallel with Ph2)*

5. `cli.py`: add `OutputFormat.ARCH = "arch"`; `emitter.py`: dispatch `arch → architecture.html` and include it in `all`.

## Phase 5 — Tests + real validation *(depends on Ph1–4)*

6. `tests/test_arch_formatter.py` + validate on the MCFS repo.

## The 5 views

| Tab | Engine | Shows |
|---|---|---|
| **Overview / Service map** | vis.js | modules (sized by symbols) + aggregated inter-module edges + `CALLS_SERVICE` + an "External" node for unresolved outbound HTTP — a *small* graph, no hairball |
| **Per-service API (UML)** | Mermaid `classDiagram` | controllers as classes, routes as methods (`+GET /api/... handler`), inheritance to `ControllerBase` |
| **Cross-service sequence** | Mermaid `sequenceDiagram` | `ModuleA ->> ModuleB: METHOD /path` from `CALLS_SERVICE`/flow/outbound |
| **Per-service detail** | Mermaid flowchart | routes grouped by URL prefix → handlers + outbound calls |
| **Deployment topology** | Mermaid flowchart | service → image → k8s deployment → service/ingress (only if IaC present) |

## Relevant files

- `ast_intel/formatters/_service_graph.py` *(new)* — core `ArchModel` derivation; reuses `route_group`/`flow` hyperedges, `CALLS_SERVICE`/`HANDLES`/`EXPOSES`, IaC `DEPLOYS`/`IMAGE_OF`/`ROUTES_TO`.
- `ast_intel/formatters/_arch_mermaid.py`, `graph_arch_html_formatter.py`, `_arch_html_template.py`, `_mermaid.min.js` *(new)*.
- `ast_intel/cli.py` (L103) — `OutputFormat` enum + `--format` help.
- `ast_intel/core/emitter.py` — dispatch (~L115–203) + `all`.
- Reference patterns: `ast_intel/formatters/graph_html_formatter.py` (offline-inline + template injection), `ast_intel/formatters/graph_mermaid_formatter.py` (subgraph clustering).

## Verification

1. Unit tests on `build_arch_model` (modules from crates; route grouping; controller extraction; sequence from `CALLS_SERVICE` + outbound; deployment from IaC) on synthetic single-repo **and** merged graphs.
2. Mermaid-builder string assertions (e.g. SovereignViews `classDiagram` contains all 5 routes).
3. HTML smoke test: output contains module names + `mermaid` blocks; `--offline` inlines the bundle.
4. Real run: scan MCFS to a temp dir with `-f arch` → confirm modules = projects, the SovereignViews UML with 5 routes, deployment topology from its k8s (2 deployments / 2 services), and the HTML opens & renders.
5. `make check` (lint + tests).

## Decisions

- Scope = single-repo modules first (auto-detect merged `SERVICE` nodes); engine = vis.js overview + Mermaid; all 5 views in v1.
- v1 uses **existing** graph edges only — no new extraction. Diagrams are **bounded** (caps + "+N more") to stay readable.
- Excluded: editing `graph.html`/existing formatters, new graph extractors, anything beyond bounded static diagrams.

## Resolved decisions

1. **Format name** — `-f arch`, producing a self-contained **`architecture.html`**. It coexists with the existing `-f html` → `graph.html` (the two are independent viewers).
2. **Included in `-f all`** — yes. A normal `-f all` scan emits `architecture.html` alongside `ast.json`, `summary.md`, `graph.json`, and `graph.html`.
3. **Single-repo module granularity** — project/crate level: one module per project (e.g. each `.csproj` / `Cargo.toml` / Go module), with a top-level-folder fallback when no project manifests are detected.
