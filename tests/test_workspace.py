"""Tests for ``ast_intel.core.workspace`` — workspace discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.core.workspace import (
    WorkspaceDiscovery,
    _build_module_path,
    _is_binary,
    _is_utf8,
    _load_ast_intel_scan,
    _load_gitignore,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_repo(tmp_path: Path) -> Path:
    """Create a minimal Rust repo with a Cargo.toml and one .rs file."""
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "my-crate"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text("fn main() {}")
    (src / "lib.rs").write_text("pub fn hello() {}")
    (src / "handler.rs").write_text("pub fn handle() {}")
    return tmp_path


@pytest.fixture
def multi_crate_repo(tmp_path: Path) -> Path:
    """Create a repo with two sub-crates."""
    for name in ("alpha", "beta"):
        crate_dir = tmp_path / "crates" / name
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        src = crate_dir / "src"
        src.mkdir()
        (src / "lib.rs").write_text(f"// {name}")
        (src / "util.rs").write_text(f"// {name} util")
    return tmp_path


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Helper Function Tests
# ---------------------------------------------------------------------------


class TestIsBinary:
    """Tests for ``_is_binary``."""

    def test_text_file_is_not_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.rs"
        f.write_text("fn main() {}")
        assert _is_binary(f) is False

    def test_binary_file_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        assert _is_binary(f) is True

    def test_empty_file_is_not_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.rs"
        f.write_bytes(b"")
        assert _is_binary(f) is False

    def test_unreadable_file_treated_as_binary(self, tmp_path: Path) -> None:
        f = tmp_path / "nope.rs"
        # Non-existent file should be treated as binary
        assert _is_binary(f) is True


class TestIsUtf8:
    """Tests for ``_is_utf8``."""

    def test_valid_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.rs"
        f.write_text("fn main() {}", encoding="utf-8")
        assert _is_utf8(f) is True

    def test_invalid_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.rs"
        f.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        assert _is_utf8(f) is False


class TestLoadGitignore:
    """Tests for ``_load_gitignore``."""

    def test_returns_none_when_no_gitignore(self, tmp_path: Path) -> None:
        assert _load_gitignore(tmp_path) is None

    def test_parses_gitignore_file(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("target/\n*.log\n")
        spec = _load_gitignore(tmp_path)
        assert spec is not None
        assert spec.match_file("target/debug/foo") is True
        assert spec.match_file("output.log") is True
        assert spec.match_file("src/main.rs") is False


class TestBuildModulePath:
    """Tests for ``_build_module_path``."""

    def test_simple_src_file(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "handler.rs"
        result = _build_module_path("my-crate", file_path, tmp_path)
        assert result == "my-crate::handler"

    def test_nested_src_file(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "handlers" / "auth.rs"
        result = _build_module_path("my-crate", file_path, tmp_path)
        assert result == "my-crate::handlers::auth"

    def test_lib_rs_is_crate_root(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "lib.rs"
        result = _build_module_path("my-crate", file_path, tmp_path)
        assert result == "my-crate"

    def test_main_rs_is_crate_root(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "main.rs"
        result = _build_module_path("my-crate", file_path, tmp_path)
        assert result == "my-crate"

    def test_mod_rs_uses_parent(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "config" / "mod.rs"
        result = _build_module_path("my-crate", file_path, tmp_path)
        assert result == "my-crate::config"

    def test_python_init_py(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "pkg" / "__init__.py"
        result = _build_module_path("my-pkg", file_path, tmp_path)
        assert result == "my-pkg::pkg"

    def test_file_outside_manifest_dir(self, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        file_path = other / "foo.rs"
        manifest_dir = tmp_path / "crate"
        manifest_dir.mkdir()
        result = _build_module_path("crate", file_path, manifest_dir)
        assert result == "crate"


# endregion: --- Helper Function Tests


# ---------------------------------------------------------------------------
# region:    --- WorkspaceDiscovery Tests
# ---------------------------------------------------------------------------


class TestWorkspaceDiscovery:
    """Tests for ``WorkspaceDiscovery.discover``."""

    def test_discovers_single_crate(self, basic_repo: Path) -> None:
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        assert "my-crate" in ws.crates
        crate = ws.crates["my-crate"]
        assert crate.language == "rust"
        files = {f.file for f in crate.files}
        assert "src/main.rs" in files
        assert "src/lib.rs" in files
        assert "src/handler.rs" in files

    def test_discovers_multi_crate(self, multi_crate_repo: Path) -> None:
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" in ws.crates
        assert len(ws.crates["alpha"].files) == 2
        assert len(ws.crates["beta"].files) == 2

    def test_no_manifest_produces_ungrouped(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "foo.rs").write_text("fn foo() {}")
        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        assert "ungrouped" in ws.crates
        assert len(ws.crates["ungrouped"].files) == 1

    def test_skips_binary_files(self, basic_repo: Path) -> None:
        binfile = basic_repo / "src" / "data.rs"
        binfile.write_bytes(b"fn x() { }\x00 binary content")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        crate = ws.crates["my-crate"]
        assert not any(f.file.endswith("data.rs") for f in crate.files)

    def test_skips_large_files(self, basic_repo: Path) -> None:
        large = basic_repo / "src" / "huge.rs"
        large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        crate = ws.crates["my-crate"]
        assert not any(f.file.endswith("huge.rs") for f in crate.files)

    def test_skips_non_utf8_files(self, basic_repo: Path) -> None:
        bad = basic_repo / "src" / "bad.rs"
        bad.write_bytes(b"\xff\xfe\x80\x81 invalid utf-8")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        crate = ws.crates["my-crate"]
        assert not any(f.file.endswith("bad.rs") for f in crate.files)

    def test_respects_gitignore(self, basic_repo: Path) -> None:
        (basic_repo / ".gitignore").write_text("generated/\n")
        gen_dir = basic_repo / "generated"
        gen_dir.mkdir()
        (gen_dir / "auto.rs").write_text("// generated")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        all_files = {
            f.file for c in ws.crates.values() for f in c.files
        }
        assert "generated/auto.rs" not in all_files

    def test_default_excludes_target_dir(self, basic_repo: Path) -> None:
        target = basic_repo / "target" / "debug"
        target.mkdir(parents=True)
        (target / "build.rs").write_text("// build artefact")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        all_files = {
            f.file for c in ws.crates.values() for f in c.files
        }
        assert not any("target" in f for f in all_files)

    def test_include_paths_filter(self, multi_crate_repo: Path) -> None:
        ws = WorkspaceDiscovery(
            repo_root=multi_crate_repo,
            include_paths=["crates/alpha"],
        ).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates

    def test_exclude_paths_filter(self, multi_crate_repo: Path) -> None:
        ws = WorkspaceDiscovery(
            repo_root=multi_crate_repo,
            exclude_paths=["*/beta/*"],
        ).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates

    def test_language_filter(self, tmp_path: Path) -> None:
        # Create a Cargo.toml and a package.json
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "r"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        (tmp_path / "main.rs").write_text("fn main() {}")
        py_dir = tmp_path / "pylib"
        py_dir.mkdir()
        (py_dir / "package.json").write_text('{"name": "j"}')
        (py_dir / "index.ts").write_text("export const x = 1;")
        ws = WorkspaceDiscovery(
            repo_root=tmp_path,
            languages=["rust"],
        ).discover()
        # Only rust crates appear (typescript crate filtered out)
        for crate in ws.crates.values():
            assert crate.language == "rust" or crate.name == "ungrouped"

    def test_module_paths_populated(self, basic_repo: Path) -> None:
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        crate = ws.crates["my-crate"]
        handler = next(f for f in crate.files if f.file.endswith("handler.rs"))
        assert handler.module_path == "my-crate::handler"

    def test_workspace_meta_populated(self, basic_repo: Path) -> None:
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        assert ws.meta.workspace_root == str(basic_repo.resolve())
        assert ws.meta.schema_version != ""
        assert ws.meta.tool_version != ""
        # generated_at is set at emit time, not discovery time
        assert ws.meta.generated_at == ""

    def test_empty_repo(self, tmp_path: Path) -> None:
        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        assert len(ws.crates) == 0

    def test_non_source_files_ignored(self, basic_repo: Path) -> None:
        (basic_repo / "src" / "readme.md").write_text("# Hello")
        (basic_repo / "src" / "data.toml").write_text("key = 'val'")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        crate = ws.crates["my-crate"]
        exts = {Path(f.file).suffix for f in crate.files}
        assert ".md" not in exts
        assert ".toml" not in exts

    def test_crate_deduplication(self, tmp_path: Path) -> None:
        """Two Cargo.toml with same package name get deduplicated."""
        for sub in ("a", "b"):
            d = tmp_path / sub
            d.mkdir()
            (d / "Cargo.toml").write_text(
                '[package]\nname = "shared"\nversion = "0.1.0"\n'
                'edition = "2021"\n'
            )
            src = d / "src"
            src.mkdir()
            (src / "lib.rs").write_text(f"// {sub}")
        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        names = list(ws.crates.keys())
        assert "shared" in names
        assert "shared-2" in names


# endregion: --- WorkspaceDiscovery Tests


# ---------------------------------------------------------------------------
# region:    --- .ast-intel-ignore Tests
# ---------------------------------------------------------------------------


class TestAstIntelIgnore:
    """Tests for ``.ast-intel-ignore`` support."""

    def test_no_ignore_file_discovers_all(
        self, basic_repo: Path,
    ) -> None:
        """Without .ast-intel-ignore all sources are found."""
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        total = sum(len(c.files) for c in ws.crates.values())
        assert total == 3  # main.rs, lib.rs, handler.rs

    def test_ignore_file_excludes_matching(
        self, basic_repo: Path,
    ) -> None:
        """A .ast-intel-ignore entry excludes matching files."""
        (basic_repo / ".ast-intel-ignore").write_text("src/handler.rs\n")
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        all_files = [
            f.file for c in ws.crates.values() for f in c.files
        ]
        assert "src/handler.rs" not in all_files
        # Other files unaffected
        assert any("main.rs" in f for f in all_files)

    def test_ignore_glob_pattern(self, tmp_path: Path) -> None:
        """Glob patterns (e.g. ``gen/**``) work in .ast-intel-ignore."""
        manifest = tmp_path / "Cargo.toml"
        manifest.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.rs").write_text("fn app() {}")
        gen = tmp_path / "gen"
        gen.mkdir()
        (gen / "proto.rs").write_text("// generated")
        (tmp_path / ".ast-intel-ignore").write_text("gen/**\n")

        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        all_files = [
            f.file for c in ws.crates.values() for f in c.files
        ]
        assert not any("gen/" in f for f in all_files)
        assert any("app.rs" in f for f in all_files)

    def test_ignore_directory_pattern(self, tmp_path: Path) -> None:
        """A bare directory name excludes everything under it."""
        manifest = tmp_path / "Cargo.toml"
        manifest.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        vendor = tmp_path / "third_party"
        vendor.mkdir()
        (vendor / "lib.rs").write_text("// vendor")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text("fn main() {}")
        (tmp_path / ".ast-intel-ignore").write_text("third_party/\n")

        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        all_files = [
            f.file for c in ws.crates.values() for f in c.files
        ]
        assert not any("third_party" in f for f in all_files)
        assert any("main.rs" in f for f in all_files)

    def test_ignore_combines_with_gitignore(
        self, tmp_path: Path,
    ) -> None:
        """Both .gitignore and .ast-intel-ignore are respected."""
        manifest = tmp_path / "Cargo.toml"
        manifest.write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text("fn main() {}")
        (src / "gen.rs").write_text("// generated")
        (src / "tmp.rs").write_text("// temp")
        (tmp_path / ".gitignore").write_text("src/tmp.rs\n")
        (tmp_path / ".ast-intel-ignore").write_text("src/gen.rs\n")

        ws = WorkspaceDiscovery(repo_root=tmp_path).discover()
        all_files = [
            f.file for c in ws.crates.values() for f in c.files
        ]
        # Both excluded
        assert "src/gen.rs" not in all_files
        assert "src/tmp.rs" not in all_files
        # main.rs kept
        assert "src/main.rs" in all_files

    def test_comments_and_blank_lines_ignored(
        self, basic_repo: Path,
    ) -> None:
        """Comments and blank lines in .ast-intel-ignore are harmless."""
        (basic_repo / ".ast-intel-ignore").write_text(
            "# This is a comment\n\nsrc/handler.rs\n\n"
        )
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        all_files = [
            f.file for c in ws.crates.values() for f in c.files
        ]
        assert "src/handler.rs" not in all_files

    def test_ignore_also_affects_manifests(
        self, multi_crate_repo: Path,
    ) -> None:
        """Manifests under ignored paths are also skipped."""
        (multi_crate_repo / ".ast-intel-ignore").write_text(
            "crates/beta/\n"
        )
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates


class TestLoadAstIntelIgnore:
    """Tests for ``_load_ast_intel_ignore``."""

    def test_returns_none_when_no_file(self, tmp_path: Path) -> None:
        from ast_intel.core.workspace import _load_ast_intel_ignore

        assert _load_ast_intel_ignore(tmp_path) is None

    def test_parses_ignore_file(self, tmp_path: Path) -> None:
        from ast_intel.core.workspace import _load_ast_intel_ignore

        (tmp_path / ".ast-intel-ignore").write_text("gen/\n*.bak\n")
        spec = _load_ast_intel_ignore(tmp_path)
        assert spec is not None
        assert spec.match_file("gen/foo.rs")
        assert spec.match_file("temp.bak")
        assert not spec.match_file("src/main.rs")


# endregion: --- .ast-intel-ignore Tests


# ---------------------------------------------------------------------------
# region:    --- .ast-intel-scan Tests
# ---------------------------------------------------------------------------


class TestLoadAstIntelScan:
    """Tests for ``_load_ast_intel_scan`` helper."""

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert _load_ast_intel_scan(tmp_path) == []

    def test_parses_paths(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-scan").write_text("src/crates/alpha\nsrc/crates/beta\n")
        result = _load_ast_intel_scan(tmp_path)
        assert result == ["src/crates/alpha", "src/crates/beta"]

    def test_strips_comments_and_blanks(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-scan").write_text(
            "# Include only these paths\n"
            "\n"
            "src/services\n"
            "  # another comment  \n"
            "\n"
            "src/libs\n"
        )
        result = _load_ast_intel_scan(tmp_path)
        assert result == ["src/services", "src/libs"]

    def test_strips_leading_trailing_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-scan").write_text("  src/alpha  \n  src/beta  \n")
        result = _load_ast_intel_scan(tmp_path)
        assert result == ["src/alpha", "src/beta"]

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-scan").write_text("")
        assert _load_ast_intel_scan(tmp_path) == []

    def test_comments_only_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-scan").write_text("# comment 1\n# comment 2\n")
        assert _load_ast_intel_scan(tmp_path) == []


class TestAstIntelScan:
    """Integration tests for ``.ast-intel-scan`` filtering via discovery."""

    def test_no_scan_file_discovers_all(self, basic_repo: Path) -> None:
        """Without .ast-intel-scan all sources are found."""
        ws = WorkspaceDiscovery(repo_root=basic_repo).discover()
        total = sum(len(c.files) for c in ws.crates.values())
        assert total == 3  # main.rs, lib.rs, handler.rs

    def test_scan_file_limits_to_listed_paths(
        self, multi_crate_repo: Path,
    ) -> None:
        """Only crates under paths listed in .ast-intel-scan are discovered."""
        (multi_crate_repo / ".ast-intel-scan").write_text("crates/alpha\n")
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates

    def test_scan_file_multiple_paths(
        self, multi_crate_repo: Path,
    ) -> None:
        """Multiple paths in .ast-intel-scan are all included."""
        (multi_crate_repo / ".ast-intel-scan").write_text(
            "crates/alpha\ncrates/beta\n"
        )
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" in ws.crates

    def test_scan_file_merges_with_cli_include(
        self, multi_crate_repo: Path,
    ) -> None:
        """CLI --include and .ast-intel-scan entries are merged."""
        (multi_crate_repo / ".ast-intel-scan").write_text("crates/alpha\n")
        ws = WorkspaceDiscovery(
            repo_root=multi_crate_repo,
            include_paths=["crates/beta"],
        ).discover()
        # Both should be present — CLI brings beta, scan file brings alpha
        assert "alpha" in ws.crates
        assert "beta" in ws.crates

    def test_scan_file_deduplicates_with_cli(
        self, multi_crate_repo: Path,
    ) -> None:
        """Duplicate paths between CLI and .ast-intel-scan are deduplicated."""
        (multi_crate_repo / ".ast-intel-scan").write_text("crates/alpha\n")
        wd = WorkspaceDiscovery(
            repo_root=multi_crate_repo,
            include_paths=["crates/alpha"],
        )
        # Should have only one entry, not two
        assert wd.include_paths.count("crates/alpha") == 1

    def test_scan_file_cli_include_takes_precedence_in_order(
        self, multi_crate_repo: Path,
    ) -> None:
        """CLI --include paths appear before .ast-intel-scan paths."""
        (multi_crate_repo / ".ast-intel-scan").write_text(
            "crates/alpha\ncrates/beta\n"
        )
        wd = WorkspaceDiscovery(
            repo_root=multi_crate_repo,
            include_paths=["crates/beta"],
        )
        # CLI path "crates/beta" should come first (before scan-file's alpha)
        assert wd.include_paths.index("crates/beta") < wd.include_paths.index(
            "crates/alpha"
        )

    def test_scan_file_combines_with_exclude(
        self, tmp_path: Path,
    ) -> None:
        """Scan includes a path but exclude further narrows it."""
        # Create two sub-crates inside "services"
        for name in ("api", "worker"):
            d = tmp_path / "services" / name
            d.mkdir(parents=True)
            (d / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\nversion = "0.1.0"\n'
                f'edition = "2021"\n'
            )
            src = d / "src"
            src.mkdir()
            (src / "lib.rs").write_text(f"// {name}")

        # Scan file says "scan services/"
        (tmp_path / ".ast-intel-scan").write_text("services\n")
        # But exclude says "skip worker"
        ws = WorkspaceDiscovery(
            repo_root=tmp_path,
            exclude_paths=["*/worker/*"],
        ).discover()
        assert "api" in ws.crates
        assert "worker" not in ws.crates

    def test_scan_file_combines_with_ast_intel_ignore(
        self, multi_crate_repo: Path,
    ) -> None:
        """.ast-intel-scan and .ast-intel-ignore work together."""
        # Scan both crates
        (multi_crate_repo / ".ast-intel-scan").write_text(
            "crates/alpha\ncrates/beta\n"
        )
        # But ignore beta via .ast-intel-ignore
        (multi_crate_repo / ".ast-intel-ignore").write_text("crates/beta/\n")
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates

    def test_scan_file_with_comments_and_blanks(
        self, multi_crate_repo: Path,
    ) -> None:
        """Comments and blank lines in .ast-intel-scan are ignored."""
        (multi_crate_repo / ".ast-intel-scan").write_text(
            "# Only scan alpha\n\ncrates/alpha\n\n"
        )
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" not in ws.crates

    def test_empty_scan_file_discovers_all(
        self, multi_crate_repo: Path,
    ) -> None:
        """An empty .ast-intel-scan file means scan everything."""
        (multi_crate_repo / ".ast-intel-scan").write_text("")
        ws = WorkspaceDiscovery(repo_root=multi_crate_repo).discover()
        assert "alpha" in ws.crates
        assert "beta" in ws.crates


# endregion: --- .ast-intel-scan Tests
