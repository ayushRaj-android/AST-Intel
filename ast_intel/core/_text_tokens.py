"""Tokenize natural-language text into likely function-name fragments.

Splits ``snake_case``, ``kebab-case``, space-separated, and ``camelCase``/
``PascalCase`` names — including acronym runs and digit boundaries — into
lowercase fragments. Useful for fuzzy symbol search and reuse detection.

    "get HTTPResponse2 path"  → ["get", "http", "response", "2", "path"]
    "validate_email"          → ["validate", "email"]
"""

from __future__ import annotations

import re

__all__ = ["tokenize_fragments"]

_SEP_RE = re.compile(r"[_\-\s/.]+")
_CAMEL_RE = re.compile(
    r"[A-Z]+(?![a-z])"   # acronym run, e.g. HTTP
    r"|[A-Z][a-z]+"      # Capitalized word, e.g. Response
    r"|[a-z]+"           # lowercase run
    r"|[0-9]+",          # digit run
)


def tokenize_fragments(text: str, min_len: int = 1) -> list[str]:
    """Split *text* into ordered, deduplicated lowercase fragments.

    Args:
        text: Natural-language or identifier string.
        min_len: Drop fragments shorter than this length (default 1).

    Returns:
        Lowercase fragments in first-seen order, no duplicates.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for part in _SEP_RE.split(text):
        for frag in _CAMEL_RE.findall(part):
            low = frag.lower()
            if len(low) >= min_len and low not in seen:
                seen.add(low)
                tokens.append(low)
    return tokens
