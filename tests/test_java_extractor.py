"""Tests for the Java language extractor.

Covers class → StructNode, interface → TraitNode, enum → EnumNode,
abstract class → TraitNode, record → StructNode, inheritance → ImplBlockNode,
constants, imports, self_methods, imported_package_methods, annotations,
generics, call graph, rationale comments, and Maven/Gradle manifest parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.java import (
    JavaExtractor,
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

FIXTURES = Path(__file__).parent / "fixtures" / "java"


@pytest.fixture
def ext() -> JavaExtractor:
    """Create a fresh extractor instance."""
    return JavaExtractor()


def _extract(ext: JavaExtractor, fixture_name: str) -> FileAST:
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

    def test_language_id(self, ext: JavaExtractor) -> None:
        assert ext.language_id == "java"

    def test_file_extensions(self, ext: JavaExtractor) -> None:
        assert ".java" in ext.file_extensions

    def test_is_extractor_base(self, ext: JavaExtractor) -> None:
        from ast_intel.extractors.base import ExtractorBase

        assert isinstance(ext, ExtractorBase)


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    """Concrete class → StructNode + ImplBlockNode."""

    def test_class_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        assert len(result.structs) == 3  # Job, InternalHelper, GenericContainer

    def test_class_name(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        names = [s.name for s in result.structs]
        assert "Job" in names

    def test_class_visibility_public(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job = next(s for s in result.structs if s.name == "Job")
        assert job.visibility == Visibility.PUBLIC

    def test_class_visibility_package_private(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        helper = next(s for s in result.structs if s.name == "InternalHelper")
        # Package-private → CRATE
        assert helper.visibility == Visibility.CRATE

    def test_class_fields(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job = next(s for s in result.structs if s.name == "Job")
        field_names = [f.name for f in job.fields]
        assert "name" in field_names
        assert "priority" in field_names
        assert "tags" in field_names

    def test_field_types(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job = next(s for s in result.structs if s.name == "Job")
        fmap = {f.name: f for f in job.fields}
        assert fmap["name"].type == "String"
        assert fmap["priority"].type == "int"

    def test_field_visibility(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job = next(s for s in result.structs if s.name == "Job")
        fmap = {f.name: f for f in job.fields}
        assert fmap["name"].visibility == Visibility.PRIVATE
        assert fmap["priority"].visibility == Visibility.PUBLIC
        assert fmap["tags"].visibility == Visibility.PROTECTED

    def test_generic_class(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        gc = next(s for s in result.structs if s.name == "GenericContainer")
        assert "<T extends Comparable<T>>" in gc.generics

    def test_class_impl_block(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job_impls = [i for i in result.impl_blocks if i.self_type == "Job"]
        assert len(job_impls) == 1
        assert job_impls[0].trait_type == "Serializable"

    def test_class_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job_impl = next(i for i in result.impl_blocks if i.self_type == "Job")
        method_names = [m.name for m in job_impl.methods]
        assert "Job" in method_names  # constructor
        assert "reset" in method_names
        assert "computeHash" in method_names

    def test_constructor_params(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job_impl = next(i for i in result.impl_blocks if i.self_type == "Job")
        ctor = next(m for m in job_impl.methods if m.name == "Job")
        param_names = [p.name for p in ctor.params]
        assert "name" in param_names
        assert "priority" in param_names

    def test_static_method(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        job_impl = next(i for i in result.impl_blocks if i.self_type == "Job")
        compute = next(m for m in job_impl.methods if m.name == "computeHash")
        assert compute.visibility == Visibility.PRIVATE
        assert compute.return_type == "int"


# endregion: --- Class extraction


# ---------------------------------------------------------------------------
# region:    --- Interface extraction
# ---------------------------------------------------------------------------


class TestInterfaceExtraction:
    """Interface → TraitNode."""

    def test_interface_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        assert len(result.traits) == 4  # IService, GenericMapper, Predicate, IStorageHelper

    def test_interface_name(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        names = [t.name for t in result.traits]
        assert "IService" in names

    def test_interface_required_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        svc = next(t for t in result.traits if t.name == "IService")
        items = [(i.kind, i.name) for i in svc.items]
        assert (TraitItemKind.REQUIRED_METHOD, "handle") in items
        assert (TraitItemKind.REQUIRED_METHOD, "getName") in items

    def test_interface_default_method(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        mapper = next(t for t in result.traits if t.name == "GenericMapper")
        defaults = [i for i in mapper.items if i.kind == TraitItemKind.DEFAULT_METHOD]
        assert any(d.name == "applyOrDefault" for d in defaults)

    def test_interface_generics(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        mapper = next(t for t in result.traits if t.name == "GenericMapper")
        assert "<T, R>" in mapper.generics

    def test_interface_extends(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        storage = next(t for t in result.traits if t.name == "IStorageHelper")
        assert "IService" in storage.super_traits

    def test_functional_interface_annotation(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        pred = next(t for t in result.traits if t.name == "Predicate")
        assert "@FunctionalInterface" in pred.attributes

    def test_interface_method_params(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        svc = next(t for t in result.traits if t.name == "IService")
        handle = next(i for i in svc.items if i.name == "handle")
        assert any(p.name == "request" for p in handle.params)


# endregion: --- Interface extraction


# ---------------------------------------------------------------------------
# region:    --- Enum extraction
# ---------------------------------------------------------------------------


class TestEnumExtraction:
    """Enum → EnumNode."""

    def test_enum_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        assert len(result.enums) == 3  # Status, HttpMethod, Color

    def test_enum_variants(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        status = next(e for e in result.enums if e.name == "Status")
        vnames = [v.name for v in status.variants]
        assert vnames == ["ACTIVE", "INACTIVE", "PENDING"]

    def test_enum_variant_kind_unit(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        color = next(e for e in result.enums if e.name == "Color")
        assert all(v.kind == EnumVariantKind.UNIT for v in color.variants)

    def test_enum_variant_kind_tuple(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        http = next(e for e in result.enums if e.name == "HttpMethod")
        assert all(v.kind == EnumVariantKind.TUPLE for v in http.variants)

    def test_enum_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        impls = [i for i in result.impl_blocks if i.self_type == "Status"]
        assert len(impls) == 1
        method_names = [m.name for m in impls[0].methods]
        assert "label" in method_names

    def test_enum_http_method_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        http = next(e for e in result.enums if e.name == "HttpMethod")
        assert len(http.variants) == 4


# endregion: --- Enum extraction


# ---------------------------------------------------------------------------
# region:    --- Inheritance & abstract classes
# ---------------------------------------------------------------------------


class TestInheritanceExtraction:
    """Abstract class → TraitNode, inheritance → ImplBlockNode."""

    def test_abstract_class_as_trait(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "inheritance.java")
        traits = [t.name for t in result.traits]
        assert "BaseHandler" in traits

    def test_abstract_required_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "inheritance.java")
        bh = next(t for t in result.traits if t.name == "BaseHandler")
        required = [i for i in bh.items if i.kind == TraitItemKind.REQUIRED_METHOD]
        names = [r.name for r in required]
        assert "handle" in names
        assert "process" in names

    def test_abstract_default_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "inheritance.java")
        bh = next(t for t in result.traits if t.name == "BaseHandler")
        defaults = [i for i in bh.items if i.kind == TraitItemKind.DEFAULT_METHOD]
        names = [d.name for d in defaults]
        assert "log" in names

    def test_implements_interface(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "inheritance.java")
        disk_impls = [i for i in result.impl_blocks if i.self_type == "DiskStorage"]
        traits = [i.trait_type for i in disk_impls]
        assert "IStorageHelper" in traits

    def test_extends_and_implements(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "inheritance.java")
        worker_impls = [i for i in result.impl_blocks if i.self_type == "Worker"]
        traits = sorted(i.trait_type for i in worker_impls)
        assert "BaseHandler" in traits
        assert "Runnable" in traits


# endregion: --- Inheritance & abstract classes


# ---------------------------------------------------------------------------
# region:    --- Record extraction
# ---------------------------------------------------------------------------


class TestRecordExtraction:
    """Record → StructNode."""

    def test_record_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        assert len(result.structs) == 3  # Point, UserDto, Config

    def test_record_fields(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        point = next(s for s in result.structs if s.name == "Point")
        fnames = [f.name for f in point.fields]
        assert fnames == ["x", "y"]

    def test_record_field_types(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        point = next(s for s in result.structs if s.name == "Point")
        ftypes = [f.type for f in point.fields]
        assert ftypes == ["int", "int"]

    def test_record_field_visibility(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        point = next(s for s in result.structs if s.name == "Point")
        assert all(f.visibility == Visibility.PUBLIC for f in point.fields)

    def test_record_with_method(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        cfg_impls = [i for i in result.impl_blocks if i.self_type == "Config"]
        assert len(cfg_impls) == 1
        assert any(m.name == "url" for m in cfg_impls[0].methods)

    def test_record_three_fields(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "records.java")
        dto = next(s for s in result.structs if s.name == "UserDto")
        assert len(dto.fields) == 3


# endregion: --- Record extraction


# ---------------------------------------------------------------------------
# region:    --- Import and package extraction
# ---------------------------------------------------------------------------


class TestImportExtraction:
    """Import statements → uses and imported_package_methods."""

    def test_imports_collected(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "imports.java")
        assert len(result.uses) >= 6

    def test_regular_import(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "imports.java")
        assert any("java.util.List" in u for u in result.uses)

    def test_static_import(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "imports.java")
        assert any("static java.lang.Math.abs" in u for u in result.uses)

    def test_package_declaration(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "imports.java")
        assert result.module_path == "com.example.imports"

    def test_imported_package_methods(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "imports.java")
        # List.of() and Map.of() should be captured
        assert "java.util.List" in result.imported_package_methods
        assert "of" in result.imported_package_methods["java.util.List"]


# endregion: --- Import and package extraction


# ---------------------------------------------------------------------------
# region:    --- Import map builder
# ---------------------------------------------------------------------------


class TestImportMap:
    """Unit tests for build_import_map."""

    def test_regular_import(self) -> None:
        m = build_import_map(["import java.util.List;"])
        assert m == {"List": "java.util.List"}

    def test_static_import(self) -> None:
        m = build_import_map(["import static java.lang.Math.abs;"])
        assert m == {"abs": "java.lang.Math.abs"}

    def test_wildcard_import_skipped(self) -> None:
        m = build_import_map(["import java.util.*;"])
        assert m == {}

    def test_multiple_imports(self) -> None:
        m = build_import_map([
            "import java.util.List;",
            "import java.util.Map;",
            "import static java.lang.Math.abs;",
        ])
        assert "List" in m
        assert "Map" in m
        assert "abs" in m


# endregion: --- Import map builder


# ---------------------------------------------------------------------------
# region:    --- Constant extraction
# ---------------------------------------------------------------------------


class TestConstantExtraction:
    """static final fields → ConstantNode."""

    def test_constant_count(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "constants.java")
        assert len(result.constants) == 4

    def test_constant_names(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "constants.java")
        names = [c.name for c in result.constants]
        assert "APP_NAME" in names
        assert "MAX_RETRIES" in names
        assert "PI" in names
        assert "TIMEOUT_MS" in names

    def test_constant_visibility(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "constants.java")
        cmap = {c.name: c for c in result.constants}
        assert cmap["APP_NAME"].visibility == Visibility.PUBLIC
        assert cmap["PI"].visibility == Visibility.PRIVATE
        assert cmap["TIMEOUT_MS"].visibility == Visibility.PROTECTED

    def test_constant_raw_text(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "constants.java")
        app = next(c for c in result.constants if c.name == "APP_NAME")
        assert "static final" in app.raw
        assert "String" in app.raw


# endregion: --- Constant extraction


# ---------------------------------------------------------------------------
# region:    --- Annotation extraction
# ---------------------------------------------------------------------------


class TestAnnotationExtraction:
    """@Annotation → attributes."""

    def test_class_annotation(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "annotations.java")
        legacy = next(s for s in result.structs if s.name == "LegacyService")
        assert "@Deprecated" in legacy.attributes

    def test_controller_no_class_annotation(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "annotations.java")
        ctrl = next(s for s in result.structs if s.name == "Controller")
        # Controller has no class-level annotations
        assert len(ctrl.attributes) == 0


# endregion: --- Annotation extraction


# ---------------------------------------------------------------------------
# region:    --- Self-methods
# ---------------------------------------------------------------------------


class TestSelfMethods:
    """Flat self_methods list."""

    def test_self_methods_populated(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        assert len(result.self_methods) > 0

    def test_self_methods_context(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        contexts = [m.context for m in result.self_methods]
        assert any("impl:Serializable for Job" in c for c in contexts)

    def test_inherent_impl_context(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        contexts = [m.context for m in result.self_methods]
        assert any("impl:InternalHelper" in c for c in contexts)


# endregion: --- Self-methods


# ---------------------------------------------------------------------------
# region:    --- Call graph
# ---------------------------------------------------------------------------


class TestCallGraph:
    """Intra-file call edges."""

    def test_call_edges_extracted(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "call_graph.java")
        assert len(result.call_edges) > 0

    def test_intra_file_call_resolved(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "call_graph.java")
        callee_names = [e.callee for e in result.call_edges]
        assert "helper" in callee_names
        assert "process" in callee_names
        assert "transform" in callee_names


# endregion: --- Call graph


# ---------------------------------------------------------------------------
# region:    --- Rationale comments
# ---------------------------------------------------------------------------


class TestRationaleComments:
    """Rationale comment extraction."""

    def test_rationale_comments_extracted(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "rationale_comments.java")
        assert len(result.rationale_comments) > 0

    def test_note_kind(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "rationale_comments.java")
        kinds = [r.kind for r in result.rationale_comments]
        assert "NOTE" in kinds

    def test_hack_kind(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "rationale_comments.java")
        kinds = [r.kind for r in result.rationale_comments]
        assert "HACK" in kinds

    def test_todo_kind(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "rationale_comments.java")
        kinds = [r.kind for r in result.rationale_comments]
        assert "TODO" in kinds


# endregion: --- Rationale comments


# ---------------------------------------------------------------------------
# region:    --- Spans
# ---------------------------------------------------------------------------


class TestSpans:
    """Span information on extracted nodes."""

    def test_struct_has_span(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        for s in result.structs:
            assert s.span.start_line > 0
            assert s.span.end_line >= s.span.start_line

    def test_trait_has_span(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "interfaces.java")
        for t in result.traits:
            assert t.span.start_line > 0

    def test_enum_has_span(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "enums.java")
        for e in result.enums:
            assert e.span.start_line > 0

    def test_impl_block_has_span(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        for i in result.impl_blocks:
            assert i.span.start_line > 0

    def test_method_has_span(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "classes.java")
        for i in result.impl_blocks:
            for m in i.methods:
                assert m.span.start_line > 0


# endregion: --- Spans


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing (Maven POM)
# ---------------------------------------------------------------------------


class TestMavenPomParsing:
    """Maven pom.xml → CrateModel."""

    def test_pom_name(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        assert crate.name == "sample-app"

    def test_pom_version(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        assert crate.version == "1.2.3"

    def test_pom_language(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        assert crate.language == "java"

    def test_pom_dependencies(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        dep_names = [d.name for d in crate.dependencies]
        assert "org.springframework.boot:spring-boot-starter-web" in dep_names
        assert "com.google.guava:guava" in dep_names

    def test_pom_test_dependency(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        junit = next(d for d in crate.dependencies if "junit" in d.name)
        assert junit.is_dev is True

    def test_pom_dep_versions(self, ext: JavaExtractor) -> None:
        crate = ext._parse_pom(FIXTURES / "sample_pom.xml")
        spring = next(d for d in crate.dependencies if "spring-boot" in d.name)
        assert spring.version == "3.2.0"

    def test_pom_malformed_xml(
        self, ext: JavaExtractor, tmp_path: Path,
    ) -> None:
        """Malformed XML returns fallback CrateModel, no crash."""
        bad = tmp_path / "pom.xml"
        bad.write_text("<project><unclosed>")
        crate = ext._parse_pom(bad)
        assert crate.language == "java"
        assert crate.dependencies == []

    def test_pom_xxe_blocked(
        self, ext: JavaExtractor, tmp_path: Path,
    ) -> None:
        """defusedxml blocks XXE entity expansion."""
        xxe = tmp_path / "pom.xml"
        xxe.write_text(
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE foo [\n"
            '  <!ENTITY xxe SYSTEM "file:///etc/passwd">\n'
            "]>\n"
            "<project><artifactId>&xxe;</artifactId></project>"
        )
        crate = ext._parse_pom(xxe)
        # defusedxml should reject this; fallback crate returned
        assert crate.language == "java"


# endregion: --- Manifest parsing (Maven POM)


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing (Gradle)
# ---------------------------------------------------------------------------


class TestGradleParsing:
    """Gradle build.gradle → CrateModel."""

    def test_gradle_version(self, ext: JavaExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "build.gradle")
        assert crate.version == "1.0.0"

    def test_gradle_language(self, ext: JavaExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "build.gradle")
        assert crate.language == "java"

    def test_gradle_dependencies(self, ext: JavaExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "build.gradle")
        dep_names = [d.name for d in crate.dependencies]
        assert "org.springframework.boot:spring-boot-starter-web" in dep_names
        assert "com.google.guava:guava" in dep_names

    def test_gradle_test_dependency(self, ext: JavaExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "build.gradle")
        junit = next(d for d in crate.dependencies if "junit" in d.name)
        assert junit.is_dev is True

    def test_gradle_api_dependency(self, ext: JavaExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "build.gradle")
        commons = next(
            d for d in crate.dependencies if "commons-lang3" in d.name
        )
        assert commons.is_dev is False


# endregion: --- Manifest parsing (Gradle)


# ---------------------------------------------------------------------------
# region:    --- Comprehensive / integration
# ---------------------------------------------------------------------------


class TestComprehensive:
    """End-to-end extraction from comprehensive fixture."""

    def test_structs_found(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        names = [s.name for s in result.structs]
        assert "Application" in names
        assert "CorePlugin" in names
        assert "Settings" in names  # record

    def test_traits_found(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        names = [t.name for t in result.traits]
        assert "Plugin" in names
        assert "BasePlugin" in names  # abstract class

    def test_enums_found(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        names = [e.name for e in result.enums]
        assert "AppState" in names

    def test_constants_found(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        names = [c.name for c in result.constants]
        assert "VERSION" in names

    def test_imports_found(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        assert len(result.uses) >= 5

    def test_package_method_calls(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        # Map.of() should be captured
        assert "java.util.Map" in result.imported_package_methods

    def test_self_methods_comprehensive(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        assert len(result.self_methods) >= 4

    def test_no_errors(self, ext: JavaExtractor) -> None:
        result = _extract(ext, "comprehensive.java")
        assert result.errors == []


# endregion: --- Comprehensive / integration


# ---------------------------------------------------------------------------
# region:    --- Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and robustness."""

    def test_empty_file(self, ext: JavaExtractor, tmp_path: Path) -> None:
        f = tmp_path / "Empty.java"
        f.write_text("")
        result = ext.extract(f, b"")
        assert result.structs == []
        assert result.enums == []

    def test_syntax_error(self, ext: JavaExtractor, tmp_path: Path) -> None:
        f = tmp_path / "Bad.java"
        src = b"public class { broken }"
        f.write_bytes(src)
        result = ext.extract(f, src)
        # Should not crash; may have errors
        assert isinstance(result.errors, list)

    def test_no_package(self, ext: JavaExtractor, tmp_path: Path) -> None:
        f = tmp_path / "NoPackage.java"
        src = b"public class Foo { }"
        f.write_bytes(src)
        result = ext.extract(f, src)
        assert result.module_path == ""
        assert len(result.structs) == 1

    def test_manifest_unknown_file(self, ext: JavaExtractor, tmp_path: Path) -> None:
        f = tmp_path / "settings.gradle"
        f.write_text("rootProject.name = 'test'")
        crate = ext.parse_manifest(f)
        assert crate.language == "java"


# endregion: --- Edge cases


# ---------------------------------------------------------------------------
# region:    --- Dispatcher integration
# ---------------------------------------------------------------------------


class TestDispatcherRegistration:
    """Verify Java is registered in the dispatcher."""

    def test_java_extension_registered(self) -> None:
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert ".java" in registry

    def test_java_extractor_type(self) -> None:
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert registry[".java"] is JavaExtractor


# endregion: --- Dispatcher registration
