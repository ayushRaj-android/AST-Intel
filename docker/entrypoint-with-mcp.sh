#!/usr/bin/env bash
# Start ast-intel MCP (Streamable HTTP) alongside the service process.
#
# Env (defaults match the multi-service Docker plan):
#   AST_INTEL_REPO   — path to source tree to index (default: /app)
#   AST_INTEL_HOST   — bind address (default: 0.0.0.0)
#   AST_INTEL_PORT   — MCP port (default: 7500)
#   AST_INTEL_EXTRA_ARGS — optional extra flags for `ast-intel serve`
#
# Usage as Docker ENTRYPOINT:
#   ENTRYPOINT ["entrypoint-with-mcp.sh"]
#   CMD ["your-service", "--flag"]
set -euo pipefail

REPO="${AST_INTEL_REPO:-/app}"
HOST="${AST_INTEL_HOST:-0.0.0.0}"
PORT="${AST_INTEL_PORT:-7500}"
# shellcheck disable=SC2206
EXTRA=(${AST_INTEL_EXTRA_ARGS:-})

if ! command -v ast-intel >/dev/null 2>&1; then
  echo "entrypoint-with-mcp: ast-intel not on PATH" >&2
  exit 1
fi

echo "entrypoint-with-mcp: starting MCP at http://${HOST}:${PORT}/mcp (repo=${REPO})"
ast-intel serve "${REPO}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --no-watch \
  "${EXTRA[@]}" &
MCP_PID=$!

cleanup() {
  if kill -0 "${MCP_PID}" 2>/dev/null; then
    kill "${MCP_PID}" 2>/dev/null || true
    wait "${MCP_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Wait until the MCP HTTP port accepts connections (max ~30s)
python_bin="$(command -v python3 || command -v python || true)"
ready=0
for _ in $(seq 1 60); do
  if [[ -n "${python_bin}" ]]; then
    if "${python_bin}" - <<PY 2>/dev/null
import socket
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int("${PORT}")))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
    then
      ready=1
      break
    fi
  elif command -v curl >/dev/null 2>&1; then
    if curl -sf "http://127.0.0.1:${PORT}/mcp" >/dev/null 2>&1; then
      ready=1
      break
    fi
  else
    # No probe tool — brief sleep then proceed
    sleep 2
    ready=1
    break
  fi
  if ! kill -0 "${MCP_PID}" 2>/dev/null; then
    echo "entrypoint-with-mcp: MCP process exited before becoming ready" >&2
    exit 1
  fi
  sleep 0.5
done

if [[ "${ready}" -ne 1 ]]; then
  echo "entrypoint-with-mcp: timed out waiting for MCP on port ${PORT}" >&2
  exit 1
fi

echo "entrypoint-with-mcp: MCP ready; exec $*"
exec "$@"
