#!/usr/bin/env bash
# Publish ast-intel to an Azure Artifacts PyPI feed.
#
# Defaults for https://dev.azure.com/ayush-azure/LeetNexus (override via env):
#   AZURE_ORG=ayush-azure  AZURE_PROJECT=LeetNexus  AZURE_FEED=python
#
# Required:
#   AZURE_PAT — PAT with Packaging (Read & Write). Or set TWINE_PASSWORD.
#
# Usage:
#   export AZURE_PAT=xxxxxx
#   ./scripts/publish_azure_artifacts.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AZURE_ORG="${AZURE_ORG:-ayush-azure}"
AZURE_PROJECT="${AZURE_PROJECT:-LeetNexus}"
AZURE_FEED="${AZURE_FEED:-python}"

PAT="${AZURE_PAT:-${TWINE_PASSWORD:-}}"
if [[ -z "$PAT" ]]; then
  echo "Set AZURE_PAT (or TWINE_PASSWORD) to a PAT with Packaging Read & Write." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3.13}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

REPO_URL="https://pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/upload/"
INDEX_URL="https://pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/"

VENV="${ROOT}/.venv-publish"
echo "==> Using $PYTHON (venv: $VENV)"
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip build twine
rm -rf dist/ build/
python -m build

echo "==> Uploading to $REPO_URL"
# Azure Artifacts expects username "any" (or anything) + PAT as password
TWINE_USERNAME="${TWINE_USERNAME:-any}" \
TWINE_PASSWORD="$PAT" \
python -m twine upload \
  --repository-url "$REPO_URL" \
  --non-interactive \
  dist/*

echo
echo "Published. Install with:"
echo "  pip install ast-intel[all,analysis,mcp] \\"
echo "    --index-url ${INDEX_URL} \\"
echo "    --extra-index-url https://pypi.org/simple"
