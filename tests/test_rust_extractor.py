"""Comprehensive tests for the Rust extractor.

Covers:
- Struct extraction (named, tuple, unit, generic, derives, doc comments)
- Enum extraction (unit, tuple, struct variants)
- Trait extraction (required/default methods, associated types, super-traits)
- Impl block extraction (inherent, trait impls, generic)
- Function extraction (async, unsafe, visibility, params, return types)
- Module declarations
- Type aliases, constants, statics, macros
- Import map building
- Scoped method call resolution
- self_methods aggregation
- Cargo.toml manifest parsing
- Edge cases (empty files, parse errors)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.rust import (
    RustExtractor,
    build_import_map,
)
from ast_intel.models.ast_node import (
    EnumVariantKind,
    FileAST,
    TraitItemKind,
    Visibility,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "rust"


@pytest.fixture
def extractor() -> RustExtractor:
    """Provide a fresh RustExtractor instance."""
    return RustExtractor()


def _parse_fixture(extractor: RustExtractor, name: str) -> FileAST:
    """Helper to parse a fixture file and return the FileAST."""
    path = FIXTURE_DIR / name
    source = path.read_bytes()
    return extractor.extract(path, source)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor Identity Tests
# ---------------------------------------------------------------------------


class TestExtractorIdentity:
    """Verify extractor metadata and contract compliance."""

    def test_language_id(self, extractor: RustExtractor) -> None:
        """Language ID must be 'rust'."""
        assert extractor.language_id == "rust"

    def test_file_extensions(self, extractor: RustExtractor) -> None:
        """Must handle .rs files."""
        assert extractor.file_extensions == [".rs"]

    def test_is_extractor_base_subclass(self) -> None:
        """RustExtractor must inherit from ExtractorBase."""
        from ast_intel.extractors.base import ExtractorBase

        assert issubclass(RustExtractor, ExtractorBase)


# endregion: --- Extractor Identity Tests


# ---------------------------------------------------------------------------
# region:    --- Struct Tests
# ---------------------------------------------------------------------------


class TestStructExtraction:
    """Verify struct parsing from struct_with_fields.rs."""

    def test_struct_count(self, extractor: RustExtractor) -> None:
        """Must extract all 4 structs."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        assert len(ast.structs) == 4

    def test_named_struct_fields(self, extractor: RustExtractor) -> None:
        """AppConfig must have 4 fields with correct visibility."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        app_config = next(s for s in ast.structs if s.name == "AppConfig")
        assert len(app_config.fields) == 4

        name_field = app_config.fields[0]
        assert name_field.name == "name"
        assert name_field.type == "String"
        assert name_field.visibility == Visibility.PUBLIC

        workers = next(f for f in app_config.fields if f.name == "workers")
        assert workers.visibility == Visibility.PRIVATE

        secret = next(f for f in app_config.fields if f.name == "secret")
        assert secret.visibility == Visibility.CRATE

    def test_struct_visibility(self, extractor: RustExtractor) -> None:
        """AppConfig is pub, Container is pub."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        app_config = next(s for s in ast.structs if s.name == "AppConfig")
        assert app_config.visibility == Visibility.PUBLIC

    def test_struct_attributes(self, extractor: RustExtractor) -> None:
        """AppConfig has derive(Debug, Clone, Deserialize)."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        app_config = next(s for s in ast.structs if s.name == "AppConfig")
        assert len(app_config.attributes) > 0
        assert any("derive" in a for a in app_config.attributes)

    def test_struct_doc_comment(self, extractor: RustExtractor) -> None:
        """AppConfig has a doc comment."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        app_config = next(s for s in ast.structs if s.name == "AppConfig")
        assert "configuration" in app_config.doc.lower()

    def test_tuple_struct(self, extractor: RustExtractor) -> None:
        """Wrapper is a tuple struct with fields."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        wrapper = next(s for s in ast.structs if s.name == "Wrapper")
        assert wrapper.visibility == Visibility.PUBLIC
        assert len(wrapper.fields) >= 1

    def test_unit_struct(self, extractor: RustExtractor) -> None:
        """Marker is a unit struct with no fields."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        marker = next(s for s in ast.structs if s.name == "Marker")
        assert len(marker.fields) == 0

    def test_generic_struct(self, extractor: RustExtractor) -> None:
        """Container has generic parameters <T, U>."""
        ast = _parse_fixture(extractor, "struct_with_fields.rs")
        container = next(s for s in ast.structs if s.name == "Container")
        assert "T" in container.generics
        assert "U" in container.generics


