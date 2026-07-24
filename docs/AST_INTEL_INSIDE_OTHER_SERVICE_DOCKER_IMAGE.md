# AST Intel Inside Other Service Docker Images

How to install private `ast-intel` into another service’s Docker image and run the
MCP server alongside that service (1A + 2C).

**Feed (private):** [ayush-azure / LeetNexus](https://dev.azure.com/ayush-azure/LeetNexus)  
**Index URL:**

```text
https://pkgs.dev.azure.com/ayush-azure/LeetNexus/_packaging/python/pypi/simple/
```

---

## Architecture

```text
┌─ service container ──────────────────────────────────────┐
│                                                          │
│   ast-intel serve /app --host 0.0.0.0 --port 7500        │
│        │                                                 │
│        └── Streamable HTTP ──► http://127.0.0.1:7500/mcp │
│                                                          │
│   your-service-process                                   │
│        │                                                 │
│        └── runtime agents call MCP at /mcp (localhost)   │
│                                                          │
│   /app  ← COPY’d source (required for indexing)          │
└──────────────────────────────────────────────────────────┘

Developer laptop (separate):
  ast-intel install cursor   → stdio MCP (no container required)
```

| Client | Transport | URL / command |
|--------|-----------|----------------|
| Runtime agents in the container | Streamable HTTP | `http://127.0.0.1:7500/mcp` |
| Cursor / VS Code on a laptop | stdio | `ast-intel serve /abs/path/to/service` |
| Remote IDE (optional, needs auth) | Streamable HTTP | `https://…:7500/mcp` behind gateway |

---

## Prerequisites

1. `ast-intel` published to the LeetNexus Azure Artifacts feed (already done for `0.1.8+`).
2. A PAT (or pipeline secret) with **Packaging → Read** for image builds.
3. Docker BuildKit enabled (`DOCKER_BUILDKIT=1` / Docker Desktop default).
4. Service source is copied into the image (MCP indexes `/app`).

---

## Step 1 — Copy templates into the service repo

From the `ast-intel` repository, copy:

| File | Into service repo |
|------|-------------------|
| [`docker/entrypoint-with-mcp.sh`](../docker/entrypoint-with-mcp.sh) | `docker/entrypoint-with-mcp.sh` |
| Pattern from [`docker/service.Dockerfile.snippet`](../docker/service.Dockerfile.snippet) | Merge into your `Dockerfile` |

Make the entrypoint executable in git:

```bash
chmod +x docker/entrypoint-with-mcp.sh
```

---

## Step 2 — Dockerfile pattern

```dockerfile
# syntax=docker/dockerfile:1.6
FROM python:3.13-slim
# Or keep your existing base image and only add the install + entrypoint blocks.

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    && rm -rf /var/lib/apt/lists/*

ARG AZURE_ORG=ayush-azure
ARG AZURE_PROJECT=LeetNexus
ARG AZURE_FEED=python

# Install ast-intel from private Azure Artifacts.
# PAT must be a BuildKit secret — never ENV or ARG for the token value.
RUN --mount=type=secret,id=ado_pat \
    PAT="$(cat /run/secrets/ado_pat)" && \
    pip install --no-cache-dir "ast-intel[all,analysis,mcp]" \
      --index-url "https://build:${PAT}@pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/" \
      --extra-index-url "https://pypi.org/simple"

WORKDIR /app
COPY . /app
# RUN pip install --no-cache-dir .    # install YOUR service as usual

COPY docker/entrypoint-with-mcp.sh /usr/local/bin/entrypoint-with-mcp.sh
RUN chmod +x /usr/local/bin/entrypoint-with-mcp.sh

ENV AST_INTEL_REPO=/app \
    AST_INTEL_HOST=0.0.0.0 \
    AST_INTEL_PORT=7500

EXPOSE 7500
# Also EXPOSE your service port (e.g. 8080)

ENTRYPOINT ["entrypoint-with-mcp.sh"]
CMD ["python", "-m", "your_service"]
```

### What the entrypoint does

1. Starts `ast-intel serve "$AST_INTEL_REPO" --host … --port … --no-watch` in the background.
2. Waits until port `7500` accepts connections.
3. `exec`s your real `CMD` (the service).

Environment overrides:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AST_INTEL_REPO` | `/app` | Path MCP indexes |
| `AST_INTEL_HOST` | `0.0.0.0` | Bind address |
| `AST_INTEL_PORT` | `7500` | MCP HTTP port |
| `AST_INTEL_EXTRA_ARGS` | _(empty)_ | Extra flags for `ast-intel serve` |

---

## Step 3 — Build the image

### Local

```bash
export AZURE_PAT='...'   # Packaging Read

docker build \
  --secret id=ado_pat,env=AZURE_PAT \
  --build-arg AZURE_ORG=ayush-azure \
  --build-arg AZURE_PROJECT=LeetNexus \
  --build-arg AZURE_FEED=python \
  -t my-service:latest .
```

### Azure Pipelines

1. Variable group (e.g. `ast-intel-artifacts`):
   - `AZURE_ORG` = `ayush-azure`
   - `AZURE_PROJECT` = `LeetNexus`
   - `AZURE_FEED` = `python`
   - `AZURE_PAT` = secret (Packaging Read)
2. Build step:

```yaml
- script: |
    docker build \
      --secret id=ado_pat,env=AZURE_PAT \
      --build-arg AZURE_ORG=$(AZURE_ORG) \
      --build-arg AZURE_PROJECT=$(AZURE_PROJECT) \
      --build-arg AZURE_FEED=$(AZURE_FEED) \
      -t $(imageName):$(tag) .
  displayName: Build service image with ast-intel
  env:
    AZURE_PAT: $(AZURE_PAT)
```

See also [`docker/ado-pipeline-secrets.md`](../docker/ado-pipeline-secrets.md).

---

## Step 4 — Use the MCP server

### A. Runtime agents inside the same container (primary)

Configure your agent / MCP client:

```text
http://127.0.0.1:7500/mcp
```

Optional convenience env for the app:

```bash
AST_INTEL_MCP_URL=http://127.0.0.1:7500/mcp
```

Keep `7500` container-local unless a service mesh / API gateway terminates auth.

### B. Developer IDE on a laptop (stdio)

Install from the same feed (once per machine):

```bash
pip install "ast-intel[all,analysis,mcp]" \
  --index-url "https://pkgs.dev.azure.com/ayush-azure/LeetNexus/_packaging/python/pypi/simple/" \
  --extra-index-url https://pypi.org/simple
```

In the **service** checkout:

```bash
ast-intel install cursor    # or: vscode / claude / windsurf
```

That writes local MCP config pointing at `ast-intel serve <absolute-repo-path>` (stdio).  
Developers do **not** need the Docker MCP port for day-to-day coding.

### C. Remote URL-based MCP (optional)

Only when `7500` is exposed behind Easy Auth / mTLS / API key:

```json
{
  "mcpServers": {
    "ast-intel": {
      "url": "https://my-service.example.com/mcp"
    }
  }
}
```

Do **not** expose MCP on the public internet without authentication.

---

## Step 5 — Smoke test

```bash
docker run --rm -p 8080:8080 -p 7500:7500 my-service:latest
```

MCP initialize:

```bash
curl -sS -X POST http://127.0.0.1:7500/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "smoke", "version": "0"}
    }
  }'
