# Ponytail — Deep Dive & Integration Analysis for AST-Intel

## What Is Ponytail?

**Ponytail** (`DietrichGebert/ponytail`) is an AI agent behavior-shaping plugin that embodies the philosophy of "the laziest senior developer in the room." It is not a code library or framework — it is a **prompt engineering ruleset** delivered as a plugin/skill for AI coding agents (Claude Code, Codex, GitHub Copilot CLI, Cursor, Windsurf, Gemini CLI, etc.).

**Tagline:** *"He says nothing. He writes one line. It works."*

- **Stars:** 65.6k+ (trending)
- **License:** MIT
- **Languages:** JavaScript (55%), Python (44%)
- **Version:** 4.8.4 (14 releases, very active)
- **Contributors:** 48
- **Supports:** 16+ AI agent hosts

---

## How It Works

### The "Laziness Ladder"

Before writing any code, the agent stops at the **first rung that holds**:

```
1. Does this need to exist?        → No: skip it (YAGNI)
2. Already in this codebase?       → Reuse it, don't rewrite
3. Stdlib does it?                 → Use it
4. Native platform feature?        → Use it
5. Installed dependency?           → Use it
6. One line?                       → One line
7. Only then: the minimum that works
```

**Key principle:** The ladder runs *after* the agent understands the problem — it reads the code, traces the flow, *then* picks the laziest correct rung.

### Safety Guardrails (Not Lazy About)

- Trust-boundary validation
- Error handling that prevents data loss
- Security (input sanitization, auth)
- Accessibility
- Explicit user requests

### Modes

| Mode | Intensity |
|------|-----------|
| `lite` | Gentle nudges toward simplicity |
| `full` | Full laziness ladder enforced (default) |
| `ultra` | Maximum minimalism — "when the codebase has wronged you personally" |
| `off` | Disabled |

---

## Architecture & Engineering

### Component Map

```
ponytail/
├── hooks/                    # Lifecycle hooks (SessionStart, SubagentStart)
│   ├── ponytail-activate.js      # Injects ruleset on session start
│   ├── ponytail-instructions.js  # Core instruction builder
│   ├── ponytail-config.js        # Mode resolution (env, config file)
│   ├── ponytail-subagent.js      # Propagates rules to spawned subagents
│   └── ponytail-mode-tracker.js  # Runtime mode switching
├── skills/                   # Slash commands as agent skills
│   ├── ponytail/                 # Core activation skill
│   ├── ponytail-review/          # /ponytail-review — diff over-engineering audit
│   ├── ponytail-audit/           # /ponytail-audit — whole-repo audit
│   ├── ponytail-debt/            # /ponytail-debt — deferred shortcut ledger
│   ├── ponytail-gain/            # /ponytail-gain — measured impact scoreboard
│   └── ponytail-help/            # /ponytail-help — quick reference
├── ponytail-mcp/             # MCP server (serves ruleset as prompt/tool)
│   ├── index.js                  # MCP server (stdio, @modelcontextprotocol/sdk)
│   ├── instructions.js           # Pure instruction builder (unit-testable)
│   └── package.json              # Depends on @modelcontextprotocol/sdk + zod
├── pi-extension/             # Pi agent harness adapter
├── commands/                 # Slash command implementations
├── benchmarks/               # Reproducible promptfoo benchmarks
├── .cursor/rules/            # Cursor adapter
├── .windsurf/rules/          # Windsurf adapter
├── .clinerules/              # Cline adapter
├── .github/copilot-instructions.md  # GitHub Copilot adapter
├── .kiro/steering/           # Kiro adapter
├── AGENTS.md                 # Universal instruction file (Codex, CodeWhale, etc.)
└── gemini-extension.json     # Gemini CLI / Antigravity adapter
```

### MCP Server Implementation

The `ponytail-mcp` server is deliberately minimal:

```javascript
// Exposes:
// 1. Prompt "ponytail" — returns ruleset as user message
// 2. Tool "ponytail_instructions" — same text + structuredContent
server.registerPrompt("ponytail", ...);
server.registerTool("ponytail_instructions", ...);
```

- Read-only, stateless
- Mode resolution via `PONYTAIL_DEFAULT_MODE` env var or `~/.config/ponytail/config.json`
- Same instruction builder shared across all adapters (hooks, MCP, Pi extension)

### Hooks Architecture

- **SessionStart hook:** Injects the ruleset into system context at session start
- **SubagentStart hook:** Propagates ruleset into spawned subagents (so child agents are also "lazy")
- **Mode tracker:** Watches for `/ponytail lite|full|ultra|off` and hot-swaps intensity

