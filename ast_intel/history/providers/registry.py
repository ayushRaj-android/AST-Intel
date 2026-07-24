"""Factory helpers for selecting and constructing providers."""

from __future__ import annotations

import logging
from pathlib import Path

from ast_intel.history.git_ops import GitError, run_git
from ast_intel.history.provider_registry import (
    ProviderIdentity,
    ProviderKind,
    parse_remote_list,
    parse_remote_url,
    pick_primary_remote,
)
from ast_intel.history.providers.ado_provider import AdoProvider
from ast_intel.history.providers.base import HistoryProvider
from ast_intel.history.providers.github_provider import GitHubProvider
from ast_intel.history.providers.stubs import (
    BitbucketProvider,
    CodeCommitProvider,
    NoopProvider,
)

__all__: list[str] = [
    "build_provider",
    "detect_identity",
    "provider_from_identity",
]

logger = logging.getLogger(__name__)


def detect_identity(repo: Path) -> ProviderIdentity | None:
    """Return the :class:`ProviderIdentity` for *repo*'s primary remote."""
    try:
        raw = run_git(["remote", "-v"], cwd=repo)
    except GitError as exc:
        logger.debug("git remote -v failed: %s", exc)
        return None
    remotes = parse_remote_list(raw)
    primary = pick_primary_remote(remotes)
    if primary is None:
        return None
    return parse_remote_url(primary.url)


def provider_from_identity(identity: ProviderIdentity) -> HistoryProvider:
    """Construct the provider adapter for *identity*."""
    if identity.kind == ProviderKind.GITHUB:
        return GitHubProvider(identity)
    if identity.kind == ProviderKind.AZURE_DEVOPS:
        return AdoProvider(identity)
    if identity.kind == ProviderKind.BITBUCKET:
        return BitbucketProvider(identity)
    if identity.kind == ProviderKind.CODECOMMIT:
        return CodeCommitProvider(identity)
    return NoopProvider(identity)


def build_provider(repo: Path) -> HistoryProvider | None:
    """Detect + construct + initialize a provider for *repo*.

    Initialisation failures are downgraded to a :class:`NoopProvider`
    that retains the original identity, so callers can still link
    commit messages to PR URLs without API access.
    """
    identity = detect_identity(repo)
    if identity is None:
        return None
    provider = provider_from_identity(identity)
    try:
        provider.initialize()
    except Exception as exc:  # noqa: BLE001 — fall back gracefully.
        logger.warning(
            "Provider %s failed to initialize: %s — falling back to noop",
            identity.kind.value,
            exc,
        )
        return NoopProvider(identity)
    return provider
