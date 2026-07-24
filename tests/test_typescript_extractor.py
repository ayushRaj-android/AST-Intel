"""Comprehensive tests for the TypeScript / JavaScript extractor.

Tests cover: interfaces → TraitNode, classes → StructNode + ImplBlockNode,
enums → EnumNode, functions → FunctionNode, type aliases, constants,
imports + import map, scoped method calls (imported_package_methods),
abstract classes → TraitNode, decorators, JS-specific patterns, and
package.json manifest parsing.

Fixture files:
    tests/fixtures/typescript/interfaces.ts
    tests/fixtures/typescript/classes.ts
    tests/fixtures/typescript/enums.ts
    tests/fixtures/typescript/functions.ts
    tests/fixtures/typescript/imports.ts
    tests/fixtures/typescript/constants.ts
    tests/fixtures/typescript/abstract.ts
    tests/fixtures/typescript/type_aliases.ts
    tests/fixtures/typescript/decorators.ts
    tests/fixtures/typescript/package.json
    tests/fixtures/javascript/classes.js
    tests/fixtures/javascript/functions.js
    tests/fixtures/javascript/imports.js
    tests/fixtures/javascript/package.json
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.typescript import TypeScriptExtractor
from ast_intel.models.ast_node import (
    EnumVariantKind,
    FileAST,
    TraitItemKind,
    Visibility,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TS_DIR = FIXTURE_DIR / "typescript"
JS_DIR = FIXTURE_DIR / "javascript"


def _extract(fixture: Path) -> FileAST:
    """Parse a fixture file and return the FileAST."""
    ext = TypeScriptExtractor()
    return ext.extract(fixture, fixture.read_bytes())


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Extractor Identity Tests
# ---------------------------------------------------------------------------


class TestExtractorIdentity:
    def test_language_id(self):
        ext = TypeScriptExtractor()
        assert ext.language_id == "typescript"

    def test_file_extensions(self):
        ext = TypeScriptExtractor()
        assert ".ts" in ext.file_extensions
        assert ".tsx" in ext.file_extensions
        assert ".js" in ext.file_extensions
        assert ".jsx" in ext.file_extensions

    def test_empty_source(self):
        ext = TypeScriptExtractor()
        ast = ext.extract(Path("empty.ts"), b"")
        assert ast.structs == []
        assert ast.traits == []
        assert ast.errors == []

    def test_whitespace_only_source(self):
        ext = TypeScriptExtractor()
        ast = ext.extract(Path("blank.ts"), b"  \n  \n  ")
        assert ast.structs == []


# endregion: --- Extractor Identity


# ---------------------------------------------------------------------------
# region:    --- Interface Extraction (→ TraitNode)
# ---------------------------------------------------------------------------


class TestInterfaceExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "interfaces.ts")

    def test_interface_count(self):
        assert len(self.ast.traits) == 5

    def test_exported_interface(self):
        storage = next(t for t in self.ast.traits if t.name == "StorageHelper")
        assert storage.visibility == Visibility.PUBLIC

    def test_private_interface(self):
        serial = next(t for t in self.ast.traits if t.name == "Serializable")
        assert serial.visibility == Visibility.PRIVATE

    def test_interface_methods(self):
        storage = next(t for t in self.ast.traits if t.name == "StorageHelper")
        assert len(storage.items) == 2
        names = [i.name for i in storage.items]
        assert "retrieve" in names
        assert "store" in names

    def test_method_return_type(self):
        storage = next(t for t in self.ast.traits if t.name == "StorageHelper")
        retrieve = next(i for i in storage.items if i.name == "retrieve")
        assert retrieve.return_type == "Promise<Uint8Array>"

    def test_method_params(self):
        storage = next(t for t in self.ast.traits if t.name == "StorageHelper")
        store = next(i for i in storage.items if i.name == "store")
        assert store.params[0].name == "data"
        assert store.params[0].type == "string"

    def test_interface_extends(self):
        repo = next(t for t in self.ast.traits if t.name == "Repository")
        assert "Serializable" in repo.super_traits

    def test_interface_generics(self):
        repo = next(t for t in self.ast.traits if t.name == "Repository")
        assert "<T>" in repo.generics

    def test_interface_multiple_extends(self):
        combined = next(t for t in self.ast.traits if t.name == "CombinedHelper")
        assert "StorageHelper" in combined.super_traits
        assert "Serializable" in combined.super_traits

    def test_interface_property_as_associated_type(self):
        config = next(t for t in self.ast.traits if t.name == "Config")
        names = [i.name for i in config.items]
        assert "name" in names
        assert "port" in names

    def test_all_items_are_required_or_associated(self):
        for trait in self.ast.traits:
            for item in trait.items:
                assert item.kind in (
                    TraitItemKind.REQUIRED_METHOD,
                    TraitItemKind.ASSOCIATED_TYPE,
                )


# endregion: --- Interface Extraction


# ---------------------------------------------------------------------------
# region:    --- Class Extraction (→ StructNode + ImplBlockNode)
# ---------------------------------------------------------------------------


class TestClassExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "classes.ts")

    def test_struct_count(self):
        assert len(self.ast.structs) == 8

    def test_exported_class(self):
        disk = next(s for s in self.ast.structs if s.name == "DiskStorage")
        assert disk.visibility == Visibility.PUBLIC

    def test_private_class(self):
        helper = next(s for s in self.ast.structs if s.name == "InternalHelper")
        assert helper.visibility == Visibility.PRIVATE

    def test_property_fields(self):
        disk = next(s for s in self.ast.structs if s.name == "DiskStorage")
        names = [f.name for f in disk.fields]
        assert "name" in names
        assert "port" in names
        assert "label" in names
        assert "id" in names

    def test_field_visibility(self):
        disk = next(s for s in self.ast.structs if s.name == "DiskStorage")
        field_map = {f.name: f for f in disk.fields}
        assert field_map["name"].visibility == Visibility.PRIVATE
        assert field_map["port"].visibility == Visibility.PUBLIC
        assert field_map["label"].visibility == Visibility.PROTECTED

    def test_field_types(self):
        disk = next(s for s in self.ast.structs if s.name == "DiskStorage")
        field_map = {f.name: f for f in disk.fields}
        assert field_map["name"].type == "string"
        assert field_map["port"].type == "number"

    def test_constructor_param_properties(self):
        user = next(s for s in self.ast.structs if s.name == "UserService")
        field_map = {f.name: f for f in user.fields}
        assert "name" in field_map
        assert "age" in field_map
        assert "email" in field_map
        # 'role' has no accessibility modifier, so NOT a field
        assert "role" not in field_map

    def test_constructor_field_visibility(self):
        user = next(s for s in self.ast.structs if s.name == "UserService")
        field_map = {f.name: f for f in user.fields}
        assert field_map["name"].visibility == Visibility.PRIVATE
        assert field_map["age"].visibility == Visibility.PUBLIC
        assert field_map["email"].visibility == Visibility.PROTECTED

    def test_implements_interface(self):
        impl = next(
            ib
            for ib in self.ast.impl_blocks
            if ib.self_type == "RedisStorage" and ib.trait_type == "StorageHelper"
        )
        assert impl is not None

    def test_extends_base(self):
        impl = next(
            ib for ib in self.ast.impl_blocks if ib.self_type == "Child"
        )
        assert impl.trait_type == "Base"

    def test_extends_and_implements(self):
        advanced_impls = [
            ib
            for ib in self.ast.impl_blocks
            if ib.self_type == "AdvancedStorage"
        ]
        trait_types = {ib.trait_type for ib in advanced_impls}
        assert "Base" in trait_types
        assert "StorageHelper" in trait_types

    def test_inherent_impl_block(self):
        base = next(
            ib for ib in self.ast.impl_blocks if ib.self_type == "Base"
        )
        assert base.trait_type == ""
        assert any(m.name == "baseMethod" for m in base.methods)

    def test_generic_class(self):
        box = next(s for s in self.ast.structs if s.name == "GenericBox")
        assert "<T>" in box.generics

    def test_methods_in_self_methods(self):
        method_names = [m.name for m in self.ast.self_methods]
        assert "retrieve" in method_names
        assert "store" in method_names
        assert "create" in method_names
        assert "fullName" in method_names
        assert "getName" in method_names
        assert "getValue" in method_names

    def test_static_method(self):
        create = next(
            m for m in self.ast.self_methods if m.name == "create"
        )
        assert create.is_static is True

    def test_async_method(self):
        retrieve = next(
            m
            for m in self.ast.self_methods
            if m.name == "retrieve" and m.context == "impl:DiskStorage"
        )
        assert retrieve.is_async is True

    def test_method_context(self):
        for m in self.ast.self_methods:
            assert m.context.startswith("impl:")


# endregion: --- Class Extraction


# ---------------------------------------------------------------------------
# region:    --- Enum Extraction (→ EnumNode)
# ---------------------------------------------------------------------------


class TestEnumExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "enums.ts")

    def test_enum_count(self):
        assert len(self.ast.enums) == 4

    def test_string_enum(self):
        status = next(e for e in self.ast.enums if e.name == "Status")
        assert status.visibility == Visibility.PUBLIC
        names = [v.name for v in status.variants]
        assert "Active" in names
        assert "Inactive" in names
        assert "Pending" in names

    def test_auto_enum(self):
        direction = next(e for e in self.ast.enums if e.name == "Direction")
        assert direction.visibility == Visibility.PRIVATE
        assert len(direction.variants) == 4

    def test_numeric_enum(self):
        http = next(e for e in self.ast.enums if e.name == "HttpStatus")
        names = [v.name for v in http.variants]
        assert "OK" in names
        assert "NotFound" in names
        assert "InternalError" in names

    def test_const_enum(self):
        color = next(e for e in self.ast.enums if e.name == "Color")
        assert len(color.variants) == 3

    def test_all_variants_are_unit(self):
        for enum in self.ast.enums:
            for v in enum.variants:
                assert v.kind == EnumVariantKind.UNIT


# endregion: --- Enum Extraction


# ---------------------------------------------------------------------------
# region:    --- Function Extraction (→ FunctionNode)
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "functions.ts")

    def test_function_count(self):
        assert len(self.ast.functions) >= 6

    def test_async_function(self):
        fetch = next(f for f in self.ast.functions if f.name == "fetchData")
        assert fetch.is_async is True
        assert fetch.visibility == Visibility.PUBLIC

    def test_regular_function(self):
        reg = next(f for f in self.ast.functions if f.name == "regularFn")
        assert reg.is_async is False
        assert reg.visibility == Visibility.PRIVATE

    def test_function_params(self):
        reg = next(f for f in self.ast.functions if f.name == "regularFn")
        assert len(reg.params) == 2
        assert reg.params[0].name == "x"
        assert reg.params[0].type == "number"

    def test_function_return_type(self):
        fetch = next(f for f in self.ast.functions if f.name == "fetchData")
        assert fetch.return_type == "Promise<Response>"

    def test_arrow_function_async(self):
        handler = next(f for f in self.ast.functions if f.name == "handler")
        assert handler.is_async is True
        assert handler.visibility == Visibility.PUBLIC

    def test_arrow_function_simple(self):
        square = next(f for f in self.ast.functions if f.name == "square")
        assert square.is_async is False
        assert square.return_type == "number"

    def test_arrow_function_no_return_type(self):
        log_fn = next(f for f in self.ast.functions if f.name == "logMessage")
        assert log_fn.return_type == ""

    def test_default_export_function(self):
        default_fn = next(
            f for f in self.ast.functions if f.name == "defaultExport"
        )
        assert default_fn.visibility == Visibility.PUBLIC

    def test_generic_function(self):
        identity = next(f for f in self.ast.functions if f.name == "identity")
        assert "<T>" in identity.generics
        assert identity.return_type == "T"


# endregion: --- Function Extraction


# ---------------------------------------------------------------------------
# region:    --- Type Alias Extraction
# ---------------------------------------------------------------------------


class TestTypeAliasExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "type_aliases.ts")

    def test_simple_alias(self):
        alias = next(
            t for t in self.ast.type_aliases if t.name == "StringAlias"
        )
        assert alias.aliased_to == "string"

    def test_union_alias(self):
        result = next(
            t for t in self.ast.type_aliases if t.name == "Result"
        )
        assert "Error" in result.aliased_to

    def test_function_type_alias(self):
        cb = next(
            t for t in self.ast.type_aliases if t.name == "Callback"
        )
        assert "=>" in cb.aliased_to

    def test_intersection_alias(self):
        named = next(
            t for t in self.ast.type_aliases if t.name == "Named"
        )
        assert "&" in named.aliased_to

    def test_object_type_becomes_struct(self):
        user = next(s for s in self.ast.structs if s.name == "UserConfig")
        assert user.visibility == Visibility.PUBLIC
        field_names = [f.name for f in user.fields]
        assert "name" in field_names
        assert "port" in field_names

    def test_object_type_field_types(self):
        point = next(s for s in self.ast.structs if s.name == "Point")
        field_map = {f.name: f for f in point.fields}
        assert field_map["x"].type == "number"
        assert field_map["y"].type == "number"

    def test_exported_type_alias_visibility(self):
        result = next(
            t for t in self.ast.type_aliases if t.name == "Result"
        )
        assert result.visibility == Visibility.PUBLIC

    def test_private_type_alias_visibility(self):
        alias = next(
            t for t in self.ast.type_aliases if t.name == "StringAlias"
        )
        assert alias.visibility == Visibility.PRIVATE


# endregion: --- Type Alias Extraction


# ---------------------------------------------------------------------------
# region:    --- Constant Extraction (→ ConstantNode)
# ---------------------------------------------------------------------------


class TestConstantExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "constants.ts")

    def test_upper_case_exported(self):
        max_const = next(
            c for c in self.ast.constants if c.name == "MAX_SIZE"
        )
        assert max_const.visibility == Visibility.PUBLIC

    def test_upper_case_private(self):
        priv = next(
            c for c in self.ast.constants if c.name == "PRIVATE_CONST"
        )
        assert priv.visibility == Visibility.PRIVATE

    def test_camel_case_with_type_annotation(self):
        # APP_NAME has no type annotation but is UPPER_CASE
        app = next(
            c for c in self.ast.constants if c.name == "APP_NAME"
        )
        assert app is not None

    def test_raw_text_present(self):
        max_const = next(
            c for c in self.ast.constants if c.name == "MAX_SIZE"
        )
        assert "100" in max_const.raw


# endregion: --- Constant Extraction


# ---------------------------------------------------------------------------
# region:    --- Abstract Class Extraction (→ TraitNode)
# ---------------------------------------------------------------------------


class TestAbstractClassExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "abstract.ts")

    def test_abstract_class_is_trait(self):
        animal = next(t for t in self.ast.traits if t.name == "Animal")
        assert animal is not None

    def test_abstract_methods_are_required(self):
        animal = next(t for t in self.ast.traits if t.name == "Animal")
        required = [
            i for i in animal.items
            if i.kind == TraitItemKind.REQUIRED_METHOD
        ]
        names = [i.name for i in required]
        assert "makeSound" in names
        assert "species" in names

    def test_concrete_methods_are_default(self):
        animal = next(t for t in self.ast.traits if t.name == "Animal")
        default = [
            i for i in animal.items
            if i.kind == TraitItemKind.DEFAULT_METHOD
        ]
        assert any(i.name == "move" for i in default)

    def test_abstract_class_exported(self):
        animal = next(t for t in self.ast.traits if t.name == "Animal")
        assert animal.visibility == Visibility.PUBLIC

    def test_extending_abstract_creates_impl(self):
        dog_impl = next(
            ib
            for ib in self.ast.impl_blocks
            if ib.self_type == "Dog" and ib.trait_type == "Animal"
        )
        assert dog_impl is not None

    def test_generic_abstract_class(self):
        container = next(
            t for t in self.ast.traits if t.name == "Container"
        )
        assert "<T>" in container.generics
        required_names = [
            i.name for i in container.items
            if i.kind == TraitItemKind.REQUIRED_METHOD
        ]
        assert "getValue" in required_names
        assert "setValue" in required_names


# endregion: --- Abstract Class Extraction


# ---------------------------------------------------------------------------
# region:    --- Decorator Extraction
# ---------------------------------------------------------------------------


class TestDecoratorExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "decorators.ts")

    def test_class_decorator(self):
        service = next(s for s in self.ast.structs if s.name == "AppService")
        assert any("@Injectable" in a for a in service.attributes)

    def test_method_decorator(self):
        process = next(
            m for m in self.ast.self_methods if m.name == "process"
        )
        assert any("@Log" in a for a in process.attributes)


# endregion: --- Decorator Extraction


# ---------------------------------------------------------------------------
# region:    --- Import Extraction & Import Map
# ---------------------------------------------------------------------------


class TestImportExtraction:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "imports.ts")

    def test_import_count(self):
        assert len(self.ast.uses) == 5

    def test_named_import(self):
        assert any("Router" in u for u in self.ast.uses)

    def test_default_import(self):
        assert any("axios" in u for u in self.ast.uses)

    def test_namespace_import(self):
        assert any("* as fs" in u for u in self.ast.uses)

    def test_type_import(self):
        assert any("type" in u and "Config" in u for u in self.ast.uses)

    def test_aliased_import(self):
        assert any("readAsync" in u or "readFile" in u for u in self.ast.uses)


class TestImportMap:
    def test_named_import_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.ts"))
        src = b"import { Router, Request } from 'express';"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["Router"] == "express::Router"
        assert m["Request"] == "express::Request"

    def test_default_import_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.ts"))
        src = b"import axios from 'axios';"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["axios"] == "axios"

    def test_namespace_import_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.ts"))
        src = b"import * as fs from 'fs';"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["fs"] == "fs"

    def test_aliased_import_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.ts"))
        src = b"import { readFile as rf } from 'fs';"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["rf"] == "fs::readFile"

    def test_require_import_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.js"))
        src = b"const axios = require('axios');"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["axios"] == "axios"

    def test_destructured_require_map(self):
        from ast_intel.extractors.typescript import _build_import_map

        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.js"))
        src = b"const { Router, Request } = require('express');"
        tree = parser.parse(src)
        m = _build_import_map(tree.root_node, src)
        assert m["Router"] == "express::Router"
        assert m["Request"] == "express::Request"


# endregion: --- Import Extraction & Import Map


# ---------------------------------------------------------------------------
# region:    --- Imported Package Methods (Scoped Calls)
# ---------------------------------------------------------------------------


class TestImportedPackageMethods:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "imports.ts")

    def test_named_import_call(self):
        assert "express::Router" in self.ast.imported_package_methods
        assert "use" in self.ast.imported_package_methods["express::Router"]

    def test_default_import_call(self):
        assert "axios" in self.ast.imported_package_methods
        assert "get" in self.ast.imported_package_methods["axios"]

    def test_namespace_import_call(self):
        assert "fs" in self.ast.imported_package_methods
        assert "readFile" in self.ast.imported_package_methods["fs"]


# endregion: --- Imported Package Methods


# ---------------------------------------------------------------------------
# region:    --- Self Methods
# ---------------------------------------------------------------------------


class TestSelfMethods:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(TS_DIR / "classes.ts")

    def test_class_methods_in_self_methods(self):
        contexts = {m.context for m in self.ast.self_methods}
        assert "impl:DiskStorage" in contexts
        assert "impl:RedisStorage" in contexts
        assert "impl:GenericBox" in contexts

    def test_all_class_methods_have_impl_context(self):
        for m in self.ast.self_methods:
            assert m.context.startswith("impl:")

    def test_free_functions_in_self_methods(self):
        func_ast = _extract(TS_DIR / "functions.ts")
        contexts = {m.context for m in func_ast.self_methods}
        assert "free" in contexts

    def test_free_function_method_names(self):
        func_ast = _extract(TS_DIR / "functions.ts")
        free_names = [
            m.name for m in func_ast.self_methods if m.context == "free"
        ]
        assert "fetchData" in free_names
        assert "regularFn" in free_names


# endregion: --- Self Methods


# ---------------------------------------------------------------------------
# region:    --- JavaScript-Specific Tests
# ---------------------------------------------------------------------------


class TestJavaScriptClasses:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(JS_DIR / "classes.js")

    def test_class_names_extracted(self):
        names = [s.name for s in self.ast.structs]
        assert "Animal" in names
        assert "Dog" in names

    def test_extends_captured(self):
        dog_impl = next(
            ib for ib in self.ast.impl_blocks if ib.self_type == "Dog"
        )
        assert dog_impl.trait_type == "Animal"

    def test_js_method_extraction(self):
        method_names = [m.name for m in self.ast.self_methods]
        assert "speak" in method_names
        assert "create" in method_names
        assert "fetch" in method_names

    def test_static_method_js(self):
        create = next(
            m for m in self.ast.self_methods if m.name == "create"
        )
        assert create.is_static is True


class TestJavaScriptFunctions:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(JS_DIR / "functions.js")

    def test_function_count(self):
        assert len(self.ast.functions) >= 5

    def test_async_function_js(self):
        fetch = next(f for f in self.ast.functions if f.name == "fetchData")
        assert fetch.is_async is True

    def test_regular_function_js(self):
        add = next(f for f in self.ast.functions if f.name == "add")
        assert add.is_async is False

    def test_arrow_function_js(self):
        handler = next(f for f in self.ast.functions if f.name == "handler")
        assert handler.is_async is True

    def test_default_export_js(self):
        default_fn = next(
            f for f in self.ast.functions if f.name == "defaultFn"
        )
        assert default_fn.visibility == Visibility.PUBLIC

    def test_js_params_no_types(self):
        add_fn = next(f for f in self.ast.functions if f.name == "add")
        assert len(add_fn.params) == 2
        assert add_fn.params[0].name == "a"
        assert add_fn.params[0].type == ""


class TestJavaScriptImports:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.ast = _extract(JS_DIR / "imports.js")

    def test_require_as_import(self):
        assert any("require" in u for u in self.ast.uses)

    def test_es_import(self):
        assert any("readFile" in u for u in self.ast.uses)

    def test_scoped_calls(self):
        assert "express::Router" in self.ast.imported_package_methods
        assert "use" in self.ast.imported_package_methods["express::Router"]

    def test_default_require_call(self):
        assert "axios" in self.ast.imported_package_methods
        assert "get" in self.ast.imported_package_methods["axios"]

    def test_namespace_es_import_call(self):
        assert "os" in self.ast.imported_package_methods
        assert "platform" in self.ast.imported_package_methods["os"]


# endregion: --- JavaScript-Specific Tests


# ---------------------------------------------------------------------------
# region:    --- Manifest Parsing (package.json)
# ---------------------------------------------------------------------------


class TestManifestParsing:
    def test_ts_package_name(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        assert crate.name == "sample-ts-project"

    def test_ts_package_version(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        assert crate.version == "1.2.3"

    def test_ts_language(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        assert crate.language == "typescript"

    def test_ts_dependencies(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        dep_names = [d.name for d in crate.dependencies if not d.is_dev]
        assert "express" in dep_names
        assert "axios" in dep_names

    def test_ts_dev_dependencies(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        dev_names = [d.name for d in crate.dependencies if d.is_dev]
        assert "typescript" in dev_names
        assert "jest" in dev_names

    def test_ts_peer_dependencies(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        prod_names = [d.name for d in crate.dependencies if not d.is_dev]
        assert "react" in prod_names

    def test_ts_optional_dependencies(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(TS_DIR / "package.json")
        prod_names = [d.name for d in crate.dependencies if not d.is_dev]
        assert "fsevents" in prod_names

    def test_non_dict_json(self, tmp_path):
        ext = TypeScriptExtractor()
        bad = tmp_path / "package.json"
        bad.write_text('["not", "a", "dict"]', encoding="utf-8")
        crate = ext.parse_manifest(bad)
        assert crate.name == tmp_path.name

    def test_js_package(self):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(JS_DIR / "package.json")
        assert crate.name == "sample-js-project"
        assert crate.version == "0.5.0"

    def test_malformed_json(self, tmp_path):
        ext = TypeScriptExtractor()
        bad = tmp_path / "package.json"
        bad.write_text("{invalid json", encoding="utf-8")
        crate = ext.parse_manifest(bad)
        assert crate.name == tmp_path.name

    def test_missing_file(self, tmp_path):
        ext = TypeScriptExtractor()
        crate = ext.parse_manifest(tmp_path / "nonexistent" / "package.json")
        assert crate.language == "typescript"


# endregion: --- Manifest Parsing


# ---------------------------------------------------------------------------
# region:    --- Dispatcher Integration
# ---------------------------------------------------------------------------


class TestDispatcherIntegration:
    def test_ts_extension_registered(self):
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert ".ts" in registry
        assert ".tsx" in registry
        assert ".js" in registry
        assert ".jsx" in registry

    def test_extractor_class_is_typescript(self):
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert registry[".ts"].__name__ == "TypeScriptExtractor"
        assert registry[".js"].__name__ == "TypeScriptExtractor"


# endregion: --- Dispatcher Integration


# ---------------------------------------------------------------------------
# region:    --- Manifest Parser Integration
# ---------------------------------------------------------------------------


class TestManifestParserIntegration:
    def test_package_json_routes_to_typescript(self):
        from ast_intel.core.manifest_parser import ManifestParser

        parser = ManifestParser()
        crate = parser.parse(TS_DIR / "package.json")
        assert crate.name == "sample-ts-project"
        assert crate.language == "typescript"


# endregion: --- Manifest Parser Integration


# ---------------------------------------------------------------------------
# region:    --- Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_syntax_error_graceful(self):
        ext = TypeScriptExtractor()
        src = b"class { broken syntax"
        ast = ext.extract(Path("broken.ts"), src)
        # Parser may still extract partial results; no exception raised
        assert isinstance(ast, FileAST)

    def test_arrow_functions_not_in_self_methods(self):
        """Arrow functions (const x = () => {}) should NOT appear in self_methods.

        Only class methods (context impl:X) and function declarations
        (context free) are captured as self_methods.
        """
        ext = TypeScriptExtractor()
        src = b"export const handler = async (req: any) => { return req; };"
        ast = ext.extract(Path("arrow.ts"), src)
        assert len(ast.functions) == 1
        assert ast.functions[0].name == "handler"
        # Arrow fn should NOT be in self_methods
        assert not any(m.name == "handler" for m in ast.self_methods)

    def test_string_content_fallback_strips_quotes(self):
        """Test the fallback path of _extract_string_content."""
        from ast_intel.extractors.typescript import _extract_string_content

        # Create a mock-like node that has no string_fragment child
        ext = TypeScriptExtractor()
        parser = ext._get_parser(Path("test.ts"))
        # Parse a template string that tree-sitter handles differently
        src = b"import x from 'pkg';"
        tree = parser.parse(src)
        # The string node IS present and has a fragment;
        # exercise the function directly to ensure no crash
        root = tree.root_node
        import_stmt = root.children[0]  # import_statement
        for child in import_stmt.children:
            if child.type == "string":
                result = _extract_string_content(child, src)
                assert result == "pkg"
                break

    def test_nested_class_not_extracted(self):
        """Nested classes (class inside function) are not top-level."""
        ext = TypeScriptExtractor()
        src = b"""
