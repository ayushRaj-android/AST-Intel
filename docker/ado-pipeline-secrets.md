# Azure DevOps variable group for service Docker builds
#
# Project: https://dev.azure.com/ayush-azure/LeetNexus
# Feed index:
#   https://pkgs.dev.azure.com/ayush-azure/LeetNexus/_packaging/python/pypi/simple/
#
# Variable group "ast-intel-artifacts":
#   AZURE_ORG     = ayush-azure       (plain)
#   AZURE_PROJECT = LeetNexus         (plain)
#   AZURE_FEED    = python            (plain)
#   AZURE_PAT     = <secret>          (Packaging Read)
#
#   docker build \
#     --secret id=ado_pat,env=AZURE_PAT \
#     --build-arg AZURE_ORG=$(AZURE_ORG) \
#     --build-arg AZURE_PROJECT=$(AZURE_PROJECT) \
#     --build-arg AZURE_FEED=$(AZURE_FEED) \
#     -t $(imageName):$(tag) .
#
# Copy into each service:
#   - docker/service.Dockerfile.snippet  (adapt CMD)
#   - docker/entrypoint-with-mcp.sh