# endregion: --- Struct Tests


# ---------------------------------------------------------------------------
# region:    --- Enum Tests
# ---------------------------------------------------------------------------


class TestEnumExtraction:
    """Verify enum parsing from enum_variants.rs."""

    def test_enum_count(self, extractor: RustExtractor) -> None:
        """Must extract both enums."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        assert len(ast.enums) == 2

    def test_unit_variant(self, extractor: RustExtractor) -> None:
        """Error::NotFound is a unit variant."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        error_enum = next(e for e in ast.enums if e.name == "Error")
        not_found = next(v for v in error_enum.variants if v.name == "NotFound")
        assert not_found.kind == EnumVariantKind.UNIT
        assert len(not_found.fields) == 0

    def test_tuple_variant(self, extractor: RustExtractor) -> None:
        """Error::IoError is a tuple variant with one field."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        error_enum = next(e for e in ast.enums if e.name == "Error")
        io_err = next(v for v in error_enum.variants if v.name == "IoError")
        assert io_err.kind == EnumVariantKind.TUPLE

    def test_struct_variant(self, extractor: RustExtractor) -> None:
        """Error::Timeout is a struct variant with named fields."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        error_enum = next(e for e in ast.enums if e.name == "Error")
        timeout = next(v for v in error_enum.variants if v.name == "Timeout")
        assert timeout.kind == EnumVariantKind.STRUCT
        assert len(timeout.fields) == 2
        field_names = {f.name for f in timeout.fields}
        assert "host" in field_names
        assert "duration_ms" in field_names

    def test_enum_visibility(self, extractor: RustExtractor) -> None:
        """Error is public, State is private."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        error_enum = next(e for e in ast.enums if e.name == "Error")
        state_enum = next(e for e in ast.enums if e.name == "State")
        assert error_enum.visibility == Visibility.PUBLIC
        assert state_enum.visibility == Visibility.PRIVATE

    def test_enum_doc_comment(self, extractor: RustExtractor) -> None:
        """Error enum has a doc comment."""
        ast = _parse_fixture(extractor, "enum_variants.rs")
        error_enum = next(e for e in ast.enums if e.name == "Error")
        assert "processing" in error_enum.doc.lower()


# endregion: --- Enum Tests


# ---------------------------------------------------------------------------
# region:    --- Trait Tests
# ---------------------------------------------------------------------------


class TestTraitExtraction:
    """Verify trait parsing from trait_with_methods.rs."""

    def test_trait_count(self, extractor: RustExtractor) -> None:
        """Must extract all 3 traits."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        assert len(ast.traits) == 3

    def test_required_methods(self, extractor: RustExtractor) -> None:
        """StorageHelper has required async methods."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        storage = next(t for t in ast.traits if t.name == "StorageHelper")
        required = [i for i in storage.items if i.kind == TraitItemKind.REQUIRED_METHOD]
        assert len(required) == 2
        get_method = next(m for m in required if m.name == "get")
        assert get_method.is_async

    def test_default_methods(self, extractor: RustExtractor) -> None:
        """StorageHelper has a default delete method."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        storage = next(t for t in ast.traits if t.name == "StorageHelper")
        defaults = [i for i in storage.items if i.kind == TraitItemKind.DEFAULT_METHOD]
        assert len(defaults) == 1
        assert defaults[0].name == "delete"

    def test_associated_type(self, extractor: RustExtractor) -> None:
        """StorageHelper has an associated type Config."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        storage = next(t for t in ast.traits if t.name == "StorageHelper")
        assoc_types = [i for i in storage.items if i.kind == TraitItemKind.ASSOCIATED_TYPE]
        assert len(assoc_types) == 1
        assert assoc_types[0].name == "Config"

    def test_trait_constant(self, extractor: RustExtractor) -> None:
        """StorageHelper has a constant MAX_KEY_LENGTH."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        storage = next(t for t in ast.traits if t.name == "StorageHelper")
        consts = [i for i in storage.items if i.kind == TraitItemKind.CONSTANT]
        assert len(consts) == 1
        assert consts[0].name == "MAX_KEY_LENGTH"

    def test_super_traits(self, extractor: RustExtractor) -> None:
        """StorageHelper extends Send + Sync."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        storage = next(t for t in ast.traits if t.name == "StorageHelper")
        assert len(storage.super_traits) >= 2
        st_names = set(storage.super_traits)
        assert "Send" in st_names
        assert "Sync" in st_names

    def test_empty_trait(self, extractor: RustExtractor) -> None:
        """Marker trait has no items."""
        ast = _parse_fixture(extractor, "trait_with_methods.rs")
        marker = next(t for t in ast.traits if t.name == "Marker")
        assert len(marker.items) == 0


# endregion: --- Trait Tests


# ---------------------------------------------------------------------------
# region:    --- Impl Block Tests
# ---------------------------------------------------------------------------


class TestImplBlockExtraction:
    """Verify impl block parsing from impl_block.rs."""

    def test_impl_count(self, extractor: RustExtractor) -> None:
        """Must extract all 3 impl blocks."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        assert len(ast.impl_blocks) == 3

    def test_inherent_impl(self, extractor: RustExtractor) -> None:
        """Inherent impl for MyServer has 3 methods."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        inherent = next(
            ib for ib in ast.impl_blocks
            if ib.self_type == "MyServer" and not ib.trait_type
        )
        assert len(inherent.methods) == 3

    def test_inherent_method_details(self, extractor: RustExtractor) -> None:
        """MyServer::new is pub, sync; start is pub, async."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        inherent = next(
            ib for ib in ast.impl_blocks
            if ib.self_type == "MyServer" and not ib.trait_type
        )
        new_method = next(m for m in inherent.methods if m.name == "new")
        assert new_method.visibility == Visibility.PUBLIC
        assert not new_method.is_async

        start_method = next(m for m in inherent.methods if m.name == "start")
        assert start_method.visibility == Visibility.PUBLIC
        assert start_method.is_async

        internal = next(m for m in inherent.methods if m.name == "internal_setup")
        assert internal.visibility == Visibility.PRIVATE

    def test_trait_impl(self, extractor: RustExtractor) -> None:
        """Display trait impl for MyServer is detected."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        display_impl = next(
            ib for ib in ast.impl_blocks if "Display" in ib.trait_type
        )
        assert display_impl.self_type == "MyServer"
        method_names = {m.name for m in display_impl.methods}
        assert "fmt" in method_names

    def test_generic_impl(self, extractor: RustExtractor) -> None:
        """Generic impl<T: Clone> Container<T> is properly parsed."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        generic = next(
            ib for ib in ast.impl_blocks
            if "Container" in ib.self_type
        )
        assert generic.generics != ""