---

## Benchmark Results (Real Agentic Workloads)

Measured on headless Claude Code sessions editing `tiangolo/full-stack-fastapi-template` (FastAPI + React). 12 feature tickets, n=4, Haiku 4.5:

| vs no-skill baseline | LOC | Tokens | Cost | Time | Safe |
|---|--:|--:|--:|--:|--:|
| **ponytail** | **-54%** | **-22%** | **-20%** | **-27%** | **100%** |
| caveman (terse-prose control) | -20% | +7% | +3% | +2% | 100% |
| "YAGNI + one-liners" prompt | -33% | -14% | -21% | -30% | 95% |

**Key findings:**
- Date picker: 404 → 23 lines (-94%) — agent uses `<input type="date">` instead of building a custom component
- ponytail is the **only arm that cuts every metric** while maintaining 100% safety
- The bare "one-liner" prompt dropped a security guard once (path traversal check)
- On irreducible code (backend CRUD), all arms converge — ponytail doesn't invent fake savings

---

## Advantages

| Advantage | Detail |
|-----------|--------|
| **Dramatic code reduction** | 54% average, up to 94% on over-build traps |
| **Cost savings** | 20% cheaper (fewer tokens generated and processed) |
| **Latency improvement** | 27% faster completions |
| **Safety-preserving** | Never drops validation, security, or error handling |
| **Universal agent support** | 16+ hosts from one repo via adapters |
| **Zero config** | Works from `AGENTS.md` in project root |
| **Lightweight** | Pure instruction injection, no runtime overhead |
| **Auditable** | `/ponytail-review` and `/ponytail-audit` commands to catch over-engineering |
| **Graduated intensity** | lite/full/ultra lets teams tune strictness |
| **Reproducible benchmarks** | Open benchmark with promptfoo, anyone can verify claims |

---

## Disadvantages & Limitations

| Limitation | Detail |
|------------|--------|
| **One model tested** | Benchmark only covers Haiku 4.5; bigger models may behave differently |
| **Heuristic, not static analysis** | The "reuse" rung requires the LLM to actually *find* existing code — it can miss things |
| **No codebase awareness** | Rung 2 ("already in codebase?") depends on the agent's context window — if the helper is in an unloaded file, it won't be reused |
| **Not for greenfield architecture** | When you *need* 500 lines of new code (new service, new module), ponytail may under-build |
| **Prompt sensitivity** | Effectiveness depends on model compliance with the instruction; reasoning models that spend tokens deliberating the ladder may negate speed gains |
| **No persistent memory** | Doesn't remember what it deferred across sessions (only tracks via `ponytail:` comments) |
| **No semantic graph** | Cannot structurally verify "already exists" — relies on LLM's best-effort search |
| **Potential under-engineering** | In `ultra` mode, teams might defer too much debt |

---

## Relevance to AST-Intel MCP

### The Core Synergy

Ponytail's **biggest weakness** is AST-Intel's **core competency**:

> Ponytail Rung 2: "Does it already exist in this codebase? Reuse it, don't rewrite."

Ponytail *asks* the agent to check. AST-Intel *actually knows the answer* — structurally, definitively, with zero false negatives.

### Why This Matters

| Ponytail's Problem | AST-Intel's Solution |
|-------------------|---------------------|
| "Is there already a helper for this?" — LLM guesses based on context window | `search_symbols` / `find_similar` — returns all matching functions structurally |
| "Which dependency already handles this?" — LLM checks `package.json` by text | `get_dependencies` — returns the full dependency graph with actual usage |
| "Can I reuse the existing pattern?" — LLM hopes it's in visible files | `get_community` — returns functionally related symbols across the whole codebase |
| "What gets impacted if I touch this?" — LLM can't know | `get_impact` — returns the blast radius structurally |
| "Is this a one-liner wrapper over stdlib?" — LLM guesses | `explain_symbol` — shows degree, callers, callees, role |

---

## How to Leverage Ponytail in AST-Intel

### Strategy 1: Ponytail-Aware Response Shaping in MCP Tools

Expose a `ponytail_mode` configuration in AST-Intel's MCP server. When active, tool responses include **actionable laziness hints**:

```python
# Example: get_dependencies response with ponytail mode
{
    "node": {"id": "UserService.create_user", ...},
    "calls": [...],
    "ponytail_hint": {
        "rung": 2,
        "message": "UserService.validate_email already exists at auth/validators.py:45. Reuse it.",
        "existing_symbol": "validate_email"
    }
}
```

