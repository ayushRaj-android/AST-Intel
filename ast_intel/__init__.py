"""AST Intel — Language-agnostic codebase semantic indexer.

Parses source code into structured AST JSON and human-readable summaries
for use by coding agents, security scanners, and impact analysis tools.
"""

from __future__ import annotations

__all__: list[str] = ["SCHEMA_VERSION", "TOOL_VERSION"]

SCHEMA_VERSION: str = "1.0.0"
"""Semantic version of the output JSON schema.

Consumers should check ``meta.schema_version`` before parsing.
Backward-incompatible changes bump the major version.
"""

TOOL_VERSION: str = "0.1.7"
"""Current release version of the ast-intel CLI tool."""
