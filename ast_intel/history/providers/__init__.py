"""Provider adapters and a shared HTTP helper.

Each adapter implements :class:`HistoryProvider` so the enrichment
pipeline can talk to GitHub, Azure DevOps, Bitbucket, or AWS
CodeCommit through a single interface.  Adapters are isolated to keep
the import surface light: importing :mod:`ast_intel.history` does not
pull in any provider unless callers explicitly request one.
"""

from __future__ import annotations

from ast_intel.history.providers.base import (
    EMPTY_CAPABILITIES,
    HistoryProvider,
    ProviderCapabilities,
)

__all__: list[str] = [
    "EMPTY_CAPABILITIES",
    "HistoryProvider",
    "ProviderCapabilities",
]
