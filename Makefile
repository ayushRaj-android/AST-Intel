# ============================================================================
# AST Intel — Makefile
# ============================================================================
# Standard targets for development workflow.
#
# Usage:
#   make install       Install in editable mode with all + dev deps
#   make install-rust  Install with Rust support only
#   make lint          Run ruff + mypy
#   make format        Auto-format with ruff
#   make test          Run pytest with coverage
#   make check         Run lint + test (CI pipeline equivalent)
#   make bump V=patch  Bump version (patch/minor/major or X.Y.Z) + build wheel
#   make clean         Remove build artifacts and caches
# ============================================================================

.DEFAULT_GOAL := help
.PHONY: help install install-rust install-dev lint format test check clean build bump

PYTHON ?= python3
PIP    ?= pip3

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

install:  ## Install in editable mode with all languages + dev deps
	$(PIP) install -e ".[all,dev]"

install-rust:  ## Install in editable mode with Rust support only
	$(PIP) install -e ".[rust,dev]"

install-dev:  ## Install in editable mode with dev deps only (no language grammars)
	$(PIP) install -e ".[dev]"

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

lint:  ## Run ruff linter + mypy type checker
	$(PYTHON) -m ruff check ast_intel/ tests/
	$(PYTHON) -m mypy ast_intel/

format:  ## Auto-format code with ruff
	$(PYTHON) -m ruff format ast_intel/ tests/
	$(PYTHON) -m ruff check --fix ast_intel/ tests/

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:  ## Run pytest with coverage
	$(PYTHON) -m pytest tests/ \
		--cov=ast_intel \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		-v

test-fast:  ## Run pytest without coverage (faster)
	$(PYTHON) -m pytest tests/ -v

# ---------------------------------------------------------------------------
# Combined Checks (CI)
# ---------------------------------------------------------------------------

check: lint test  ## Run lint + test (full CI pipeline)

# ---------------------------------------------------------------------------
# Build & Distribution
# ---------------------------------------------------------------------------

build:  ## Build wheel and sdist
	$(PYTHON) -m build

bump:  ## Bump version + build wheel (usage: make bump V=patch | V=minor | V=0.2.0)
	@test -n "$(V)" || { echo "usage: make bump V=patch|minor|major|X.Y.Z"; exit 1; }
	$(PYTHON) scripts/bump_version.py $(V)

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean:  ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