### Strategy 2: Dedicated `find_reusable` MCP Tool

A new tool that directly answers Ponytail's Rung 2:

```
Tool: find_reusable
Input: { "intent": "validate email format", "scope": "this codebase" }
Output: {
    "matches": [
        { "symbol": "validate_email", "file": "auth/validators.py", "confidence": 0.95 },
        { "symbol": "EmailValidator.check", "file": "utils/validators.py", "confidence": 0.82 }
    ],
    "stdlib_alternative": "re.fullmatch(r'...', email)",
    "ponytail_recommendation": "Rung 2: reuse auth/validators.py:validate_email"
}
```

### Strategy 3: Embed Ponytail Instructions as an MCP Prompt

Ship a `ponytail` prompt resource from AST-Intel's MCP server, combining the laziness ladder with graph-aware context:

```python
@server.register_prompt("ponytail_with_graph")
async def ponytail_prompt(symbol: str):
    context = engine.get_context(symbol)
    return f"""
    {PONYTAIL_RULESET}

    Graph context for {symbol}:
    - Callers: {context.explanation.callers}
    - Similar functions: {[n.label for n in context.similar]}
    - Community peers: {[n.label for n in context.community_peers]}

    Apply the laziness ladder with this structural knowledge.
    """
```

### Strategy 4: `/ponytail-audit` Powered by the Code Graph

Instead of relying on the LLM to *guess* which code is duplicated or over-engineered, use AST-Intel's `find_similar` + community detection to *structurally identify*:

- Duplicate implementations (same signature, different locations)
- Overly complex symbols (high cyclomatic complexity, low reuse)
- Wrapper functions that add nothing over their single callee
- Unused abstractions (0 dependents)

### Strategy 5: Integrate Ponytail Ruleset into `.github/copilot-instructions.md`

For AST-Intel repos, ship a combined instruction that tells agents:

1. **Always query the code graph before writing new code** (AST-Intel)
2. **Apply the laziness ladder to the graph results** (Ponytail philosophy)
3. **Mark deferred shortcuts with `ponytail:` comments** (debt tracking)

---

## Implementation Roadmap

> **Constraint:** We are NOT integrating or importing ponytail. We use it purely as a knowledge base — a proven validation that "laziness-first" agent behavior works. Everything below is built natively in AST-Intel, powered by our code graph.

---

### Phase 1: Graph-Aware Agent Instructions (Native Laziness Ladder)

**Inspiration from ponytail:** Their "laziness ladder" reduces code by 54% — but it relies on the LLM guessing whether something exists. We build the same philosophy with structural proof.

**What we build:**

A native instruction system embedded in AST-Intel's `.github/copilot-instructions.md` and agent configuration that tells agents to query our graph before writing anything new.

