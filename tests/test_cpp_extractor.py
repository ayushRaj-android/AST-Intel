"""Unit tests for the C / C++ extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.cpp import CppExtractor
from ast_intel.models.ast_node import (
    FileAST,
    Visibility,
)

C_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "c"
CPP_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cpp"


def _extract(fixture: Path) -> FileAST:
    """Parse a fixture file and return the FileAST."""
    ext = CppExtractor()
    return ext.extract(fixture, fixture.read_bytes())


# ---------------------------------------------------------------------------
# region:    --- C Struct Tests
# ---------------------------------------------------------------------------


class TestCStructExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(C_FIXTURE_DIR / "structs.c")

    def test_struct_count(self) -> None:
        # Config, Server, Point (typedef struct)
        assert len(self.ast.structs) >= 2

    def test_config_struct_fields(self) -> None:
        config = next(s for s in self.ast.structs if s.name == "Config")
        assert len(config.fields) == 2
        names = {f.name for f in config.fields}
        assert "name" in names
        assert "port" in names

    def test_c_fields_are_public(self) -> None:
        """C structs have no access specifiers — all fields should be pub."""
        config = next(s for s in self.ast.structs if s.name == "Config")
        for f in config.fields:
            assert f.visibility == Visibility.PUBLIC

    def test_server_struct_fields(self) -> None:
        server = next(s for s in self.ast.structs if s.name == "Server")
        assert len(server.fields) == 3

    def test_typedef_struct(self) -> None:
        point = next((s for s in self.ast.structs if s.name == "Point"), None)
        assert point is not None
        assert len(point.fields) == 2


# endregion: --- C Struct Tests


# ---------------------------------------------------------------------------
# region:    --- C Function Tests
# ---------------------------------------------------------------------------


class TestCFunctionExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(C_FIXTURE_DIR / "functions.c")

    def test_function_count(self) -> None:
        assert len(self.ast.functions) >= 2

    def test_process_function_params(self) -> None:
        process = next(f for f in self.ast.functions if f.name == "process")
        assert len(process.params) == 2

    def test_static_function_visibility(self) -> None:
        helper = next(
            (f for f in self.ast.functions if f.name == "helper"), None,
        )
        if helper is not None:
            assert helper.visibility == Visibility.PRIVATE

    def test_includes(self) -> None:
        assert len(self.ast.uses) >= 2
        texts = " ".join(self.ast.uses)
        assert "config.h" in texts
        assert "stdio.h" in texts

    def test_call_edges_exist(self) -> None:
        assert len(self.ast.call_edges) > 0

    def test_rationale_comment(self) -> None:
        assert len(self.ast.rationale_comments) >= 1
        note = self.ast.rationale_comments[0]
        assert note.kind.upper() == "NOTE"


# endregion: --- C Function Tests


# ---------------------------------------------------------------------------
# region:    --- C Typedef / Enum / Macro Tests
# ---------------------------------------------------------------------------


class TestCTypedefEnumMacro:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(C_FIXTURE_DIR / "typedefs.c")

    def test_typedef_count(self) -> None:
        assert len(self.ast.type_aliases) >= 2

    def test_typedef_uint32(self) -> None:
        ta = next(
            (t for t in self.ast.type_aliases if t.name == "uint32"), None,
        )
        assert ta is not None
        assert "unsigned" in ta.aliased_to

    def test_function_pointer_typedef(self) -> None:
        cb = next(
            (t for t in self.ast.type_aliases if t.name == "callback_fn"),
            None,
        )
        assert cb is not None

    def test_macros(self) -> None:
        names = {m.name for m in self.ast.macros}
        assert "MAX_SIZE" in names
        assert "VERSION" in names

    def test_enum_status(self) -> None:
        status = next(
            (e for e in self.ast.enums if e.name == "Status"), None,
        )
        assert status is not None
        assert len(status.variants) == 3

    def test_typedef_enum(self) -> None:
        log_level = next(
            (e for e in self.ast.enums if e.name == "LogLevel"), None,
        )
        assert log_level is not None
        assert len(log_level.variants) == 4


# endregion: --- C Typedef / Enum / Macro Tests


# ---------------------------------------------------------------------------
# region:    --- C Header Tests
# ---------------------------------------------------------------------------


class TestCHeaderExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(C_FIXTURE_DIR / "header.h")

    def test_struct_in_header(self) -> None:
        buf = next(
            (s for s in self.ast.structs if s.name == "Buffer"), None,
        )
        assert buf is not None
        assert len(buf.fields) == 3

    def test_function_declarations(self) -> None:
        names = {f.name for f in self.ast.functions}
        assert "buffer_init" in names
        assert "buffer_free" in names
        assert "buffer_append" in names

    def test_typedef_in_header(self) -> None:
        ta = next(
            (t for t in self.ast.type_aliases if t.name == "error_code"),
            None,
        )
        assert ta is not None

    def test_macro_in_header(self) -> None:
        names = {m.name for m in self.ast.macros}
        assert "BUFFER_DEFAULT_SIZE" in names


# endregion: --- C Header Tests


# ---------------------------------------------------------------------------
# region:    --- C++ Class Tests
# ---------------------------------------------------------------------------


class TestCppClassExtraction:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(CPP_FIXTURE_DIR / "classes.cpp")

    def test_storage_class(self) -> None:
        storage = next(
            (s for s in self.ast.structs if s.name == "Storage"), None,
        )
        assert storage is not None

    def test_storage_fields_visibility(self) -> None:
        storage = next(s for s in self.ast.structs if s.name == "Storage")
        priv = [f for f in storage.fields if f.visibility == Visibility.PRIVATE]
        assert len(priv) >= 1

    def test_storage_methods_in_impl(self) -> None:
        impl = next(
            (i for i in self.ast.impl_blocks
             if i.self_type == "Storage" and not i.trait_type),
            None,
        )
        assert impl is not None
        names = {m.name for m in impl.methods}
        assert "store" in names or "retrieve" in names

    def test_pure_virtual_class_is_trait(self) -> None:
        handler = next(
            (t for t in self.ast.traits if t.name == "IHandler"), None,
        )
        assert handler is not None
        item_names = {i.name for i in handler.items}
        assert "handle" in item_names
        assert "is_ready" in item_names

    def test_worker_inherits_ihandler(self) -> None:
        impl = next(
            (i for i in self.ast.impl_blocks
             if i.self_type == "Worker" and i.trait_type == "IHandler"),
            None,
        )
        assert impl is not None

    def test_template_class_generics(self) -> None:
        container = next(
            (s for s in self.ast.structs if s.name == "Container"), None,
        )
        assert container is not None
        assert "typename T" in container.generics

    def test_template_multiple_params(self) -> None:
        pair = next(
            (s for s in self.ast.structs if s.name == "Pair"), None,
        )
        assert pair is not None
        assert "typename K" in pair.generics
        assert "typename V" in pair.generics

    def test_enum_class(self) -> None:
        color = next(
            (e for e in self.ast.enums if e.name == "Color"), None,
        )
        assert color is not None
        assert len(color.variants) == 3
        names = {v.name for v in color.variants}
        assert names == {"Red", "Green", "Blue"}

    def test_old_enum(self) -> None:
        direction = next(
            (e for e in self.ast.enums if e.name == "Direction"), None,
        )
        assert direction is not None
        assert len(direction.variants) == 4

    def test_namespace_module(self) -> None:
        assert len(self.ast.modules) >= 1
        ns = self.ast.modules[0]
        assert "project" in ns.name
        assert "core" in ns.name

    def test_includes(self) -> None:
        texts = " ".join(self.ast.uses)
        assert "string" in texts
        assert "handler.h" in texts


# endregion: --- C++ Class Tests


# ---------------------------------------------------------------------------
# region:    --- C++ Out-of-Class Methods
# ---------------------------------------------------------------------------


class TestCppOutOfClassMethods:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.ast = _extract(CPP_FIXTURE_DIR / "out_of_class.cpp")

    def test_out_of_class_method_linked(self) -> None:
        impl = next(
            (i for i in self.ast.impl_blocks if i.self_type == "Worker"),
            None,
        )
        assert impl is not None
        method_names = {m.name for m in impl.methods}
        assert "handle" in method_names
        assert "is_ready" in method_names


# endregion: --- C++ Out-of-Class Methods


# ---------------------------------------------------------------------------
# region:    --- CMakeLists.txt Manifest Parsing
# ---------------------------------------------------------------------------


class TestCMakeManifest:
    def test_parse_cmake(self) -> None:
        ext = CppExtractor()
        crate = ext.parse_manifest(CPP_FIXTURE_DIR / "CMakeLists.txt")
        assert crate.name == "safeguard_core"
        assert crate.language == "cpp"
        dep_names = {d.name for d in crate.dependencies}
        assert "OpenSSL" in dep_names
        assert "Boost" in dep_names
        assert "fmt" in dep_names


# endregion: --- CMakeLists.txt Manifest Parsing


# ---------------------------------------------------------------------------
# region:    --- Grammar Selection
# ---------------------------------------------------------------------------


class TestGrammarSelection:
    def test_c_file_uses_c_grammar(self) -> None:
        ext = CppExtractor()
        _, is_c = ext._parser_for(Path("test.c"))
        assert is_c is True

    def test_h_file_uses_c_grammar(self) -> None:
        ext = CppExtractor()
        _, is_c = ext._parser_for(Path("test.h"))
        assert is_c is True

    def test_cpp_file_uses_cpp_grammar(self) -> None:
        ext = CppExtractor()
        _, is_c = ext._parser_for(Path("test.cpp"))
        assert is_c is False

    def test_hpp_file_uses_cpp_grammar(self) -> None:
        ext = CppExtractor()
        _, is_c = ext._parser_for(Path("test.hpp"))
        assert is_c is False

    def test_cc_file_uses_cpp_grammar(self) -> None:
        ext = CppExtractor()
        _, is_c = ext._parser_for(Path("test.cc"))
        assert is_c is False


# endregion: --- Grammar Selection


# ---------------------------------------------------------------------------
# region:    --- Dispatcher Integration
# ---------------------------------------------------------------------------


class TestDispatcherRegistration:
    def test_cpp_extensions_registered(self) -> None:
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        for ext in [".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx"]:
            assert ext in registry, f"{ext} not registered"


# endregion: --- Dispatcher Integration
