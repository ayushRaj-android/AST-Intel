"""Parse git remote URLs and identify the hosting provider.

Mirrors the TypeScript ``src/providers/remoteUrl.ts`` from the
``code-history-intel`` extension byte-for-byte so the Python engine
and the extension produce identical :class:`ProviderIdentity` records
for the same repository.

Supported hosts:

- ``github.com`` (HTTPS + SSH)
- ``dev.azure.com`` and the legacy ``visualstudio.com`` Azure DevOps URLs
- ``bitbucket.org`` (HTTPS + SSH)
- ``git-codecommit.<region>.amazonaws.com`` (HTTPS + SSH)

Unrecognised remotes resolve to :data:`ProviderKind.NONE`, allowing
callers to fall back to git-only history without crashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, unquote

__all__: list[str] = [
    "ProviderIdentity",
    "ProviderKind",
    "RemoteSpec",
    "parse_remote_url",
    "pick_primary_remote",
    "pull_request_url",
]


class ProviderKind(StrEnum):
    """Identifier of a code hosting provider."""

    GITHUB = "github"
    AZURE_DEVOPS = "ado"
    BITBUCKET = "bitbucket"
    CODECOMMIT = "codecommit"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Parsed coordinates of a remote on a known provider.

    Attributes:
        kind: Hosting provider.
        host: Bare host name (no scheme, no port).
        owner: Organisation / user / project — semantics vary per host.
        repo: Repository slug.
        project: ADO project (empty for non-ADO hosts).
        web_base_url: Canonical browse URL, e.g. ``https://github.com/o/r``.
        api_base_url: Provider REST root, e.g. ``https://api.github.com``.
    """

    kind: ProviderKind
    host: str
    owner: str
    repo: str
    project: str = ""
    web_base_url: str = ""
    api_base_url: str = ""


@dataclass(frozen=True, slots=True)
class RemoteSpec:
    """A raw ``(name, url)`` pair from ``git remote -v``."""

    name: str
    url: str


# ---------------------------------------------------------------------------
# region:    --- Regex patterns
# ---------------------------------------------------------------------------

