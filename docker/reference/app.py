"""Minimal demo service used by docker/reference to prove MCP dual-process."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = {
            "service": "ast-intel-reference",
            "mcp": "http://127.0.0.1:7500/mcp",
            "hint": "Runtime agents should use Streamable HTTP at mcp URL",
        }
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> None:
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