**The AST-Intel Laziness Protocol (our own, not ponytail's):**

```markdown
Before writing new code, follow this protocol:

1. STOP — Does this need to exist?
   → If the task is configuration, documentation, or an already-solved problem, skip it.

2. QUERY the graph — `search_symbols` + `find_similar`
   → If a matching symbol exists, REUSE it. The graph is authoritative.

3. CHECK dependencies — `get_dependencies`
   → If an installed library already exposes this, USE it.

4. CHECK stdlib coverage
   → If the language stdlib handles it, USE it.

5. ASSESS impact — `get_impact`
   → If the change touches many dependents, prefer the SMALLEST correct diff.

6. Only then: write the minimum that works.
   → Mark intentional shortcuts with `# minimal: <reason>` comments.
```

**Key difference from ponytail:** Our Rung 2 is not "hope the LLM finds it" — it's "call `search_symbols` and get a definitive answer." The graph eliminates false negatives.

**Deliverables:**
- [ ] `.github/copilot-instructions.md` with the AST-Intel Laziness Protocol
- [ ] `AGENTS.md` for Codex/CodeWhale/Claude Code compatibility
- [ ] `.cursor/rules/ast-intel-minimal.md` for Cursor users
- [ ] Documentation on how the protocol maps to MCP tool calls

**Success metric:** Agents using AST-Intel MCP + this protocol make fewer redundant implementations (measurable via `find_similar` showing no new duplicates after a coding session).

**Effort:** Low — text only, no code changes to the MCP server.

---

### Phase 2: Proactive Reuse Signals in MCP Responses

**Inspiration from ponytail:** Their "comprehension-first guard" ensures the agent reads before writing. We go further — our tools proactively surface reuse opportunities in every response.

**What we build:**

An optional `reuse_signals` field in MCP tool responses. When an agent queries `get_context`, `get_dependencies`, or `explain_symbol`, the response automatically includes structurally similar symbols the agent should consider before writing new code.

**Technical implementation:**

```python
# In mcp_server.py — extend existing tool handlers

def _enrich_with_reuse_signals(
    engine: QueryEngine,
    target_node: GraphNode,
    max_signals: int = 3,
) -> list[dict[str, Any]]:
    """Find symbols structurally similar to target that could be reused."""
    signals = []

    # 1. Find symbols with same signature pattern in different files
    similar = engine.find_similar(target_node.id, top_k=5)
    for node in similar:
        if node.file != target_node.file:
            signals.append({
                "type": "similar_exists",
                "symbol": node.label,
                "file": node.file,
                "line": node.span.start_line if node.span else None,
                "suggestion": f"Consider reusing {node.label} instead of creating a new implementation.",
            })

    # 2. Check if this wraps a single stdlib/dependency call (unnecessary wrapper)
    deps = engine.get_dependencies(target_node.id)
    if deps and len(deps.calls) == 1 and not deps.imports:
        sole_callee = deps.calls[0]
        signals.append({
            "type": "thin_wrapper",
            "wraps": sole_callee.label,
            "suggestion": f"This is a thin wrapper over {sole_callee.label}. Call it directly.",
        })

    # 3. Check community peers doing the same thing
    community = engine.get_community(target_node.id)
    if community:
        peer_labels = [n.label for n in community.members if n.id != target_node.id]
        if peer_labels:
            signals.append({
                "type": "community_peers",
                "peers": peer_labels[:5],
                "suggestion": "These symbols are in the same functional cluster. Check for overlap.",
            })

    return signals[:max_signals]
```

**Response shape (example `get_context` with signals):**

```json
{
    "node": {"id": "UserService.validate_input", "kind": "method", "file": "services/user.py"},
    "explanation": { "...existing fields..." },
    "dependencies": { "...existing fields..." },
    "reuse_signals": [
        {
            "type": "similar_exists",
            "symbol": "InputValidator.validate",
            "file": "core/validators.py",
            "line": 23,
            "suggestion": "Consider reusing InputValidator.validate instead of creating a new implementation."
        },
        {
            "type": "community_peers",
            "peers": ["sanitize_input", "normalize_payload", "clean_request_data"],
            "suggestion": "These symbols are in the same functional cluster. Check for overlap."
        }
    ]
}
```

**Configuration:**

```python
# Enable/disable via environment variable or server config
ASTINTEL_REUSE_SIGNALS = "on"  # "on" | "off" | "verbose"
```

**Deliverables:**
- [ ] `_enrich_with_reuse_signals()` helper in `mcp_server.py`
- [ ] Integration into `get_context`, `explain_symbol`, `get_dependencies` handlers
- [ ] `ASTINTEL_REUSE_SIGNALS` env var for opt-in/opt-out
- [ ] Tests covering signal generation for: duplicates, thin wrappers, community peers

**Success metric:** When an agent calls `get_context` before writing code, the reuse signals catch >80% of cases where a reusable symbol exists.

**Effort:** Medium — modifies existing MCP tool response shapes, adds one helper function.

---

### Phase 3: `find_reusable` — Dedicated Reuse Discovery Tool

**Inspiration from ponytail:** Their Rung 2 is the highest-impact rung (date picker: 404→23 lines). But it's purely LLM intuition. We build a tool that gives a **structural, ranked answer** to "does something like this already exist?"

**What we build:**

A new MCP tool `find_reusable` that takes a natural-language intent OR a symbol signature and returns ranked candidates from the codebase graph that could satisfy the need without writing new code.

**Technical implementation:**

```python
# New tool registration in mcp_server.py

@server.tool("find_reusable")
async def find_reusable(
    intent: str,              # "validate email", "parse CSV", "retry with backoff"
    context_file: str = "",   # File the agent is currently editing (for scope relevance)
    kind_filter: str = "",    # "function" | "class" | "method" | "" (any)
) -> list[TextContent]:
    """
    Search the code graph for existing symbols that match the described intent.
    Returns ranked candidates the agent should consider before writing new code.

    Use BEFORE implementing any new function, class, or utility.
    """
    candidates = []

    # Strategy 1: Label/name similarity search
    # Tokenize intent into likely symbol name fragments
    name_hits = engine.search_symbols(
        query=intent,
        kind=kind_filter or None,
        top_k=10,
    )

    # Strategy 2: Structural similarity (if we have a reference symbol)
    # Find symbols with similar call patterns, signatures, or community membership
    if context_file:
        file_symbols = engine.list_file_symbols(context_file)
        # Find peers of the symbols in the current file
        for sym in file_symbols[:3]:
            similar = engine.find_similar(sym.id, top_k=5)
            name_hits.extend(similar)

    # Strategy 3: Dependency check — does an installed dep already expose this?
    dep_hits = _search_dependency_exports(engine, intent)

    # Deduplicate and rank
    ranked = _rank_reuse_candidates(
        name_hits=name_hits,
        dep_hits=dep_hits,
        intent=intent,
        context_file=context_file,
    )

    # Build response
    result = {
        "intent": intent,
        "candidates_found": len(ranked),
        "recommendation": _build_recommendation(ranked),
        "candidates": [
            {
                "symbol": c.node.label,
                "file": c.node.file,
                "line": c.node.span.start_line if c.node.span else None,
                "kind": c.node.kind.value,
                "relevance_score": round(c.score, 3),
                "reason": c.reason,  # "name match" | "structural similar" | "dependency export"
            }
            for c in ranked[:5]
        ],
    }

    return _text(result)


def _build_recommendation(candidates: list) -> str:
    """Generate a one-line recommendation based on what was found."""
    if not candidates:
        return "Nothing found. Proceed with minimal implementation."
    top = candidates[0]
    if top.score > 0.9:
        return f"Strong match: reuse {top.node.label} at {top.node.file}:{top.node.span.start_line}."
    if top.score > 0.6:
        return f"Possible match: check {top.node.label} — it may already do what you need."
    return "Weak matches only. Consider implementing, but review candidates first."
```

**Tool annotation:**

```python
{
    "name": "find_reusable",
    "description": "Before writing new code, search the code graph for existing symbols that already do what you need. Returns ranked reuse candidates. Call this FIRST when implementing any new function or class.",
    "inputSchema": {
        "intent": {"type": "string", "description": "What you want to accomplish, e.g. 'validate email format'"},
        "context_file": {"type": "string", "description": "File you're currently editing (for relevance ranking)"},
        "kind_filter": {"type": "string", "enum": ["function", "class", "method", ""], "description": "Filter by symbol kind"}
    },
    "annotations": {"readOnlyHint": true, "openWorldHint": false}
}
```

**Example interaction:**

```
Agent thinking: "I need to validate an email address"
Agent calls: find_reusable(intent="validate email format", context_file="services/user.py")
Response:
{
    "intent": "validate email format",
    "candidates_found": 3,
    "recommendation": "Strong match: reuse validate_email at core/validators.py:45.",
    "candidates": [
        {"symbol": "validate_email", "file": "core/validators.py", "line": 45, "kind": "function", "relevance_score": 0.95, "reason": "name match + same community"},
        {"symbol": "EmailValidator.check", "file": "utils/email.py", "line": 12, "kind": "method", "relevance_score": 0.78, "reason": "structural similar"},
        {"symbol": "re.fullmatch", "file": "<stdlib>", "line": null, "kind": "function", "relevance_score": 0.60, "reason": "stdlib alternative"}
    ]
}
```

**Deliverables:**
- [ ] `find_reusable` tool registration in `mcp_server.py`
- [ ] `_rank_reuse_candidates()` scoring function
- [ ] `_search_dependency_exports()` helper for installed-dep checking
- [ ] Integration tests: known-duplicate detection, stdlib suggestion, no-match case
- [ ] Tool description optimized for agent auto-invocation

**Success metric:** On a codebase with known duplicates, `find_reusable` returns the existing implementation in the top-3 candidates >90% of the time.

**Effort:** Medium — new tool, builds on existing `search_symbols` + `find_similar` infrastructure.

---

### Phase 4: Structural Over-Engineering Detection (`audit_codebase`)

**Inspiration from ponytail:** Their `/ponytail-audit` command asks the LLM to guess what's over-engineered. We replace guessing with graph analysis — the code graph knows which abstractions are unused, which wrappers add nothing, and which patterns are duplicated.

**What we build:**

A new MCP tool `audit_codebase` that performs structural analysis to identify:
1. **Dead abstractions** — classes/functions with 0 dependents (nobody calls them)
2. **Unnecessary wrappers** — functions whose body is a single delegation to another function
3. **Duplicate implementations** — structurally similar symbols doing the same thing in different places
4. **Over-abstracted patterns** — interfaces with exactly 1 implementor
5. **Bloat hotspots** — files with high symbol count but low external reuse

**Technical implementation:**

```python
@server.tool("audit_codebase")
async def audit_codebase(
    scope: str = "",           # File path, directory, or "" for whole repo
    checks: str = "all",       # "dead,wrappers,duplicates,over-abstracted,bloat" or "all"
    max_findings: int = 20,
) -> list[TextContent]:
    """
    Structural audit of the codebase for over-engineering patterns.
    Identifies code that could be simplified, removed, or consolidated.
    Returns actionable findings ranked by impact.
    """
    findings = []
    check_set = set(checks.split(",")) if checks != "all" else {
        "dead", "wrappers", "duplicates", "over-abstracted", "bloat"
    }

    all_nodes = engine.graph.nodes  # Scoped to `scope` if provided

    if "dead" in check_set:
        findings.extend(_find_dead_abstractions(engine, all_nodes))

    if "wrappers" in check_set:
        findings.extend(_find_unnecessary_wrappers(engine, all_nodes))

    if "duplicates" in check_set:
        findings.extend(_find_structural_duplicates(engine, all_nodes))

    if "over-abstracted" in check_set:
        findings.extend(_find_over_abstractions(engine, all_nodes))

    if "bloat" in check_set:
        findings.extend(_find_bloat_hotspots(engine, all_nodes))

    # Rank by impact (dependents affected × lines of code)
    findings.sort(key=lambda f: f["impact_score"], reverse=True)

    return _text({
        "scope": scope or "entire repository",
        "total_findings": len(findings),
        "findings": findings[:max_findings],
        "summary": _audit_summary(findings),
    })


def _find_dead_abstractions(engine, nodes) -> list[dict]:
    """Symbols with 0 incoming edges (nobody uses them)."""
    findings = []
    for node in nodes:
        if node.kind in (NodeKind.FUNCTION, NodeKind.CLASS, NodeKind.METHOD):
            dependents = engine.get_dependents(node.id)
            if dependents and len(dependents.calls) == 0 and len(dependents.imports) == 0:
                findings.append({
                    "type": "dead_abstraction",
                    "symbol": node.label,
                    "file": node.file,
                    "line": node.span.start_line if node.span else None,
                    "reason": "Zero dependents — nothing in the codebase uses this.",
                    "action": "DELETE or verify it's an entry point / public API.",
                    "impact_score": _estimate_lines(node),
                })
    return findings


def _find_unnecessary_wrappers(engine, nodes) -> list[dict]:
    """Functions that just delegate to one other function."""
    findings = []
    for node in nodes:
        if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
            deps = engine.get_dependencies(node.id)
            # Single outgoing call, no other logic
            if deps and len(deps.calls) == 1 and not deps.imports and not deps.inherits:
                sole_target = deps.calls[0]
                findings.append({
                    "type": "unnecessary_wrapper",
                    "symbol": node.label,
                    "file": node.file,
                    "line": node.span.start_line if node.span else None,
                    "wraps": sole_target.label,
                    "reason": f"Thin wrapper over {sole_target.label}. Callers could call it directly.",
                    "action": f"INLINE — replace calls to {node.label} with direct calls to {sole_target.label}.",
                    "impact_score": _count_callers(engine, node) * 2,
                })
    return findings


def _find_structural_duplicates(engine, nodes) -> list[dict]:
    """Groups of symbols that are structurally similar (potential consolidation)."""
    findings = []
    seen = set()
    for node in nodes:
        if node.id in seen:
            continue
        similar = engine.find_similar(node.id, top_k=3)
        # Filter to genuinely similar (different file, same kind)
        dupes = [s for s in similar if s.file != node.file and s.kind == node.kind]
        if dupes:
            group = [node] + dupes
            for n in group:
                seen.add(n.id)
            findings.append({
                "type": "duplicate_implementations",
                "symbols": [{"label": n.label, "file": n.file} for n in group],
                "reason": f"{len(group)} structurally similar implementations. Consolidate into one.",
                "action": "MERGE into a shared utility and have all call sites reference it.",
                "impact_score": sum(_estimate_lines(n) for n in group),
            })
    return findings


def _find_over_abstractions(engine, nodes) -> list[dict]:
    """Interfaces/traits with exactly 1 implementor (premature abstraction)."""
    findings = []
    for node in nodes:
        if node.kind in (NodeKind.TRAIT, NodeKind.CLASS):
            implementors = engine.get_implementors(node.id)
            if implementors and len(implementors) == 1:
                findings.append({
                    "type": "over_abstraction",
                    "symbol": node.label,
                    "file": node.file,
                    "single_implementor": implementors[0].label,
                    "reason": "Interface/abstract with exactly 1 implementor. The abstraction adds no value yet.",
                    "action": "COLLAPSE — inline the interface into its sole implementor until a second one appears.",
                    "impact_score": _estimate_lines(node),
                })
    return findings


def _find_bloat_hotspots(engine, nodes) -> list[dict]:
    """Files with many symbols but low external reference count."""
    file_stats = {}
    for node in nodes:
        if node.file not in file_stats:
            file_stats[node.file] = {"symbols": 0, "external_refs": 0}
        file_stats[node.file]["symbols"] += 1
        dependents = engine.get_dependents(node.id)
        if dependents:
            ext_refs = sum(1 for d in dependents.calls if d.file != node.file)
            file_stats[node.file]["external_refs"] += ext_refs

    findings = []
    for file, stats in file_stats.items():
        if stats["symbols"] > 10 and stats["external_refs"] < stats["symbols"] * 0.3:
            findings.append({
                "type": "bloat_hotspot",
                "file": file,
                "symbol_count": stats["symbols"],
                "external_references": stats["external_refs"],
                "reuse_ratio": round(stats["external_refs"] / max(stats["symbols"], 1), 2),
                "reason": f"{stats['symbols']} symbols but only {stats['external_refs']} external references. Most code here is unused externally.",
                "action": "REVIEW — split, delete unused, or mark as internal-only.",
                "impact_score": stats["symbols"] - stats["external_refs"],
            })
    return findings
```

**Example response:**

```json
{
    "scope": "entire repository",
    "total_findings": 14,
    "summary": {
        "dead_abstractions": 4,
        "unnecessary_wrappers": 3,
        "duplicate_implementations": 2,
        "over_abstractions": 3,
        "bloat_hotspots": 2,
        "estimated_deletable_lines": 340
    },
    "findings": [
        {
            "type": "duplicate_implementations",
            "symbols": [
                {"label": "validate_email", "file": "services/validators.py"},
                {"label": "check_email_format", "file": "api/utils.py"}
            ],
            "reason": "2 structurally similar implementations. Consolidate into one.",
            "action": "MERGE into a shared utility and have all call sites reference it.",
            "impact_score": 45
        },
        {
            "type": "dead_abstraction",
            "symbol": "AbstractNotificationGateway",
            "file": "core/notifications.py",
            "line": 12,
            "reason": "Zero dependents — nothing in the codebase uses this.",
            "action": "DELETE or verify it's an entry point / public API.",
            "impact_score": 38
        }
    ]
}
```

**Deliverables:**
- [ ] `audit_codebase` tool registration in `mcp_server.py`
- [ ] Five detection functions: dead, wrappers, duplicates, over-abstractions, bloat
- [ ] `_audit_summary()` aggregation helper
- [ ] Impact scoring and ranking system
- [ ] Scope filtering (file, directory, or whole repo)
- [ ] Integration tests per detection type
- [ ] Performance guard: audit must complete in <5s for repos with <5000 symbols

**Success metric:** On a repo with known tech debt, the audit identifies >70% of manually-identified over-engineering patterns without false positives in the top-10 findings.

**Effort:** High — new tool with 5 analysis strategies, scoring, and performance constraints.

---

### Phase 5: Behavioral Feedback Loop (`track_minimalism`)

**Inspiration from ponytail:** Their `ponytail:` comments track deferred decisions, and `/ponytail-debt` harvests them. We build a structural version — the graph itself tracks when an agent chose to reuse vs. create, and measures the outcome.

**What we build:**

A lightweight tracking tool that logs agent decisions (reused existing, created new, flagged as deferred). Over time, this builds a dataset answering: "How often does the agent reuse? What's the duplicate rate after N sessions?"

**Technical implementation:**

```python
@server.tool("track_minimalism")
async def track_minimalism(
    action: str,        # "reused" | "created" | "deferred"
    symbol: str,        # What was reused/created
    file: str,          # Where
    reason: str = "",   # Why (optional, for the agent to explain)
) -> list[TextContent]:
    """
    Track a minimalism decision. Call after choosing to reuse, create, or defer.
    Builds a ledger of decisions for later review.
    """
    entry = {
        "timestamp": _now_iso(),
        "action": action,
        "symbol": symbol,
        "file": file,
        "reason": reason,
    }
    _append_to_ledger(engine.repo_path, entry)

    # Return current session stats
    stats = _session_stats(engine.repo_path)
    return _text({
        "logged": entry,
        "session_stats": {
            "reused": stats["reused"],
            "created": stats["created"],
            "deferred": stats["deferred"],
            "reuse_rate": round(stats["reused"] / max(stats["total"], 1), 2),
        },
    })
```

**The ledger file (`.ast-intel/minimalism-ledger.jsonl`):**

```jsonl
{"timestamp": "2026-06-29T14:22:01Z", "action": "reused", "symbol": "validate_email", "file": "core/validators.py", "reason": "found via find_reusable"}
{"timestamp": "2026-06-29T14:23:15Z", "action": "created", "symbol": "format_phone", "file": "utils/phone.py", "reason": "no existing match in graph"}
{"timestamp": "2026-06-29T14:25:00Z", "action": "deferred", "symbol": "CacheLayer", "file": "services/cache.py", "reason": "minimal: using dict for now, upgrade when >1000 items"}
```

**Deliverables:**
- [ ] `track_minimalism` tool registration
- [ ] JSONL ledger append logic (`.ast-intel/minimalism-ledger.jsonl`)
- [ ] Session stats aggregation
- [ ] `review_minimalism_ledger` tool to read back the ledger with stats
- [ ] Optional: `deferred` entries emit warnings when the file is next queried

**Success metric:** Agents that use the full pipeline (find_reusable → implement → track_minimalism) achieve a measurable reuse rate >40% on existing codebases.

**Effort:** Low-Medium — simple append-only storage + stats.

---

### Phase Summary Table

| Phase | What We Build | Builds On | Effort | Value |
|-------|--------------|-----------|--------|-------|
| **1** | Native laziness instructions (`.github/copilot-instructions.md`) | Nothing — text only | Low | Immediate behavior improvement |
| **2** | `reuse_signals` field in existing MCP responses | Existing `find_similar` + `get_community` | Medium | Passive reuse hints with zero agent effort |
| **3** | `find_reusable` MCP tool | Phase 2 scoring logic | Medium | Active reuse discovery on demand |
| **4** | `audit_codebase` MCP tool | Full graph traversal | High | Structural over-engineering detection |
| **5** | `track_minimalism` MCP tool | Phase 3 decisions | Low-Med | Feedback loop, measures real impact |

**Dependency chain:** Phase 1 is independent. Phases 2-3-4 can be built in parallel. Phase 5 depends on Phase 3 being available (to track `find_reusable` outcomes).

**Total new code surface:**
- 1 new helper function (Phase 2: `_enrich_with_reuse_signals`)
- 2 new MCP tools (Phase 3: `find_reusable`, Phase 4: `audit_codebase`)
- 1 lightweight tracking tool (Phase 5: `track_minimalism`)
- ~400-600 lines of Python total across all phases

---

## Comparison: Ponytail MCP vs AST-Intel MCP

| Dimension | Ponytail MCP | AST-Intel MCP |
|-----------|-------------|---------------|
| **Purpose** | Serve behavioral instructions to agents | Serve structural codebase knowledge to agents |
| **Data source** | Static text (ruleset) | Dynamic code graph (AST + relationships) |
| **Tools** | 1 (`ponytail_instructions`) | 20+ (`search_symbols`, `get_impact`, `find_similar`, etc.) |
| **Read/Write** | Read-only, stateless | Read-only, stateful (graph in memory) |
| **What it shapes** | Agent *behavior* (how it approaches problems) | Agent *knowledge* (what it knows about the codebase) |
| **Complementary?** | **Yes** — tells agent *what to do* with the knowledge AST-Intel provides |

---

## Verdict

Ponytail and AST-Intel are **orthogonal and deeply complementary**:

- **Ponytail** = the *philosophy* (minimize, reuse, prefer existing)
- **AST-Intel** = the *oracle* (structurally answers "what exists? what's similar? what's the impact?")

Together, an agent gets:
1. The **discipline** to prefer reuse over creation (Ponytail)
2. The **structural knowledge** to find what to reuse (AST-Intel)
3. The **confidence** that "nothing exists" when it's truly time to write new code

This is the difference between telling an agent "check if it exists" (hoping the LLM finds it in its context window) and **giving it a tool that definitively answers the question**.

---

## References

- [Ponytail Repository](https://github.com/DietrichGebert/ponytail)
- [Ponytail MCP Server](https://github.com/DietrichGebert/ponytail/tree/main/ponytail-mcp)
- [Agentic Benchmark Writeup](https://github.com/DietrichGebert/ponytail/blob/main/benchmarks/results/2026-06-18-agentic.md)
- [AGENTS.md (Full Ruleset)](https://github.com/DietrichGebert/ponytail/blob/main/AGENTS.md)
- [Agent Portability Docs](https://github.com/DietrichGebert/ponytail/blob/main/docs/agent-portability.md)
