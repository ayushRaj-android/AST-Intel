"""Tests for ``ast_intel.core.lockfile_parser`` and the parse_manifest merge."""

from __future__ import annotations

import json
from pathlib import Path

from ast_intel.core.lockfile_parser import (
    is_pinned_version,
    merge_resolved_deps,
    parse_cargo_lockfile,
    parse_lockfile,
    parse_npm_lockfile,
    parse_php_lockfile,
    parse_python_lockfile,
    parse_ruby_lockfile,
)
from ast_intel.extractors.go import GoExtractor
from ast_intel.extractors.php import PhpExtractor
from ast_intel.extractors.python import PythonExtractor
from ast_intel.extractors.ruby import RubyExtractor
from ast_intel.extractors.rust import RustExtractor
from ast_intel.extractors.typescript import TypeScriptExtractor
from ast_intel.models.workspace_model import CrateDependency


def test_is_pinned_version() -> None:
    assert is_pinned_version("2.30.0")
    assert is_pinned_version("1.0.0b2")
    assert not is_pinned_version(">=2.0")
    assert not is_pinned_version("2.0,<3")
    assert not is_pinned_version("")


def test_requirements_txt_pins(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "# web\n"
        "requests==2.30.0\n"
        "flask>=2.0\n"
        "uvicorn[standard]==0.30.0\n"
        "-r other.txt\n"
        "pydantic==2.9.2  # inline comment\n",
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_python_lockfile(tmp_path)}
    assert deps["requests"].version == "2.30.0"
    assert deps["requests"].is_transitive is False
    assert deps["uvicorn"].version == "0.30.0"
    assert deps["pydantic"].version == "2.9.2"
    assert deps["flask"].version == ">=2.0"          # kept, but not a pin
    assert is_pinned_version(deps["requests"].version)
    assert not is_pinned_version(deps["flask"].version)


def test_poetry_lock_resolved(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.30.0"\ncategory = "main"\n\n'
        '[[package]]\nname = "pytest"\nversion = "7.4.0"\ncategory = "dev"\n',
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_python_lockfile(tmp_path)}
    assert deps["requests"].version == "2.30.0"
    assert deps["requests"].is_transitive is True
    assert deps["pytest"].is_dev is True


def test_pipfile_lock_resolved(tmp_path: Path) -> None:
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"requests": {"version": "==2.30.0"}},'
        ' "develop": {"pytest": {"version": "==7.4.0"}}}',
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_python_lockfile(tmp_path)}
    assert deps["requests"].version == "2.30.0"
    assert deps["pytest"].is_dev is True


def test_lockfile_precedence_prefers_poetry(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests==1.0.0\n", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.30.0"\ncategory = "main"\n',
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_python_lockfile(tmp_path)}
    assert deps["requests"].version == "2.30.0"  # poetry.lock wins over requirements.txt


def test_parse_manifest_merges_lockfile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.30.0"\ncategory = "main"\n\n'
        '[[package]]\nname = "urllib3"\nversion = "2.0.7"\ncategory = "main"\n',
        encoding="utf-8",
    )
    crate = PythonExtractor().parse_manifest(tmp_path / "pyproject.toml")
    by = {d.name: d for d in crate.dependencies}
    # Pinned lockfile version overrides the manifest's declared range.
    assert by["requests"].version == "2.30.0"
    assert by["requests"].is_transitive is False   # declared in manifest -> direct
    # Lockfile-only package appears as a transitive dependency.
    assert by["urllib3"].version == "2.0.7"
    assert by["urllib3"].is_transitive is True


# ---------------------------------------------------------------------------
# npm — package-lock.json / yarn.lock / pnpm-lock.yaml
# ---------------------------------------------------------------------------


def test_npm_package_lock_v3(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps({
            "name": "demo", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {
                "": {"name": "demo", "version": "1.0.0"},  # root project
                "node_modules/lodash": {"version": "4.17.21"},
                "node_modules/@babel/core": {"version": "7.22.0", "dev": True},
            },
        }),
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_npm_lockfile(tmp_path)}
    assert deps["lodash"].version == "4.17.21"
    assert deps["lodash"].is_transitive is True
    assert deps["@babel/core"].version == "7.22.0"
    assert deps["@babel/core"].is_dev is True
    assert "" not in deps and "demo" not in deps  # root project skipped