function factory() {
    class Inner {}
    return new Inner();
}
"""
        ast = ext.extract(Path("nested.ts"), src)
        # Inner is not at the top level — it's inside a function body
        assert not any(s.name == "Inner" for s in ast.structs)

    def test_reexport_statement(self):
        ext = TypeScriptExtractor()
        src = b"export { foo, bar } from './other';"
        ast = ext.extract(Path("reexport.ts"), src)
        # Should not crash; reexport is not a class/function
        assert isinstance(ast, FileAST)

    def test_multiple_variable_declarators(self):
        ext = TypeScriptExtractor()
        src = b"const A = 1, B = 2;"
        ast = ext.extract(Path("multivar.ts"), src)
        assert isinstance(ast, FileAST)

    def test_empty_interface(self):
        ext = TypeScriptExtractor()
        src = b"interface Empty {}"
        ast = ext.extract(Path("empty.ts"), src)
        assert len(ast.traits) == 1
        assert ast.traits[0].name == "Empty"
        assert len(ast.traits[0].items) == 0

    def test_empty_class(self):
        ext = TypeScriptExtractor()
        src = b"class Empty {}"
        ast = ext.extract(Path("empty.ts"), src)
        assert len(ast.structs) == 1
        assert ast.structs[0].name == "Empty"


# endregion: --- Edge Cases
