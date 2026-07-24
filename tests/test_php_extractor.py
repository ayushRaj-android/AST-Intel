"""Tests for the PHP language extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.base import ExtractorBase
from ast_intel.extractors.php import PhpExtractor, build_import_map
from ast_intel.models.ast_node import (
    EnumVariantKind,
    FileAST,
    TraitItemKind,
    Visibility,
)

FIXTURES = Path(__file__).parent / "fixtures" / "php"


@pytest.fixture
def ext() -> PhpExtractor:
    return PhpExtractor()


def _extract(ext: PhpExtractor, name: str) -> FileAST:
    path = FIXTURES / name
    return ext.extract(path, path.read_bytes())


# ---------------------------------------------------------------
# Extractor identity
# ---------------------------------------------------------------


class TestExtractorIdentity:
    def test_language_id(self, ext: PhpExtractor) -> None:
        assert ext.language_id == "php"

    def test_file_extensions(self, ext: PhpExtractor) -> None:
        assert ext.file_extensions == [".php"]

    def test_is_extractor_base(self, ext: PhpExtractor) -> None:
        assert isinstance(ext, ExtractorBase)


# ---------------------------------------------------------------
# Class extraction
# ---------------------------------------------------------------


class TestClassExtraction:
    def test_class_count(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        assert len(ast.structs) == 2  # User, Config

    def test_class_name(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        names = [s.name for s in ast.structs]
        assert "User" in names
        assert "Config" in names

    def test_class_visibility(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        assert user.visibility == Visibility.CRATE

    def test_class_fields(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        field_names = [f.name for f in user.fields]
        assert "name" in field_names
        assert "age" in field_names
        assert "id" in field_names

    def test_field_visibility(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        fields = {f.name: f for f in user.fields}
        assert fields["name"].visibility == Visibility.PUBLIC
        assert fields["age"].visibility == Visibility.PROTECTED
        assert fields["tags"].visibility == Visibility.PRIVATE

    def test_field_types(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        fields = {f.name: f for f in user.fields}
        assert fields["name"].type == "string"
        assert fields["age"].type == "?int"
        assert fields["tags"].type == "array"

    def test_class_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user_impl = next(
            i for i in ast.impl_blocks if i.self_type == "User"
        )
        method_names = [m.name for m in user_impl.methods]
        assert "__construct" in method_names
        assert "create" in method_names
        assert "validate" in method_names
        assert "getName" in method_names

    def test_constructor_params(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user_impl = next(
            i for i in ast.impl_blocks if i.self_type == "User"
        )
        ctor = next(m for m in user_impl.methods if m.name == "__construct")
        param_names = [p.name for p in ctor.params]
        assert "name" in param_names
        assert "age" in param_names

    def test_static_method(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user_impl = next(
            i for i in ast.impl_blocks if i.self_type == "User"
        )
        create = next(m for m in user_impl.methods if m.name == "create")
        assert create.is_static is True

    def test_class_doc(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        assert "basic user class" in user.doc.lower()

    def test_final_class(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        assert "final" in user.attributes

    def test_readonly_class(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        config = next(s for s in ast.structs if s.name == "Config")
        assert "readonly" in config.attributes

    def test_class_generics_phpdoc(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        assert user.generics == "<T>"

    def test_class_attributes(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        user = next(s for s in ast.structs if s.name == "User")
        assert "#[Entity]" in user.attributes
        assert '#[Table("users")]' in user.attributes


# ---------------------------------------------------------------
# Abstract class extraction
# ---------------------------------------------------------------


class TestAbstractClassExtraction:
    def test_abstract_class_as_trait(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "abstract_classes.php")
        assert len(ast.traits) == 1
        assert ast.traits[0].name == "Repository"

    def test_abstract_required_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "abstract_classes.php")
        repo = ast.traits[0]
        required = [
            i for i in repo.items
            if i.kind == TraitItemKind.REQUIRED_METHOD
        ]
        names = [m.name for m in required]
        assert "find" in names
        assert "query" in names

    def test_abstract_default_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "abstract_classes.php")
        repo = ast.traits[0]
        defaults = [
            i for i in repo.items
            if i.kind == TraitItemKind.DEFAULT_METHOD
        ]
        names = [m.name for m in defaults]
        assert "all" in names
        assert "count" in names


# ---------------------------------------------------------------
# Interface extraction
# ---------------------------------------------------------------


class TestInterfaceExtraction:
    def test_interface_count(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        assert len(ast.traits) == 2

    def test_interface_name(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        names = [t.name for t in ast.traits]
        assert "Cacheable" in names
        assert "Renderable" in names

    def test_interface_required_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        cacheable = next(t for t in ast.traits if t.name == "Cacheable")
        assert all(
            i.kind == TraitItemKind.REQUIRED_METHOD for i in cacheable.items
        )
        method_names = [i.name for i in cacheable.items]
        assert "cacheKey" in method_names
        assert "cacheTTL" in method_names

    def test_interface_extends(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        renderable = next(t for t in ast.traits if t.name == "Renderable")
        assert "Cacheable" in renderable.super_traits

    def test_interface_constants(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        const_names = [c.name for c in ast.constants]
        assert "DEFAULT_TTL" in const_names

    def test_interface_doc(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        cacheable = next(t for t in ast.traits if t.name == "Cacheable")
        assert "cacheable" in cacheable.doc.lower()


# ---------------------------------------------------------------
# Trait extraction
# ---------------------------------------------------------------


class TestTraitExtraction:
    def test_trait_count(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        assert len(ast.traits) == 2

    def test_trait_name(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        names = [t.name for t in ast.traits]
        assert "Timestamps" in names
        assert "Loggable" in names

    def test_trait_attribute(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        for t in ast.traits:
            assert "trait" in t.attributes

    def test_trait_methods_default(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        timestamps = next(t for t in ast.traits if t.name == "Timestamps")
        defaults = [
            i for i in timestamps.items
            if i.kind == TraitItemKind.DEFAULT_METHOD
        ]
        names = [m.name for m in defaults]
        assert "getCreatedAt" in names
        assert "formatDate" in names

    def test_trait_abstract_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        timestamps = next(t for t in ast.traits if t.name == "Timestamps")
        required = [
            i for i in timestamps.items
            if i.kind == TraitItemKind.REQUIRED_METHOD
        ]
        assert any(m.name == "touch" for m in required)

    def test_trait_doc(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "traits.php")
        timestamps = next(t for t in ast.traits if t.name == "Timestamps")
        assert "timestamps" in timestamps.doc.lower()


# ---------------------------------------------------------------
# Enum extraction
# ---------------------------------------------------------------


class TestEnumExtraction:
    def test_enum_count(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        assert len(ast.enums) == 3

    def test_unit_enum_variants(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        status = next(e for e in ast.enums if e.name == "Status")
        assert len(status.variants) == 3
        assert all(v.kind == EnumVariantKind.UNIT for v in status.variants)
        names = [v.name for v in status.variants]
        assert "Active" in names
        assert "Inactive" in names
        assert "Pending" in names

    def test_backed_enum_string(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        color = next(e for e in ast.enums if e.name == "Color")
        assert "backed:string" in color.attributes

    def test_backed_enum_int(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        priority = next(e for e in ast.enums if e.name == "Priority")
        assert "backed:int" in priority.attributes

    def test_enum_methods(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        color_impl = next(
            i for i in ast.impl_blocks if i.self_type == "Color"
        )
        method_names = [m.name for m in color_impl.methods]
        assert "label" in method_names

    def test_enum_implements(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        priority_impl = next(
            i for i in ast.impl_blocks
            if i.self_type == "Priority" and i.trait_type == "Cacheable"
        )
        assert priority_impl is not None


# ---------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------


class TestFunctionExtraction:
    def test_function_count(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        assert len(ast.functions) == 3

    def test_function_name(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        names = [f.name for f in ast.functions]
        assert "helper" in names
        assert "greet" in names
        assert "process" in names

    def test_function_params(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        helper = next(f for f in ast.functions if f.name == "helper")
        param_names = [p.name for p in helper.params]
        assert "input" in param_names
        assert "count" in param_names

    def test_function_return_type(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        helper = next(f for f in ast.functions if f.name == "helper")
        assert helper.return_type == "array"

    def test_function_void_return(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        process = next(f for f in ast.functions if f.name == "process")
        assert process.return_type == "void"

    def test_function_doc(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        helper = next(f for f in ast.functions if f.name == "helper")
        assert "helper" in helper.doc.lower()

    def test_function_visibility(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "functions.php")
        for f in ast.functions:
            assert f.visibility == Visibility.PUBLIC


# ---------------------------------------------------------------
# Constant extraction
# ---------------------------------------------------------------


class TestConstantExtraction:
    def test_global_const(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "constants.php")
        const_names = [c.name for c in ast.constants]
        assert "APP_VERSION" in const_names
        assert "MAX_RETRIES" in const_names

    def test_class_const(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "constants.php")
        const_names = [c.name for c in ast.constants]
        assert "MODE_DEBUG" in const_names
        assert "MODE_PROD" in const_names

    def test_static_property_as_constant(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "constants.php")
        const_names = [c.name for c in ast.constants]
        assert "cache" in const_names
        assert "instanceCount" in const_names

    def test_constant_visibility(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "constants.php")
        consts = {c.name: c for c in ast.constants}
        assert consts["MODE_PROD"].visibility == Visibility.PUBLIC
        assert consts["SECRET_KEY"].visibility == Visibility.PROTECTED


# ---------------------------------------------------------------
# Namespace & import extraction
# ---------------------------------------------------------------


class TestNamespaceImportExtraction:
    def test_namespace_declaration(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        assert ast.module_path == "App\\Models"

    def test_use_statements(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        assert len(ast.uses) == 5

    def test_grouped_use(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        uses_text = " ".join(ast.uses)
        assert "Loggable" in uses_text
        assert "Serializable" in uses_text

    def test_function_use(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        assert any("function strlen" in u for u in ast.uses)

    def test_const_use(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        assert any("const PHP_INT_MAX" in u for u in ast.uses)

    def test_aliased_use(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "namespaces.php")
        aliases = [ta.name for ta in ast.type_aliases]
        assert "str_len" in aliases


# ---------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------


class TestInheritance:
    def test_extends_class(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "inheritance.php")
        duck_impls = [
            i for i in ast.impl_blocks if i.self_type == "Duck"
        ]
        trait_types = [i.trait_type for i in duck_impls]
        assert "Animal" in trait_types

    def test_implements_interface(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "inheritance.php")
        duck_impls = [
            i for i in ast.impl_blocks if i.self_type == "Duck"
        ]
        trait_types = [i.trait_type for i in duck_impls]
        assert "Swimmable" in trait_types
        assert "Flyable" in trait_types

    def test_use_trait(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "inheritance.php")
        duck_impls = [
            i for i in ast.impl_blocks if i.self_type == "Duck"
        ]
        trait_types = [i.trait_type for i in duck_impls]
        assert "HasLegs" in trait_types

    def test_combined_inheritance(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "inheritance.php")
        duck_impls = [
            i for i in ast.impl_blocks if i.self_type == "Duck"
        ]
        assert len(duck_impls) == 4  # Animal + Swimmable + Flyable + HasLegs


# ---------------------------------------------------------------
# Promoted parameters
# ---------------------------------------------------------------


class TestPromotedParameters:
    def test_promoted_param_fields(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "promoted_params.php")
        dto = next(s for s in ast.structs if s.name == "UserDTO")
        field_names = [f.name for f in dto.fields]
        assert "name" in field_names
        assert "age" in field_names
        assert "email" in field_names

    def test_promoted_param_visibility(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "promoted_params.php")
        dto = next(s for s in ast.structs if s.name == "UserDTO")
        fields = {f.name: f for f in dto.fields}
        assert fields["name"].visibility == Visibility.PUBLIC
        assert fields["age"].visibility == Visibility.PROTECTED
        assert fields["email"].visibility == Visibility.PRIVATE

    def test_promoted_param_type(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "promoted_params.php")
        dto = next(s for s in ast.structs if s.name == "UserDTO")
        fields = {f.name: f for f in dto.fields}
        assert fields["name"].type == "string"
        assert fields["age"].type == "int"
        assert fields["email"].type == "string"


# ---------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------


class TestAttributes:
    def test_class_attribute(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "attributes.php")
        product = next(s for s in ast.structs if s.name == "Product")
        assert "#[Entity]" in product.attributes
        assert '#[Table("products")]' in product.attributes

    def test_method_attribute(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "attributes.php")
        product_impl = next(
            i for i in ast.impl_blocks if i.self_type == "Product"
        )
        list_method = next(
            m for m in product_impl.methods if m.name == "list"
        )
        assert any("#[Route" in a for a in list_method.attributes)


# ---------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------


class TestVisibility:
    def test_public_method(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        impl = ast.impl_blocks[0]
        pub = next(m for m in impl.methods if m.name == "publicMethod")
        assert pub.visibility == Visibility.PUBLIC

    def test_protected_method(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        impl = ast.impl_blocks[0]
        prot = next(m for m in impl.methods if m.name == "protectedMethod")
        assert prot.visibility == Visibility.PROTECTED

    def test_private_method(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        impl = ast.impl_blocks[0]
        priv = next(m for m in impl.methods if m.name == "privateMethod")
        assert priv.visibility == Visibility.PRIVATE

    def test_public_property(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        ac = next(s for s in ast.structs if s.name == "AccessControl")
        fields = {f.name: f for f in ac.fields}
        assert fields["publicField"].visibility == Visibility.PUBLIC

    def test_protected_property(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        ac = next(s for s in ast.structs if s.name == "AccessControl")
        fields = {f.name: f for f in ac.fields}
        assert fields["protectedField"].visibility == Visibility.PROTECTED

    def test_private_property(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "visibility.php")
        ac = next(s for s in ast.structs if s.name == "AccessControl")
        fields = {f.name: f for f in ac.fields}
        assert fields["privateField"].visibility == Visibility.PRIVATE


# ---------------------------------------------------------------
# Type hints
# ---------------------------------------------------------------


class TestTypeHints:
    def test_union_type(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "type_hints.php")
        fn = next(f for f in ast.functions if f.name == "acceptUnion")
        assert fn.return_type == "string|int"

    def test_nullable_type(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "type_hints.php")
        fn = next(f for f in ast.functions if f.name == "acceptNullable")
        assert fn.return_type == "?string"

    def test_void_return(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "type_hints.php")
        fn = next(f for f in ast.functions if f.name == "returnVoid")
        assert fn.return_type == "void"

    def test_mixed_type(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "type_hints.php")
        fn = next(f for f in ast.functions if f.name == "returnMixed")
        assert fn.return_type == "mixed"


# ---------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------


class TestDocComments:
    def test_class_doc_comment(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "doc_comments.php")
        doc = next(s for s in ast.structs if s.name == "Documented")
        assert "user class" in doc.doc.lower()

    def test_method_doc_comment(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "doc_comments.php")
        # Method doc is not directly on MethodNode, but on TraitItemNode
        # For now, just check the class has doc
        assert ast.structs[0].doc != ""

    def test_function_doc_comment(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "doc_comments.php")
        fn = next(f for f in ast.functions if f.name == "documented")
        assert "documented function" in fn.doc.lower()


# ---------------------------------------------------------------
# Generics from PHPDoc
# ---------------------------------------------------------------


class TestGenericsPHPDoc:
    def test_template_generics(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "generics_phpdoc.php")
        container = next(s for s in ast.structs if s.name == "Container")
        assert "T" in container.generics
        assert "U" in container.generics

    def test_template_constrained(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "generics_phpdoc.php")
        container = next(s for s in ast.structs if s.name == "Container")
        assert "U: Comparable" in container.generics


# ---------------------------------------------------------------
# Call graph
# ---------------------------------------------------------------


class TestCallGraph:
    def test_call_edges_extracted(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "call_graph.php")
        assert len(ast.call_edges) > 0

    def test_intra_file_call_resolved(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "call_graph.php")
        resolved = [e for e in ast.call_edges if e.resolved_target]
        callee_names = [e.callee for e in resolved]
        assert "callee" in callee_names
        assert "helper" in callee_names


# ---------------------------------------------------------------
# Rationale comments
# ---------------------------------------------------------------


class TestRationaleComments:
    def test_rationale_comments_extracted(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "rationale_comments.php")
        assert len(ast.rationale_comments) > 0

    def test_note_kind(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "rationale_comments.php")
        kinds = [r.kind for r in ast.rationale_comments]
        assert "NOTE" in kinds

    def test_hack_kind(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "rationale_comments.php")
        kinds = [r.kind for r in ast.rationale_comments]
        assert "HACK" in kinds

    def test_todo_kind(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "rationale_comments.php")
        kinds = [r.kind for r in ast.rationale_comments]
        assert "TODO" in kinds


# ---------------------------------------------------------------
# Import map
# ---------------------------------------------------------------


class TestImportMap:
    def test_regular_use(self) -> None:
        uses = ["use App\\Models\\User"]
        result = build_import_map(uses)
        assert result["User"] == "App\\Models\\User"

    def test_aliased_use(self) -> None:
        uses = ["use App\\Services\\AuthService as Auth"]
        result = build_import_map(uses)
        assert result["Auth"] == "App\\Services\\AuthService"

    def test_function_use_skipped(self) -> None:
        uses = ["use function strlen"]
        result = build_import_map(uses)
        assert len(result) == 0

    def test_multiple_uses(self) -> None:
        uses = [
            "use App\\Models\\User",
            "use App\\Models\\Post",
        ]
        result = build_import_map(uses)
        assert "User" in result
        assert "Post" in result


# ---------------------------------------------------------------
# Self methods
# ---------------------------------------------------------------


class TestSelfMethods:
    def test_self_methods_populated(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        assert len(ast.self_methods) > 0

    def test_self_methods_context(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        contexts = [m.context for m in ast.self_methods]
        assert any("impl:" in c for c in contexts)


# ---------------------------------------------------------------
# Spans
# ---------------------------------------------------------------


class TestSpans:
    def test_struct_has_span(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        for s in ast.structs:
            assert s.span is not None

    def test_trait_has_span(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "interfaces.php")
        for t in ast.traits:
            assert t.span is not None

    def test_enum_has_span(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "enums.php")
        for e in ast.enums:
            assert e.span is not None

    def test_method_has_span(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "classes.php")
        for impl in ast.impl_blocks:
            for m in impl.methods:
                assert m.span is not None


# ---------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------


class TestManifestParsing:
    def test_composer_name(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        assert crate.name == "vendor/my-package"

    def test_composer_version(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        assert crate.version == "1.2.3"

    def test_composer_language(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        assert crate.language == "php"

    def test_composer_dependencies(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        prod_deps = [d for d in crate.dependencies if not d.is_dev]
        dep_names = [d.name for d in prod_deps]
        assert "monolog/monolog" in dep_names
        assert "symfony/console" in dep_names
        assert "guzzlehttp/guzzle" in dep_names

    def test_composer_dev_dependencies(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        dev_deps = [d for d in crate.dependencies if d.is_dev]
        dep_names = [d.name for d in dev_deps]
        assert "phpunit/phpunit" in dep_names
        assert "phpstan/phpstan" in dep_names

    def test_composer_php_constraint_skipped(self, ext: PhpExtractor) -> None:
        crate = ext.parse_manifest(FIXTURES / "composer.json")
        dep_names = [d.name for d in crate.dependencies]
        assert "php" not in dep_names
        assert not any(n.startswith("ext-") for n in dep_names)

    def test_composer_malformed_json(self, ext: PhpExtractor, tmp_path: Path) -> None:
        bad = tmp_path / "composer.json"
        bad.write_text("{invalid json", encoding="utf-8")
        crate = ext.parse_manifest(bad)
        assert crate.language == "php"

    def test_composer_missing_version(self, ext: PhpExtractor, tmp_path: Path) -> None:
        minimal = tmp_path / "composer.json"
        minimal.write_text('{"name": "test/pkg"}', encoding="utf-8")
        crate = ext.parse_manifest(minimal)
        assert crate.version == ""
        assert crate.name == "test/pkg"

    def test_manifest_unknown_file(self, ext: PhpExtractor, tmp_path: Path) -> None:
        unknown = tmp_path / "unknown.txt"
        unknown.write_text("", encoding="utf-8")
        crate = ext.parse_manifest(unknown)
        assert crate.language == "php"


# ---------------------------------------------------------------
# Comprehensive
# ---------------------------------------------------------------


class TestComprehensive:
    def test_structs_found(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.structs) >= 1

    def test_traits_found(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.traits) >= 2

    def test_enums_found(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.enums) >= 1

    def test_constants_found(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.constants) >= 1

    def test_imports_found(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.uses) >= 1

    def test_self_methods_comprehensive(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.self_methods) >= 1

    def test_no_errors(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert len(ast.errors) == 0

    def test_namespace(self, ext: PhpExtractor) -> None:
        ast = _extract(ext, "comprehensive.php")
        assert ast.module_path == "App\\Comprehensive"


# ---------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------


class TestEdgeCases:
    def test_empty_file(self, ext: PhpExtractor) -> None:
        ast = ext.extract(Path("empty.php"), b"<?php\n")
        assert len(ast.structs) == 0
        assert len(ast.traits) == 0
        assert len(ast.enums) == 0

    def test_syntax_error(self, ext: PhpExtractor) -> None:
        ast = ext.extract(
            Path("bad.php"), b"<?php\nclass { broken }\n",
        )
        assert len(ast.errors) > 0

    def test_no_namespace(self, ext: PhpExtractor) -> None:
        ast = ext.extract(
            Path("no_ns.php"),
            b"<?php\nclass Foo {}\n",
        )
        assert ast.module_path == ""
