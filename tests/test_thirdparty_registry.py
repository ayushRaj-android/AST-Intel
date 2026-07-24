"""Tests for the third-party API registry — data-egress feature (P2)."""

from __future__ import annotations

from ast_intel.extractors import _thirdparty_registry as reg
from ast_intel.extractors._thirdparty_registry import (
    CATEGORIES,
    DIRECTIONS,
    classify_host,
    classify_sdk,
    extract_host,
    is_external_host,
)

# ---------------------------------------------------------------------------
# region:    --- extract_host
# ---------------------------------------------------------------------------


class TestExtractHost:
    def test_absolute_url(self) -> None:
        assert extract_host("https://api.stripe.com/v1/charges") == "api.stripe.com"

    def test_strips_port(self) -> None:
        assert extract_host("http://host.example.com:8080/a") == "host.example.com"

    def test_strips_userinfo(self) -> None:
        assert extract_host("https://user:pass@api.x.com/p") == "api.x.com"

    def test_scheme_relative(self) -> None:
        assert extract_host("//cdn.segment.com/analytics.js") == "cdn.segment.com"

    def test_bare_host(self) -> None:
        assert extract_host("api.twilio.com") == "api.twilio.com"

    def test_root_relative_is_none(self) -> None:
        assert extract_host("/relative/path") is None

    def test_dynamic_is_none(self) -> None:
        assert extract_host("<dynamic>") is None
        assert extract_host("{param}/api") is None


# endregion: --- extract_host


# ---------------------------------------------------------------------------
# region:    --- classify_host
# ---------------------------------------------------------------------------


class TestClassifyHost:
    def test_known_vendor_full_url(self) -> None:
        info = classify_host("https://api.stripe.com/v1/charges")
        assert info is not None
        assert info.vendor == "Stripe"
        assert info.category == "payments"

    def test_subdomain_matches_on_boundary(self) -> None:
        info = classify_host("https://o123.ingest.sentry.io/api/1/store/")
        assert info is not None
        assert info.vendor == "Sentry"

    def test_bare_host(self) -> None:
        info = classify_host("api.twilio.com")
        assert info is not None
        assert info.vendor == "Twilio"

    def test_domain_boundary_prevents_false_positive(self) -> None:
        # "notstripe.com" must NOT match "stripe.com".
        assert classify_host("https://notstripe.com/pay") is None

    def test_longest_suffix_wins(self) -> None:
        info = classify_host("https://maps.googleapis.com/maps/api/geocode/json")
        assert info is not None
        assert info.vendor == "Google Maps"

    def test_unknown_host_is_none(self) -> None:
        assert classify_host("https://api.unknown-vendor-xyz.com/v1") is None

    def test_relative_url_is_none(self) -> None:
        assert classify_host("/internal/api") is None


# endregion: --- classify_host


# ---------------------------------------------------------------------------
# region:    --- is_external_host
# ---------------------------------------------------------------------------


class TestIsExternalHost:
    def test_public_hosts_are_external(self) -> None:
        assert is_external_host("api.stripe.com") is True
        assert is_external_host("example.com") is True

    def test_loopback_is_internal(self) -> None:
        assert is_external_host("localhost") is False
        assert is_external_host("127.0.0.1") is False

    def test_private_ips_are_internal(self) -> None:
        assert is_external_host("10.0.0.5") is False
        assert is_external_host("192.168.1.10") is False
        assert is_external_host("172.16.0.1") is False

    def test_cluster_internal_suffixes(self) -> None:
        assert is_external_host("user-service.default.svc.cluster.local") is False
        assert is_external_host("cache.internal") is False

    def test_single_label_is_internal(self) -> None:
        assert is_external_host("user-service") is False

    def test_none_and_dynamic(self) -> None:
        assert is_external_host(None) is False
        assert is_external_host("") is False
        assert is_external_host("{param}") is False


# endregion: --- is_external_host


# ---------------------------------------------------------------------------
# region:    --- classify_sdk
# ---------------------------------------------------------------------------


class TestClassifySdk:
    def test_bare_package(self) -> None:
        info = classify_sdk("stripe")
        assert info is not None
        assert info.vendor == "Stripe"

    def test_python_module_root(self) -> None:
        info = classify_sdk("sentry_sdk.init")
        assert info is not None
        assert info.vendor == "Sentry"

    def test_scoped_npm_package(self) -> None:
        info = classify_sdk("@sentry/node")
        assert info is not None
        assert info.vendor == "Sentry"

    def test_case_insensitive(self) -> None:
        info = classify_sdk("TWILIO")
        assert info is not None
        assert info.vendor == "Twilio"

    def test_unknown_returns_none(self) -> None:
        assert classify_sdk("requests") is None
        assert classify_sdk("") is None


# endregion: --- classify_sdk


# ---------------------------------------------------------------------------
# region:    --- Invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_all_categories_and_directions_valid(self) -> None:
        tables = (reg._HOST_MAP, reg._HOST_EXACT, reg._SDK_MAP)
        for table in tables:
            for info in table.values():
                assert info.category in CATEGORIES, info
                assert info.direction in DIRECTIONS, info


# endregion: --- Invariants
