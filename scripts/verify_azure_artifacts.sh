#!/usr/bin/env bash
# Verify ast-intel is installable from the LeetNexus Azure Artifacts feed.
#
# Usage (PAT must already be exported):
#   export AZURE_PAT=...
#   ./scripts/verify_azure_artifacts.sh
set -euo pipefail

AZURE_ORG="${AZURE_ORG:-ayush-azure}"
AZURE_PROJECT="${AZURE_PROJECT:-LeetNexus}"
AZURE_FEED="${AZURE_FEED:-python}"
PKG_VERSION="${PKG_VERSION:-0.1.8}"

: "${AZURE_PAT:?Set AZURE_PAT (Packaging Read)}"

INDEX_URL="https://pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/"
AUTH_INDEX="https://build:${AZURE_PAT}@pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/"

echo "==> Listing package on feed index"
code="$(curl -sS -o /tmp/ast-intel-simple.html -w '%{http_code}' \
  -u "build:${AZURE_PAT}" \
  "${INDEX_URL}ast-intel/")"
echo "HTTP ${code}"
if [[ "${code}" != "200" ]]; then
  echo "Feed index failed. Body:" >&2
  head -c 400 /tmp/ast-intel-simple.html >&2 || true
  exit 1
fi
if ! grep -qi "0\\.1\\.8\\|ast.intel\\|ast_intel" /tmp/ast-intel-simple.html; then
  echo "Index returned 200 but version ${PKG_VERSION} not obviously listed:" >&2
  head -c 800 /tmp/ast-intel-simple.html >&2
  echo >&2
  exit 1
fi
echo "Index lists ast-intel (saw version markers)."

echo "==> Smoke install into a throwaway venv"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
python3.13 -m venv "${TMP}/v" 2>/dev/null || python3 -m venv "${TMP}/v"
# shellcheck disable=SC1091
source "${TMP}/v/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q "ast-intel==${PKG_VERSION}" \
  --index-url "${AUTH_INDEX}" \
  --extra-index-url "https://pypi.org/simple"
python -c "import ast_intel; print('TOOL_VERSION=', ast_intel.TOOL_VERSION)"
command -v ast-intel >/dev/null
ast-intel --version

echo
echo "OK — feed publish verified for ast-intel==${PKG_VERSION}"
