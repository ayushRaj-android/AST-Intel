# AST Intel

A language-agnostic CLI tool that parses a codebase and produces a structured semantic
index — an AST JSON and human-readable summary — capturing every type, function, trait,
import, and package method call, organized by file, module, and crate, with cross-reference
indexes built on top.

## Quick Start

```bash
# Install with Rust support
pip install -e ".[rust,dev]"

# Analyze a repository
ast-intel /path/to/your/project --include src/ --output ./analysis

# MCP for IDEs (stdio) or containers (HTTP)
ast-intel serve /path/to/your/project
ast-intel serve /app --host 0.0.0.0 --port 7500 --no-watch

# Output: ast.json + summary.md
```

## Installation

```bash
# Install with all language support
pip install -e ".[all,dev]"

# Install with specific language(s)
pip install -e ".[rust,dev]"          # Rust only
pip install -e ".[go,dev]"            # Go only
pip install -e ".[python,dev]"        # Python only
pip install -e ".[typescript,dev]"    # TypeScript/JavaScript
pip install -e ".[csharp,dev]"        # C#/.NET
pip install -e ".[cpp,dev]"           # C/C++

# Or use Make
make install                          # All languages + dev
make install-rust                     # Rust + dev
```

## Usage

```bash
# Full repo, auto-detect language
ast-intel /home/user/my-project

# Specific paths, exclude tests
ast-intel /home/user/my-project \
  --include src/crates/libs \
  --include src/crates/services \
  --exclude "*/tests/*" \
  --exclude "*/target/*"

# Only Rust and Go, JSON output for CI
ast-intel /home/user/polyglot-repo --lang rust --lang go --format json --quiet
```

## CLI Options

```
ast-intel [OPTIONS] REPO_PATH

Arguments:
  REPO_PATH                          Path to repo root

Options:
  --include    -i  PATH              Include specific paths (repeatable)
  --exclude    -e  PATH              Exclude specific paths (repeatable)
  --lang       -l  LANGUAGE          Languages: rust, python, go, typescript, csharp, cpp, java
  --output     -o  DIR               Output directory (default: repo root)
  --format     -f  [json|md|both]    Output format (default: both)
  --workers    -w  INT               Parallel workers (default: CPU count)
  --no-methods                       Skip package method call extraction (faster)
  --no-cache                         Ignore cached results, full re-parse
  --quiet      -q                    Suppress progress output (for CI)
  --debug                            Verbose logging to stderr
  --version                          Print version and exit
```

## Development

```bash
# Install in dev mode
make install

# Run linter + type checker
make lint

# Run tests with coverage
make test

# Auto-format code
make format

# Full CI check (lint + test)
make check
```

## Output

### `ast.json`
Machine-readable semantic index containing:
- Every struct/class/type with fields
- Every function with signature, visibility, async/sync
- `self_methods[]` per file
- `imported_package_methods{}` per file
- Trait → implementors map
- Function → file index (O(1) lookup)
- Package method → files index (CVE blast radius)
- Inter-crate dependency graph

### `summary.md`
Human/LLM-readable reference (~129 KB for a 232-file Rust workspace). Use as
a system prompt prefix — gives an agent full semantic awareness without reading
source files.

## Requirements

- Python >= 3.11
- tree-sitter + language grammar(s) for target language

## Project Structure

```
ast-intel/
├── pyproject.toml
├── Makefile
├── ast_intel/
│   ├── __init__.py          # SCHEMA_VERSION, TOOL_VERSION
│   ├── cli.py               # Typer CLI entry point
│   ├── core/
│   │   ├── workspace.py     # Repo discovery & file walking
│   │   ├── manifest_parser.py
│   │   ├── dispatcher.py    # Parallel extraction orchestrator
│   │   ├── indexer.py       # Cross-reference builder
│   │   └── emitter.py       # Output writer
│   ├── extractors/
│   │   ├── base.py          # ExtractorBase ABC
│   │   └── rust.py          # (Phase 1)
│   ├── models/
│   │   ├── ast_node.py      # FileAST, StructNode, etc.
│   │   └── workspace_model.py
│   └── formatters/
│       ├── json_formatter.py
│       └── markdown_formatter.py
└── tests/
    ├── conftest.py
    ├── fixtures/
    └── test_*.py
```
