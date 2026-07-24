"""Bitbucket / CodeCommit / no-op stub providers.

These stubs keep the registry contract complete so the rest of the
engine never has to special-case missing providers.  Bitbucket and
CodeCommit support will be filled in alongside live verification on
real repositories.
"""

from __future__ import annotations

from ast_intel.history.provider_registry import ProviderIdentity
from ast_intel.history.providers.base import (
    EMPTY_CAPABILITIES,
    HistoryProvider,
    ProviderCapabilities,
)

__all__: list[str] = [
    "BitbucketProvider",
    "CodeCommitProvider",
    "NoopProvider",
]


class _StubProvider(HistoryProvider):
    capabilities: ProviderCapabilities = EMPTY_CAPABILITIES

    def __init__(self, identity: ProviderIdentity) -> None:
        self.identity = identity

    def initialize(self) -> None:  # noqa: D401 — no-op
        """No-op."""


class BitbucketProvider(_StubProvider):
    """Placeholder for the Bitbucket Cloud adapter."""


class CodeCommitProvider(_StubProvider):
    """Placeholder for the AWS CodeCommit adapter."""


class NoopProvider(_StubProvider):
    """A provider that returns nothing — used when no remote is recognised."""
