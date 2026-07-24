#!/usr/bin/env bash
# End-to-end: install (Artifacts or local wheel) + MCP Streamable HTTP smoke test.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${AST_INTEL_TEST_PORT:-17501}"
HOST=127.0.0.1
TMP="$(mktemp -d)"
trap 'kill ${MCP_PID:-} 2>/dev/null || true; rm -rf "${TMP}"' EXIT

echo "==> 1) Install ast-intel into throwaway venv"
python3.13 -m venv "${TMP}/v" 2>/dev/null || python3 -m venv "${TMP}/v"
# shellcheck disable=SC1091
source "${TMP}/v/bin/activate"
python -m pip install -q --upgrade pip

if [[ -n "${AZURE_PAT:-}" ]]; then
  INDEX="https://build:${AZURE_PAT}@pkgs.dev.azure.com/ayush-azure/LeetNexus/_packaging/python/pypi/simple/"
  echo "    source: Azure Artifacts (LeetNexus/python)"
  python -m pip install -q "ast-intel[all,analysis,mcp]==0.1.8" \
    --index-url "${INDEX}" \
    --extra-index-url "https://pypi.org/simple"
else
  WHL="$(ls -1 "${ROOT}"/dist/ast_intel-0.1.8-py3-none-any.whl 2>/dev/null | head -1 || true)"
  if [[ -z "${WHL}" ]]; then
    echo "No AZURE_PAT and no local dist/ast_intel-0.1.8 wheel. Build or set PAT." >&2
    exit 1
  fi
  echo "    source: local wheel ${WHL} (AZURE_PAT not set)"
  python -m pip install -q "${WHL}[all,analysis,mcp]"
fi

python -c "import ast_intel; assert ast_intel.TOOL_VERSION=='0.1.8'; print('    TOOL_VERSION=', ast_intel.TOOL_VERSION)"
ast-intel --version

# Tiny repo for the server to index
mkdir -p "${TMP}/repo"
cat > "${TMP}/repo/sample.py" <<'PY'
def hello(name: str) -> str:
    return f"hi {name}"
PY

echo "==> 2) Start MCP Streamable HTTP on ${HOST}:${PORT}"
ast-intel serve "${TMP}/repo" --host "${HOST}" --port "${PORT}" --no-watch --no-similarity &
MCP_PID=$!

ready=0
for _ in $(seq 1 60); do
  if python - <<PY 2>/dev/null
import socket
s=socket.socket(); s.settimeout(0.3)
try:
    s.connect(("${HOST}", int("${PORT}")))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
  then ready=1; break; fi
  if ! kill -0 "${MCP_PID}" 2>/dev/null; then
    echo "MCP process died" >&2
    wait "${MCP_PID}" || true
    exit 1
  fi
  sleep 0.5
done
[[ "${ready}" == 1 ]] || { echo "timeout waiting for MCP"; exit 1; }

HDR=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')

echo "==> 3) initialize"
INIT="$(curl -sS "${HDR[@]}" -X POST "http://${HOST}:${PORT}/mcp" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}')"
echo "${INIT}" | python -c 'import json,sys; d=json.load(sys.stdin); assert d["result"]["serverInfo"]["name"]=="ast-intel"; print("    server=", d["result"]["serverInfo"]["name"])'

curl -sS "${HDR[@]}" -X POST "http://${HOST}:${PORT}/mcp" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null

echo "==> 4) tools/list"
TOOLS="$(curl -sS "${HDR[@]}" -X POST "http://${HOST}:${PORT}/mcp" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')"
echo "${TOOLS}" | python -c 'import json,sys; d=json.load(sys.stdin); names={t["name"] for t in d["result"]["tools"]}; assert "search_symbols" in names and "get_context" in names; print("    tools=", len(names), "(search_symbols, get_context ok)")'

echo "==> 5) search_symbols via HTTP"
SEARCH="$(curl -sS "${HDR[@]}" -X POST "http://${HOST}:${PORT}/mcp" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_symbols","arguments":{"pattern":"hello"}}}')"
echo "${SEARCH}" | python -c 'import json,sys; d=json.load(sys.stdin); assert "result" in d; print("    search_symbols ok")'

echo
echo "OK — install + MCP HTTP verified"
echo "MCP URL: http://${HOST}:${PORT}/mcp"
echo "Example client setting:"
cat <<EOF
{
  "mcpServers": {
    "ast-intel": {
      "url": "http://${HOST}:${PORT}/mcp"
    }
  }
}
EOF
