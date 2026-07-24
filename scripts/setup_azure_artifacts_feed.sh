#!/usr/bin/env bash
# Create (or show) an Azure Artifacts Python feed for ast-intel.
#
# Defaults for https://dev.azure.com/ayush-azure/LeetNexus:
#   AZURE_ORG=ayush-azure  AZURE_PROJECT=LeetNexus  AZURE_FEED=python
#
# Prerequisites: Azure CLI (`az`) + `azure-devops` extension, logged in.
#
# Usage:
#   ./scripts/setup_azure_artifacts_feed.sh
set -euo pipefail

AZURE_ORG="${AZURE_ORG:-ayush-azure}"
AZURE_PROJECT="${AZURE_PROJECT:-LeetNexus}"
AZURE_FEED="${AZURE_FEED:-python}"

if ! command -v az >/dev/null 2>&1; then
  echo "Install Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  echo "Or create the feed in the portal:" >&2
  echo "  https://dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_artifacts" >&2
  exit 1
fi

az extension add --name azure-devops --upgrade 2>/dev/null || az extension add --name azure-devops
az devops configure --defaults organization="https://dev.azure.com/${AZURE_ORG}" project="${AZURE_PROJECT}"

INDEX_URL="https://pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/"
SCOPE_ARGS=(--project "${AZURE_PROJECT}" --org "https://dev.azure.com/${AZURE_ORG}")

if az artifacts feed show --feed "${AZURE_FEED}" "${SCOPE_ARGS[@]}" >/dev/null 2>&1; then
  echo "Feed already exists: ${AZURE_FEED}"
else
  echo "Creating project-scoped feed: ${AZURE_FEED} in ${AZURE_PROJECT}"
  az artifacts feed create \
    --name "${AZURE_FEED}" \
    --description "Private PyPI feed for ast-intel" \
    --project "${AZURE_PROJECT}" \
    --org "https://dev.azure.com/${AZURE_ORG}"
  echo "Enable PyPI upstream: Feed → Upstream sources → Add → PyPI"
fi

echo
echo "Next:"
echo "  export AZURE_PAT=<PAT with Packaging Read & Write>"
echo "  ./scripts/publish_azure_artifacts.sh"
echo
echo "Install URL:"
echo "  ${INDEX_URL}"
echo
echo "Portal: https://dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_artifacts"
