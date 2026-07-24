"""Third-party API taxonomy — classify outbound calls to external providers.

This module is the **single source of truth** for deciding whether an outgoing
HTTP call or SDK usage targets a known third-party vendor (Stripe, Twilio,
OpenAI, Sentry, …) and what kind of service it is.  It mirrors the structure of
:mod:`ast_intel.extractors._cloud_taxonomy`.

The taxonomy answers two questions:

* Given a URL/host (``https://api.stripe.com/v1/charges``) — which vendor is it?
* Given an import/client name (``stripe``, ``@sentry/node``) — which vendor?

It also provides :func:`is_external_host`, the deterministic signal behind the
"unknown external host = third-party" rule: a host that is not localhost, a
private IP, or an internal cluster name is treated as external even when the
vendor is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

__all__: list[str] = [
    "CATEGORIES",
    "DIRECTIONS",
    "ThirdPartyInfo",
    "classify_host",
    "classify_sdk",
    "extract_host",
    "is_external_host",
]


# ---------------------------------------------------------------------------
# region:    --- Category / direction vocabularies
# ---------------------------------------------------------------------------

CATEGORIES: Final[frozenset[str]] = frozenset({
    "payments",
    "communication",
    "email",
    "ai",
    "analytics",
    "monitoring",
    "auth",
    "search",
    "maps",
    "storage",
    "dev",
    "automation",
    "productivity",
    "other",
})

# Data-flow direction relative to *this* codebase.
#   outbound      — data primarily flows to the vendor (we push).
#   bidirectional — we send data and receive a substantive response.
#   inbound       — we primarily fetch data (reserved; still egresses auth/query).
DIRECTIONS: Final[frozenset[str]] = frozenset({
    "outbound",
    "bidirectional",
    "inbound",
})


# ---------------------------------------------------------------------------
# region:    --- Data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThirdPartyInfo:
    """Normalized classification of a third-party API target."""

    vendor: str
    """Human-readable vendor name (e.g. ``"Stripe"``, ``"OpenAI"``)."""

    category: str
    """One of :data:`CATEGORIES`."""

    direction: str
    """One of :data:`DIRECTIONS` — typical data-flow direction."""


# ---------------------------------------------------------------------------
# region:    --- Host-suffix classification
# ---------------------------------------------------------------------------

# Registrable-domain suffix → ThirdPartyInfo.  Matched on a domain boundary:
# a host matches ``key`` when ``host == key`` or ``host.endswith("." + key)``.
# When several suffixes match, the longest (most specific) wins.
_HOST_MAP: dict[str, ThirdPartyInfo] = {
    # --- payments ---
    "stripe.com": ThirdPartyInfo("Stripe", "payments", "bidirectional"),
    "braintreegateway.com": ThirdPartyInfo("Braintree", "payments", "bidirectional"),
    "paypal.com": ThirdPartyInfo("PayPal", "payments", "bidirectional"),
    "squareup.com": ThirdPartyInfo("Square", "payments", "bidirectional"),
    "razorpay.com": ThirdPartyInfo("Razorpay", "payments", "bidirectional"),
    "adyen.com": ThirdPartyInfo("Adyen", "payments", "bidirectional"),
    # --- communication ---
    "twilio.com": ThirdPartyInfo("Twilio", "communication", "outbound"),
    "vonage.com": ThirdPartyInfo("Vonage", "communication", "outbound"),
    "nexmo.com": ThirdPartyInfo("Vonage", "communication", "outbound"),
    "slack.com": ThirdPartyInfo("Slack", "communication", "outbound"),
    "discord.com": ThirdPartyInfo("Discord", "communication", "outbound"),
    "discordapp.com": ThirdPartyInfo("Discord", "communication", "outbound"),
    # --- email ---
    "sendgrid.com": ThirdPartyInfo("SendGrid", "email", "outbound"),
    "mailgun.net": ThirdPartyInfo("Mailgun", "email", "outbound"),
    "postmarkapp.com": ThirdPartyInfo("Postmark", "email", "outbound"),
    "resend.com": ThirdPartyInfo("Resend", "email", "outbound"),
    # --- ai ---
    "openai.com": ThirdPartyInfo("OpenAI", "ai", "bidirectional"),
    "anthropic.com": ThirdPartyInfo("Anthropic", "ai", "bidirectional"),
    "cohere.ai": ThirdPartyInfo("Cohere", "ai", "bidirectional"),
    "cohere.com": ThirdPartyInfo("Cohere", "ai", "bidirectional"),
    "replicate.com": ThirdPartyInfo("Replicate", "ai", "bidirectional"),
    "huggingface.co": ThirdPartyInfo("Hugging Face", "ai", "bidirectional"),
    # --- analytics ---
    "segment.io": ThirdPartyInfo("Segment", "analytics", "outbound"),
    "segment.com": ThirdPartyInfo("Segment", "analytics", "outbound"),
    "amplitude.com": ThirdPartyInfo("Amplitude", "analytics", "outbound"),
    "mixpanel.com": ThirdPartyInfo("Mixpanel", "analytics", "outbound"),
    "google-analytics.com": ThirdPartyInfo("Google Analytics", "analytics", "outbound"),
    "posthog.com": ThirdPartyInfo("PostHog", "analytics", "outbound"),
    # --- monitoring ---
    "sentry.io": ThirdPartyInfo("Sentry", "monitoring", "outbound"),
    "datadoghq.com": ThirdPartyInfo("Datadog", "monitoring", "outbound"),
    "newrelic.com": ThirdPartyInfo("New Relic", "monitoring", "outbound"),
    "rollbar.com": ThirdPartyInfo("Rollbar", "monitoring", "outbound"),
    "bugsnag.com": ThirdPartyInfo("Bugsnag", "monitoring", "outbound"),
    "honeycomb.io": ThirdPartyInfo("Honeycomb", "monitoring", "outbound"),
    # --- auth ---
    "auth0.com": ThirdPartyInfo("Auth0", "auth", "bidirectional"),
    "okta.com": ThirdPartyInfo("Okta", "auth", "bidirectional"),
    "clerk.com": ThirdPartyInfo("Clerk", "auth", "bidirectional"),
    "clerk.dev": ThirdPartyInfo("Clerk", "auth", "bidirectional"),
    # --- search ---
    "algolia.net": ThirdPartyInfo("Algolia", "search", "bidirectional"),
    "algolianet.com": ThirdPartyInfo("Algolia", "search", "bidirectional"),
    "typesense.net": ThirdPartyInfo("Typesense", "search", "bidirectional"),
    # --- maps ---
    "mapbox.com": ThirdPartyInfo("Mapbox", "maps", "bidirectional"),
    # --- storage / media ---
    "cloudinary.com": ThirdPartyInfo("Cloudinary", "storage", "bidirectional"),
    # --- dev ---
    "github.com": ThirdPartyInfo("GitHub", "dev", "bidirectional"),
    "gitlab.com": ThirdPartyInfo("GitLab", "dev", "bidirectional"),
    # --- automation ---
    "zapier.com": ThirdPartyInfo("Zapier", "automation", "outbound"),
    # --- productivity ---
    "airtable.com": ThirdPartyInfo("Airtable", "productivity", "bidirectional"),
    "notion.com": ThirdPartyInfo("Notion", "productivity", "bidirectional"),
}

# Exact-host entries for vendors whose registrable domain is shared/ambiguous
# (matched by exact host or ``.``-boundary suffix, same as ``_HOST_MAP``).
_HOST_EXACT: dict[str, ThirdPartyInfo] = {
    "maps.googleapis.com": ThirdPartyInfo("Google Maps", "maps", "bidirectional"),
    "generativelanguage.googleapis.com": ThirdPartyInfo("Google Gemini", "ai", "bidirectional"),
    "identitytoolkit.googleapis.com": ThirdPartyInfo("Firebase Auth", "auth", "bidirectional"),
}


# ---------------------------------------------------------------------------
# region:    --- SDK / import classification
# ---------------------------------------------------------------------------

# Import/package/client name (lower-cased) → ThirdPartyInfo.
_SDK_MAP: dict[str, ThirdPartyInfo] = {
    # --- payments ---
    "stripe": ThirdPartyInfo("Stripe", "payments", "bidirectional"),
    "braintree": ThirdPartyInfo("Braintree", "payments", "bidirectional"),
    "paypalrestsdk": ThirdPartyInfo("PayPal", "payments", "bidirectional"),
    "paypalhttp": ThirdPartyInfo("PayPal", "payments", "bidirectional"),
    "razorpay": ThirdPartyInfo("Razorpay", "payments", "bidirectional"),
    "squareup": ThirdPartyInfo("Square", "payments", "bidirectional"),
    # --- communication ---
    "twilio": ThirdPartyInfo("Twilio", "communication", "outbound"),
    "vonage": ThirdPartyInfo("Vonage", "communication", "outbound"),
    "nexmo": ThirdPartyInfo("Vonage", "communication", "outbound"),
    "slack_sdk": ThirdPartyInfo("Slack", "communication", "outbound"),
    "slackclient": ThirdPartyInfo("Slack", "communication", "outbound"),
    "@slack/web-api": ThirdPartyInfo("Slack", "communication", "outbound"),
    # --- email ---
    "sendgrid": ThirdPartyInfo("SendGrid", "email", "outbound"),
    "@sendgrid/mail": ThirdPartyInfo("SendGrid", "email", "outbound"),
    "mailgun": ThirdPartyInfo("Mailgun", "email", "outbound"),
    "postmark": ThirdPartyInfo("Postmark", "email", "outbound"),
    "postmarker": ThirdPartyInfo("Postmark", "email", "outbound"),
    "resend": ThirdPartyInfo("Resend", "email", "outbound"),
    # --- ai ---
    "openai": ThirdPartyInfo("OpenAI", "ai", "bidirectional"),
    "anthropic": ThirdPartyInfo("Anthropic", "ai", "bidirectional"),
    "cohere": ThirdPartyInfo("Cohere", "ai", "bidirectional"),
    "replicate": ThirdPartyInfo("Replicate", "ai", "bidirectional"),
    # --- analytics ---
    "amplitude": ThirdPartyInfo("Amplitude", "analytics", "outbound"),
    "mixpanel": ThirdPartyInfo("Mixpanel", "analytics", "outbound"),
    "posthog": ThirdPartyInfo("PostHog", "analytics", "outbound"),
    "@segment/analytics-node": ThirdPartyInfo("Segment", "analytics", "outbound"),
    # --- monitoring ---
    "sentry_sdk": ThirdPartyInfo("Sentry", "monitoring", "outbound"),
    "@sentry/node": ThirdPartyInfo("Sentry", "monitoring", "outbound"),
    "@sentry/browser": ThirdPartyInfo("Sentry", "monitoring", "outbound"),
    "raven": ThirdPartyInfo("Sentry", "monitoring", "outbound"),
    "datadog": ThirdPartyInfo("Datadog", "monitoring", "outbound"),
    "ddtrace": ThirdPartyInfo("Datadog", "monitoring", "outbound"),
    "newrelic": ThirdPartyInfo("New Relic", "monitoring", "outbound"),
    "rollbar": ThirdPartyInfo("Rollbar", "monitoring", "outbound"),
    "bugsnag": ThirdPartyInfo("Bugsnag", "monitoring", "outbound"),
    # --- auth ---
    "auth0": ThirdPartyInfo("Auth0", "auth", "bidirectional"),
    "okta": ThirdPartyInfo("Okta", "auth", "bidirectional"),
    "firebase_admin": ThirdPartyInfo("Firebase", "auth", "bidirectional"),
    # --- search ---
    "algoliasearch": ThirdPartyInfo("Algolia", "search", "bidirectional"),
    "typesense": ThirdPartyInfo("Typesense", "search", "bidirectional"),
    # --- maps ---
    "mapbox": ThirdPartyInfo("Mapbox", "maps", "bidirectional"),
    "googlemaps": ThirdPartyInfo("Google Maps", "maps", "bidirectional"),
    # --- storage / media ---
    "cloudinary": ThirdPartyInfo("Cloudinary", "storage", "bidirectional"),
    # --- dev ---
    "pygithub": ThirdPartyInfo("GitHub", "dev", "bidirectional"),
    "@octokit/rest": ThirdPartyInfo("GitHub", "dev", "bidirectional"),
    "octokit": ThirdPartyInfo("GitHub", "dev", "bidirectional"),
    # --- productivity ---
    "notion_client": ThirdPartyInfo("Notion", "productivity", "bidirectional"),
    "pyairtable": ThirdPartyInfo("Airtable", "productivity", "bidirectional"),
}


# ---------------------------------------------------------------------------
# region:    --- Host parsing
# ---------------------------------------------------------------------------

# Host tokens that are never external (loopback / unspecified).
_LOCAL_HOSTS: frozenset[str] = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]",  # noqa: S104
})

# Suffixes that denote internal / cluster-local names.
_INTERNAL_SUFFIXES: tuple[str, ...] = (
    ".local", ".internal", ".svc", ".cluster.local",
    ".localhost", ".test", ".example", ".invalid",
)

# Private / link-local IPv4 prefixes.
_PRIVATE_PREFIXES: tuple[str, ...] = (
    "10.", "192.168.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
)


def extract_host(url_or_host: str) -> str | None:
    """Return the lower-cased host of a URL, or ``None`` when unavailable.

    Handles absolute URLs (``https://api.x.com:8080/p``), scheme-relative
    (``//api.x.com/p``) and bare hosts (``api.x.com``).  Returns ``None`` for
    root-relative paths (``/api/x``) and unresolved/dynamic hosts (those
    containing ``{`` interpolation markers or ``<dynamic>``).
    """
    raw = url_or_host.strip()
    if not raw or (raw.startswith("/") and not raw.startswith("//")):
        return None

    if "://" in raw or raw.startswith("//"):
        netloc = urlsplit(raw).netloc
    else:
        netloc = urlsplit("//" + raw).netloc

    if not netloc:
        return None

    # Drop userinfo (user:pass@) and port.
    host = netloc.rsplit("@", 1)[-1]
    # IPv6 literal keeps its brackets; only strip a trailing :port otherwise.
    if not host.startswith("["):
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    host = host.strip().lower()

    if not host or "{" in host or "}" in host or "<" in host or ">" in host:
        return None
    return host


def is_external_host(host: str | None) -> bool:
    """Return ``True`` when *host* looks like a public, external address.

    This is the deterministic signal behind "unknown external host =
    third-party".  Loopback, private/link-local IPs, cluster-internal
    suffixes, and single-label service names (no dot) are treated as
    internal.
    """
    if not host:
        return False
    host = host.lower()
    if "{" in host or "}" in host:
        return False
    if host in _LOCAL_HOSTS:
        return False
    if host.startswith(_PRIVATE_PREFIXES):
        return False
    if host.endswith(_INTERNAL_SUFFIXES):
        return False
    # A bare single-label name (no dot) is an internal service name.
    return "." in host


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def classify_host(url_or_host: str) -> ThirdPartyInfo | None:
    """Classify a URL or host to a known third-party vendor.

    Matches on a domain boundary and prefers the longest (most specific)
    suffix.  Returns ``None`` when the host is unknown or unresolved — use
    :func:`is_external_host` to decide whether an unknown host is still
    external.
    """
    host = extract_host(url_or_host)
    if host is None:
        return None

    best: ThirdPartyInfo | None = None
    best_len = 0
    for suffix, info in (*_HOST_EXACT.items(), *_HOST_MAP.items()):
        if (host == suffix or host.endswith("." + suffix)) and len(suffix) > best_len:
            best = info
            best_len = len(suffix)
    return best


def classify_sdk(name: str) -> ThirdPartyInfo | None:
    """Classify an import/package/client name to a known third-party vendor.

    Accepts Python module paths (``sentry_sdk.init`` → ``sentry_sdk``),
    scoped npm packages (``@sentry/node``), and bare package names
    (``stripe``).  Returns ``None`` when unrecognized.
    """
    normalized = name.strip().strip("\"'").lower()
    if not normalized:
        return None

    # Direct hit (covers scoped npm packages like "@sentry/node").
    info = _SDK_MAP.get(normalized)
    if info is not None:
        return info

    # Python module root: "sentry_sdk.init" → "sentry_sdk".
    root = normalized.split(".", 1)[0]
    info = _SDK_MAP.get(root)
    if info is not None:
        return info

    # Unscoped package path: "stripe/lib/x" → "stripe".
    if not normalized.startswith("@"):
        first = normalized.split("/", 1)[0]
        info = _SDK_MAP.get(first)
        if info is not None:
            return info

    return None
