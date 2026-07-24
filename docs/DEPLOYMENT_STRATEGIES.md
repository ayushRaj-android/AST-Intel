# AST_INTEL — Deployment Strategies (Azure)

> Generated 2026-04-29 · Architecture Document  
> Covers: CLI distribution, MCP server hosting, CI/CD pipeline options

---

## Table of Contents

1. [Product Surface Area](#product-surface-area)
2. [Strategy 1 — PyPI Package + Local Install (Baseline)](#strategy-1--pypi-package--local-install-baseline)
3. [Strategy 2 — Docker Container on Azure Container Apps](#strategy-2--docker-container-on-azure-container-apps)
4. [Strategy 3 — Azure Container Instances (per-job)](#strategy-3--azure-container-instances-per-job)
5. [Strategy 4 — Azure Kubernetes Service (AKS)](#strategy-4--azure-kubernetes-service-aks)
6. [Strategy 5 — Azure Functions (Serverless)](#strategy-5--azure-functions-serverless)
7. [Strategy 6 — Azure DevOps / GitHub Actions Pipeline Task](#strategy-6--azure-devops--github-actions-pipeline-task)
8. [Strategy 7 — Azure App Service (MCP over HTTP)](#strategy-7--azure-app-service-mcp-over-http)
9. [Comparison Matrix](#comparison-matrix)
10. [Recommended Architecture](#recommended-architecture)
11. [Infrastructure as Code — Sketch](#infrastructure-as-code--sketch)

---

## Product Surface Area

AST_INTEL has **two distinct deployment modes** with very different runtime characteristics:

| Mode | Entry Point | Transport | Lifecycle | State | Typical Consumer |
|-|-|-|-|-|-|
| **CLI (batch)** | `ast-intel scan /repo` | Filesystem I/O | Short-lived (seconds–minutes) | Reads source tree, writes `ast.json` / `summary.md` / `graph.*` | CI pipeline, developer terminal, scripts |
| **MCP Server** | `ast-intel serve /repo` | stdio or HTTP+SSE | Long-lived (hours–days) | Holds `CodeGraph` + `QueryEngine` in memory | AI agents (Copilot, Cursor, Claude) |

### Runtime Requirements

| Requirement | CLI | MCP Server |
|-|-|-|
| **Python** | 3.11+ | 3.11+ |
| **CPU** | Burst (parallel tree-sitter parsing) | Idle mostly, burst on queries |
| **Memory** | Proportional to codebase size (50–500 MB typical) | Same + graph kept in memory for session |
| **Disk** | Needs read access to source tree | Same |
| **Network** | Not required | Inbound connections for HTTP/SSE transport |
| **Native libs** | tree-sitter C extensions (compiled per-platform) | Same |
| **Startup time** | ~2–10s (grammar loading + discovery) | Same + graph build on first load |
| **Concurrency** | Single invocation, internal thread pool | Single async event loop, concurrent MCP tool calls |

### Key Constraints for Deployment Planning

1. **Source code access**: The tool must read the repository source tree. For cloud deployment this means either mounting/cloning the repo or receiving it as input.
2. **Native extensions**: `tree-sitter` and language grammars compile C code. Docker images must include build tools or use pre-built wheels.
3. **Optional heavy deps**: `networkx` + `graspologic` (for `--analyze`) pull in NumPy/SciPy — adds ~200 MB to image size.
4. **No external services required**: No database, no API keys, no message queues — the tool is fully self-contained.

---

## Strategy 1 — PyPI Package + Local Install (Baseline)

### What

Publish to PyPI (or a private Azure Artifacts feed). Users install with `pip install ast-intel[all]`.

### How

```bash
# Public PyPI
pip install ast-intel[all,analysis,mcp]

# Private Azure Artifacts feed
pip install ast-intel[all] --index-url https://pkgs.dev.azure.com/{org}/_packaging/{feed}/pypi/simple/
```

### When to Use

- Developer workstations (the default)
- Local MCP server for editor integration (stdio transport)
- Environments where Python is already available

### Trade-offs

| Pros | Cons |
|-|-|
| Zero infrastructure cost | Requires Python 3.11+ on every machine |
| Simplest distribution | Native extension compilation may fail on exotic platforms |
| Instant updates via `pip install --upgrade` | No centralized management or access control |
| Works with stdio MCP transport natively | Cannot serve MCP over network without manual setup |

### Azure Touchpoints

- **Azure Artifacts**: Host a private PyPI feed for internal distribution
- **Azure DevOps Pipelines**: `pip install` in pipeline YAML

---

## Strategy 2 — Docker Container on Azure Container Apps

### What

Package AST_INTEL into a Docker image. Deploy the **MCP server** as a long-running Azure Container App with HTTP ingress. Deploy the **CLI** as a job-type Container App triggered on demand.

### Why This is the Primary Recommendation

Azure Container Apps (ACA) is the sweet spot: serverless scaling (scale-to-zero), no cluster management, built-in HTTPS ingress, revision-based deployments, and per-second billing.

### How

#### Dockerfile

```dockerfile
# --- Build stage ---
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ast_intel/ ast_intel/

RUN pip install --no-cache-dir ".[all,analysis,mcp]"

# --- Runtime stage ---
FROM python:3.12-slim

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/ast-intel /usr/local/bin/ast-intel
COPY --from=builder /app/ast_intel /app/ast_intel

WORKDIR /workspace
# Default: MCP server mode. Override CMD for CLI batch jobs.
ENTRYPOINT ["ast-intel"]
CMD ["serve", "/workspace", "--similarity"]
```

Image size estimate: ~450 MB (slim Python + tree-sitter grammars + networkx/scipy).

#### MCP Server Deployment (Container App — Service)

```yaml
# infra/container-app-mcp.bicep (conceptual)
resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  properties:
    configuration:
      ingress:
        external: true
        targetPort: 8080
        transport: 'http'
      secrets: []
    template:
      containers:
        - name: 'ast-intel-mcp'
          image: '{acr}.azurecr.io/ast-intel:latest'
          command: ['ast-intel']
          args: ['serve', '/workspace', '--port', '8080']
          resources:
            cpu: 1.0
            memory: '2Gi'
          volumeMounts:
            - volumeName: repo-volume
              mountPath: /workspace
      scale:
        minReplicas: 0    # scale to zero when idle
        maxReplicas: 5
        rules:
          - name: http-scaling
            http:
              metadata:
                concurrentRequests: '10'
      volumes:
        - name: repo-volume
          storageName: repo-share  # Azure Files SMB mount
          storageType: AzureFile
}
```

#### CLI Batch Deployment (Container App — Job)

```yaml
resource scanJob 'Microsoft.App/jobs@2024-03-01' = {
  properties:
    configuration:
      triggerType: 'Event'       # or 'Manual' or 'Schedule'
      replicaTimeout: 600
      eventTriggerConfig:
        scale:
          rules:
            - name: azure-queue
              type: 'azure-queue'
              metadata:
                queueName: 'scan-requests'
    template:
      containers:
        - name: 'ast-intel-scan'
          image: '{acr}.azurecr.io/ast-intel:latest'
          command: ['ast-intel']
          args: ['scan', '/workspace', '--format', 'both', '--analyze']
          resources:
            cpu: 2.0
            memory: '4Gi'
          volumeMounts:
            - volumeName: repo-volume
              mountPath: /workspace
}
```

### Source Code Delivery Patterns

The container needs access to the repo. Options:

| Pattern | How | Best For |
|-|-|-|
| **Azure Files mount** | SMB/NFS share mounted as volume | Repos already on Azure, persistent access |
| **Git clone at startup** | Init container runs `git clone` | CI/CD triggers, always-fresh code |
| **Azure Repos / GitHub webhook** | Webhook triggers job with repo URL param | Event-driven scanning |
| **Blob upload** | Client zips repo, uploads to Blob, container downloads | API-driven service model |

### Trade-offs

| Pros | Cons |
|-|-|
| Scale-to-zero on idle (cost efficient) | Cold start ~10–15s (container pull + Python startup) |
| No cluster management | MCP stdio transport doesn't work over HTTP — need SSE adapter |
| Built-in HTTPS, auth via Easy Auth | Azure Files mount adds latency for large repos |
| Revision-based blue/green deploys | Image size ~450 MB (tree-sitter + scipy) |
| Per-second billing | Requires ACR for private images |

---

## Strategy 3 — Azure Container Instances (per-job)

### What

Spin up ephemeral ACI containers for batch CLI scans. Each scan runs in an isolated container that is destroyed after completion.

### How

```bash
az container create \
  --resource-group ast-intel-rg \
  --name scan-$(date +%s) \
  --image {acr}.azurecr.io/ast-intel:latest \
  --cpu 2 --memory 4 \
  --command-line "ast-intel scan /workspace --format both --analyze" \
  --azure-file-volume-account-name {storage} \
  --azure-file-volume-share-name repos \
  --azure-file-volume-mount-path /workspace \
  --restart-policy Never
```

### When to Use

- One-off or infrequent batch scans (< 10/day)
- When you want full isolation per scan (multi-tenant)
- When Container Apps Jobs feels like overkill

### Trade-offs

| Pros | Cons |
|-|-|
| True per-job isolation | No scale-to-zero (you pay per container-second from creation) |
| Simple CLI / ARM API | Slow startup (~30s for image pull on first run) |
| No infrastructure to manage | No built-in HTTP ingress — bad for MCP server |
| Good for burst workloads | No persistent process — unsuitable for long-lived MCP server |

---

## Strategy 4 — Azure Kubernetes Service (AKS)

### What

Deploy AST_INTEL on a managed Kubernetes cluster. The MCP server runs as a Deployment with a Service + Ingress. CLI scans run as Kubernetes Jobs.

### When to Use

- You already operate an AKS cluster for other workloads
- You need fine-grained control over scheduling, affinity, and resource limits
- Multi-tenant deployment with namespace isolation per team

### How

```yaml
# k8s/mcp-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ast-intel-mcp
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ast-intel-mcp
  template:
    spec:
      containers:
        - name: mcp
          image: {acr}.azurecr.io/ast-intel:latest
          args: ["serve", "/workspace", "--port", "8080"]
          ports:
            - containerPort: 8080
          resources:
            requests: { cpu: "500m", memory: "1Gi" }
            limits: { cpu: "2", memory: "4Gi" }
          volumeMounts:
            - name: repo
              mountPath: /workspace
              readOnly: true
          readinessProbe:
            httpGet:
              path: /health     # Would need a health endpoint
              port: 8080
            initialDelaySeconds: 10
      volumes:
        - name: repo
          persistentVolumeClaim:
            claimName: repo-pvc  # Azure Disk or Azure Files
---
apiVersion: v1
kind: Service
metadata:
  name: ast-intel-mcp
spec:
  type: ClusterIP
  ports:
    - port: 80
      targetPort: 8080
  selector:
    app: ast-intel-mcp
```

```yaml
# k8s/scan-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ast-intel-scan
spec:
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: scan
          image: {acr}.azurecr.io/ast-intel:latest
          args: ["scan", "/workspace", "--format", "both", "--analyze"]
          resources:
            requests: { cpu: "2", memory: "4Gi" }
          volumeMounts:
            - name: repo
              mountPath: /workspace
```

### Trade-offs

| Pros | Cons |
|-|-|
| Full Kubernetes ecosystem (HPA, KEDA, Istio, etc.) | Cluster management overhead (even managed AKS) |
| Multi-replica MCP server with load balancing | Minimum cost = cluster node(s) running 24/7 |
| Fine-grained RBAC and network policies | Overkill if AST_INTEL is the only workload |
| GPU node pools available (future: embedding models) | More YAML to maintain |
| Consistent with large org infrastructure patterns | Requires Kubernetes expertise |

---

## Strategy 5 — Azure Functions (Serverless)

### What

Wrap CLI scan operations as HTTP-triggered Azure Functions. Each function invocation runs a scan and returns the result.

### When to Use

- Lightweight API: "POST a repo URL → GET back `ast.json`"
- Very infrequent usage (pay-per-execution)
- Integration with Azure Logic Apps / Power Automate workflows

### How

```python
# function_app.py
import azure.functions as func
import tempfile, subprocess, json

app = func.FunctionApp()

@app.route(route="scan", methods=["POST"])
def scan_repo(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()
    repo_url = body["repo_url"]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone repo
        subprocess.run(["git", "clone", "--depth", "1", repo_url, tmpdir], check=True)
        # Run AST_INTEL
        subprocess.run(
            ["ast-intel", "scan", tmpdir, "--format", "json", "--output", tmpdir],
            check=True,
        )
        # Read result
        result = (Path(tmpdir) / "ast.json").read_text()
    
    return func.HttpResponse(result, mimetype="application/json")
```

### Critical Limitations

| Limitation | Impact |
|-|-|
| **Execution timeout**: 10 min (Consumption), 60 min (Premium) | Large repos may not complete |
| **Package size**: 250 MB compressed deployment limit | tree-sitter + grammars + scipy may exceed this |
| **Cold start**: 5–30s on Consumption plan | Unacceptable for interactive MCP queries |
| **No persistent process**: Functions are request/response | Cannot run a stateful MCP server |
| **No stdio**: Functions only support HTTP triggers | MCP stdio transport is impossible |

### Trade-offs

| Pros | Cons |
|-|-|
| True pay-per-execution, zero idle cost | Cannot host MCP server (no persistent process) |
| Massive auto-scale (hundreds of concurrent scans) | Cold start latency |
| Built-in Azure AD auth, API Management integration | Deployment size limit is tight |
| Simple HTTP API surface | No filesystem persistence between invocations |

**Verdict**: Viable for batch CLI scans only. **Not suitable for MCP server**.

---

## Strategy 6 — Azure DevOps / GitHub Actions Pipeline Task

### What

Run AST_INTEL as a step in CI/CD pipelines. Not a standalone deployment — the tool runs inside the existing pipeline agent.

### How — Azure DevOps

```yaml
# azure-pipelines.yml
trigger:
  branches:
    include: [main]

pool:
  vmImage: 'ubuntu-latest'

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.12'

  - script: pip install ast-intel[all,analysis]
    displayName: 'Install AST Intel'

  - script: |
      ast-intel scan $(Build.SourcesDirectory) \
        --format both \
        --analyze \
        --output $(Build.ArtifactStagingDirectory)/ast-intel
    displayName: 'Run AST Intel Scan'

  - publish: $(Build.ArtifactStagingDirectory)/ast-intel
    artifact: ast-intel-report
    displayName: 'Publish AST Report'
```

### How — GitHub Actions

```yaml
# .github/workflows/ast-intel.yml
name: AST Intel Scan
on:
  push:
    branches: [main]
  pull_request:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - run: pip install ast-intel[all,analysis]

      - run: |
          ast-intel scan . --format both --analyze --output ./ast-output
          ast-intel scan . --format html --output ./ast-output

      - uses: actions/upload-artifact@v4
        with:
          name: ast-intel-report
          path: ./ast-output/
```

### When to Use

- Every PR should trigger an AST scan (code quality gate)
- Diff-aware analysis: scan before/after and compare graph metrics
- Publish `GRAPH_REPORT.md` as a PR comment
- Store `ast.json` as a build artifact for downstream tools

### Trade-offs

| Pros | Cons |
|-|-|
| Zero infrastructure — runs on existing pipeline agents | Not a standalone service (no MCP server) |
| Source code already checked out | Re-installs on every run (cache pip to mitigate) |
| Naturally integrates with PR workflows | Pipeline minutes cost |
| Can gate PRs on graph metrics (god nodes, complexity) | Limited to CI context |

---

## Strategy 7 — Azure App Service (MCP over HTTP)

### What

Deploy the MCP server as a containerized Azure App Service with always-on enabled. The MCP server uses HTTP+SSE transport instead of stdio.

### When to Use

- You want a managed PaaS experience without containers orchestration
- The MCP server needs to be always-available with minimal cold starts
- You want built-in deployment slots for blue/green releases

### How

```bash
# Create App Service Plan (Linux, B2 tier)
az appservice plan create \
  --name ast-intel-plan \
  --resource-group ast-intel-rg \
  --sku B2 --is-linux

# Create Web App from container
az webapp create \
  --name ast-intel-mcp \
  --resource-group ast-intel-rg \
  --plan ast-intel-plan \
  --deployment-container-image-name {acr}.azurecr.io/ast-intel:latest

# Configure always-on and startup command
az webapp config set \
  --name ast-intel-mcp \
  --resource-group ast-intel-rg \
  --always-on true \
  --startup-file "ast-intel serve /workspace --port 8080"
```

### Trade-offs

| Pros | Cons |
|-|-|
| Always-on eliminates cold starts | Minimum cost ~$50/month (B2 plan) even at zero traffic |
| Deployment slots for zero-downtime releases | Only HTTP transport (no stdio) |
| Built-in Easy Auth (Azure AD) | Less flexible than Container Apps scaling |
| Custom domain + managed TLS certificates | Single instance per App Service (scale up, not out) |
| WebSocket support for SSE | Storage mount performance worse than Container Apps |

---

## Comparison Matrix

| Criterion | PyPI (Local) | Container Apps | ACI | AKS | Functions | Pipeline Task | App Service |
|-|-|-|-|-|-|-|-|
| **CLI batch scans** | ✅ Native | ✅ Jobs | ✅ Ephemeral | ✅ K8s Jobs | ⚠️ Size limits | ✅ Best fit | ❌ Wrong tool |
| **MCP server** | ✅ stdio | ✅ HTTP/SSE | ❌ No persist | ✅ Deployment | ❌ No persist | ❌ Wrong tool | ✅ HTTP/SSE |
| **Scale to zero** | N/A | ✅ | N/A (ephemeral) | ❌ (KEDA possible) | ✅ | N/A | ❌ |
| **Cold start** | 0s | ~10–15s | ~30s | ~5s (warm) | ~5–30s | ~30s (install) | 0s (always-on) |
| **Monthly cost (idle)** | $0 | $0 | $0 | ~$70+ (1 node) | $0 | $0 | ~$50+ |
| **Monthly cost (moderate)** | $0 | ~$20–60 | ~$30–80 | ~$100–200 | ~$5–20 | Pipeline mins | ~$50–100 |
| **Ops complexity** | None | Low | Low | High | Medium | None | Low |
| **Multi-tenant** | ❌ | ✅ (revisions) | ✅ (isolation) | ✅ (namespaces) | ✅ | ❌ | ⚠️ (slots) |
| **Source code access** | Local FS | Volume mount | Volume mount | PVC | Git clone | Checkout step | Volume mount |
| **Auth / RBAC** | OS-level | Easy Auth / APIM | Azure AD | K8s RBAC + AD | Function keys / AD | Pipeline perms | Easy Auth |
| **Best for** | Developers | **Most teams** | Sporadic jobs | Large orgs | Scan API | CI/CD gates | Simple MCP host |

---

## Recommended Architecture

For a typical team deploying AST_INTEL on Azure, the recommended combination is:

```
┌─────────────────────────────────────────────────────────────────┐
│                        Azure Cloud                              │
│                                                                 │
│  ┌──────────────────┐       ┌──────────────────────────────┐    │
│  │ Azure Container  │       │ Azure Container Apps          │    │
│  │ Registry (ACR)   │──────▶│                               │    │
│  │                  │       │  ┌────────────────────────┐   │    │
│  │ ast-intel:latest │       │  │ MCP Server (Service)   │   │    │
│  │ ast-intel:v0.1.0 │       │  │ ast-intel serve /repo  │   │    │
│  └──────────────────┘       │  │ replicas: 0–5          │   │    │
│                             │  │ port: 8080 (SSE)       │   │    │
│                             │  └────────────────────────┘   │    │
│                             │                               │    │
│  ┌──────────────────┐       │  ┌────────────────────────┐   │    │
│  │ Azure Files      │◀─────│  │ CLI Scan Job           │   │    │
│  │ (repo storage)   │      │  │ ast-intel scan /repo   │   │    │
│  │                  │──────│  │ trigger: queue/manual   │   │    │
│  │ /repos/my-app/   │      │  └────────────────────────┘   │    │
│  └──────────────────┘       └──────────────────────────────┘    │
│                                          │                      │
│  ┌──────────────────┐                    │  HTTPS + SSE         │
│  │ Azure Blob       │◀── output files    │                      │
│  │ ast.json, etc.   │                    ▼                      │
│  └──────────────────┘          ┌──────────────────┐             │
│                                │ API Management    │             │
│                                │ (optional)        │             │
│                                │ rate limit + auth │             │
│                                └──────────────────┘             │
│                                         │                       │
└─────────────────────────────────────────│───────────────────────┘
                                          │
                              ┌───────────▼──────────────┐
                              │ AI Agents / Editors       │
                              │ Copilot, Cursor, Claude   │
                              │ (MCP client over SSE)     │
                              └──────────────────────────┘
```

### Why This Combination

| Component | Azure Service | Rationale |
|-|-|-|
| **Container registry** | ACR | Private image storage, geo-replicated, integrated with ACA |
| **MCP server** | Container Apps (service) | Scale-to-zero, HTTPS ingress, per-second billing, no cluster ops |
| **CLI batch scans** | Container Apps (job) | Event-triggered, isolated, same image as MCP server |
| **Repo storage** | Azure Files (SMB/NFS) | Mountable by both service and job containers |
| **Output storage** | Azure Blob | Durable, cheap, accessible via REST for downstream tools |
| **API gateway** | API Management (optional) | Rate limiting, API keys, usage analytics for MCP endpoints |
| **CI integration** | Pipeline task (parallel) | Runs in Azure DevOps/GitHub Actions alongside the cloud deployment |

### MCP Transport Consideration

The MCP server currently uses **stdio** transport (ideal for local editor integration). For cloud deployment, an **HTTP+SSE** transport layer is needed:

| Transport | Where | How |
|-|-|-|
| **stdio** | Local (developer machine) | `ast-intel serve /repo` — editor spawns process directly |
| **HTTP+SSE** | Cloud (Container Apps) | `ast-intel serve /repo --port 8080` — SSE over HTTPS ingress |

The `mcp` Python SDK supports both transports. The `--port` flag in `serve_cmd` already signals SSE mode. The Container Apps ingress handles TLS termination.

### Security Layers

| Layer | Mechanism |
|-|-|
| **Network** | Container Apps environment with VNet integration; MCP endpoint not exposed to public internet without API Management |
| **Authentication** | Azure Easy Auth (Azure AD) on Container Apps ingress, or API Management subscription keys |
| **Authorization** | Tool-level: MCP tools are read-only (no write operations on the repo) |
| **Source code protection** | Azure Files with private endpoint; no public blob access |
| **Secrets** | No secrets needed (no API keys, no DB passwords) — the tool is self-contained |
| **Image signing** | ACR Content Trust (Notary v2) for image provenance |

---

## Infrastructure as Code — Sketch

### Bicep (Azure-native)

```
infra/
├── main.bicep                  # Orchestrator
├── modules/
│   ├── acr.bicep               # Container Registry
│   ├── storage.bicep           # Azure Files + Blob Storage
│   ├── container-env.bicep     # Container Apps Environment
│   ├── mcp-service.bicep       # MCP Server Container App
│   ├── scan-job.bicep          # CLI Scan Container App Job
│   └── apim.bicep              # API Management (optional)
└── parameters/
    ├── dev.bicepparam
    ├── staging.bicepparam
    └── prod.bicepparam
```

### CI/CD Pipeline (GitHub Actions)

```yaml
name: Build & Deploy AST Intel

on:
  push:
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Build & push image
        run: |
          az acr build \
            --registry ${{ vars.ACR_NAME }} \
            --image ast-intel:${{ github.ref_name }} \
            --image ast-intel:latest \
            .

      - name: Deploy MCP service
        run: |
          az containerapp update \
            --name ast-intel-mcp \
            --resource-group ast-intel-rg \
            --image ${{ vars.ACR_NAME }}.azurecr.io/ast-intel:${{ github.ref_name }}

      - name: Deploy Scan job
        run: |
          az containerapp job update \
            --name ast-intel-scan \
            --resource-group ast-intel-rg \
            --image ${{ vars.ACR_NAME }}.azurecr.io/ast-intel:${{ github.ref_name }}
```

---

## Decision Checklist

Use this to pick the right strategy for your situation:

- [ ] **"We just need it on developer machines"** → Strategy 1 (PyPI)
- [ ] **"We want AST scans on every PR"** → Strategy 6 (Pipeline Task)
- [ ] **"We want AI agents to query our codebase graph"** → Strategy 2 (Container Apps) for MCP server
- [ ] **"We want an API that scans repos on demand"** → Strategy 2 (Container Apps Job) or Strategy 5 (Functions, if repos are small)
- [ ] **"We already run AKS for everything"** → Strategy 4 (AKS)
- [ ] **"We need the simplest possible always-on MCP host"** → Strategy 7 (App Service)
- [ ] **"We want all of the above"** → Recommended Architecture (Container Apps + Pipeline Task + PyPI)

---

*This document covers deployment strategies only. For feature roadmap, see `ROADMAP_ARCHITECTURE_V2.md`. For caching/incremental updates, see `ast_intel/core/cache.py`.*