# endregion: --- Impl Block Tests


# ---------------------------------------------------------------------------
# region:    --- Function Tests
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    """Verify function parsing from functions_and_modules.rs."""

    def test_function_count(self, extractor: RustExtractor) -> None:
        """Must extract all 3 free functions."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        assert len(ast.functions) == 3

    def test_async_function(self, extractor: RustExtractor) -> None:
        """process_data is async, public, with params and return type."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        func = next(f for f in ast.functions if f.name == "process_data")
        assert func.is_async
        assert func.visibility == Visibility.PUBLIC
        assert len(func.params) == 2
        assert func.return_type != ""

    def test_sync_private_function(self, extractor: RustExtractor) -> None:
        """helper is sync, private."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        func = next(f for f in ast.functions if f.name == "helper")
        assert not func.is_async
        assert func.visibility == Visibility.PRIVATE

    def test_unsafe_function(self, extractor: RustExtractor) -> None:
        """dangerous_operation is unsafe."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        func = next(f for f in ast.functions if f.name == "dangerous_operation")
        assert func.is_unsafe
        assert func.visibility == Visibility.PUBLIC


# endregion: --- Function Tests


# ---------------------------------------------------------------------------
# region:    --- Module & Auxiliary Tests
# ---------------------------------------------------------------------------