# github.com:owner/repo(.git)?   /   git@github.com:owner/repo(.git)?
_GITHUB_HTTPS = re.compile(
    r"^https?://(?:[^@/]+@)?(?P<host>github\.com)/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GITHUB_SSH = re.compile(
    r"^git@(?P<host>github\.com):(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)

# dev.azure.com/{org}/{project}/_git/{repo}
_ADO_DEV = re.compile(
    r"^https?://(?:[^@/]+@)?(?P<host>dev\.azure\.com)/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/?#]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
# Legacy: {org}.visualstudio.com/{project}/_git/{repo}
_ADO_VS = re.compile(
    r"^https?://(?:[^@/]+@)?(?P<org>[^.]+)\.(?P<host>visualstudio\.com)/(?P<project>[^/]+)/_git/(?P<repo>[^/?#]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
# SSH: git@ssh.dev.azure.com:v3/{org}/{project}/{repo}
_ADO_SSH = re.compile(
    r"^git@ssh\.dev\.azure\.com:v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)

_BITBUCKET_HTTPS = re.compile(
    r"^https?://(?:[^@/]+@)?(?P<host>bitbucket\.org)/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)
_BITBUCKET_SSH = re.compile(
    r"^git@(?P<host>bitbucket\.org):(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)

_CODECOMMIT_HTTPS = re.compile(
    r"^https?://git-codecommit\.(?P<region>[^.]+)\.amazonaws\.com/v1/repos/(?P<repo>[^/?#]+?)/?$",
    re.IGNORECASE,
)
_CODECOMMIT_SSH = re.compile(
    r"^ssh://[^@]+@git-codecommit\.(?P<region>[^.]+)\.amazonaws\.com/v1/repos/(?P<repo>[^/?#]+?)/?$",
    re.IGNORECASE,
)


# endregion: --- Regex patterns


def parse_remote_url(url: str) -> ProviderIdentity | None:
    """Return the :class:`ProviderIdentity` for *url* or ``None``.

    URL-encoded path segments (e.g. ``Cloud%20Transfer%20Service``) are
    decoded so callers see the human-readable project name.
    """
    if not url:
        return None
    url = url.strip()

    m = _GITHUB_HTTPS.match(url) or _GITHUB_SSH.match(url)
    if m:
        owner = unquote(m.group("owner"))
        repo = unquote(m.group("repo"))
        return ProviderIdentity(
            kind=ProviderKind.GITHUB,
            host="github.com",
            owner=owner,
            repo=repo,
            web_base_url=f"https://github.com/{owner}/{repo}",
            api_base_url="https://api.github.com",
        )

    m = _ADO_DEV.match(url)
    if m:
        org = unquote(m.group("org"))
        project = unquote(m.group("project"))
        repo = unquote(m.group("repo"))
        org_q = quote(org, safe="")
        project_q = quote(project, safe="")
        repo_q = quote(repo, safe="")
        return ProviderIdentity(
            kind=ProviderKind.AZURE_DEVOPS,
            host="dev.azure.com",
            owner=org,
            repo=repo,
            project=project,
            web_base_url=f"https://dev.azure.com/{org_q}/{project_q}/_git/{repo_q}",
            api_base_url=f"https://dev.azure.com/{org_q}/{project_q}/_apis",
        )

    m = _ADO_VS.match(url)
    if m:
        org = unquote(m.group("org"))
        project = unquote(m.group("project"))
        repo = unquote(m.group("repo"))
        project_q = quote(project, safe="")
        repo_q = quote(repo, safe="")
        return ProviderIdentity(
            kind=ProviderKind.AZURE_DEVOPS,
            host=f"{org}.visualstudio.com",
            owner=org,
            repo=repo,
            project=project,
            web_base_url=f"https://{org}.visualstudio.com/{project_q}/_git/{repo_q}",
            api_base_url=f"https://{org}.visualstudio.com/{project_q}/_apis",
        )

    m = _ADO_SSH.match(url)
    if m:
        org = unquote(m.group("org"))
        project = unquote(m.group("project"))
        repo = unquote(m.group("repo"))
        org_q = quote(org, safe="")
        project_q = quote(project, safe="")
        repo_q = quote(repo, safe="")
        return ProviderIdentity(
            kind=ProviderKind.AZURE_DEVOPS,
            host="dev.azure.com",
            owner=org,
            repo=repo,
            project=project,
            web_base_url=f"https://dev.azure.com/{org_q}/{project_q}/_git/{repo_q}",
            api_base_url=f"https://dev.azure.com/{org_q}/{project_q}/_apis",
        )

    m = _BITBUCKET_HTTPS.match(url) or _BITBUCKET_SSH.match(url)
    if m:
        owner = unquote(m.group("owner"))
        repo = unquote(m.group("repo"))
        return ProviderIdentity(
            kind=ProviderKind.BITBUCKET,
            host="bitbucket.org",
            owner=owner,
            repo=repo,
            web_base_url=f"https://bitbucket.org/{owner}/{repo}",
            api_base_url="https://api.bitbucket.org/2.0",
        )

    m = _CODECOMMIT_HTTPS.match(url) or _CODECOMMIT_SSH.match(url)
    if m:
        region = m.group("region")
        repo = unquote(m.group("repo"))
        return ProviderIdentity(
            kind=ProviderKind.CODECOMMIT,
            host=f"git-codecommit.{region}.amazonaws.com",
            owner="",
            repo=repo,
            web_base_url=(
                f"https://{region}.console.aws.amazon.com/codesuite/codecommit"
                f"/repositories/{repo}/browse"
            ),
            api_base_url=f"https://codecommit.{region}.amazonaws.com",
        )

    return None


def pull_request_url(identity: ProviderIdentity, pr_id: str) -> str:
    """Return a browser URL for a pull-request on *identity*."""
    if identity.kind == ProviderKind.GITHUB:
        return f"{identity.web_base_url}/pull/{pr_id}"
    if identity.kind == ProviderKind.AZURE_DEVOPS:
        return f"{identity.web_base_url}/pullrequest/{pr_id}"
    if identity.kind == ProviderKind.BITBUCKET:
        return f"{identity.web_base_url}/pull-requests/{pr_id}"
    if identity.kind == ProviderKind.CODECOMMIT:
        return f"{identity.web_base_url}/pull-requests/{pr_id}"
    return ""


# ---------------------------------------------------------------------------
# region:    --- Remote selection
# ---------------------------------------------------------------------------

_REMOTE_PREFERENCE: tuple[str, ...] = ("origin", "upstream")


def pick_primary_remote(remotes: list[RemoteSpec]) -> RemoteSpec | None:
    """Choose the canonical remote for history queries.

    Preference order: ``origin``, then ``upstream``, then the first
    fetch URL.  Push-only URLs are ignored.
    """
    if not remotes:
        return None
    by_name: dict[str, RemoteSpec] = {r.name: r for r in remotes}
    for name in _REMOTE_PREFERENCE:
        if name in by_name:
            return by_name[name]
    return remotes[0]


def parse_remote_list(raw: str) -> list[RemoteSpec]:
    """Parse the output of ``git remote -v``.

    Lines look like ``origin\\thttps://...\\t(fetch)``. We keep only
    ``(fetch)`` URLs and deduplicate by name.
    """
    seen: set[str] = set()
    out: list[RemoteSpec] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1] != "(fetch)":  # noqa: PLR2004
            continue
        name, url = parts[0], parts[1]
        if name in seen:
            continue
        seen.add(name)
        out.append(RemoteSpec(name=name, url=url))
    return out


# endregion: --- Remote selection
