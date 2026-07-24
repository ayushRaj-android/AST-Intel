# AST_INTEL — Setup & Testing Instructions

> Share this doc with teammates so they can install, run, and test AST_INTEL on their repos.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation Methods](#installation-methods)
3. [Quick Test Run](#quick-test-run)
4. [CLI Commands Reference](#cli-commands-reference)
5. [MCP in IDE — Quick Setup](#mcp-in-ide--quick-setup)
6. [MCP Server Setup (Detailed)](#mcp-server-setup)
7. [Azure Artifacts + Docker (multi-service)](#azure-artifacts--docker-multi-service)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.11+** (check: `python3 --version`)
- **pip** (check: `pip3 --version`)
- **Git** (to clone the repo)

---

## Installation Methods

### Method 1 — Install from Git (Recommended for Teammates)

No need to clone manually. One command installs directly from the repo:

```bash
# All languages + analysis + MCP server
pip install "ast-intel[all,analysis,mcp] @ git+https://github.com/AstIntel/ast-intel.git"
```

Pick only what you need:

```bash
# Python + TypeScript only
pip install "ast-intel[python,typescript] @ git+https://github.com/AstIntel/ast-intel.git"

# Rust + analysis (community detection)
pip install "ast-intel[rust,analysis] @ git+https://github.com/AstIntel/ast-intel.git"
```

> **Private repo?** Use SSH: `git+ssh://git@github.com/AstIntel/ast-intel.git`

### Method 2 — Install from Local Clone

```bash
git clone https://github.com/AstIntel/ast-intel.git
cd ast-intel

# All languages + MCP + analysis
pip install -e ".[all,analysis,mcp]"

# Or use Make
make install  # installs all + dev deps
```

### Method 3 — Install from Built Wheel

The maintainer builds a `.whl` file and shares it (Slack, Teams, email):

```bash
# Maintainer builds:
cd ast-intel
python3 -m build
# → dist/ast_intel-0.1.1-py3-none-any.whl

# Teammate installs:
pip install ast_intel-0.1.1-py3-none-any.whl[all,analysis,mcp]
```

### Method 4 — Virtual Environment (Recommended)

Always use a venv to avoid polluting your system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -e ".[all,analysis,mcp]"
```

### Method 5 — Azure Artifacts (private org feed)

```bash
# Create feed once (Azure CLI):
#   export AZURE_ORG=... AZURE_FEED=python
#   ./scripts/setup_azure_artifacts_feed.sh
# Publish:
#   export AZURE_PAT=<Packaging Read&Write PAT>
#   ./scripts/publish_azure_artifacts.sh

# Install (org-scoped feed):
pip install "ast-intel[all,analysis,mcp]" \
  --index-url "https://${USER}:${AZURE_PAT}@pkgs.dev.azure.com/${AZURE_ORG}/_packaging/${AZURE_FEED}/pypi/simple/" \
  --extra-index-url https://pypi.org/simple
```

Project-scoped feed URL:

```text
https://pkgs.dev.azure.com/${AZURE_ORG}/${AZURE_PROJECT}/_packaging/${AZURE_FEED}/pypi/simple/
```

---

## Available Extras

| Extra | What It Installs | When You Need It |
|-------|-----------------|------------------|
| `rust` | tree-sitter-rust | Analyzing Rust repos |
| `python` | tree-sitter-python | Analyzing Python repos |
| `typescript` | tree-sitter-typescript + tree-sitter-javascript | Analyzing TS/JS repos |
| `csharp` | tree-sitter-c-sharp + defusedxml | Analyzing C#/.NET repos |
| `java` | tree-sitter-java + defusedxml | Analyzing Java repos |
| `go` | tree-sitter-go | Analyzing Go repos |
| `cpp` | tree-sitter-c + tree-sitter-cpp | Analyzing C/C++ repos |
| `all` | All language grammars above | Full language support |
| `analysis` | networkx | `community`, `impact`, `path` commands |
| `leiden` | networkx + graspologic | Leiden community detection (Python <3.13) |
| `mcp` | mcp SDK | MCP server for editor integration |
| `dev` | pytest, ruff, mypy, pre-commit | Contributing/development |

---

## Quick Test Run

### 1. Scan a Repo

```bash
# Scan your project (auto-detects languages)
ast-intel scan /path/to/your/repo

# Output appears in /path/to/your/repo/ast_output/
#   → ast.json        (machine-readable graph)
#   → summary.md      (human-readable summary)
#   → graph.json      (knowledge graph with nodes + edges)
```

### 2. Scan Specific Paths

```bash
ast-intel scan /path/to/repo \
  --include src/ \
  --exclude "*/tests/*" \
  --format both
```

### 3. Query the Graph

After scanning, use the query subcommands:

```bash
# Search for a symbol
ast-intel query /path/to/repo "UserService"

# Find dependencies of a symbol
ast-intel deps /path/to/repo "UserService"

# Show impact/blast radius
ast-intel impact /path/to/repo "UserService"

# Explain a symbol in plain English
ast-intel explain /path/to/repo "UserService"

# Find shortest path between two symbols
ast-intel path /path/to/repo "UserService" "OrderService"

# List all files in the graph
ast-intel files /path/to/repo

# Find similar symbols (structural similarity)
ast-intel similar /path/to/repo "UserService"

# Detect communities/clusters
ast-intel community /path/to/repo
```

### 4. Use .ast-intel-scan (Include File)

Place a `.ast-intel-scan` file in your repo root to define which paths to scan:

```
# .ast-intel-scan — one path per line
src/
lib/
# comments and blank lines are ignored
```

Then just run `ast-intel scan /path/to/repo` — it auto-reads the include list.

### 5. Use .ast-intel-ignore (Exclude File)

Place a `.ast-intel-ignore` in your repo root (gitignore syntax):

```
# .ast-intel-ignore
**/tests/**
**/node_modules/**
**/target/**
**/dist/**
*.generated.ts
```

---

## CLI Commands Reference

| Command | Description |
|---------|-------------|
| `ast-intel scan <repo>` | Parse repo → ast.json + summary.md + graph.json |
| `ast-intel query <repo> <symbol>` | Search for symbols by name |
| `ast-intel path <repo> <from> <to>` | Shortest path between two symbols |
| `ast-intel explain <repo> <symbol>` | Human-readable explanation of a symbol |
| `ast-intel impact <repo> <symbol>` | Blast radius — what depends on this? |
| `ast-intel deps <repo> <symbol>` | What does this symbol depend on? |
| `ast-intel dependents <repo> <symbol>` | What depends on this symbol? |
| `ast-intel context <repo> <symbol>` | Full context (deps + dependents + file) |
| `ast-intel usages <repo> <symbol>` | Find all usages of a symbol |
| `ast-intel files <repo>` | List scanned files |
| `ast-intel implementors <repo> <trait>` | Find implementors of a trait/interface |
| `ast-intel similar <repo> <symbol>` | Find structurally similar symbols |
| `ast-intel community <repo>` | Detect code communities/clusters |
| `ast-intel serve <repo>` | Start MCP server (see below) |

---

## MCP in IDE — Quick Setup

Get ast-intel's code graph tools available in your IDE in 3 steps:

### Step 1: Auto-configure with `ast-intel install` (Recommended)

Run this from your project root — it auto-detects your IDE and writes the correct MCP config:

```bash
cd /path/to/your/repo

# Auto-detect IDE and configure
ast-intel install

# Or specify the platform explicitly
ast-intel install --platform vscode
ast-intel install --platform cursor
ast-intel install --platform windsurf
ast-intel install --platform claude-desktop
ast-intel install --platform claude-code
ast-intel install --platform codex

# Specify a different project root
ast-intel install --platform cursor --root /path/to/project
```

This creates/updates the appropriate config file (`.vscode/mcp.json`, `.cursor/mcp.json`, etc.) with the correct absolute paths.

To remove the configuration later:

```bash
ast-intel uninstall
ast-intel uninstall --platform vscode
```

### Step 2 (Alternative): Manual Setup

If you prefer manual configuration or `ast-intel install` doesn't detect your IDE:

#### Locate the `ast-intel` binary

```bash
which ast-intel
# Example: /home/rajayush/anaconda3/bin/ast-intel
```

#### Add MCP config to your IDE

Create the appropriate config file for your IDE and paste the JSON below.  
Replace `/full/path/to/ast-intel` and `/full/path/to/your/repo` with absolute paths.

| IDE | Config File Location |
|-----|---------------------|
| **VS Code + Copilot** | `.vscode/mcp.json` (workspace) or User `settings.json` |
| **Cursor** | `.cursor/mcp.json` (project root) |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` |
| **Claude Desktop** | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) |

**Config (VS Code `.vscode/mcp.json`):**

```json
{
  "servers": {
    "ast-intel": {
      "command": "/full/path/to/ast-intel",
      "args": ["serve", "/full/path/to/your/repo", "--analyze"]
    }
  }
}
```

**Config (Cursor / Windsurf / Claude Desktop):**

```json
{
  "mcpServers": {
    "ast-intel": {
      "command": "/full/path/to/ast-intel",
      "args": ["serve", "/full/path/to/your/repo", "--analyze"]
    }
  }
}
```

> **Tip**: Always use absolute paths. IDEs don't inherit shell environments (conda, pyenv, nvm), so relative paths or bare `ast-intel` may fail.

### Step 3: Restart / Reload IDE

- **VS Code**: Reload window (`Ctrl+Shift+P` → "Reload Window")
- **Cursor**: Restart Cursor
- **Windsurf / Claude Desktop**: Restart the application

Once connected, your AI agent gains access to 12+ code graph tools (`search_symbols`, `get_impact`, `get_dependencies`, `find_usages`, `explain_symbol`, etc.).

---

## MCP Server Setup

The MCP server exposes the full code graph as 13 tools + 1 resource, accessible to any MCP-compatible editor (VS Code + Copilot, Cursor, Windsurf, etc.).

### Install MCP Extra

```bash
pip install -e ".[all,analysis,mcp]"
```

### Test MCP Server Manually

```bash
# Quick check — should start and wait for stdio input:
ast-intel serve /path/to/repo

# With community detection + similarity:
ast-intel serve /path/to/repo --analyze --similarity
```

Press `Ctrl+C` to stop.

### VS Code + GitHub Copilot

Add to your **workspace** `.vscode/mcp.json`:

```json
{
  "servers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/absolute/path/to/your/repo", "--analyze"]
    }
  }
}
```

Or add to your **user** `settings.json`:

```json
{
  "mcp": {
    "servers": {
      "ast-intel": {
        "command": "ast-intel",
        "args": ["serve", "/absolute/path/to/your/repo", "--analyze"]
      }
    }
  }
}
```

> **Important**: Use the absolute path to the repo, not a relative one.

### VS Code + Copilot (venv-aware)

If ast-intel is installed in a virtualenv, point to the venv's binary:

```json
{
  "servers": {
    "ast-intel": {
      "command": "/home/you/project/.venv/bin/ast-intel",
      "args": ["serve", "/home/you/project", "--analyze"]
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/absolute/path/to/your/repo", "--analyze"]
    }
  }
}
```

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/absolute/path/to/your/repo", "--analyze"]
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/absolute/path/to/your/repo", "--analyze"]
    }
  }
}
```

### Available MCP Tools

Once the server is running, your AI agent gets these tools:

| Tool | Description |
|------|-------------|
| `search_symbols` | Search for symbols by name/pattern |
| `find_path` | Shortest path between two symbols |
| `explain_symbol` | Human-readable symbol explanation |
| `get_impact` | Blast radius of a symbol change |
| `get_dependencies` | What a symbol depends on |
| `get_dependents` | What depends on a symbol |
| `get_context` | Full context (deps + dependents) |
| `find_usages` | All usages of a symbol |
| `list_files` | Files in the scanned graph |
| `get_implementors` | Implementors of a trait/interface |
| `find_similar` | Structurally similar symbols |
| `get_community` | Code community/cluster detection |

**Resource**: `ast-intel://summary` — full codebase summary as text.

### MCP Server Options

```bash
ast-intel serve <repo> [OPTIONS]

Options:
  --output DIR                Output directory for graph cache
  --no-cache                  Force full re-parse
  --analyze                   Enable community detection (get_community tool)
  --similarity/--no-similarity  Auto-compute SIMILAR_TO edges (default: on)
  --similarity-threshold FLOAT  Min Jaccard similarity 0.0–1.0 (default: 0.4)
```

---

## Troubleshooting

### `command not found: ast-intel`

The CLI is not on your PATH. Fix:

```bash
# Check where pip installed it
pip show ast-intel

# If using a venv, activate it first
source .venv/bin/activate

# Or use the full path
python3 -m ast_intel.cli scan /path/to/repo
```

### `ModuleNotFoundError: tree_sitter_rust`

You didn't install the language extra for your repo's language:

```bash
pip install -e ".[rust]"     # For Rust repos
pip install -e ".[python]"   # For Python repos
pip install -e ".[all]"      # For all languages
```

### `MCP support requires the mcp package`

```bash
pip install -e ".[mcp]"
```

### `graspologic` fails on Python 3.13

Use `[analysis]` instead of `[leiden]` — it uses networkx without graspologic:

```bash
pip install -e ".[all,analysis,mcp]"
```

### Empty output / no files scanned

1. Check that the language grammar is installed (`pip install -e ".[all]"`)
2. Check that your source files aren't excluded by `.ast-intel-ignore`
3. Try: `ast-intel scan /path/to/repo --debug` for verbose logging
4. Try specifying the language: `ast-intel scan /path/to/repo --lang python`

### MCP server doesn't connect in VS Code (`EACCES` or `ENOENT`)

VS Code's remote extension host doesn't inherit your shell's PATH (conda, pyenv, nvm, etc.), so it can't find `ast-intel`. Fix by using the **absolute path** to the binary:

```bash
# Find the full path to ast-intel
which ast-intel
# Example output: /home/rajayush/anaconda3/bin/ast-intel
```

Then use that full path in your `.vscode/mcp.json`:

```json
{
  "servers": {
    "ast-intel": {
      "command": "/home/rajayush/anaconda3/bin/ast-intel",
      "args": ["serve", "/absolute/path/to/your/repo"]
    }
  }
}
```

**Other checks:**

1. Verify the repo path in `args` is also **absolute**
2. Verify `ast-intel serve /path/to/repo` works when run manually in terminal
3. Check VS Code Output panel → select "MCP" from the dropdown for error details
4. Restart VS Code after editing mcp.json

---

## Azure Artifacts + Docker (multi-service)

Pattern: each service image installs private `ast-intel`, runs the app **and** MCP
(Streamable HTTP on port `7500` at `/mcp`). Local IDEs keep using stdio.

### Publish (maintainers)

```bash
export AZURE_ORG=your-org AZURE_FEED=python AZURE_PAT=...
./scripts/setup_azure_artifacts_feed.sh   # once
./scripts/publish_azure_artifacts.sh
```

Tag-triggered CI: see `azure-pipelines.yml`.

### Dockerfile (production services)

Copy `docker/service.Dockerfile.snippet` and `docker/entrypoint-with-mcp.sh` into the
service repo, then build with a BuildKit secret (never bake the PAT into layers):

```bash
export AZURE_PAT=...   # Packaging Read is enough for install
docker build \
  --secret id=ado_pat,env=AZURE_PAT \
  --build-arg AZURE_ORG=your-org \
  --build-arg AZURE_FEED=python \
  -t my-service:latest .
```

### Client wiring (2C)

**A. Developer IDE (stdio)** — on a laptop checkout of the service:

```bash
pip install "ast-intel[all,analysis,mcp]" --index-url "https://...Azure Artifacts.../simple/"
ast-intel install cursor    # or: vscode / claude / windsurf
# writes .cursor/mcp.json → command ast-intel serve <abs-repo>
```

**B. Runtime / remote agents (HTTP)** — inside the same container:

```text
http://127.0.0.1:7500/mcp
```

Example Cursor remote MCP entry (only if you intentionally expose the port behind auth):

```json
{
  "mcpServers": {
    "ast-intel": {
      "url": "http://my-service.internal:7500/mcp"
    }
  }
}
```

Prefer keeping `7500` container-local; put Easy Auth / API key / mTLS in front of any
external exposure.

### Reference image (local proof without Azure)

```bash
python -m build
docker build -f docker/reference/Dockerfile -t ast-intel-reference:local .
docker run --rm -p 8080:8080 -p 7500:7500 ast-intel-reference:local
# service: http://127.0.0.1:8080/
# MCP:     http://127.0.0.1:7500/mcp
```

---

## For the Maintainer: Building & Sharing

### Build the Wheel

```bash
cd ast-intel
pip install build
python3 -m build
# Output: dist/ast_intel-0.1.1-py3-none-any.whl
```

### Share via Azure Artifacts

```bash
export AZURE_ORG=your-org AZURE_FEED=python AZURE_PAT=...
./scripts/publish_azure_artifacts.sh

# Teammates / Docker builds install with:
pip install "ast-intel[all,analysis,mcp]" \
  --index-url "https://build:${AZURE_PAT}@pkgs.dev.azure.com/${AZURE_ORG}/_packaging/${AZURE_FEED}/pypi/simple/" \
  --extra-index-url https://pypi.org/simple
```

### Share via Internal PyPI / Artifacts (generic twine)

```bash
# Upload to your org's private PyPI
pip install twine
twine upload --repository internal dist/ast_intel-0.1.8-py3-none-any.whl

# Teammates install from private registry
pip install --index-url https://pypi.yourorg.com/simple ast-intel[all,analysis,mcp]
```

### Share via TestPyPI (for preview)

```bash
twine upload --repository testpypi dist/*

# Teammates install
pip install --index-url https://test.pypi.org/simple --extra-index-url https://pypi.org/simple ast-intel[all,analysis,mcp]
```

### Share the Wheel Directly

Just send the `.whl` file (Slack, Teams, email). Teammates install with:

```bash
pip install ast_intel-0.1.1-py3-none-any.whl[all,analysis,mcp]
```
