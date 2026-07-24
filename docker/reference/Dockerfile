# syntax=docker/dockerfile:1.6
# Reference image: proves install + entrypoint dual-process without Azure.
# Builds from a local wheel (CI / laptop). Production services should use
# docker/service.Dockerfile.snippet (Azure Artifacts + BuildKit secret).
#
# From repo root:
#   python -m build
#   docker build -f docker/reference/Dockerfile -t ast-intel-reference:local .

FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install ast-intel from the pre-built wheel copied into the build context
COPY dist/ast_intel-*-py3-none-any.whl /tmp/wheels/
RUN whl="$(ls /tmp/wheels/ast_intel-*-py3-none-any.whl | sort -V | tail -1)" \
    && pip install --no-cache-dir "${whl}[all,analysis,mcp]" \
    && rm -rf /tmp/wheels

COPY docker/reference/app.py /app/app.py
COPY docker/reference/sample_src/ /app/sample_src/
COPY docker/entrypoint-with-mcp.sh /usr/local/bin/entrypoint-with-mcp.sh
RUN chmod +x /usr/local/bin/entrypoint-with-mcp.sh

ENV AST_INTEL_REPO=/app \
    AST_INTEL_HOST=0.0.0.0 \
    AST_INTEL_PORT=7500

EXPOSE 7500 8080
ENTRYPOINT ["entrypoint-with-mcp.sh"]
CMD ["python", "/app/app.py"]