class TestModulesAndAuxiliary:
    """Verify module declarations, type aliases, constants, macros."""

    def test_module_declarations(self, extractor: RustExtractor) -> None:
        """Must find 'internal' and 'api' module declarations."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        names = {m.name for m in ast.modules}
        assert "internal" in names
        assert "api" in names

    def test_module_visibility(self, extractor: RustExtractor) -> None:
        """'api' is pub, 'internal' is private."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        api = next(m for m in ast.modules if m.name == "api")
        internal = next(m for m in ast.modules if m.name == "internal")
        assert api.visibility == Visibility.PUBLIC
        assert internal.visibility == Visibility.PRIVATE

    def test_type_alias(self, extractor: RustExtractor) -> None:
        """type Result<T> = core::result::Result<T, Error> is captured."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        assert len(ast.type_aliases) == 1
        alias = ast.type_aliases[0]
        assert alias.name == "Result"
        assert alias.visibility == Visibility.PUBLIC

    def test_constants(self, extractor: RustExtractor) -> None:
        """MAX_RETRIES const and COUNTER static are captured."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        names = {c.name for c in ast.constants}
        assert "MAX_RETRIES" in names
        assert "COUNTER" in names

    def test_macro_definition(self, extractor: RustExtractor) -> None:
        """my_macro is captured."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        assert len(ast.macros) == 1
        assert ast.macros[0].name == "my_macro"


# endregion: --- Module & Auxiliary Tests


# ---------------------------------------------------------------------------
# region:    --- Import Map Tests
# ---------------------------------------------------------------------------


class TestImportMap:
    """Verify build_import_map logic."""

    def test_simple_import(self) -> None:
        """use std::collections::HashMap resolves."""
        result = build_import_map(["use std::collections::HashMap;"])
        assert result["HashMap"] == "std::collections::HashMap"

    def test_braced_import(self) -> None:
        """use bytes::{Buf, BytesMut} resolves both."""
        result = build_import_map(["use bytes::{Buf, BytesMut};"])
        assert result["Buf"] == "bytes::Buf"
        assert result["BytesMut"] == "bytes::BytesMut"

    def test_aliased_import(self) -> None:
        """use crate::config::Settings as AppSettings resolves alias."""
        result = build_import_map(["use crate::config::Settings as AppSettings;"])
        assert result["AppSettings"] == "crate::config::Settings"

    def test_nested_braced_import(self) -> None:
        """use tokio::sync::mpsc::{self, Sender} resolves items."""
        result = build_import_map(["use tokio::sync::mpsc::{self, Sender, Receiver};"])
        assert result["Sender"] == "tokio::sync::mpsc::Sender"
        assert result["Receiver"] == "tokio::sync::mpsc::Receiver"


# endregion: --- Import Map Tests


# ---------------------------------------------------------------------------
# region:    --- Scoped Method Calls Tests
# ---------------------------------------------------------------------------


class TestScopedMethodCalls:
    """Verify imported_package_methods extraction."""

    def test_scoped_calls_detected(self, extractor: RustExtractor) -> None:
        """BytesMut::with_capacity is detected in functions_and_modules.rs."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        assert len(ast.imported_package_methods) > 0
        # BytesMut should be resolved via import map
        has_bytes_mut = any(
            "BytesMut" in key for key in ast.imported_package_methods
        )
        assert has_bytes_mut

    def test_scoped_calls_from_complex_imports(
        self, extractor: RustExtractor
    ) -> None:
        """Complex imports fixture should resolve HashMap and BytesMut calls."""
        ast = _parse_fixture(extractor, "complex_imports.rs")
        has_hash_map = any(
            "HashMap" in key for key in ast.imported_package_methods
        )
        assert has_hash_map


# endregion: --- Scoped Method Calls Tests


# ---------------------------------------------------------------------------
# region:    --- self_methods Tests
# ---------------------------------------------------------------------------


class TestSelfMethods:
    """Verify the self_methods aggregation."""

    def test_self_methods_from_functions_file(
        self, extractor: RustExtractor
    ) -> None:
        """functions_and_modules.rs should aggregate free functions."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        method_names = {m.name for m in ast.self_methods}
        assert "process_data" in method_names
        assert "helper" in method_names
        assert "dangerous_operation" in method_names

    def test_self_methods_include_impl_methods(
        self, extractor: RustExtractor
    ) -> None:
        """impl_block.rs self_methods should include impl methods."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        method_names = {m.name for m in ast.self_methods}
        assert "new" in method_names
        assert "start" in method_names
        assert "fmt" in method_names

    def test_self_method_context(self, extractor: RustExtractor) -> None:
        """Methods from impls have context strings."""
        ast = _parse_fixture(extractor, "impl_block.rs")
        new_method = next(m for m in ast.self_methods if m.name == "new")
        assert "impl:" in new_method.context
        assert "MyServer" in new_method.context


# endregion: --- self_methods Tests


# ---------------------------------------------------------------------------
# region:    --- Manifest Parsing Tests
# ---------------------------------------------------------------------------