def test_npm_package_lock_v1_tree(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps({
            "name": "demo", "lockfileVersion": 1,
            "dependencies": {
                "express": {
                    "version": "4.18.2",
                    "dependencies": {"cookie": {"version": "0.5.0"}},
                },
                "mocha": {"version": "10.2.0", "dev": True},
            },
        }),
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_npm_lockfile(tmp_path)}
    assert deps["express"].version == "4.18.2"
    assert deps["cookie"].version == "0.5.0"     # nested transitive captured
    assert deps["mocha"].is_dev is True


def test_yarn_lock_classic(tmp_path: Path) -> None:
    (tmp_path / "yarn.lock").write_text(
        "# yarn lockfile v1\n\n"
        'lodash@^4.17.0:\n  version "4.17.21"\n  resolved "https://x"\n\n'
        '"@babel/core@^7.0.0", "@babel/core@^7.1.0":\n  version "7.22.0"\n',
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_npm_lockfile(tmp_path)}
    assert deps["lodash"].version == "4.17.21"
    assert deps["@babel/core"].version == "7.22.0"


def test_pnpm_lock_best_effort(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '6.0'\n\n"
        "packages:\n\n"
        "  /lodash@4.17.21:\n    resolution: {integrity: sha512-x}\n    dev: false\n\n"
        "  /@babel/core@7.22.0:\n    resolution: {integrity: sha512-y}\n    dev: true\n",
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_npm_lockfile(tmp_path)}
    assert deps["lodash"].version == "4.17.21"
    assert deps["@babel/core"].version == "7.22.0"


def test_npm_lockfile_precedence_prefers_package_lock(tmp_path: Path) -> None:
    (tmp_path / "yarn.lock").write_text(
        'lodash@^4.0.0:\n  version "4.0.0"\n', encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps({
            "lockfileVersion": 3,
            "packages": {"node_modules/lodash": {"version": "4.17.21"}},
        }),
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_npm_lockfile(tmp_path)}
    assert deps["lodash"].version == "4.17.21"  # package-lock.json wins


# ---------------------------------------------------------------------------
# Rust / Ruby / PHP lockfiles
# ---------------------------------------------------------------------------


def test_cargo_lock_resolved(tmp_path: Path) -> None:
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.188"\n\n'
        '[[package]]\nname = "libc"\nversion = "0.2.147"\n',
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_cargo_lockfile(tmp_path)}
    assert deps["serde"].version == "1.0.188"
    assert deps["serde"].is_transitive is True
    assert deps["libc"].version == "0.2.147"


def test_gemfile_lock_specs(tmp_path: Path) -> None:
    (tmp_path / "Gemfile.lock").write_text(
        "GEM\n  remote: https://rubygems.org/\n  specs:\n"
        "    rack (2.2.4)\n"
        "    actionpack (7.0.4)\n      rack (>= 1.0)\n\n"
        "PLATFORMS\n  ruby\n\n"
        "DEPENDENCIES\n  rails\n",
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_ruby_lockfile(tmp_path)}
    assert deps["rack"].version == "2.2.4"
    assert deps["actionpack"].version == "7.0.4"
    assert deps["rack"].is_transitive is True
    # Only the two 4-space resolved specs; the 6-space nested dep is ignored.
    assert len(deps) == 2


def test_composer_lock_packages(tmp_path: Path) -> None:
    (tmp_path / "composer.lock").write_text(
        json.dumps({
            "packages": [
                {"name": "monolog/monolog", "version": "2.9.1"},
                {"name": "psr/log", "version": "1.1.4"},
            ],
            "packages-dev": [
                {"name": "phpunit/phpunit", "version": "9.6.0"},
            ],
        }),
        encoding="utf-8",
    )
    deps = {d.name: d for d in parse_php_lockfile(tmp_path)}
    assert deps["monolog/monolog"].version == "2.9.1"
    assert deps["psr/log"].is_transitive is True
    assert deps["phpunit/phpunit"].is_dev is True


# ---------------------------------------------------------------------------
# Dispatch + merge
# ---------------------------------------------------------------------------


def test_parse_lockfile_dispatch(tmp_path: Path) -> None:
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.188"\n', encoding="utf-8",
    )
    rust = {d.name: d for d in parse_lockfile(tmp_path, "rust")}
    assert rust["serde"].version == "1.0.188"
    # Unknown / unsupported language → no lockfile parsing.
    assert parse_lockfile(tmp_path, "haskell") == []


