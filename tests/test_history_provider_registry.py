"""Tests for remote-URL parsing."""

from __future__ import annotations

import pytest

from ast_intel.history.provider_registry import (
    ProviderKind,
    parse_remote_list,
    parse_remote_url,
    pick_primary_remote,
    pull_request_url,
)


@pytest.mark.parametrize(
    ("url", "kind", "owner", "repo"),
    [
        ("https://github.com/octo/cat.git", ProviderKind.GITHUB, "octo", "cat"),
        ("git@github.com:octo/cat.git", ProviderKind.GITHUB, "octo", "cat"),
        ("https://bitbucket.org/o/r", ProviderKind.BITBUCKET, "o", "r"),
        ("git@bitbucket.org:o/r.git", ProviderKind.BITBUCKET, "o", "r"),
    ],
)
def test_parse_simple_hosts(url, kind, owner, repo) -> None:  # type: ignore[no-untyped-def]
    ident = parse_remote_url(url)
    assert ident is not None
    assert ident.kind is kind
    assert ident.owner == owner
    assert ident.repo == repo


def test_parse_ado_dev_url_url_decodes_project() -> None:
    url = "https://msazuredev@dev.azure.com/msazuredev/Cloud%20Transfer%20Service/_git/Safeguard"
    ident = parse_remote_url(url)
    assert ident is not None
    assert ident.kind is ProviderKind.AZURE_DEVOPS
    assert ident.project == "Cloud Transfer Service"
    # API base must keep the encoded form so urllib does not raise.
    assert "Cloud%20Transfer%20Service" in ident.api_base_url
    assert ident.web_base_url.endswith("/Safeguard")


def test_parse_ado_legacy_visualstudio_url() -> None:
    url = "https://myorg.visualstudio.com/MyProj/_git/myrepo"
    ident = parse_remote_url(url)
    assert ident is not None
    assert ident.kind is ProviderKind.AZURE_DEVOPS
    assert ident.host.endswith("visualstudio.com")
    assert ident.project == "MyProj"


def test_parse_codecommit_url() -> None:
    url = "https://git-codecommit.us-east-1.amazonaws.com/v1/repos/my-repo"
    ident = parse_remote_url(url)
    assert ident is not None
    assert ident.kind is ProviderKind.CODECOMMIT
    assert ident.repo == "my-repo"


def test_unknown_remote_returns_none() -> None:
    assert parse_remote_url("https://gitlab.example.com/o/r.git") is None


def test_pull_request_url_per_provider() -> None:
    gh = parse_remote_url("https://github.com/octo/cat.git")
    assert gh is not None
    assert pull_request_url(gh, "5") == "https://github.com/octo/cat/pull/5"

    ado = parse_remote_url(
        "https://dev.azure.com/myorg/MyProj/_git/myrepo",
    )
    assert ado is not None
    assert pull_request_url(ado, "5").endswith("/pullrequest/5")


def test_pick_primary_remote_prefers_origin() -> None:
    raw = (
        "upstream\thttps://github.com/u/r.git\t(fetch)\n"
        "origin\thttps://github.com/o/r.git\t(fetch)\n"
        "origin\thttps://github.com/o/r.git\t(push)\n"
    )
    remotes = parse_remote_list(raw)
    primary = pick_primary_remote(remotes)
    assert primary is not None
    assert primary.name == "origin"
