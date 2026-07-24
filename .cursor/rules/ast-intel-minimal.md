---
description: AST-Intel minimalism protocol — query the code graph before writing new code
globs: ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"]
alwaysApply: true
---

# Minimalism Protocol

You have access to the `ast-intel` MCP server (a structural code graph).
Use it before writing new code.

## Before implementing anything new:

1. `search_symbols(query="<what you're about to write>")` — if it exists, reuse it.
2. `find_similar(symbol="<reference>")` — if something close exists, check if it fits.
3. `get_dependencies(symbol="<module>")` — if a dep already handles it, use it.
4. If stdlib covers it, use stdlib. No wrapper.
5. `get_impact(symbol="<target>")` before modifying shared code.
6. Only then: write the minimum that works.

## Hard rules:

- Never skip: input validation, error handling, security, accessibility.
- No abstractions that weren't requested.
- Deletion over addition. Boring over clever.
- Mark shortcuts: `# minimal: <reason>`
- Mark reuse: `# reuse: <source>`
