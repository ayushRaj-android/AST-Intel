"""Tests for the C# / .NET language extractor.

Covers class → StructNode, interface → TraitNode, enum → EnumNode,
abstract class → TraitNode, struct → StructNode, record → StructNode,
inheritance → ImplBlockNode, constants, using directives, self_methods,
imported_package_methods, and .csproj manifest parsing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ast_intel.extractors.csharp import (
    CSharpExtractor,
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

FIXTURES = Path(__file__).parent / "fixtures" / "csharp"


@pytest.fixture
def ext() -> CSharpExtractor:
    """Create a fresh extractor instance."""
    return CSharpExtractor()


def _extract(ext: CSharpExtractor, fixture_name: str) -> FileAST:
    """Helper: read fixture and extract."""
    path = FIXTURES / fixture_name
    src = path.read_bytes()
    return ext.extract(path, src)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor identity
# ---------------------------------------------------------------------------


class TestExtractorIdentity:
    """Basic extractor metadata."""

    def test_language_id(self, ext: CSharpExtractor) -> None:
        assert ext.language_id == "csharp"

    def test_file_extensions(self, ext: CSharpExtractor) -> None:
        assert ".cs" in ext.file_extensions

    def test_is_extractor_base(self, ext: CSharpExtractor) -> None:
        from ast_intel.extractors.base import ExtractorBase

        assert isinstance(ext, ExtractorBase)


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    """Concrete class → StructNode + ImplBlockNode."""

    def test_class_count(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        assert len(result.structs) == 3  # Job, InternalHelper, GenericContainer

    def test_class_name(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        names = [s.name for s in result.structs]
        assert "Job" in names

    def test_class_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        assert job.visibility == Visibility.PUBLIC

    def test_internal_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        helper = next(s for s in result.structs if s.name == "InternalHelper")
        assert helper.visibility == Visibility.CRATE

    def test_class_fields(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        field_names = [f.name for f in job.fields]
        assert "Id" in field_names
        assert "Name" in field_names
        assert "_priority" in field_names
        assert "Priority" in field_names

    def test_field_types(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        id_field = next(f for f in job.fields if f.name == "Id")
        assert id_field.type == "Guid"

    def test_field_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        priv = next(f for f in job.fields if f.name == "_priority")
        assert priv.visibility == Visibility.PRIVATE
        pub = next(f for f in job.fields if f.name == "Name")
        assert pub.visibility == Visibility.PUBLIC

    def test_generic_class(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        gc = next(s for s in result.structs if s.name == "GenericContainer")
        assert gc.generics == "<T>"
        assert len(gc.fields) == 2

    def test_class_attributes(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        assert "DataContract" in job.attributes

    def test_class_doc(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job = next(s for s in result.structs if s.name == "Job")
        assert "Simple model class" in job.doc

    def test_inherent_impl_block(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job_impl = next(ib for ib in result.impl_blocks if ib.self_type == "Job")
        assert job_impl.trait_type == ""
        assert len(job_impl.methods) == 3  # constructor + Reset + ComputeHash

    def test_method_params(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job_impl = next(ib for ib in result.impl_blocks if ib.self_type == "Job")
        ctor = next(m for m in job_impl.methods if m.name == "Job")
        assert len(ctor.params) == 2
        assert ctor.params[0].name == "name"
        assert ctor.params[0].type == "string"

    def test_static_method(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job_impl = next(ib for ib in result.impl_blocks if ib.self_type == "Job")
        compute = next(m for m in job_impl.methods if m.name == "ComputeHash")
        assert compute.is_static is True
        assert compute.visibility == Visibility.PRIVATE

    def test_return_type(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job_impl = next(ib for ib in result.impl_blocks if ib.self_type == "Job")
        reset = next(m for m in job_impl.methods if m.name == "Reset")
        assert reset.return_type == "void"

    def test_constants_from_class(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        const_names = [c.name for c in result.constants]
        assert "ValidStates" in const_names


# endregion: --- Class extraction


# ---------------------------------------------------------------------------
# region:    --- Interface extraction
# ---------------------------------------------------------------------------


class TestInterfaceExtraction:
    """Interface → TraitNode."""

    def test_interface_count(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        assert len(result.traits) == 3

    def test_interface_name(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        names = [t.name for t in result.traits]
        assert "IJobService" in names
        assert "IRepository" in names
        assert "ILoggingService" in names

    def test_interface_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        assert svc.visibility == Visibility.PUBLIC

    def test_interface_generics(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        repo = next(t for t in result.traits if t.name == "IRepository")
        assert repo.generics == "<T>"

    def test_interface_methods(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        method_names = [item.name for item in svc.items]
        assert "GetAsync" in method_names
        assert "Process" in method_names
        assert "DeleteAsync" in method_names

    def test_interface_method_kind(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        for item in svc.items:
            assert item.kind == TraitItemKind.REQUIRED_METHOD

    def test_interface_async_detection(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        get = next(i for i in svc.items if i.name == "GetAsync")
        assert get.is_async is True
        process = next(i for i in svc.items if i.name == "Process")
        assert process.is_async is False

    def test_interface_return_types(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        get = next(i for i in svc.items if i.name == "GetAsync")
        assert get.return_type == "Task<string>"

    def test_interface_params(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        get = next(i for i in svc.items if i.name == "GetAsync")
        assert len(get.params) == 1
        assert get.params[0].name == "id"
        assert get.params[0].type == "Guid"

    def test_interface_doc(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "interfaces.cs")
        svc = next(t for t in result.traits if t.name == "IJobService")
        assert "Service contract" in svc.doc


# endregion: --- Interface extraction


# ---------------------------------------------------------------------------
# region:    --- Enum extraction
# ---------------------------------------------------------------------------


class TestEnumExtraction:
    """Enum → EnumNode."""

    def test_enum_count(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        assert len(result.enums) == 3

    def test_enum_names(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        names = [e.name for e in result.enums]
        assert "JobStatus" in names
        assert "JobType" in names
        assert "Priority" in names

    def test_enum_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        js = next(e for e in result.enums if e.name == "JobStatus")
        assert js.visibility == Visibility.PUBLIC
        pri = next(e for e in result.enums if e.name == "Priority")
        assert pri.visibility == Visibility.CRATE

    def test_enum_variants(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        js = next(e for e in result.enums if e.name == "JobStatus")
        variant_names = [v.name for v in js.variants]
        assert variant_names == [
            "NotStarted", "InProgress", "Passed", "Failed", "Error",
        ]

    def test_variant_kind(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        js = next(e for e in result.enums if e.name == "JobStatus")
        for v in js.variants:
            assert v.kind == EnumVariantKind.UNIT

    def test_enum_doc(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        js = next(e for e in result.enums if e.name == "JobStatus")
        assert "Job execution status" in js.doc

    def test_enum_without_explicit_values(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "enums.cs")
        jt = next(e for e in result.enums if e.name == "JobType")
        assert len(jt.variants) == 2


# endregion: --- Enum extraction


# ---------------------------------------------------------------------------
# region:    --- Inheritance / impl blocks
# ---------------------------------------------------------------------------


class TestInheritance:
    """Class implementing interface → ImplBlockNode."""

    def test_impl_block_count(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        assert len(result.impl_blocks) == 2

    def test_impl_trait_type(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        js_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "JobService"
        )
        assert js_impl.trait_type == "IJobService"

    def test_impl_methods(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        js_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "JobService"
        )
        method_names = [m.name for m in js_impl.methods]
        assert "GetAsync" in method_names
        assert "Process" in method_names
        assert "DeleteAsync" in method_names
        assert "JobService" in method_names  # constructor
        assert "ValidateInput" in method_names  # private method

    def test_async_method_detection(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        js_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "JobService"
        )
        get = next(m for m in js_impl.methods if m.name == "GetAsync")
        assert get.is_async is True

    def test_logging_service_impl(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        ls_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "LoggingService"
        )
        assert ls_impl.trait_type == "ILoggingService"
        assert len(ls_impl.methods) == 2


# endregion: --- Inheritance / impl blocks


# ---------------------------------------------------------------------------
# region:    --- Abstract class extraction
# ---------------------------------------------------------------------------


class TestAbstractClass:
    """Abstract class → TraitNode."""

    def test_abstract_class_becomes_trait(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        handler = next(t for t in result.traits if t.name == "HandlerBase")
        assert handler.visibility == Visibility.PUBLIC

    def test_abstract_methods_required(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        handler = next(t for t in result.traits if t.name == "HandlerBase")
        handle = next(i for i in handler.items if i.name == "HandleAsync")
        assert handle.kind == TraitItemKind.REQUIRED_METHOD

    def test_virtual_methods_default(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        handler = next(t for t in result.traits if t.name == "HandlerBase")
        on_err = next(i for i in handler.items if i.name == "OnError")
        assert on_err.kind == TraitItemKind.DEFAULT_METHOD

    def test_protected_abstract_method(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        handler = next(t for t in result.traits if t.name == "HandlerBase")
        get_name = next(i for i in handler.items if i.name == "GetName")
        assert get_name.kind == TraitItemKind.REQUIRED_METHOD

    def test_abstract_class_doc(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        handler = next(t for t in result.traits if t.name == "HandlerBase")
        assert "Abstract base class" in handler.doc

    def test_concrete_subclass_impl(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        impl = next(
            ib for ib in result.impl_blocks
            if ib.self_type == "ConcreteHandler"
        )
        assert impl.trait_type == "HandlerBase"
        method_names = [m.name for m in impl.methods]
        assert "HandleAsync" in method_names
        assert "OnError" in method_names
        assert "GetName" in method_names


# endregion: --- Abstract class extraction


# ---------------------------------------------------------------------------
# region:    --- Record extraction
# ---------------------------------------------------------------------------


class TestRecordExtraction:
    """Record → StructNode with positional fields."""

    def test_positional_record(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        point = next(s for s in result.structs if s.name == "Point")
        assert len(point.fields) == 2

    def test_record_field_names(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        point = next(s for s in result.structs if s.name == "Point")
        field_names = [f.name for f in point.fields]
        assert "X" in field_names
        assert "Y" in field_names

    def test_record_field_types(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        point = next(s for s in result.structs if s.name == "Point")
        x = next(f for f in point.fields if f.name == "X")
        assert x.type == "int"

    def test_record_with_body(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        person = next(s for s in result.structs if s.name == "Person")
        field_names = [f.name for f in person.fields]
        assert "Name" in field_names
        assert "Age" in field_names
        assert "Greeting" in field_names  # body prop


# endregion: --- Record extraction


# ---------------------------------------------------------------------------
# region:    --- Struct (value type) extraction
# ---------------------------------------------------------------------------


class TestStructValueType:
    """C# struct → StructNode."""

    def test_struct_fields(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        coord = next(s for s in result.structs if s.name == "Coordinate")
        field_names = [f.name for f in coord.fields]
        assert "Latitude" in field_names
        assert "Longitude" in field_names

    def test_struct_methods(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        coord_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "Coordinate"
        )
        assert coord_impl.trait_type == ""
        method_names = [m.name for m in coord_impl.methods]
        assert "DistanceTo" in method_names


# endregion: --- Struct (value type) extraction


# ---------------------------------------------------------------------------
# region:    --- Static class / constants
# ---------------------------------------------------------------------------


class TestStaticClassAndConstants:
    """Static utility class → StructNode with constants."""

    def test_static_class_methods(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        utils_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "MathUtils"
        )
        method_names = [m.name for m in utils_impl.methods]
        assert "Add" in method_names
        assert "AddAsync" in method_names
        assert "Square" in method_names

    def test_static_methods_are_static(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        utils_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "MathUtils"
        )
        add = next(m for m in utils_impl.methods if m.name == "Add")
        assert add.is_static is True

    def test_async_static_method(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        utils_impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "MathUtils"
        )
        add_async = next(m for m in utils_impl.methods if m.name == "AddAsync")
        assert add_async.is_async is True

    def test_constants_extracted(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        const_names = [c.name for c in result.constants]
        assert "Pi" in const_names
        assert "Version" in const_names
        assert "MaxRetries" in const_names

    def test_constant_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        pi = next(c for c in result.constants if c.name == "Pi")
        assert pi.visibility == Visibility.PUBLIC

    def test_constants_from_fixture(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "constants.cs")
        const_names = [c.name for c in result.constants]
        assert "AppName" in const_names
        assert "MaxRetries" in const_names
        assert "Timeout" in const_names
        assert "InternalKey" in const_names
        assert "ValidRoles" in const_names

    def test_constant_internal_visibility(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "constants.cs")
        key = next(c for c in result.constants if c.name == "InternalKey")
        assert key.visibility == Visibility.CRATE


# endregion: --- Static class / constants


# ---------------------------------------------------------------------------
# region:    --- Exception class / custom inheritance
# ---------------------------------------------------------------------------


class TestCustomInheritance:
    """Custom exception class → StructNode + ImplBlockNode."""

    def test_exception_struct(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        exc = next(s for s in result.structs if s.name == "AppException")
        assert exc.visibility == Visibility.PUBLIC
        field_names = [f.name for f in exc.fields]
        assert "ErrorCode" in field_names

    def test_exception_inherits_base(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "comprehensive.cs")
        impl = next(
            ib for ib in result.impl_blocks if ib.self_type == "AppException"
        )
        assert impl.trait_type == "Exception"


# endregion: --- Exception class / custom inheritance


# ---------------------------------------------------------------------------
# region:    --- Using directives
# ---------------------------------------------------------------------------


class TestUsingDirectives:
    """Using statements → uses list."""

    def test_using_count(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        assert len(result.uses) >= 3

    def test_using_content(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        assert any("System" in u for u in result.uses)
        assert any("System.Collections.Generic" in u for u in result.uses)

    def test_using_alias_creates_type_alias(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "constants.cs")
        alias_names = [a.name for a in result.type_aliases]
        assert "AliasType" in alias_names


# endregion: --- Using directives


# ---------------------------------------------------------------------------
# region:    --- Import map
# ---------------------------------------------------------------------------


class TestImportMap:
    """build_import_map from using statements."""

    def test_simple_namespace(self) -> None:
        im = build_import_map(["using System.Text.Json;"])
        assert im["Json"] == "System.Text.Json"

    def test_alias(self) -> None:
        im = build_import_map(["using Dict = System.Collections.Dictionary;"])
        assert im["Dict"] == "System.Collections.Dictionary"

    def test_static_using(self) -> None:
        im = build_import_map(["using static System.Math;"])
        assert im["Math"] == "System.Math"

    def test_single_name(self) -> None:
        im = build_import_map(["using System;"])
        assert im["System"] == "System"

    def test_empty_list(self) -> None:
        assert build_import_map([]) == {}


# endregion: --- Import map


# ---------------------------------------------------------------------------
# region:    --- Self methods
# ---------------------------------------------------------------------------


class TestSelfMethods:
    """Post-pass: self_methods collection."""

    def test_self_methods_populated(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        assert len(result.self_methods) > 0

    def test_inherent_context(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "classes.cs")
        job_methods = [
            m for m in result.self_methods if m.context == "impl:Job"
        ]
        assert len(job_methods) == 3  # Job, Reset, ComputeHash

    def test_trait_impl_context(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "inheritance.cs")
        svc_methods = [
            m for m in result.self_methods
            if m.context == "impl:IJobService for JobService"
        ]
        assert len(svc_methods) >= 4  # constructor + 3 interface methods + 1 private


# endregion: --- Self methods


# ---------------------------------------------------------------------------
# region:    --- Controller with attributes
# ---------------------------------------------------------------------------


class TestControllerExtraction:
    """Controller class with HTTP attributes."""

    def test_controller_struct(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "controllers.cs")
        ctrl = next(s for s in result.structs if s.name == "JobsController")
        assert ctrl.visibility == Visibility.PUBLIC

    def test_controller_inherits(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "controllers.cs")
        impl = next(
            ib for ib in result.impl_blocks
            if ib.self_type == "JobsController"
        )
        assert impl.trait_type == "ControllerBase"

    def test_controller_class_attributes(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "controllers.cs")
        ctrl = next(s for s in result.structs if s.name == "JobsController")
        assert "ApiController" in ctrl.attributes

    def test_controller_method_attributes(self, ext: CSharpExtractor) -> None:
        result = _extract(ext, "controllers.cs")
        impl = next(
            ib for ib in result.impl_blocks
            if ib.self_type == "JobsController"
        )
        get_job = next(m for m in impl.methods if m.name == "GetJob")
        attr_text = " ".join(get_job.attributes)
        assert "HttpGet" in attr_text


# endregion: --- Controller with attributes


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing
# ---------------------------------------------------------------------------


class TestManifestParsing:
    """Parse .csproj manifest files."""

    def test_project_name(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        assert crate.name == "sample"

    def test_target_framework(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        assert crate.version == "net9.0"

    def test_language(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        assert crate.language == "csharp"

    def test_dependencies(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        dep_names = [d.name for d in crate.dependencies]
        assert "Microsoft.EntityFrameworkCore.InMemory" in dep_names
        assert "Swashbuckle.AspNetCore" in dep_names
        assert "Serilog.AspNetCore" in dep_names

    def test_dependency_version(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        ef = next(
            d for d in crate.dependencies
            if d.name == "Microsoft.EntityFrameworkCore.InMemory"
        )
        assert ef.version == "9.0.0"

    def test_dependency_without_version(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "sample.csproj")
        json = next(
            d for d in crate.dependencies if d.name == "System.Text.Json"
        )
        assert json.version == ""

    def test_missing_manifest(self, ext: CSharpExtractor) -> None:
        crate = ext.parse_manifest(Path("/nonexistent/fake.csproj"))
        assert crate.name == "fake"
        assert crate.language == "csharp"
        assert crate.dependencies == []

    def test_malformed_csproj(self, ext: CSharpExtractor, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csproj"
        bad.write_text("<broken><xml")
        crate = ext.parse_manifest(bad)
        assert crate.name == "bad"
        assert crate.language == "csharp"
        assert crate.dependencies == []


# endregion: --- Manifest parsing


# ---------------------------------------------------------------------------
# region:    --- Imported package methods
# ---------------------------------------------------------------------------


class TestImportedPackageMethods:
    """Post-pass: imported_package_methods from scoped calls."""

    def test_scoped_call_resolution(self, ext: CSharpExtractor) -> None:
        """Scoped calls resolve the leftmost identifier via the import map.

        ``using static System.Console`` maps ``Console`` → ``System.Console``.
        ``using System.Text.Json`` maps ``Json`` → ``System.Text.Json``.
        Resolution matches the leftmost identifier in ``X.Method()`` against
        the import map.
        """
        src = b"""
using static System.Console;
using System.Text.Json;

public class Utility
{
    public void Run()
    {
        Console.WriteLine("hello");
        Json.SomeMethod();
    }
}
"""
        result = ext.extract(Path("utility.cs"), src)
        # "Console" via using static
        assert "System.Console" in result.imported_package_methods
        assert "WriteLine" in result.imported_package_methods["System.Console"]
        # "Json" is the shortname for System.Text.Json
        assert "System.Text.Json" in result.imported_package_methods
        assert "SomeMethod" in result.imported_package_methods[
            "System.Text.Json"
        ]

    def test_no_imports_no_methods(self, ext: CSharpExtractor) -> None:
        src = b"""
public class Plain
{
    public void Run() { }
}
"""
        result = ext.extract(Path("plain.cs"), src)
        assert result.imported_package_methods == {}

    def test_multiple_namespaces(self, ext: CSharpExtractor) -> None:
        """Multiple using directives resolve independently."""
        src = b"""
using System.IO;
using System.Net;

public class Helper
{
    public void Run()
    {
        IO.DoSomething();
        Net.Request();
    }
}
"""
        result = ext.extract(Path("helper.cs"), src)
        assert "System.IO" in result.imported_package_methods
        assert "DoSomething" in result.imported_package_methods["System.IO"]
        assert "System.Net" in result.imported_package_methods
        assert "Request" in result.imported_package_methods["System.Net"]


# endregion: --- Imported package methods


# ---------------------------------------------------------------------------
# region:    --- Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and robustness."""

    def test_empty_file(self, ext: CSharpExtractor) -> None:
        result = ext.extract(Path("empty.cs"), b"")
        assert result.errors == []
        assert result.structs == []
        assert result.traits == []

    def test_whitespace_only(self, ext: CSharpExtractor) -> None:
        result = ext.extract(Path("ws.cs"), b"   \n\n  ")
        assert result.errors == []

    def test_syntax_error_graceful(self, ext: CSharpExtractor) -> None:
        """Tree-sitter is error-tolerant; verify extractor doesn't crash."""
        result = ext.extract(
            Path("bad.cs"), b"public class { broken syntax }"
        )
        # tree-sitter partially parses this — just ensure no crash
        assert result.file == "bad.cs"

    def test_file_path_preserved(self, ext: CSharpExtractor) -> None:
        result = ext.extract(Path("my/file.cs"), b"")
        assert result.file == "my/file.cs"

    def test_no_namespace(self, ext: CSharpExtractor) -> None:
        src = b"""
public class TopLevel
{
    public int Value { get; set; }
}
"""
        result = ext.extract(Path("top.cs"), src)
        assert len(result.structs) == 1
        assert result.structs[0].name == "TopLevel"

    def test_nested_namespace(self, ext: CSharpExtractor) -> None:
        src = b"""
namespace Outer
{
    namespace Inner
    {
        public class Nested
        {
            public string Data { get; set; }
        }
    }
}
"""
        result = ext.extract(Path("nested.cs"), src)
        assert len(result.structs) == 1
        assert result.structs[0].name == "Nested"


# endregion: --- Edge cases


# ---------------------------------------------------------------------------
# region:    --- Dispatcher & manifest parser integration
# ---------------------------------------------------------------------------


class TestDispatcherIntegration:
    """C# extractor wired into the dispatcher."""

    def test_cs_extension_registered(self) -> None:
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert ".cs" in registry

    def test_cs_extractor_class(self) -> None:
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert registry[".cs"].__name__ == "CSharpExtractor"


class TestManifestParserIntegration:
    """ManifestParser routes .csproj to C# extractor."""

    def test_csproj_route(self, tmp_path: Path) -> None:
        from ast_intel.core.manifest_parser import ManifestParser

        src = FIXTURES / "sample.csproj"
        dest = tmp_path / "MyProject.csproj"
        shutil.copy(src, dest)

        parser = ManifestParser()
        crate = parser.parse(dest)
        assert crate.language == "csharp"
        assert crate.name == "MyProject"
        assert len(crate.dependencies) > 0


# endregion: --- Dispatcher & manifest parser integration