def test_merge_resolved_deps_direct_vs_transitive() -> None:
    manifest = [CrateDependency(name="requests", version=">=2.0")]
    lock = [
        CrateDependency(name="requests", version="2.30.0", is_transitive=True),
        CrateDependency(name="urllib3", version="2.0.7", is_transitive=True),
    ]
    merged = {d.name: d for d in merge_resolved_deps(manifest, lock)}
    assert merged["requests"].version == "2.30.0"      # lock pin wins
    assert merged["requests"].is_transitive is False   # declared → direct
    assert merged["urllib3"].version == "2.0.7"
    assert merged["urllib3"].is_transitive is True      # lock-only → transitive


# ---------------------------------------------------------------------------
# Extractor integration — parse_manifest merges the sibling lockfile
# ---------------------------------------------------------------------------


def test_typescript_parse_manifest_merges_lockfile(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({
            "name": "demo", "version": "1.0.0",
            "dependencies": {"lodash": "^4.17.0"},
        }),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps({
            "name": "demo", "lockfileVersion": 3,
            "packages": {
                "": {"name": "demo"},
                "node_modules/lodash": {"version": "4.17.21"},
                "node_modules/ms": {"version": "2.1.3"},
            },
        }),
        encoding="utf-8",
    )
    crate = TypeScriptExtractor().parse_manifest(tmp_path / "package.json")
    by = {d.name: d for d in crate.dependencies}
    assert by["lodash"].version == "4.17.21"     # pin overrides ^4.17.0
    assert by["lodash"].is_transitive is False   # declared → direct
    assert by["ms"].version == "2.1.3"
    assert by["ms"].is_transitive is True         # lock-only → transitive


def test_rust_parse_manifest_merges_lockfile(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n'
        '[dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.188"\n\n'
        '[[package]]\nname = "libc"\nversion = "0.2.147"\n',
        encoding="utf-8",
    )
    crate = RustExtractor().parse_manifest(tmp_path / "Cargo.toml")
    by = {d.name: d for d in crate.dependencies}
    assert by["serde"].version == "1.0.188"
    assert by["serde"].is_transitive is False
    assert by["libc"].version == "0.2.147"
    assert by["libc"].is_transitive is True


def test_ruby_parse_manifest_merges_lockfile(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text(
        'source "https://rubygems.org"\ngem "rack"\n', encoding="utf-8",
    )
    (tmp_path / "Gemfile.lock").write_text(
        "GEM\n  specs:\n    rack (2.2.4)\n    nokogiri (1.15.0)\n\n"
        "DEPENDENCIES\n  rack\n",
        encoding="utf-8",
    )
    crate = RubyExtractor().parse_manifest(tmp_path / "Gemfile")
    by = {d.name: d for d in crate.dependencies}
    assert by["rack"].version == "2.2.4"
    assert by["rack"].is_transitive is False     # declared in Gemfile → direct
    assert by["nokogiri"].version == "1.15.0"
    assert by["nokogiri"].is_transitive is True   # lock-only → transitive


def test_php_parse_manifest_merges_lockfile(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text(
        json.dumps({
            "name": "acme/demo", "require": {"monolog/monolog": "^2.0"},
        }),
        encoding="utf-8",
    )
    (tmp_path / "composer.lock").write_text(
        json.dumps({
            "packages": [
                {"name": "monolog/monolog", "version": "2.9.1"},
                {"name": "psr/log", "version": "1.1.4"},
            ],
        }),
        encoding="utf-8",
    )
    crate = PhpExtractor().parse_manifest(tmp_path / "composer.json")
    by = {d.name: d for d in crate.dependencies}
    assert by["monolog/monolog"].version == "2.9.1"
    assert by["monolog/monolog"].is_transitive is False
    assert by["psr/log"].version == "1.1.4"
    assert by["psr/log"].is_transitive is True


def test_go_indirect_is_transitive(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text(
        "module example.com/demo\n\ngo 1.21\n\n"
        "require (\n"
        "\tgithub.com/gin-gonic/gin v1.9.1\n"
        "\tgithub.com/davecgh/go-spew v1.1.1 // indirect\n"
        ")\n",
        encoding="utf-8",
    )
    crate = GoExtractor().parse_manifest(tmp_path / "go.mod")
    by = {d.name: d for d in crate.dependencies}
    assert by["github.com/gin-gonic/gin"].is_transitive is False
    assert by["github.com/davecgh/go-spew"].is_transitive is True
    assert by["github.com/davecgh/go-spew"].is_dev is True   # back-compat
