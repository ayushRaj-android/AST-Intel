"""Tests for intra-function data-flow resolution (P4 / L2)."""

from __future__ import annotations

from pathlib import Path

from ast_intel.extractors._egress_dataflow import SOURCE_KINDS


def _http(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor().extract(Path("test.py"), source).http_calls


def _sdk(source: bytes) -> list:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor().extract(Path("test.py"), source).sdk_calls


def _field(call, location: str, name: str):
    for f in call.payload:
        if f.location == location and f.name == name:
            return f
    msg = f"no field {location}/{name} in {call.payload}"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# region:    --- source_kind resolution
# ---------------------------------------------------------------------------


class TestSourceResolution:
    def test_literal_assignment(self) -> None:
        source = b"""
import requests
def send():
    key = "abc"
    requests.post("http://api.x.com/u", json={"k": key})
"""
        (call,) = _http(source)
        assert _field(call, "body", "k").source_kind == "literal"

    def test_from_input_whole_body(self) -> None:
        source = b"""
import requests
def send():
    data = request.get_json()
    requests.post("http://api.x.com/u", json=data)
"""
        (call,) = _http(source)
        (field,) = call.payload
        assert field.source_kind == "from-input"

    def test_from_env(self) -> None:
        source = b"""
import requests
def send():
    tok = os.environ["T"]
    requests.post("http://api.x.com/u", json={"t": tok})
"""
        (call,) = _http(source)
        assert _field(call, "body", "t").source_kind == "from-env"

    def test_from_db(self) -> None:
        source = b"""
import requests
def send():
    row = db.query(User).first()
    requests.post("http://api.x.com/u", json={"u": row})
"""
        (call,) = _http(source)
        assert _field(call, "body", "u").source_kind == "from-db"

    def test_computed(self) -> None:
        source = b"""
import requests
def send(a, b):
    total = a + b
    requests.post("http://api.x.com/u", json={"total": total})
"""
        (call,) = _http(source)
        assert _field(call, "body", "total").source_kind == "computed"

    def test_parameter_is_unknown(self) -> None:
        source = b"""
import requests
def send(x):
    requests.post("http://api.x.com/u", json={"x": x})
"""
        (call,) = _http(source)
        assert _field(call, "body", "x").source_kind == "unknown"

    def test_alias_hop(self) -> None:
        source = b"""
import requests
def send():
    raw = request.args
    v = raw
    requests.post("http://api.x.com/u", json={"v": v})
"""
        (call,) = _http(source)
        assert _field(call, "body", "v").source_kind == "from-input"


# endregion: --- source_kind resolution


# ---------------------------------------------------------------------------
# region:    --- Non-variable + redaction interaction
# ---------------------------------------------------------------------------


class TestNonVariableAndRedaction:
    def test_expression_is_computed(self) -> None:
        source = b"""
import requests
def send(user):
    requests.post("http://api.x.com/u", json={"e": user.email})
"""
        (call,) = _http(source)
        assert _field(call, "body", "e").source_kind == "computed"

    def test_redacted_secret_not_resolved(self) -> None:
        source = b"""
import requests
def send():
    tok = os.environ["T"]
    requests.post("http://api.x.com/u", headers={"Authorization": tok})
"""
        (call,) = _http(source)
        field = _field(call, "header", "Authorization")
        assert field.redacted is True
        assert field.source_kind == "unknown"

    def test_all_source_kinds_valid(self) -> None:
        source = b"""
import requests
def send(p):
    a = "x"
    b = os.getenv("B")
    requests.post("http://api.x.com/u", json={"a": a, "b": b, "p": p, "c": p.y})
"""
        (call,) = _http(source)
        for f in call.payload:
            assert f.source_kind in SOURCE_KINDS


# endregion: --- Non-variable + redaction interaction


# ---------------------------------------------------------------------------
# region:    --- SDK path
# ---------------------------------------------------------------------------


class TestSdkResolution:
    def test_sdk_kwarg_resolved(self) -> None:
        source = b"""
import stripe
def charge():
    key = os.getenv("K")
    stripe.Charge.create(source=key)
"""
        (call,) = _sdk(source)
        assert _field(call, "body", "source").source_kind == "from-env"


# endregion: --- SDK path
