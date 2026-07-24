# AST-Intel — Agent Instructions

You have access to the `ast-intel` MCP server. It exposes a structural code graph
of this repository. Use it before writing new code.

## The Minimalism Protocol

Before implementing anything new, follow this protocol in order. Stop at the
first rung that holds:

### 1. STOP — Does this need to exist?

If the task is configuration, documentation, or an already-solved problem, skip it.
Question complex requests: "Do you actually need X, or does Y cover it?"

### 2. QUERY the graph — check if it already exists

Call `search_symbols` with the name or intent of what you're about to write.
Call `find_similar` if you have a reference symbol. The graph is authoritative —
if it returns a match, REUSE it. Do not rewrite.

```
Example: Before writing a new `validate_email` function:
→ search_symbols(query="validate_email")
→ find_similar(symbol="validate_email") if you found one in a different file
```

### 3. CHECK dependencies — does an installed library handle this?

Call `get_dependencies` on the relevant module. If an installed package already
exposes this functionality, USE it. Do not wrap it unless the wrapper adds
real value (error translation, retry logic, interface normalization).

### 4. CHECK stdlib coverage

If the language standard library handles the task, use it directly.
No wrapper, no abstraction, just the stdlib call.

### 5. ASSESS impact before touching shared code

Call `get_impact` on any symbol you plan to modify. If the blast radius is large,
prefer the SMALLEST correct diff. Do not refactor adjacent code unless asked.

### 6. Only then: write the minimum that works

- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem.

## What is NEVER minimal (do not skip these)

- Input validation at trust boundaries
- Error handling that prevents data loss
- Security: auth checks, sanitization, path traversal guards
- Accessibility requirements
- Anything explicitly requested by the user

## Markers

When you intentionally take a shortcut, mark it:

```python
# minimal: using dict for now, upgrade to LRU cache if >1000 items
```

When you reuse an existing symbol found via the graph:

```python
# reuse: found via search_symbols — core/validators.py:45
```

## MCP Tools Quick Reference

| Tool | When to call |
|------|-------------|
| `search_symbols` | Before writing any new function/class — check if it exists |
| `find_similar` | When you found something close but want to verify no better match |
| `get_dependencies` | To check what a module already uses (avoid redundant imports) |
| `get_dependents` | Before modifying — who depends on this? |
| `get_impact` | Before touching shared code — what's the blast radius? |
| `get_community` | To find functionally related symbols you might not know about |
| `explain_symbol` | To understand a symbol's role, callers, callees before changing it |
| `find_usages` | To see everywhere a symbol is referenced |
| `get_context` | Full picture: deps + dependents + similar + community in one call |
