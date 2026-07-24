"""Tests for :mod:`ast_intel.core._text_tokens`."""

from __future__ import annotations

from ast_intel.core._text_tokens import tokenize_fragments


def test_snake_case() -> None:
    assert tokenize_fragments("validate_email") == ["validate", "email"]


def test_kebab_and_space() -> None:
    assert tokenize_fragments("get-config path") == ["get", "config", "path"]


def test_camel_and_pascal() -> None:
    assert tokenize_fragments("StorageHelper") == ["storage", "helper"]
    assert tokenize_fragments("getConfigPath") == ["get", "config", "path"]


def test_acronym_run() -> None:
    assert tokenize_fragments("parseHTTPResponse") == ["parse", "http", "response"]


def test_digits_and_mixed() -> None:
    assert tokenize_fragments("get HTTPResponse2 path") == [
        "get", "http", "response", "2", "path",
    ]


def test_dedup_order_preserved() -> None:
    assert tokenize_fragments("user_user getUser") == ["user", "get"]


def test_min_len_filter() -> None:
    assert tokenize_fragments("a_bb_ccc", min_len=3) == ["ccc"]


def test_empty() -> None:
    assert tokenize_fragments("") == []
