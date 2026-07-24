"""Tests for ``ast_intel.core.manifest_parser`` — manifest file parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.core.manifest_parser import (
    CSPROJ_EXTENSION,
    MANIFEST_FILENAMES,
    MANIFEST_LANGUAGE_MAP,
    ManifestParser,
)

# ---------------------------------------------------------------------------
# region:    --- Constants Tests
# ---------------------------------------------------------------------------


class TestManifestConstants:
    """Verify module-level constants."""

    def test_manifest_filenames_is_frozenset(self) -> None:
        assert isinstance(MANIFEST_FILENAMES, frozenset)
        assert "Cargo.toml" in MANIFEST_FILENAMES
        assert "package.json" in MANIFEST_FILENAMES

    def test_manifest_language_map_keys_match_filenames(self) -> None:
        assert set(MANIFEST_LANGUAGE_MAP.keys()) == MANIFEST_FILENAMES

    def test_csproj_extension(self) -> None:
        assert CSPROJ_EXTENSION == ".csproj"


# endregion: --- Constants Tests


# ---------------------------------------------------------------------------
# region:    --- Language Detection Tests
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    """Tests for ``ManifestParser._detect_language``."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("Cargo.toml", "rust"),
            ("package.json", "typescript"),
            ("go.mod", "go"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("setup.cfg", "python"),
            ("CMakeLists.txt", "cpp"),
        ],
    )
    def test_known_manifests(self, filename: str, expected: str) -> None:
        parser = ManifestParser()
        path = Path("/fake/project") / filename
        assert parser._detect_language(path) == expected

    def test_csproj_detected(self) -> None:
        parser = ManifestParser()
        path = Path("/fake/MyApp.csproj")
        assert parser._detect_language(path) == "csharp"

    def test_unknown_returns_unknown(self) -> None:
        parser = ManifestParser()
        path = Path("/fake/mystery.xyz")
        assert parser._detect_language(path) == "unknown"


# endregion: --- Language Detection Tests


# ---------------------------------------------------------------------------
# region:    --- Parse Tests
# ---------------------------------------------------------------------------


class TestManifestParse:
    """Tests for ``ManifestParser.parse``."""

    def test_parse_cargo_toml(self, tmp_path: Path) -> None:
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[package]\nname = "my-crate"\nversion = "1.2.3"\n'
            'edition = "2021"\n'
        )
        parser = ManifestParser()
        crate = parser.parse(cargo)
        assert crate.name == "my-crate"
        assert crate.language == "rust"
        assert crate.version == "1.2.3"

    def test_parse_cargo_workspace_only(self, tmp_path: Path) -> None:
        """Workspace-only Cargo.toml with no [package] section."""
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[workspace]\nmembers = ["crates/*"]\n'
        )
        parser = ManifestParser()
        crate = parser.parse(cargo)
        # Should fall back to directory name
        assert crate.name == tmp_path.name
        assert crate.language == "rust"

    def test_parse_package_json_fallback(self, tmp_path: Path) -> None:
        pkg = tmp_path / "package.json"
        pkg.write_text('{"name": "my-pkg","version":"2.0.0"}')
        parser = ManifestParser()
        crate = parser.parse(pkg)
        # Now properly parsed via _parse_typescript
        assert crate.name == "my-pkg"
        assert crate.language == "typescript"

    def test_parse_go_mod_fallback(self, tmp_path: Path) -> None:
        gomod = tmp_path / "go.mod"
        gomod.write_text("module example.com/mymod\n\ngo 1.21\n")
        parser = ManifestParser()
        crate = parser.parse(gomod)
        # Now properly parsed via _parse_go
        assert crate.name == "example.com/mymod"
        assert crate.language == "go"

    def test_parse_pyproject_fallback(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "my-pkg"\n')
        parser = ManifestParser()
        crate = parser.parse(pyproject)
        # Now routed to PythonExtractor which extracts the real name
        assert crate.name == "my-pkg"
        assert crate.language == "python"

    def test_parse_csproj_fallback(self, tmp_path: Path) -> None:
        csproj = tmp_path / "MyApp.csproj"
        csproj.write_text("<Project></Project>")
        parser = ManifestParser()
        crate = parser.parse(csproj)
        assert crate.name == "MyApp"
        assert crate.language == "csharp"

    def test_manifest_path_set(self, tmp_path: Path) -> None:
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n'
        )
        parser = ManifestParser()
        crate = parser.parse(cargo)
        assert crate.manifest_path == str(cargo)


# endregion: --- Parse Tests
