"""Tests for third-party SDK egress detection (P3)."""

from __future__ import annotations

from pathlib import Path

from ast_intel.models.ast_node import Confidence, PayloadField


def _sdk_calls(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor().extract(Path("test.py"), source).sdk_calls


def _payload_map(call) -> dict[tuple[str, str], PayloadField]:
    return {(f.location, f.name): f for f in call.payload}


# ---------------------------------------------------------------------------
# region:    --- Basic detection
# ---------------------------------------------------------------------------


class TestSdkDetection:
    def test_module_call_with_kwargs(self) -> None:
        source = b"""
import stripe
def charge(token):
    stripe.Charge.create(amount=2000, currency="usd", source=token)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "Stripe"
        assert call.category == "payments"
        assert call.sdk == "stripe"
        assert call.method == "stripe.Charge.create"
        assert call.caller == "charge"
        pm = _payload_map(call)
        assert pm[("body", "amount")].value == "2000"
        assert pm[("body", "currency")].value == "usd"
        assert pm[("body", "source")].value_kind == "variable"

    def test_import_alias(self) -> None:
        source = b"""
import stripe as strp
def charge():
    strp.Charge.create(amount=1)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "Stripe"
        assert call.sdk == "stripe"

    def test_method_chain_module(self) -> None:
        source = b"""
import openai
def ask(msgs):
    openai.chat.completions.create(model="gpt-4", messages=msgs)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "OpenAI"
        assert call.method == "openai.chat.completions.create"
        assert call.payload_confidence == Confidence.INFERRED

    def test_from_import_bare_function(self) -> None:
        source = b"""
import sentry_sdk
def boom(err):
    sentry_sdk.capture_exception(err)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "Sentry"
        assert call.method == "sentry_sdk.capture_exception"
        (field,) = call.payload
        assert field.location == "body"
        assert field.value == "err"
        assert field.value_kind == "variable"

    def test_from_import_constructor_positional(self) -> None:
        source = b"""
from twilio.rest import Client
def make(sid, tok):
    Client(sid, tok)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "Twilio"
        assert call.method == "Client"
        assert len(call.payload) == 2
        assert all(f.location == "body" for f in call.payload)


# endregion: --- Basic detection


# ---------------------------------------------------------------------------
# region:    --- Redaction + confidence
# ---------------------------------------------------------------------------


class TestSdkRedactionConfidence:
    def test_secret_kwarg_redacted(self) -> None:
        source = b"""
import stripe
def charge():
    stripe.Charge.create(api_key="sk_test_ABCDEFabcdef0123", amount=5)
"""
        (call,) = _sdk_calls(source)
        pm = _payload_map(call)
        assert pm[("body", "api_key")].redacted is True
        assert pm[("body", "api_key")].value == "<redacted>"
        assert pm[("body", "amount")].redacted is False

    def test_literal_only_is_extracted(self) -> None:
        source = b"""
import stripe
def charge():
    stripe.Charge.create(amount=2000, currency="usd")
"""
        (call,) = _sdk_calls(source)
        assert call.payload_confidence == Confidence.EXTRACTED


# endregion: --- Redaction + confidence


# ---------------------------------------------------------------------------
# region:    --- No false positives
# ---------------------------------------------------------------------------


class TestSdkNoFalsePositives:
    def test_non_sdk_import_ignored(self) -> None:
        source = b"""
import requests
import os
def load():
    requests.get("http://api.x.com/u")
    os.getenv("HOME")
"""
        assert _sdk_calls(source) == []

    def test_local_variable_not_resolved_here(self) -> None:
        # Calls on an instance variable are deferred to the L2 data-flow pass.
        source = b"""
import stripe
def charge():
    client = stripe.StripeClient("k")
    client.charges.create(amount=1)
"""
        calls = _sdk_calls(source)
        methods = {c.method for c in calls}
        # The direct `stripe.StripeClient(...)` construction is detected,
        # but `client.charges.create(...)` is not (client is a local var).
        assert "stripe.StripeClient" in methods
        assert "client.charges.create" not in methods


# endregion: --- No false positives


# ---------------------------------------------------------------------------
# region:    --- Unknown third-party SDK detection (heuristic)
# ---------------------------------------------------------------------------


class TestUnknownSdkDetection:
    def test_unknown_third_party_flagged(self) -> None:
        source = b"""
import acmesdk
def notify(payload):
    acmesdk.send_event(payload)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "unknown"
        assert call.category == "unknown"
        assert call.sdk == "acmesdk"
        assert call.method == "acmesdk.send_event"

    def test_alias_unknown(self) -> None:
        source = b"""
import acmesdk as ac
def notify(payload):
    ac.track(payload)
"""
        (call,) = _sdk_calls(source)
        assert call.vendor == "unknown"
        assert call.sdk == "acmesdk"

    def test_stdlib_not_flagged(self) -> None:
        source = b"""
import json
import os
def f(x):
    json.dumps(x)
    os.getenv("X")
"""
        assert _sdk_calls(source) == []

    def test_http_and_cloud_libs_excluded(self) -> None:
        # Handled by the HTTP-call / cloud-resource detectors, not here.
        source = b"""
import httpx
import boto3
def f(url):
    httpx.post(url, json={"a": 1})
    boto3.client("s3")
"""
        assert _sdk_calls(source) == []


# endregion: --- Unknown third-party SDK detection (heuristic)

