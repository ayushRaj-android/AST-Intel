"""Manifest parser — language-specific manifest file parsing.

Parses ``Cargo.toml``, ``package.json``, ``go.mod``, ``pyproject.toml``,
``*.csproj``, and ``CMakeLists.txt`` into :class:`CrateModel` instances.

Each language extractor provides a ``parse_manifest()`` method via the
:class:`~ast_intel.extractors.base.ExtractorBase` contract. This module
provides the dispatch logic to route a manifest file to the correct parser.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ast_intel.models.workspace_model import CrateModel

__all__: list[str] = ["ManifestParser"]

logger = logging.getLogger(__name__)

# Manifest filename → language mapping
MANIFEST_LANGUAGE_MAP: dict[str, str] = {
    "Cargo.toml": "rust",
    "package.json": "typescript",  # Also JavaScript
    "go.mod": "go",
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "CMakeLists.txt": "cpp",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "Gemfile": "ruby",
    "build.sbt": "scala",
    "Package.swift": "swift",
    "composer.json": "php",
}

# Recognized manifest filenames
MANIFEST_FILENAMES: frozenset[str] = frozenset(MANIFEST_LANGUAGE_MAP.keys())

# .csproj files are matched by extension, not exact name
CSPROJ_EXTENSION: str = ".csproj"


class ManifestParser:
    """Route manifest files to the correct language parser.

    When a language-specific extractor is available (e.g., ``RustExtractor``),
    its ``parse_manifest()`` method is used. Otherwise a minimal ``CrateModel``
    is returned with name, language, and manifest path inferred from the
    file system.
    """

    def parse(self, manifest_path: Path) -> CrateModel:  # noqa: PLR0911, C901
        """Parse a manifest file and return crate metadata.

        Args:
            manifest_path: Absolute path to the manifest file.

        Returns:
            A ``CrateModel`` with name, language, manifest_path, and
            (where supported) version and dependency information.
        """
        language = self._detect_language(manifest_path)

        logger.debug("Parsing manifest: %s (language=%s)", manifest_path, language)

        # Dispatch to language-specific parser when available
        if language == "rust":
            return self._parse_rust(manifest_path)
        if language == "python":
            return self._parse_python(manifest_path)
        if language == "csharp":
            return self._parse_csharp(manifest_path)
        if language == "typescript":
            return self._parse_typescript(manifest_path)
        if language == "go":
            return self._parse_go(manifest_path)
        if language == "java":
            return self._parse_java(manifest_path)
        if language == "ruby":
            return self._parse_ruby(manifest_path)
        if language == "kotlin":
            return self._parse_kotlin(manifest_path)
        if language == "scala":
            return self._parse_scala(manifest_path)
        if language == "swift":
            return self._parse_swift(manifest_path)
        if language == "php":
            return self._parse_php(manifest_path)

        # Fallback: minimal metadata from file-system heuristics
        return self._fallback_parse(manifest_path, language)

    # --- Language-specific parsers ---

    @staticmethod
    def _parse_rust(manifest_path: Path) -> CrateModel:
        """Parse ``Cargo.toml`` via the Rust extractor."""
        try:
            from ast_intel.extractors.rust import RustExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-rust not installed; "
                "Cargo.toml will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "rust")

        extractor = RustExtractor()
        crate = extractor.parse_manifest(manifest_path)

        # Skip workspace-only Cargo.toml (has [workspace] but no [package])
        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_python(manifest_path: Path) -> CrateModel:
        """Parse ``pyproject.toml`` via the Python extractor."""
        try:
            from ast_intel.extractors.python import PythonExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-python not installed; "
                "pyproject.toml will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "python")

        extractor = PythonExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_csharp(manifest_path: Path) -> CrateModel:
        """Parse ``.csproj`` via the C# extractor."""
        try:
            from ast_intel.extractors.csharp import CSharpExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-c-sharp not installed; "
                ".csproj will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "csharp")

        extractor = CSharpExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_typescript(manifest_path: Path) -> CrateModel:
        """Parse ``package.json`` via the TypeScript extractor."""
        try:
            from ast_intel.extractors.typescript import TypeScriptExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-typescript not installed; "
                "package.json will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "typescript")

        extractor = TypeScriptExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_go(manifest_path: Path) -> CrateModel:
        """Parse ``go.mod`` via the Go extractor."""
        try:
            from ast_intel.extractors.go import GoExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-go not installed; "
                "go.mod will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "go")

        extractor = GoExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_java(manifest_path: Path) -> CrateModel:
        """Parse ``pom.xml`` or ``build.gradle`` via the Java extractor."""
        try:
            from ast_intel.extractors.java import JavaExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-java not installed; "
                "Java manifest will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "java")

        extractor = JavaExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_ruby(manifest_path: Path) -> CrateModel:
        """Parse ``Gemfile`` via the Ruby extractor."""
        try:
            from ast_intel.extractors.ruby import RubyExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-ruby not installed; "
                "Gemfile will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "ruby")

        extractor = RubyExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_kotlin(manifest_path: Path) -> CrateModel:
        """Parse ``build.gradle.kts`` via the Kotlin extractor."""
        try:
            from ast_intel.extractors.kotlin import KotlinExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-kotlin not installed; "
                "Kotlin manifest will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "kotlin")

        extractor = KotlinExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_scala(manifest_path: Path) -> CrateModel:
        """Parse ``build.sbt`` via the Scala extractor."""
        try:
            from ast_intel.extractors.scala import ScalaExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-scala not installed; "
                "Scala manifest will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "scala")

        extractor = ScalaExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_swift(manifest_path: Path) -> CrateModel:
        """Parse ``Package.swift`` via the Swift extractor."""
        try:
            from ast_intel.extractors.swift import SwiftExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-swift not installed; "
                "Swift manifest will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "swift")

        extractor = SwiftExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    @staticmethod
    def _parse_php(manifest_path: Path) -> CrateModel:
        """Parse ``composer.json`` via the PHP extractor."""
        try:
            from ast_intel.extractors.php import PhpExtractor
        except ImportError:
            logger.warning(
                "tree-sitter-php not installed; "
                "PHP manifest will use fallback parser",
            )
            return ManifestParser._fallback_parse(manifest_path, "php")

        extractor = PhpExtractor()
        crate = extractor.parse_manifest(manifest_path)

        if not crate.name:
            crate.name = manifest_path.parent.name or "unknown"

        return crate

    # --- Fallback parser ---

    @staticmethod
    def _fallback_parse(
        manifest_path: Path,
        language: str,
    ) -> CrateModel:
        """Create a minimal ``CrateModel`` from the directory name.

        Used when no extractor is available for the language.
        """
        name = manifest_path.parent.name or manifest_path.name
        return CrateModel(
            name=name,
            language=language,
            manifest_path=str(manifest_path),
        )

    @staticmethod
    def _detect_language(manifest_path: Path) -> str:
        """Detect the language from a manifest filename or extension.

        Args:
            manifest_path: Path to the manifest file.

        Returns:
            Language identifier string, or ``"unknown"``.
        """
        filename = manifest_path.name

        if filename in MANIFEST_LANGUAGE_MAP:
            return MANIFEST_LANGUAGE_MAP[filename]

        if filename.endswith(CSPROJ_EXTENSION):
            return "csharp"

        return "unknown"
