"""Comprehensive tests for the Go language extractor.

Tests cover: structs → StructNode, interfaces → TraitNode,
functions → FunctionNode, methods → ImplBlockNode,
constants/vars → ConstantNode, type aliases → TypeAliasNode,
imports + import map, scoped method calls (imported_package_methods),
doc comments, generics, and go.mod manifest parsing.

Fixture files:
    tests/fixtures/go/structs.go
    tests/fixtures/go/interfaces.go
    tests/fixtures/go/functions.go
    tests/fixtures/go/methods.go
    tests/fixtures/go/constants.go
    tests/fixtures/go/imports.go
    tests/fixtures/go/type_aliases.go
    tests/fixtures/go/comments.go
    tests/fixtures/go/go.mod
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.go import GoExtractor
from ast_intel.models.ast_node import (
    FileAST,
    TraitItemKind,
    Visibility,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "go"


def _extract(fixture: Path) -> FileAST:
    """Parse a fixture file and return the FileAST."""
    ext = GoExtractor()
    return ext.extract(fixture, fixture.read_bytes())


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Extractor Identity Tests
# ---------------------------------------------------------------------------


class TestExtractorIdentity:
    def test_language_id(self) -> None:
        ext = GoExtractor()
        assert ext.language_id == "go"

    def test_file_extensions(self) -> None:
        ext = GoExtractor()
        assert ".go" in ext.file_extensions

    def test_empty_source(self) -> None:
        ext = GoExtractor()
        ast = ext.extract(Path("empty.go"), b"")
        assert ast.structs == []
        assert ast.traits == []
        assert ast.errors == []

    def test_whitespace_only_source(self) -> None:
        ext = GoExtractor()
        ast = ext.extract(Path("blank.go"), b"  \n  \n  ")
        assert ast.structs == []

    def test_package_only_source(self) -> None:
        ext = GoExtractor()
        ast = ext.extract(Path("minimal.go"), b"package main\n")
        assert ast.structs == []
        assert ast.functions == []
        assert ast.errors == []


# endregion: --- Extractor Identity


# ---------------------------------------------------------------------------
# region:    --- Struct Extraction (→ StructNode)
# ---------------------------------------------------------------------------


class TestStructExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "structs.go")

    def test_struct_count(self) -> None:
        assert len(self.ast.structs) == 7

    def test_exported_struct_visibility(self) -> None:
        sc = next(s for s in self.ast.structs if s.name == "StorageConfig")
        assert sc.visibility == Visibility.PUBLIC

    def test_unexported_struct_visibility(self) -> None:
        us = next(s for s in self.ast.structs if s.name == "unexportedStruct")
        assert us.visibility == Visibility.PRIVATE

    def test_exported_field(self) -> None:
        sc = next(s for s in self.ast.structs if s.name == "StorageConfig")
        field_map = {f.name: f for f in sc.fields}
        assert field_map["BasePath"].visibility == Visibility.PUBLIC
        assert field_map["BasePath"].type == "string"

    def test_unexported_field(self) -> None:
        sc = next(s for s in self.ast.structs if s.name == "StorageConfig")
        field_map = {f.name: f for f in sc.fields}
        assert field_map["timeout"].visibility == Visibility.PRIVATE
        assert field_map["timeout"].type == "time.Duration"

    def test_field_count(self) -> None:
        sc = next(s for s in self.ast.structs if s.name == "StorageConfig")
        assert len(sc.fields) == 4

    def test_generic_struct(self) -> None:
        gc = next(s for s in self.ast.structs if s.name == "GenericContainer")
        assert gc.visibility == Visibility.PUBLIC
        assert "T comparable" in gc.generics
        assert "U any" in gc.generics
        assert len(gc.fields) == 3

    def test_generic_struct_fields(self) -> None:
        gc = next(s for s in self.ast.structs if s.name == "GenericContainer")
        field_map = {f.name: f for f in gc.fields}
        assert field_map["Items"].type == "[]T"
        assert field_map["Meta"].type == "U"
        assert field_map["count"].visibility == Visibility.PRIVATE

    def test_embedded_struct(self) -> None:
        es = next(s for s in self.ast.structs if s.name == "EmbeddedStruct")
        field_map = {f.name: f for f in es.fields}
        assert "StorageConfig" in field_map
        assert field_map["StorageConfig"].type == "StorageConfig"
        assert field_map["Extra"].type == "string"

    def test_qualified_type_fields(self) -> None:
        qf = next(s for s in self.ast.structs if s.name == "QualifiedFields")
        field_map = {f.name: f for f in qf.fields}
        assert field_map["Deadline"].type == "time.Time"
        assert field_map["Elapsed"].type == "time.Duration"

    def test_pointer_fields(self) -> None:
        pf = next(s for s in self.ast.structs if s.name == "PointerFields")
        field_map = {f.name: f for f in pf.fields}
        assert field_map["Config"].type == "*StorageConfig"
        assert field_map["Data"].type == "*[]byte"

    def test_map_fields(self) -> None:
        mf = next(s for s in self.ast.structs if s.name == "MapFields")
        field_map = {f.name: f for f in mf.fields}
        assert field_map["Entries"].type == "map[string]int"
        assert field_map["Nested"].type == "map[string][]byte"

    def test_slice_field(self) -> None:
        sc = next(s for s in self.ast.structs if s.name == "StorageConfig")
        field_map = {f.name: f for f in sc.fields}
        assert field_map["labels"].type == "[]string"

    def test_all_struct_names(self) -> None:
        names = {s.name for s in self.ast.structs}
        expected = {
            "StorageConfig",
            "unexportedStruct",
            "GenericContainer",
            "EmbeddedStruct",
            "QualifiedFields",
            "PointerFields",
            "MapFields",
        }
        assert names == expected


# endregion: --- Struct Extraction


# ---------------------------------------------------------------------------
# region:    --- Interface Extraction (→ TraitNode)
# ---------------------------------------------------------------------------


class TestInterfaceExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "interfaces.go")

    def test_interface_count(self) -> None:
        assert len(self.ast.traits) == 7

    def test_exported_interface(self) -> None:
        sh = next(t for t in self.ast.traits if t.name == "StorageHelper")
        assert sh.visibility == Visibility.PUBLIC

    def test_unexported_interface(self) -> None:
        s = next(t for t in self.ast.traits if t.name == "serializable")
        assert s.visibility == Visibility.PRIVATE

    def test_interface_methods(self) -> None:
        sh = next(t for t in self.ast.traits if t.name == "StorageHelper")
        assert len(sh.items) == 2
        names = [i.name for i in sh.items]
        assert "Retrieve" in names
        assert "Store" in names

    def test_method_params(self) -> None:
        sh = next(t for t in self.ast.traits if t.name == "StorageHelper")
        store = next(i for i in sh.items if i.name == "Store")
        assert store.params[0].name == "key"
        assert store.params[0].type == "string"
        assert store.params[1].name == "data"
        assert store.params[1].type == "[]byte"

    def test_multi_return_type(self) -> None:
        sh = next(t for t in self.ast.traits if t.name == "StorageHelper")
        retrieve = next(i for i in sh.items if i.name == "Retrieve")
        assert retrieve.return_type == "([]byte, error)"

    def test_single_return_type(self) -> None:
        sh = next(t for t in self.ast.traits if t.name == "StorageHelper")
        store = next(i for i in sh.items if i.name == "Store")
        assert store.return_type == "error"

    def test_embedded_interface(self) -> None:
        repo = next(t for t in self.ast.traits if t.name == "Repository")
        assert "StorageHelper" in repo.super_traits

    def test_generic_interface(self) -> None:
        repo = next(t for t in self.ast.traits if t.name == "Repository")
        assert "[T any]" in repo.generics

    def test_multiple_embedded(self) -> None:
        ch = next(t for t in self.ast.traits if t.name == "CombinedHelper")
        assert "StorageHelper" in ch.super_traits
        assert "serializable" in ch.super_traits
        assert len(ch.items) == 1  # Close()

    def test_qualified_embedded(self) -> None:
        rwc = next(t for t in self.ast.traits if t.name == "ReadWriteCloser")
        assert "io.Reader" in rwc.super_traits
        assert "io.Writer" in rwc.super_traits

    def test_property_like_methods(self) -> None:
        cfg = next(t for t in self.ast.traits if t.name == "Config")
        names = [i.name for i in cfg.items]
        assert "Name" in names
        assert "Port" in names
        assert "IsEnabled" in names

    def test_empty_interface(self) -> None:
        ei = next(t for t in self.ast.traits if t.name == "EmptyInterface")
        assert len(ei.items) == 0
        assert len(ei.super_traits) == 0

    def test_all_items_are_required(self) -> None:
        for trait in self.ast.traits:
            for item in trait.items:
                assert item.kind == TraitItemKind.REQUIRED_METHOD

    def test_all_interface_names(self) -> None:
        names = {t.name for t in self.ast.traits}
        expected = {
            "StorageHelper",
            "serializable",
            "Repository",
            "CombinedHelper",
            "ReadWriteCloser",
            "Config",
            "EmptyInterface",
        }
        assert names == expected


# endregion: --- Interface Extraction


# ---------------------------------------------------------------------------
# region:    --- Function Extraction (→ FunctionNode)
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "functions.go")

    def test_function_count(self) -> None:
        assert len(self.ast.functions) == 7

    def test_exported_function(self) -> None:
        pd = next(f for f in self.ast.functions if f.name == "ProcessData")
        assert pd.visibility == Visibility.PUBLIC

    def test_unexported_function(self) -> None:
        hf = next(f for f in self.ast.functions if f.name == "helperFunc")
        assert hf.visibility == Visibility.PRIVATE

    def test_multi_return(self) -> None:
        mr = next(f for f in self.ast.functions if f.name == "MultiReturn")
        assert mr.return_type == "(int, int, error)"

    def test_single_return(self) -> None:
        hf = next(f for f in self.ast.functions if f.name == "helperFunc")
        assert hf.return_type == "string"

    def test_no_return(self) -> None:
        nr = next(f for f in self.ast.functions if f.name == "NoReturn")
        assert nr.return_type == ""

    def test_multi_return_with_context(self) -> None:
        pd = next(f for f in self.ast.functions if f.name == "ProcessData")
        assert pd.return_type == "([]byte, error)"

    def test_params(self) -> None:
        pd = next(f for f in self.ast.functions if f.name == "ProcessData")
        assert len(pd.params) == 2
        assert pd.params[0].name == "ctx"
        assert pd.params[0].type == "context.Context"
        assert pd.params[1].name == "data"
        assert pd.params[1].type == "[]byte"

    def test_generic_function(self) -> None:
        gf = next(f for f in self.ast.functions if f.name == "GenericFunc")
        assert "[T comparable]" in gf.generics
        assert gf.return_type == "int"
        assert len(gf.params) == 2

    def test_variadic_function(self) -> None:
        vf = next(f for f in self.ast.functions if f.name == "VariadicFunc")
        assert vf.return_type == "[]string"
        assert len(vf.params) == 2

    def test_higher_order_function(self) -> None:
        hof = next(f for f in self.ast.functions if f.name == "HigherOrderFunc")
        assert hof.params[0].name == "fn"
        assert "func(int) bool" in hof.params[0].type
        assert hof.return_type == "[]int"

    def test_all_function_names(self) -> None:
        names = {f.name for f in self.ast.functions}
        expected = {
            "ProcessData",
            "helperFunc",
            "MultiReturn",
            "NoReturn",
            "GenericFunc",
            "VariadicFunc",
            "HigherOrderFunc",
        }
        assert names == expected


# endregion: --- Function Extraction


# ---------------------------------------------------------------------------
# region:    --- Method Extraction (→ ImplBlockNode)
# ---------------------------------------------------------------------------


class TestMethodExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "methods.go")

    def test_impl_block_count(self) -> None:
        assert len(self.ast.impl_blocks) == 3

    def test_server_impl(self) -> None:
        server = next(ib for ib in self.ast.impl_blocks if ib.self_type == "Server")
        method_names = [m.name for m in server.methods]
        assert "Start" in method_names
        assert "Stop" in method_names
        assert "Address" in method_names
        assert "isRunning" in method_names
        assert len(server.methods) == 4

    def test_client_impl(self) -> None:
        client = next(ib for ib in self.ast.impl_blocks if ib.self_type == "Client")
        method_names = [m.name for m in client.methods]
        assert "Connect" in method_names
        assert "Disconnect" in method_names
        assert "SetTimeout" in method_names
        assert len(client.methods) == 3

    def test_generic_receiver(self) -> None:
        gs = next(
            ib for ib in self.ast.impl_blocks if ib.self_type == "GenericService"
        )
        method_names = [m.name for m in gs.methods]
        assert "Add" in method_names
        assert "Count" in method_names

    def test_method_visibility(self) -> None:
        server = next(ib for ib in self.ast.impl_blocks if ib.self_type == "Server")
        m_map = {m.name: m for m in server.methods}
        assert m_map["Start"].visibility == Visibility.PUBLIC
        assert m_map["isRunning"].visibility == Visibility.PRIVATE

    def test_method_return_type(self) -> None:
        server = next(ib for ib in self.ast.impl_blocks if ib.self_type == "Server")
        m_map = {m.name: m for m in server.methods}
        assert m_map["Start"].return_type == "error"
        assert m_map["Stop"].return_type == ""
        assert m_map["Address"].return_type == "string"

    def test_method_params(self) -> None:
        client = next(ib for ib in self.ast.impl_blocks if ib.self_type == "Client")
        st = next(m for m in client.methods if m.name == "SetTimeout")
        assert len(st.params) == 1
        assert st.params[0].name == "ms"
        assert st.params[0].type == "int"

    def test_self_methods_count(self) -> None:
        # All impl methods: Server(4) + Client(3) + GenericService(2)
        assert len(self.ast.self_methods) == 9

    def test_self_methods_context(self) -> None:
        server_methods = [
            m for m in self.ast.self_methods if m.context == "impl:Server"
        ]
        assert len(server_methods) == 4

    def test_no_free_functions_in_methods_fixture(self) -> None:
        assert len(self.ast.functions) == 0


# endregion: --- Method Extraction


# ---------------------------------------------------------------------------
# region:    --- Constant / Variable Extraction (→ ConstantNode)
# ---------------------------------------------------------------------------


class TestConstantExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "constants.go")

    def test_total_constant_count(self) -> None:
        assert len(self.ast.constants) == 15

    def test_exported_standalone_const(self) -> None:
        mr = next(c for c in self.ast.constants if c.name == "MaxRetries")
        assert mr.visibility == Visibility.PUBLIC
        assert "3" in mr.raw

    def test_unexported_standalone_const(self) -> None:
        dt = next(c for c in self.ast.constants if c.name == "defaultTimeout")
        assert dt.visibility == Visibility.PRIVATE

    def test_typed_const(self) -> None:
        an = next(c for c in self.ast.constants if c.name == "AppName")
        assert an.visibility == Visibility.PUBLIC
        assert "string" in an.raw

    def test_iota_constants(self) -> None:
        north = next(c for c in self.ast.constants if c.name == "North")
        assert north.visibility == Visibility.PUBLIC
        south = next(c for c in self.ast.constants if c.name == "South")
        assert south.visibility == Visibility.PUBLIC
        east = next(c for c in self.ast.constants if c.name == "East")
        assert east.visibility == Visibility.PUBLIC
        west = next(c for c in self.ast.constants if c.name == "West")
        assert west.visibility == Visibility.PUBLIC

    def test_explicit_value_constants(self) -> None:
        ok = next(c for c in self.ast.constants if c.name == "StatusOK")
        assert "200" in ok.raw
        err = next(c for c in self.ast.constants if c.name == "StatusError")
        assert "500" in err.raw

    def test_exported_variable(self) -> None:
        gc = next(c for c in self.ast.constants if c.name == "GlobalConfig")
        assert gc.visibility == Visibility.PUBLIC

    def test_unexported_variable(self) -> None:
        igs = next(c for c in self.ast.constants if c.name == "internalState")
        assert igs.visibility == Visibility.PRIVATE

    def test_typed_variable(self) -> None:
        mc = next(c for c in self.ast.constants if c.name == "MaxConnections")
        assert mc.visibility == Visibility.PUBLIC
        assert "int" in mc.raw

    def test_block_var_declarations(self) -> None:
        dh = next(c for c in self.ast.constants if c.name == "DefaultHost")
        assert dh.visibility == Visibility.PUBLIC
        dp = next(c for c in self.ast.constants if c.name == "DefaultPort")
        assert dp.visibility == Visibility.PUBLIC

    def test_all_constant_names(self) -> None:
        names = {c.name for c in self.ast.constants}
        expected = {
            "MaxRetries",
            "defaultTimeout",
            "AppName",
            "North",
            "South",
            "East",
            "West",
            "StatusOK",
            "StatusError",
            "StatusNotFound",
            "GlobalConfig",
            "internalState",
            "MaxConnections",
            "DefaultHost",
            "DefaultPort",
        }
        assert names == expected


# endregion: --- Constant Extraction


# ---------------------------------------------------------------------------
# region:    --- Type Alias / Definition Extraction (→ TypeAliasNode)
# ---------------------------------------------------------------------------


class TestTypeAliasExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "type_aliases.go")

    def test_type_alias_count(self) -> None:
        assert len(self.ast.type_aliases) == 7

    def test_true_alias(self) -> None:
        hf = next(ta for ta in self.ast.type_aliases if ta.name == "HandlerFunc")
        assert "func(string) error" in hf.aliased_to
        assert hf.visibility == Visibility.PUBLIC

    def test_type_definition(self) -> None:
        mw = next(ta for ta in self.ast.type_aliases if ta.name == "Middleware")
        assert "func(HandlerFunc) HandlerFunc" in mw.aliased_to

    def test_map_definition(self) -> None:
        sm = next(ta for ta in self.ast.type_aliases if ta.name == "StringMap")
        assert sm.aliased_to == "map[string]string"

    def test_slice_definition(self) -> None:
        il = next(ta for ta in self.ast.type_aliases if ta.name == "IDList")
        assert il.aliased_to == "[]int64"

    def test_simple_definition(self) -> None:
        nid = next(ta for ta in self.ast.type_aliases if ta.name == "NodeID")
        assert nid.aliased_to == "string"

    def test_exported_alias(self) -> None:
        ea = next(ta for ta in self.ast.type_aliases if ta.name == "ExportedAlias")
        assert ea.visibility == Visibility.PUBLIC
        assert ea.aliased_to == "int"

    def test_unexported_definition(self) -> None:
        ud = next(ta for ta in self.ast.type_aliases if ta.name == "unexportedDef")
        assert ud.visibility == Visibility.PRIVATE
        assert ud.aliased_to == "string"

    def test_all_alias_names(self) -> None:
        names = {ta.name for ta in self.ast.type_aliases}
        expected = {
            "HandlerFunc",
            "Middleware",
            "StringMap",
            "IDList",
            "NodeID",
            "ExportedAlias",
            "unexportedDef",
        }
        assert names == expected


# endregion: --- Type Alias Extraction


# ---------------------------------------------------------------------------
# region:    --- Import & Scoped Call Extraction
# ---------------------------------------------------------------------------


class TestImportExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "imports.go")

    def test_uses_count(self) -> None:
        assert len(self.ast.uses) == 5

    def test_simple_import(self) -> None:
        assert any('"fmt"' in u for u in self.ast.uses)

    def test_nested_path_import(self) -> None:
        assert any('"net/http"' in u for u in self.ast.uses)

    def test_aliased_import(self) -> None:
        assert any("alias" in u and "github.com/pkg/errors" in u for u in self.ast.uses)

    def test_scoped_calls_fmt(self) -> None:
        assert "fmt" in self.ast.imported_package_methods
        calls = self.ast.imported_package_methods["fmt"]
        assert "Println" in calls
        assert "Sprintf" in calls

    def test_scoped_calls_http(self) -> None:
        assert "net/http" in self.ast.imported_package_methods
        calls = self.ast.imported_package_methods["net/http"]
        assert "ListenAndServe" in calls
        assert "NewRequest" in calls

    def test_scoped_calls_alias(self) -> None:
        assert "github.com/pkg/errors" in self.ast.imported_package_methods
        calls = self.ast.imported_package_methods["github.com/pkg/errors"]
        assert "New" in calls

    def test_scoped_calls_os(self) -> None:
        assert "os" in self.ast.imported_package_methods
        calls = self.ast.imported_package_methods["os"]
        assert "Getenv" in calls

    def test_scoped_calls_context(self) -> None:
        assert "context" in self.ast.imported_package_methods
        calls = self.ast.imported_package_methods["context"]
        assert "Background" in calls


# endregion: --- Import & Scoped Call Extraction


# ---------------------------------------------------------------------------
# region:    --- Doc Comment Extraction
# ---------------------------------------------------------------------------


class TestDocCommentExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(FIXTURE_DIR / "comments.go")

    def test_struct_doc(self) -> None:
        logger = next(s for s in self.ast.structs if s.name == "Logger")
        assert "structured logger" in logger.doc
        assert "multiple output formats" in logger.doc

    def test_function_doc(self) -> None:
        nl = next(f for f in self.ast.functions if f.name == "NewLogger")
        assert "creates a new Logger" in nl.doc

    def test_interface_doc(self) -> None:
        le = next(t for t in self.ast.traits if t.name == "LogEntry")
        assert "single log entry" in le.doc

    def test_constant_doc(self) -> None:
        # Constants doc comments are on the const_declaration, not captured per-spec.
        # Verify the constant exists and is exported.
        v = next(c for c in self.ast.constants if c.name == "Version")
        assert v.visibility == Visibility.PUBLIC


# endregion: --- Doc Comment Extraction


# ---------------------------------------------------------------------------
# region:    --- go.mod Manifest Parsing
# ---------------------------------------------------------------------------


class TestManifestParsing:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        ext = GoExtractor()
        self.crate = ext.parse_manifest(FIXTURE_DIR / "go.mod")

    def test_module_name(self) -> None:
        assert self.crate.name == "github.com/example/safeguard"

    def test_language(self) -> None:
        assert self.crate.language == "go"

    def test_go_version(self) -> None:
        assert self.crate.version == "1.21"

    def test_manifest_path(self) -> None:
        assert "go.mod" in self.crate.manifest_path

    def test_direct_dependency_count(self) -> None:
        direct = [d for d in self.crate.dependencies if not d.is_dev]
        assert len(direct) == 4

    def test_indirect_dependency_count(self) -> None:
        indirect = [d for d in self.crate.dependencies if d.is_dev]
        assert len(indirect) == 3

    def test_specific_dependency(self) -> None:
        gin = next(
            d for d in self.crate.dependencies if d.name == "github.com/gin-gonic/gin"
        )
        assert gin.version == "v1.9.1"
        assert gin.is_dev is False

    def test_indirect_dependency(self) -> None:
        spew = next(
            d
            for d in self.crate.dependencies
            if d.name == "github.com/davecgh/go-spew"
        )
        assert spew.version == "v1.1.1"
        assert spew.is_dev is True

    def test_total_dependency_count(self) -> None:
        assert len(self.crate.dependencies) == 7


# endregion: --- Manifest Parsing


# ---------------------------------------------------------------------------
# region:    --- Integration / Cross-Cutting Tests
# ---------------------------------------------------------------------------


class TestCrossCuttingBehavior:
    def test_self_methods_include_free_functions(self) -> None:
        ast = _extract(FIXTURE_DIR / "functions.go")
        free = [m for m in ast.self_methods if m.context == "free"]
        assert len(free) == 7

    def test_self_methods_include_receiver_methods(self) -> None:
        ast = _extract(FIXTURE_DIR / "methods.go")
        impl_methods = [m for m in ast.self_methods if m.context.startswith("impl:")]
        assert len(impl_methods) == 9

    def test_error_from_invalid_syntax(self) -> None:
        ext = GoExtractor()
        ast = ext.extract(Path("bad.go"), b"package main\nfunc {broken")
        assert len(ast.errors) >= 1

    def test_direction_type_not_in_structs(self) -> None:
        """``type Direction int`` should be a TypeAliasNode, not a StructNode."""
        ast = _extract(FIXTURE_DIR / "constants.go")
        struct_names = {s.name for s in ast.structs}
        assert "Direction" not in struct_names
        alias_names = {ta.name for ta in ast.type_aliases}
        assert "Direction" in alias_names

    def test_structs_in_methods_fixture(self) -> None:
        """Structs defined alongside methods should still be extracted."""
        ast = _extract(FIXTURE_DIR / "methods.go")
        names = {s.name for s in ast.structs}
        assert "Server" in names
        assert "Client" in names
        assert "GenericService" in names

    def test_no_impl_blocks_in_functions_fixture(self) -> None:
        ast = _extract(FIXTURE_DIR / "functions.go")
        assert len(ast.impl_blocks) == 0

    def test_manifest_missing_file(self) -> None:
        ext = GoExtractor()
        crate = ext.parse_manifest(Path("/nonexistent/go.mod"))
        assert crate.language == "go"
        assert crate.name  # Should fallback to directory name

    def test_manifest_empty_mod(self, tmp_path: Path) -> None:
        mod = tmp_path / "go.mod"
        mod.write_text("")
        ext = GoExtractor()
        crate = ext.parse_manifest(mod)
        assert crate.language == "go"


# endregion: --- Cross-Cutting Tests
