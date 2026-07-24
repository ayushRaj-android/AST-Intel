"""Tests for the Ruby language extractor.

Covers class → StructNode, module → TraitNode/ModuleNode,
methods, visibility, attr_* fields, eigenclass, inheritance,
constants, type aliases, Struct.new, imports, edge cases,
self_methods, call_edges, rationale_comments, and Gemfile manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.ruby import (
    RubyExtractor,
    build_import_map,
)
from ast_intel.models.ast_node import (
    Confidence,
    FileAST,
    TraitItemKind,
    Visibility,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "ruby"


@pytest.fixture
def ext() -> RubyExtractor:
    """Create a fresh extractor instance."""
    return RubyExtractor()


def _extract(ext: RubyExtractor, fixture_name: str) -> FileAST:
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

    def test_language_id(self, ext: RubyExtractor) -> None:
        assert ext.language_id == "ruby"

    def test_file_extensions(self, ext: RubyExtractor) -> None:
        assert ".rb" in ext.file_extensions

    def test_is_extractor_base(self, ext: RubyExtractor) -> None:
        from ast_intel.extractors.base import ExtractorBase

        assert isinstance(ext, ExtractorBase)


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    """class → StructNode + ImplBlockNode."""

    def test_class_count(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        names = [s.name for s in result.structs]
        assert "SimpleClass" in names
        assert "User" in names
        assert "Admin" in names
        assert "Container" in names

    def test_simple_class(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        sc = next(s for s in result.structs if s.name == "SimpleClass")
        assert sc.visibility == Visibility.PUBLIC
        assert len(sc.fields) == 0

    def test_class_fields_from_initialize(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        user = next(s for s in result.structs if s.name == "User")
        field_names = [f.name for f in user.fields]
        assert "name" in field_names
        assert "age" in field_names
        assert "active" in field_names

    def test_class_doc(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        user = next(s for s in result.structs if s.name == "User")
        assert "fields" in user.doc.lower() or len(user.doc) > 0

    def test_class_methods(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        user_impls = [i for i in result.impl_blocks if i.self_type == "User"]
        assert len(user_impls) >= 1
        methods = user_impls[0].methods
        method_names = [m.name for m in methods]
        assert "initialize" in method_names
        assert "greet" in method_names

    def test_inheritance_creates_impl(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        admin_impls = [i for i in result.impl_blocks if i.self_type == "Admin"]
        trait_impls = [i for i in admin_impls if i.trait_type == "User"]
        assert len(trait_impls) == 1

    def test_inheritance_methods(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        admin_impl = next(
            i for i in result.impl_blocks
            if i.self_type == "Admin" and i.trait_type == "User"
        )
        method_names = [m.name for m in admin_impl.methods]
        assert "initialize" in method_names
        assert "admin?" in method_names

    def test_attr_accessor_fields(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        container = next(s for s in result.structs if s.name == "Container")
        field_names = [f.name for f in container.fields]
        assert "items" in field_names

    def test_initialize_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "classes.rb")
        user_impl = next(
            i for i in result.impl_blocks if i.self_type == "User"
        )
        init = next(m for m in user_impl.methods if m.name == "initialize")
        param_names = [p.name for p in init.params]
        assert "name" in param_names
        assert "age" in param_names


# endregion: --- Class extraction


# ---------------------------------------------------------------------------
# region:    --- Module extraction
# ---------------------------------------------------------------------------


class TestModuleExtraction:
    """module → TraitNode (mixin) or ModuleNode (namespace)."""

    def test_namespace_module(self, ext: RubyExtractor) -> None:
        """Validators has nested class but no methods → namespace."""
        result = _extract(ext, "modules.rb")
        mod_names = [m.name for m in result.modules]
        assert "Validators" in mod_names

    def test_mixin_module_is_trait(self, ext: RubyExtractor) -> None:
        """Serializable has methods → TraitNode."""
        result = _extract(ext, "modules.rb")
        trait_names = [t.name for t in result.traits]
        assert "Serializable" in trait_names

    def test_trait_items(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "modules.rb")
        ser = next(t for t in result.traits if t.name == "Serializable")
        item_names = [i.name for i in ser.items]
        assert "to_hash" in item_names
        assert "to_json" in item_names

    def test_trait_item_kind(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "modules.rb")
        ser = next(t for t in result.traits if t.name == "Serializable")
        for item in ser.items:
            assert item.kind == TraitItemKind.DEFAULT_METHOD

    def test_both_module(self, ext: RubyExtractor) -> None:
        """Logging has methods + nested class → both trait and module."""
        result = _extract(ext, "modules.rb")
        trait_names = [t.name for t in result.traits]
        mod_names = [m.name for m in result.modules]
        assert "Logging" in trait_names
        assert "Logging" in mod_names

    def test_include_creates_impl(self, ext: RubyExtractor) -> None:
        """Document includes Serializable → ImplBlockNode."""
        result = _extract(ext, "modules.rb")
        doc_impls = [
            i for i in result.impl_blocks
            if i.self_type == "Document" and i.trait_type == "Serializable"
        ]
        assert len(doc_impls) == 1
        assert doc_impls[0].confidence == Confidence.INFERRED

    def test_include_adds_use(self, ext: RubyExtractor) -> None:
        """Include statements generate use entries."""
        result = _extract(ext, "modules.rb")
        assert any("Serializable" in u for u in result.uses)

    def test_nested_class_in_module(self, ext: RubyExtractor) -> None:
        """Nested classes within modules are extracted."""
        result = _extract(ext, "modules.rb")
        struct_names = [s.name for s in result.structs]
        assert "EmailValidator" in struct_names
        assert "Logger" in struct_names


# endregion: --- Module extraction


# ---------------------------------------------------------------------------
# region:    --- Method extraction
# ---------------------------------------------------------------------------


class TestMethodExtraction:
    """def → FunctionNode (top-level) or MethodNode (in class)."""

    def test_top_level_functions(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        fn_names = [f.name for f in result.functions]
        assert "greet" in fn_names
        assert "connect" in fn_names
        assert "sum" in fn_names
        assert "create_user" in fn_names
        assert "configure" in fn_names
        assert "with_retry" in fn_names

    def test_function_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        greet = next(f for f in result.functions if f.name == "greet")
        assert len(greet.params) == 1
        assert greet.params[0].name == "name"

    def test_splat_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        fn = next(f for f in result.functions if f.name == "sum")
        param_names = [p.name for p in fn.params]
        assert any("*" in p for p in param_names)

    def test_keyword_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        fn = next(f for f in result.functions if f.name == "create_user")
        param_names = [p.name for p in fn.params]
        assert any("name" in p for p in param_names)
        assert any("email" in p for p in param_names)

    def test_double_splat_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        fn = next(f for f in result.functions if f.name == "configure")
        param_names = [p.name for p in fn.params]
        assert any("**" in p for p in param_names)

    def test_block_params(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        fn = next(f for f in result.functions if f.name == "with_retry")
        param_names = [p.name for p in fn.params]
        assert any("&" in p for p in param_names)

    def test_class_method_via_self(self, ext: RubyExtractor) -> None:
        """def self.description → static MethodNode."""
        result = _extract(ext, "methods.rb")
        calc_impls = [i for i in result.impl_blocks if i.self_type == "Calculator"]
        assert len(calc_impls) >= 1
        all_methods = []
        for impl in calc_impls:
            all_methods.extend(impl.methods)
        desc = next((m for m in all_methods if m.name == "description"), None)
        assert desc is not None
        assert desc.is_static is True

    def test_class_instance_method(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        calc_impls = [i for i in result.impl_blocks if i.self_type == "Calculator"]
        all_methods = []
        for impl in calc_impls:
            all_methods.extend(impl.methods)
        add = next(m for m in all_methods if m.name == "add")
        assert add.is_static is False


# endregion: --- Method extraction


# ---------------------------------------------------------------------------
# region:    --- Import extraction
# ---------------------------------------------------------------------------


class TestImportExtraction:
    """require / require_relative → uses."""

    def test_require_statements(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "imports.rb")
        assert len(result.uses) == 5

    def test_require_json(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "imports.rb")
        assert any("json" in u for u in result.uses)

    def test_require_net_http(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "imports.rb")
        assert any("net/http" in u for u in result.uses)

    def test_require_relative(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "imports.rb")
        assert any("require_relative" in u for u in result.uses)
        assert any("string_utils" in u for u in result.uses)


# endregion: --- Import extraction


# ---------------------------------------------------------------------------
# region:    --- Constant extraction
# ---------------------------------------------------------------------------


class TestConstantExtraction:
    """UPPER_CASE → ConstantNode, CamelCase → TypeAliasNode."""

    def test_constants_found(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "constants.rb")
        const_names = [c.name for c in result.constants]
        assert "MAX_RETRIES" in const_names
        assert "DEFAULT_TIMEOUT" in const_names
        assert "API_BASE_URL" in const_names
        assert "VALID_STATUSES" in const_names

    def test_constant_visibility(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "constants.rb")
        for c in result.constants:
            assert c.visibility == Visibility.PUBLIC

    def test_constant_confidence(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "constants.rb")
        for c in result.constants:
            assert c.confidence == Confidence.EXTRACTED

    def test_type_alias(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "constants.rb")
        alias_names = [t.name for t in result.type_aliases]
        assert "StringArray" in alias_names
        assert "UserList" in alias_names

    def test_type_alias_confidence(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "constants.rb")
        for t in result.type_aliases:
            assert t.confidence == Confidence.INFERRED

    def test_lowercase_ignored(self, ext: RubyExtractor) -> None:
        """lowercase assignments → not captured as constants or aliases."""
        result = _extract(ext, "constants.rb")
        const_names = [c.name for c in result.constants]
        alias_names = [t.name for t in result.type_aliases]
        assert "helper_value" not in const_names
        assert "helper_value" not in alias_names


# endregion: --- Constant extraction


# ---------------------------------------------------------------------------
# region:    --- Visibility tracking
# ---------------------------------------------------------------------------


class TestVisibility:
    """Stateful + targeted visibility modifiers."""

    def test_public_method(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        ac_impls = [i for i in result.impl_blocks if i.self_type == "AccessControl"]
        methods = ac_impls[0].methods
        pub = next(m for m in methods if m.name == "public_method")
        assert pub.visibility == Visibility.PUBLIC

    def test_private_after_modifier(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        ac_impls = [i for i in result.impl_blocks if i.self_type == "AccessControl"]
        methods = ac_impls[0].methods
        priv = next(m for m in methods if m.name == "private_method")
        assert priv.visibility == Visibility.PRIVATE

    def test_another_private(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        ac_impls = [i for i in result.impl_blocks if i.self_type == "AccessControl"]
        methods = ac_impls[0].methods
        priv2 = next(m for m in methods if m.name == "another_private")
        assert priv2.visibility == Visibility.PRIVATE

    def test_protected_method(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        ac_impls = [i for i in result.impl_blocks if i.self_type == "AccessControl"]
        methods = ac_impls[0].methods
        prot = next(m for m in methods if m.name == "protected_method")
        assert prot.visibility == Visibility.PROTECTED

    def test_back_to_public(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        ac_impls = [i for i in result.impl_blocks if i.self_type == "AccessControl"]
        methods = ac_impls[0].methods
        pub2 = next(m for m in methods if m.name == "back_to_public")
        assert pub2.visibility == Visibility.PUBLIC

    def test_targeted_private(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        tv_impls = [i for i in result.impl_blocks if i.self_type == "TargetedVisibility"]
        methods = tv_impls[0].methods
        method_b = next(m for m in methods if m.name == "method_b")
        assert method_b.visibility == Visibility.PRIVATE

    def test_targeted_protected(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        tv_impls = [i for i in result.impl_blocks if i.self_type == "TargetedVisibility"]
        methods = tv_impls[0].methods
        method_c = next(m for m in methods if m.name == "method_c")
        assert method_c.visibility == Visibility.PROTECTED

    def test_targeted_public_remains(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "visibility.rb")
        tv_impls = [i for i in result.impl_blocks if i.self_type == "TargetedVisibility"]
        methods = tv_impls[0].methods
        method_a = next(m for m in methods if m.name == "method_a")
        assert method_a.visibility == Visibility.PUBLIC


# endregion: --- Visibility tracking


# ---------------------------------------------------------------------------
# region:    --- attr_accessor extraction
# ---------------------------------------------------------------------------


class TestAttrAccessors:
    """attr_accessor/reader/writer → FieldNode."""

    def test_attr_accessor_fields(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "attr_accessors.rb")
        person = next(s for s in result.structs if s.name == "Person")
        field_names = [f.name for f in person.fields]
        assert "name" in field_names
        assert "email" in field_names

    def test_attr_reader_field(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "attr_accessors.rb")
        person = next(s for s in result.structs if s.name == "Person")
        field_names = [f.name for f in person.fields]
        assert "id" in field_names

    def test_attr_writer_field(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "attr_accessors.rb")
        person = next(s for s in result.structs if s.name == "Person")
        field_names = [f.name for f in person.fields]
        assert "password" in field_names

    def test_no_duplicate_fields(self, ext: RubyExtractor) -> None:
        """attr_* + initialize should not duplicate field names."""
        result = _extract(ext, "attr_accessors.rb")
        person = next(s for s in result.structs if s.name == "Person")
        field_names = [f.name for f in person.fields]
        # name appears in attr_accessor AND @name = name → should appear once
        assert field_names.count("name") == 1
        assert field_names.count("id") == 1
        assert field_names.count("email") == 1

    def test_attr_fields_are_public(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "attr_accessors.rb")
        person = next(s for s in result.structs if s.name == "Person")
        for f in person.fields:
            if f.name in ("name", "email", "id", "password"):
                assert f.visibility == Visibility.PUBLIC


# endregion: --- attr_accessor extraction


# ---------------------------------------------------------------------------
# region:    --- Eigenclass (class << self)
# ---------------------------------------------------------------------------


class TestEigenclass:
    """class << self → static MethodNode."""

    def test_eigenclass_methods_are_static(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "eigenclass.rb")
        config_impls = [i for i in result.impl_blocks if i.self_type == "Configuration"]
        all_methods = []
        for impl in config_impls:
            all_methods.extend(impl.methods)
        instance_method = next(
            (m for m in all_methods if m.name == "instance"), None,
        )
        assert instance_method is not None
        assert instance_method.is_static is True

    def test_eigenclass_reset(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "eigenclass.rb")
        config_impls = [i for i in result.impl_blocks if i.self_type == "Configuration"]
        all_methods = []
        for impl in config_impls:
            all_methods.extend(impl.methods)
        reset = next((m for m in all_methods if m.name == "reset!"), None)
        assert reset is not None
        assert reset.is_static is True

    def test_eigenclass_private(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "eigenclass.rb")
        config_impls = [i for i in result.impl_blocks if i.self_type == "Configuration"]
        all_methods = []
        for impl in config_impls:
            all_methods.extend(impl.methods)
        load = next(
            (m for m in all_methods if m.name == "load_defaults"), None,
        )
        assert load is not None
        assert load.is_static is True
        assert load.visibility == Visibility.PRIVATE

    def test_instance_methods_not_static(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "eigenclass.rb")
        config_impls = [i for i in result.impl_blocks if i.self_type == "Configuration"]
        all_methods = []
        for impl in config_impls:
            all_methods.extend(impl.methods)
        get = next((m for m in all_methods if m.name == "get"), None)
        assert get is not None
        assert get.is_static is False


# endregion: --- Eigenclass


# ---------------------------------------------------------------------------
# region:    --- Inheritance + mixins
# ---------------------------------------------------------------------------


class TestInheritance:
    """Inheritance → ImplBlockNode, include → ImplBlockNode."""

    def test_traits_defined(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "inheritance.rb")
        trait_names = [t.name for t in result.traits]
        assert "Printable" in trait_names
        assert "Loggable" in trait_names

    def test_base_inherits_nothing(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "inheritance.rb")
        # Base has no superclass → check no trait_type impl with parent
        base_impls = [
            i for i in result.impl_blocks
            if i.self_type == "Base" and i.trait_type and i.trait_type != "Printable"
        ]
        assert len(base_impls) == 0

    def test_base_includes_printable(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "inheritance.rb")
        base_printable = [
            i for i in result.impl_blocks
            if i.self_type == "Base" and i.trait_type == "Printable"
        ]
        assert len(base_printable) == 1

    def test_child_inherits_base(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "inheritance.rb")
        child_base = [
            i for i in result.impl_blocks
            if i.self_type == "Child" and i.trait_type == "Base"
        ]
        assert len(child_base) == 1

    def test_child_includes_loggable(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "inheritance.rb")
        child_log = [
            i for i in result.impl_blocks
            if i.self_type == "Child" and i.trait_type == "Loggable"
        ]
        assert len(child_log) == 1


# endregion: --- Inheritance + mixins


# ---------------------------------------------------------------------------
# region:    --- Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Nested classes, Struct.new, ?/! methods, method_missing."""

    def test_nested_classes(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        names = [s.name for s in result.structs]
        assert "Outer" in names
        assert "Inner" in names

    def test_struct_new(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        point = next((s for s in result.structs if s.name == "Point"), None)
        assert point is not None
        field_names = [f.name for f in point.fields]
        assert "x" in field_names
        assert "y" in field_names

    def test_question_mark_method(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        validator_impls = [
            i for i in result.impl_blocks if i.self_type == "Validator"
        ]
        all_methods = []
        for impl in validator_impls:
            all_methods.extend(impl.methods)
        valid = next((m for m in all_methods if m.name == "valid?"), None)
        assert valid is not None

    def test_bang_method(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        validator_impls = [
            i for i in result.impl_blocks if i.self_type == "Validator"
        ]
        all_methods = []
        for impl in validator_impls:
            all_methods.extend(impl.methods)
        validate = next(
            (m for m in all_methods if m.name == "validate!"), None,
        )
        assert validate is not None

    def test_method_missing(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        dp_impls = [
            i for i in result.impl_blocks if i.self_type == "DynamicProxy"
        ]
        all_methods = []
        for impl in dp_impls:
            all_methods.extend(impl.methods)
        mm = next(
            (m for m in all_methods if m.name == "method_missing"), None,
        )
        assert mm is not None

    def test_respond_to_missing(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "edge_cases.rb")
        dp_impls = [
            i for i in result.impl_blocks if i.self_type == "DynamicProxy"
        ]
        all_methods = []
        for impl in dp_impls:
            all_methods.extend(impl.methods)
        rtm = next(
            (m for m in all_methods if m.name == "respond_to_missing?"),
            None,
        )
        assert rtm is not None


# endregion: --- Edge cases


# ---------------------------------------------------------------------------
# region:    --- Gemfile manifest parsing
# ---------------------------------------------------------------------------


class TestGemfileParsing:
    """Gemfile → CrateModel with dependencies."""

    def test_parse_gemfile(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        assert result.language == "ruby"

    def test_gemfile_name(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        assert result.name == "ruby"  # parent dir name

    def test_production_deps(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        dep_names = [d.name for d in result.dependencies if not d.is_dev]
        assert "rails" in dep_names
        assert "pg" in dep_names
        assert "puma" in dep_names
        assert "redis" in dep_names

    def test_dev_deps(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        dev_dep_names = [d.name for d in result.dependencies if d.is_dev]
        assert "rspec" in dev_dep_names
        assert "rubocop" in dev_dep_names

    def test_dep_versions(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        rails = next(d for d in result.dependencies if d.name == "rails")
        assert rails.version == "~> 7.0"

    def test_dep_without_version(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        debug = next(
            (d for d in result.dependencies if d.name == "debug"), None,
        )
        assert debug is not None
        assert debug.version == ""

    def test_total_dep_count(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(FIXTURES / "Gemfile")
        assert len(result.dependencies) >= 9


# endregion: --- Gemfile manifest parsing


# ---------------------------------------------------------------------------
# region:    --- Post-pass: self_methods
# ---------------------------------------------------------------------------


class TestSelfMethods:
    """self_methods populated from functions + impl_blocks."""

    def test_self_methods_populated(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        method_names = [m.name for m in result.self_methods]
        assert "greet" in method_names
        assert "sum" in method_names

    def test_class_methods_in_self_methods(self, ext: RubyExtractor) -> None:
        result = _extract(ext, "methods.rb")
        method_names = [m.name for m in result.self_methods]
        assert "add" in method_names
        assert "description" in method_names


# endregion: --- Post-pass: self_methods


# ---------------------------------------------------------------------------
# region:    --- build_import_map
# ---------------------------------------------------------------------------


class TestBuildImportMap:
    """Test the import map builder."""

    def test_simple_require(self) -> None:
        m = build_import_map(['require "json"'])
        assert "json" in m

    def test_nested_require(self) -> None:
        m = build_import_map(['require "net/http"'])
        assert "http" in m
        assert m["http"] == "net/http"

    def test_require_relative(self) -> None:
        m = build_import_map(['require_relative "./helpers"'])
        assert "helpers" in m

    def test_empty_list(self) -> None:
        m = build_import_map([])
        assert len(m) == 0


# endregion: --- build_import_map


# ---------------------------------------------------------------------------
# region:    --- Empty / invalid inputs
# ---------------------------------------------------------------------------


class TestEmptyInputs:
    """Edge cases: empty file, invalid syntax."""

    def test_empty_file(self, ext: RubyExtractor) -> None:
        result = ext.extract(Path("empty.rb"), b"")
        assert len(result.structs) == 0
        assert len(result.functions) == 0

    def test_comment_only(self, ext: RubyExtractor) -> None:
        result = ext.extract(Path("comment.rb"), b"# just a comment\n")
        assert len(result.structs) == 0
        assert len(result.functions) == 0

    def test_nonexistent_gemfile(self, ext: RubyExtractor) -> None:
        result = ext.parse_manifest(Path("/nonexistent/Gemfile"))
        assert result.language == "ruby"
        assert len(result.dependencies) == 0


# endregion: --- Empty / invalid inputs
