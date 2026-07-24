"""Test fixture for top-level functions — sync, async, decorated."""

from functools import cache, lru_cache
from typing import Any


def simple_add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def no_annotations(x, y):
    """Function with no type annotations."""
    return x + y


def no_return_type(name: str):
    """Function missing return type."""
    print(name)


async def fetch_data(url: str, timeout: float = 30.0) -> bytes:
    """Async function to fetch data."""
    return b""


async def stream_events(source: str) -> list[dict[str, Any]]:
    """Async function returning complex type."""
    return []


@cache
def cached_value(key: str) -> int:
    """Cached function."""
    return hash(key)


@lru_cache(maxsize=128)
def lru_lookup(query: str) -> list[str]:
    """LRU cached lookup."""
    return []


def _private_helper(data: bytes) -> str:
    """Private helper function."""
    return data.decode()


def complex_signature(
    name: str,
    items: list[int],
    *args: str,
    verbose: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Function with complex parameter signature."""
    return {}