```

Expect HTTP **200** and `"name": "ast-intel"` in `result.serverInfo`.

From the `ast-intel` repo you can also run:

```bash
export AZURE_PAT='...'
./scripts/verify_mcp_http.sh      # install + HTTP tools/list
./scripts/verify_azure_artifacts.sh
```

---

## Checklist (per service)

- [ ] `docker/entrypoint-with-mcp.sh` copied and executable  
- [ ] Dockerfile installs `ast-intel[all,analysis,mcp]` via BuildKit secret  
- [ ] `COPY . /app` (or equivalent) so source is present for indexing  
- [ ] `ENTRYPOINT` is the MCP wrapper; `CMD` is the real service  
- [ ] CI provides `AZURE_PAT` as a secret  
- [ ] In-container agents use `http://127.0.0.1:7500/mcp`  
- [ ] Local IDE uses `ast-intel install` (stdio)  
- [ ] Port `7500` not public without auth  

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `pip` 401 during build | Missing/invalid PAT | Pass `--secret id=ado_pat,env=AZURE_PAT` with Packaging Read |
| MCP never becomes ready | `ast-intel` not on PATH / wrong extras | Install `[all,analysis,mcp]`; check entrypoint logs |
| `networkx is required` | Missing `analysis` extra | Use `ast-intel[all,analysis,mcp]` |
| Empty / wrong graph | Source not in image or wrong `AST_INTEL_REPO` | Ensure `COPY` and `AST_INTEL_REPO=/app` |
| IDE cannot connect to container MCP | Using stdio config against HTTP | Use `"url": "http://…:7500/mcp"` or keep local stdio install |
| 307 on `/mcp` | Old image without path fix | Use `ast-intel>=0.1.8` |

---

## Related files in this repo

| Path | Role |
|------|------|
| [`docker/service.Dockerfile.snippet`](../docker/service.Dockerfile.snippet) | Copy-paste Dockerfile pattern |
| [`docker/entrypoint-with-mcp.sh`](../docker/entrypoint-with-mcp.sh) | Dual-process entrypoint |
| [`docker/client-wiring.md`](../docker/client-wiring.md) | IDE vs HTTP client snippets |
| [`docker/reference/`](../docker/reference/) | Local proof image (wheel, no Azure) |
| [`scripts/publish_azure_artifacts.sh`](../scripts/publish_azure_artifacts.sh) | Publish new versions to the feed |
| [`azure-pipelines.yml`](../azure-pipelines.yml) | Tag-triggered republish of `ast-intel` |
| [`docs/SETUP_INSTRUCTIONS_README.md`](SETUP_INSTRUCTIONS_README.md) | Broader setup / install methods |