class TestManifestParsing:
    """Verify Cargo.toml manifest parsing."""

    def test_parse_manifest_name(self, extractor: RustExtractor) -> None:
        """Package name is extracted."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        assert crate.name == "sample-service"

    def test_parse_manifest_version(self, extractor: RustExtractor) -> None:
        """Package version is extracted."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        assert crate.version == "0.2.0"

    def test_parse_manifest_language(self, extractor: RustExtractor) -> None:
        """Language is set to 'rust'."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        assert crate.language == "rust"

    def test_parse_manifest_dependencies(self, extractor: RustExtractor) -> None:
        """Dependencies are parsed correctly."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        dep_names = {d.name for d in crate.dependencies if not d.is_dev}
        assert "tokio" in dep_names
        assert "serde" in dep_names
        assert "lib-common" in dep_names

    def test_parse_manifest_dev_deps(self, extractor: RustExtractor) -> None:
        """Dev dependencies are flagged."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        dev_deps = {d.name for d in crate.dependencies if d.is_dev}
        assert "mockall" in dev_deps
        assert "tokio-test" in dev_deps

    def test_parse_manifest_features(self, extractor: RustExtractor) -> None:
        """Dependency features are extracted."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        tokio = next(d for d in crate.dependencies if d.name == "tokio")
        assert "full" in tokio.features

    def test_parse_manifest_path_dep(self, extractor: RustExtractor) -> None:
        """Path dependencies have path set."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "Cargo.toml")
        lib_common = next(d for d in crate.dependencies if d.name == "lib-common")
        assert lib_common.path != ""

    def test_parse_manifest_missing_file(self, extractor: RustExtractor) -> None:
        """Missing Cargo.toml returns a fallback CrateModel."""
        crate = extractor.parse_manifest(Path("/nonexistent/Cargo.toml"))
        assert crate.name != ""
        assert crate.language == "rust"


# endregion: --- Manifest Parsing Tests


# ---------------------------------------------------------------------------
# region:    --- Use Statements Tests
# ---------------------------------------------------------------------------


class TestUseStatements:
    """Verify use statement extraction."""

    def test_use_statements_captured(self, extractor: RustExtractor) -> None:
        """functions_and_modules.rs captures use statements."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        assert len(ast.uses) >= 4
        has_hashmap = any("HashMap" in u for u in ast.uses)
        assert has_hashmap


# endregion: --- Use Statements Tests


# ---------------------------------------------------------------------------
# region:    --- Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Verify edge cases and robustness."""

    def test_empty_file(self, extractor: RustExtractor) -> None:
        """Empty file produces empty FileAST."""
        ast = extractor.extract(Path("/tmp/empty.rs"), b"")
        assert ast.file == "/tmp/empty.rs"
        assert len(ast.structs) == 0
        assert len(ast.functions) == 0

    def test_comment_only_file(self, extractor: RustExtractor) -> None:
        """File with only comments produces empty AST."""
        source = b"// This is a comment\n/// Doc comment\n/* block */\n"
        ast = extractor.extract(Path("/tmp/comments.rs"), source)
        assert len(ast.structs) == 0
        assert len(ast.functions) == 0

    def test_uses_as_strings(self, extractor: RustExtractor) -> None:
        """Use statements are stored as raw strings."""
        source = b"use std::collections::HashMap;\n"
        ast = extractor.extract(Path("/tmp/import.rs"), source)
        assert len(ast.uses) == 1
        assert "HashMap" in ast.uses[0]

    def test_parse_error_captured(self, extractor: RustExtractor) -> None:
        """Malformed Rust syntax produces an error entry."""
        source = b"pub struct Broken { fn inside struct }\n"
        ast = extractor.extract(Path("/tmp/broken.rs"), source)
        assert len(ast.errors) > 0

    def test_is_test_and_module_path_defaults(
        self, extractor: RustExtractor,
    ) -> None:
        """is_test and module_path default to empty (set by workspace orchestrator)."""
        ast = extractor.extract(Path("/tmp/test.rs"), b"fn foo() {}")
        assert ast.is_test is False
        assert ast.module_path == ""

    def test_free_function_params_in_self_methods(
        self, extractor: RustExtractor,
    ) -> None:
        """Free functions in self_methods must carry params."""
        ast = _parse_fixture(extractor, "functions_and_modules.rs")
        process = next(m for m in ast.self_methods if m.name == "process_data")
        assert len(process.params) > 0


# endregion: --- Edge Cases
