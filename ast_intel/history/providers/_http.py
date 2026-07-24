"""Tiny stdlib-only HTTP helper.

We deliberately avoid taking on ``httpx`` or ``requests`` as a runtime
dependency. :mod:`urllib.request` covers the GET/POST + custom-header
needs of the provider adapters with a fraction of the install
footprint.

The helper supports:

- Bearer / Basic auth via the ``headers`` parameter
- JSON request bodies (auto-serialised, content-type set)
- A hard ``timeout`` (default 15 s) to bound stuck calls
- A retry-once strategy on 5xx with exponential backoff
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

__all__: list[str] = ["HttpError", "http_get", "http_post"]

logger = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """Raised when an HTTP request returns a non-2xx status."""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} on {url}: {body[:200]}")


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    body: bytes | None,
    timeout: float,
) -> Any:
    """Issue *method* against *url* and return parsed JSON."""
    req = urllib.request.Request(  # noqa: S310 — URLs originate from trusted git config.
        url,
        method=method,
        data=body,
        headers={"Accept": "application/json", **(headers or {})},
    )
    last_exc: Exception | None = None
    backoffs = (0.0, 0.5)
    for attempt, delay in enumerate(backoffs):
        if delay > 0:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            min_retryable = 500
            if exc.code < min_retryable or attempt == len(backoffs) - 1:
                raise HttpError(exc.code, url, text) from exc
            last_exc = exc
            logger.debug("Retrying %s %s after HTTP %s", method, url, exc.code)
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt == len(backoffs) - 1:
                msg = f"Network error contacting {url}: {exc.reason}"
                raise HttpError(0, url, msg) from exc
    # Unreachable in normal operation.
    raise HttpError(0, url, str(last_exc))


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> Any:
    """GET *url* and return the JSON-decoded body (or raw text)."""
    return _request("GET", url, headers=headers, body=None, timeout=timeout)


def http_post(
    url: str,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> Any:
    """POST a JSON *payload* and return the parsed JSON response."""
    body = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json", **(headers or {})}
    return _request("POST", url, headers=merged, body=body, timeout=timeout)
