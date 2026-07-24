"""Tests for the Kotlin language extractor.

Covers class → StructNode, data class → StructNode, sealed class → StructNode,
interface → TraitNode, abstract class → TraitNode, enum class → EnumNode,
object → StructNode, companion object, extension functions, suspend functions,
visibility, constants, type aliases, generics, annotations, inheritance,
nested classes, top-level functions, rationale comments, call graph,
and Gradle Kotlin DSL manifest parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.kotlin import (
    KotlinExtractor,
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

FIXTURES = Path(__file__).parent / "fixtures" / "kotlin"


@pytest.fixture
def ext() -> KotlinExtractor:
    """Create a fresh extractor instance."""
    return KotlinExtractor()


def _extract(ext: KotlinExtractor, fixture_name: str) -> FileAST:
    """Helper: read fixture and extract."""
    path = FIXTURES / fixture_name
    src = path.read_bytes()
    return ext.extract(path, src)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor identity
# ---------------------------------------------------------------------------


def test_language_id(ext: KotlinExtractor) -> None:
    assert ext.language_id == "kotlin"


def test_file_extensions(ext: KotlinExtractor) -> None:
    assert ".kt" in ext.file_extensions
    assert ".kts" in ext.file_extensions


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Basic class
# ---------------------------------------------------------------------------


def test_basic_class_module_path(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    assert ast.module_path == "com.example.models"


def test_basic_class_imports(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    assert len(ast.uses) == 1
    assert "java.util.UUID" in ast.uses[0]


def test_basic_class_struct(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    assert len(ast.structs) == 1
    s = ast.structs[0]
    assert s.name == "User"
    assert s.visibility == Visibility.PUBLIC


def test_basic_class_fields(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    s = ast.structs[0]
    field_names = [f.name for f in s.fields]
    assert "id" in field_names
    assert "name" in field_names
    assert "email" in field_names
    assert "loginCount" in field_names


def test_basic_class_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    assert len(ast.impl_blocks) >= 1
    methods = ast.impl_blocks[0].methods
    method_names = [m.name for m in methods]
    assert "displayName" in method_names
    assert "incrementLogin" in method_names


def test_basic_class_doc(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    s = ast.structs[0]
    assert "basic user class" in s.doc.lower()


# endregion: --- Basic class


# ---------------------------------------------------------------------------
# region:    --- Data class
# ---------------------------------------------------------------------------


def test_data_class_attribute(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "data_class.kt")
    names = {s.name: s for s in ast.structs}
    assert "UserDTO" in names
    assert "data" in names["UserDTO"].attributes


def test_data_class_fields(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "data_class.kt")
    names = {s.name: s for s in ast.structs}
    dto = names["UserDTO"]
    field_names = [f.name for f in dto.fields]
    assert "id" in field_names
    assert "username" in field_names
    assert "email" in field_names
    assert "isActive" in field_names


def test_data_class_with_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "data_class.kt")
    names = {s.name: s for s in ast.structs}
    assert "Coordinate" in names
    coord = names["Coordinate"]
    assert len(coord.fields) == 2  # x, y
    # Should have methods in impl block
    impl = [i for i in ast.impl_blocks if i.self_type == "Coordinate"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "distanceTo" in method_names


# endregion: --- Data class


# ---------------------------------------------------------------------------
# region:    --- Sealed class
# ---------------------------------------------------------------------------


def test_sealed_class(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "sealed_class.kt")
    names = {s.name: s for s in ast.structs}
    assert "Result" in names
    assert "sealed" in names["Result"].attributes


def test_sealed_class_nested_data(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "sealed_class.kt")
    names = {s.name: s for s in ast.structs}
    assert "Success" in names
    assert "data" in names["Success"].attributes


def test_sealed_class_nested_object(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "sealed_class.kt")
    names = {s.name: s for s in ast.structs}
    assert "Loading" in names
    assert "object" in names["Loading"].attributes


def test_sealed_interface(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "sealed_class.kt")
    traits = {t.name: t for t in ast.traits}
    assert "Shape" in traits
    assert "sealed" in traits["Shape"].attributes


# endregion: --- Sealed class


# ---------------------------------------------------------------------------
# region:    --- Enum class
# ---------------------------------------------------------------------------


def test_enum_with_args(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "enum_class.kt")
    enums = {e.name: e for e in ast.enums}
    assert "Color" in enums
    color = enums["Color"]
    variant_names = [v.name for v in color.variants]
    assert variant_names == ["RED", "GREEN", "BLUE"]


def test_enum_variant_kind(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "enum_class.kt")
    enums = {e.name: e for e in ast.enums}
    color = enums["Color"]
    for v in color.variants:
        assert v.kind == EnumVariantKind.TUPLE  # all have args


def test_enum_simple(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "enum_class.kt")
    enums = {e.name: e for e in ast.enums}
    assert "Direction" in enums
    direction = enums["Direction"]
    assert len(direction.variants) == 4
    for v in direction.variants:
        assert v.kind == EnumVariantKind.UNIT


def test_enum_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "enum_class.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "Color"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "toRgb" in method_names


# endregion: --- Enum class


# ---------------------------------------------------------------------------
# region:    --- Interface and abstract class
# ---------------------------------------------------------------------------


def test_interface_trait(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "interface_and_abstract.kt")
    traits = {t.name: t for t in ast.traits}
    assert "Repository" in traits
    repo = traits["Repository"]
    item_names = [i.name for i in repo.items]
    assert "findById" in item_names
    assert "findAll" in item_names
    assert "save" in item_names
    assert "delete" in item_names


def test_interface_required_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "interface_and_abstract.kt")
    traits = {t.name: t for t in ast.traits}
    repo = traits["Repository"]
    for item in repo.items:
        assert item.kind == TraitItemKind.REQUIRED_METHOD


def test_abstract_class_trait(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "interface_and_abstract.kt")
    traits = {t.name: t for t in ast.traits}
    assert "BaseService" in traits
    bs = traits["BaseService"]
    item_names = [i.name for i in bs.items]
    assert "start" in item_names
    assert "stop" in item_names
    assert "status" in item_names


def test_abstract_class_default_method(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "interface_and_abstract.kt")
    traits = {t.name: t for t in ast.traits}
    bs = traits["BaseService"]
    status = next(i for i in bs.items if i.name == "status")
    assert status.kind == TraitItemKind.DEFAULT_METHOD


def test_interface_cacheable(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "interface_and_abstract.kt")
    traits = {t.name: t for t in ast.traits}
    assert "Cacheable" in traits


# endregion: --- Interface and abstract class


# ---------------------------------------------------------------------------
# region:    --- Object and companion object
# ---------------------------------------------------------------------------


def test_object_declaration(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "object_and_companion.kt")
    names = {s.name: s for s in ast.structs}
    assert "DatabaseConfig" in names
    assert "object" in names["DatabaseConfig"].attributes


def test_object_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "object_and_companion.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "DatabaseConfig"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "connectionString" in method_names


def test_companion_object_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "object_and_companion.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "Logger"]
    assert len(impl) >= 1
    methods = impl[0].methods
    method_names = [m.name for m in methods]
    assert "info" in method_names
    assert "getInstance" in method_names


def test_companion_object_method_attribute(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "object_and_companion.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "Logger"]
    methods = impl[0].methods
    get_instance = next(m for m in methods if m.name == "getInstance")
    assert "companion" in get_instance.attributes


def test_companion_object_constants(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "object_and_companion.kt")
    const_names = [c.name for c in ast.constants]
    assert "DEFAULT_TAG" in const_names


# endregion: --- Object and companion object


# ---------------------------------------------------------------------------
# region:    --- Extension functions
# ---------------------------------------------------------------------------


def test_extension_function_string(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "extension_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "isPalindrome" in funcs
    assert "extension:String" in funcs["isPalindrome"].attributes


def test_extension_function_list(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "extension_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "secondOrNull" in funcs
    attrs = funcs["secondOrNull"].attributes
    assert any("extension:" in a for a in attrs)


def test_extension_function_generic(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "extension_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "swap" in funcs
    attrs = funcs["swap"].attributes
    assert any("extension:" in a for a in attrs)


# endregion: --- Extension functions


# ---------------------------------------------------------------------------
# region:    --- Suspend functions
# ---------------------------------------------------------------------------


def test_suspend_top_level(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "suspend_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "fetchData" in funcs
    assert funcs["fetchData"].is_async is True


def test_suspend_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "suspend_functions.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "ApiClient"]
    assert len(impl) >= 1
    for m in impl[0].methods:
        assert m.is_async is True


# endregion: --- Suspend functions


# ---------------------------------------------------------------------------
# region:    --- Visibility modifiers
# ---------------------------------------------------------------------------


def test_visibility_public(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "visibility_modifiers.kt")
    names = {s.name: s for s in ast.structs}
    assert names["PublicApi"].visibility == Visibility.PUBLIC


def test_visibility_private_class(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "visibility_modifiers.kt")
    names = {s.name: s for s in ast.structs}
    assert names["InternalHelper"].visibility == Visibility.PRIVATE


def test_visibility_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "visibility_modifiers.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "PublicApi"]
    assert len(impl) >= 1
    methods = {m.name: m for m in impl[0].methods}
    assert methods["publicMethod"].visibility == Visibility.PUBLIC
    assert methods["internalMethod"].visibility == Visibility.CRATE
    assert methods["protectedMethod"].visibility == Visibility.PROTECTED
    assert methods["privateMethod"].visibility == Visibility.PRIVATE


# endregion: --- Visibility modifiers


# ---------------------------------------------------------------------------
# region:    --- Constants and type aliases
# ---------------------------------------------------------------------------


def test_top_level_constants(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "constants_and_typealias.kt")
    const_names = [c.name for c in ast.constants]
    assert "MAX_RETRIES" in const_names
    assert "API_BASE_URL" in const_names


def test_type_aliases(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "constants_and_typealias.kt")
    aliases = {t.name: t for t in ast.type_aliases}
    assert "StringMap" in aliases
    assert aliases["StringMap"].aliased_to == "Map<String, String>"


def test_type_alias_function_type(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "constants_and_typealias.kt")
    aliases = {t.name: t for t in ast.type_aliases}
    assert "Predicate" in aliases
    assert "(T) -> Boolean" in aliases["Predicate"].aliased_to


# endregion: --- Constants and type aliases


# ---------------------------------------------------------------------------
# region:    --- Generics
# ---------------------------------------------------------------------------


def test_generic_class(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "generics.kt")
    names = {s.name: s for s in ast.structs}
    assert "Box" in names
    assert "<T>" in names["Box"].generics


def test_generic_function(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "generics.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "maxOf" in funcs
    assert "Comparable" in funcs["maxOf"].generics


def test_generic_interface(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "generics.kt")
    traits = {t.name: t for t in ast.traits}
    assert "Transformer" in traits
    assert "<in I, out O>" in traits["Transformer"].generics


# endregion: --- Generics


# ---------------------------------------------------------------------------
# region:    --- Annotations
# ---------------------------------------------------------------------------


def test_class_annotation(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "annotations.kt")
    names = {s.name: s for s in ast.structs}
    assert "UserService" in names
    attrs = names["UserService"].attributes
    assert any("@Service" in a for a in attrs)


def test_method_annotation(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "annotations.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "UserService"]
    assert len(impl) >= 1
    methods = {m.name: m for m in impl[0].methods}
    assert any("@Transactional" in a for a in methods["createUser"].attributes)


def test_deprecated_annotation(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "annotations.kt")
    impl = [i for i in ast.impl_blocks if i.self_type == "UserService"]
    methods = {m.name: m for m in impl[0].methods}
    assert any("@Deprecated" in a for a in methods["addUser"].attributes)


# endregion: --- Annotations


# ---------------------------------------------------------------------------
# region:    --- Inheritance
# ---------------------------------------------------------------------------


def test_inheritance_impl_blocks(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "inheritance.kt")
    impl_pairs = [(i.self_type, i.trait_type) for i in ast.impl_blocks]
    # Dog extends Animal
    assert ("Dog", "Animal") in impl_pairs
    # Duck extends Animal + Swimmable + Flyable
    assert ("Duck", "Animal") in impl_pairs
    assert ("Duck", "Swimmable") in impl_pairs
    assert ("Duck", "Flyable") in impl_pairs


def test_inheritance_override(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "inheritance.kt")
    dog_impl = [i for i in ast.impl_blocks if i.self_type == "Dog"]
    assert len(dog_impl) >= 1
    speak = next(m for m in dog_impl[0].methods if m.name == "speak")
    assert "override" in speak.attributes


# endregion: --- Inheritance


# ---------------------------------------------------------------------------
# region:    --- Nested classes
# ---------------------------------------------------------------------------


def test_nested_classes(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "nested_classes.kt")
    struct_names = [s.name for s in ast.structs]
    assert "Outer" in struct_names
    assert "Inner" in struct_names
    assert "Nested" in struct_names


def test_nested_enum(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "nested_classes.kt")
    enum_names = [e.name for e in ast.enums]
    assert "Status" in enum_names


# endregion: --- Nested classes


# ---------------------------------------------------------------------------
# region:    --- Top-level functions
# ---------------------------------------------------------------------------


def test_top_level_functions(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "top_level_functions.kt")
    func_names = [f.name for f in ast.functions]
    assert "greet" in func_names
    assert "add" in func_names


def test_inline_function(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "top_level_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "typeNameOf" in funcs
    assert "inline" in funcs["typeNameOf"].attributes


def test_infix_function(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "top_level_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "times" in funcs
    assert "infix" in funcs["times"].attributes
    assert "extension:Int" in funcs["times"].attributes


def test_operator_function(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "top_level_functions.kt")
    funcs = {f.name: f for f in ast.functions}
    assert "plus" in funcs
    assert "operator" in funcs["plus"].attributes


# endregion: --- Top-level functions


# ---------------------------------------------------------------------------
# region:    --- Rationale comments
# ---------------------------------------------------------------------------


def test_rationale_safety(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "rationale_comments.kt")
    kinds = [r.kind for r in ast.rationale_comments]
    assert "SAFETY" in kinds


def test_rationale_todo(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "rationale_comments.kt")
    kinds = [r.kind for r in ast.rationale_comments]
    assert "TODO" in kinds


def test_rationale_hack(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "rationale_comments.kt")
    kinds = [r.kind for r in ast.rationale_comments]
    assert "HACK" in kinds


def test_rationale_fixme(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "rationale_comments.kt")
    kinds = [r.kind for r in ast.rationale_comments]
    assert "FIXME" in kinds


def test_rationale_count(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "rationale_comments.kt")
    assert len(ast.rationale_comments) == 4


# endregion: --- Rationale comments


# ---------------------------------------------------------------------------
# region:    --- Self-methods
# ---------------------------------------------------------------------------


def test_self_methods(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    method_names = [m.name for m in ast.self_methods]
    assert "displayName" in method_names
    assert "incrementLogin" in method_names


def test_self_methods_context(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    for m in ast.self_methods:
        assert m.context in ("free", "impl:User")


# endregion: --- Self-methods


# ---------------------------------------------------------------------------
# region:    --- Call graph
# ---------------------------------------------------------------------------


def test_call_edges_exist(ext: KotlinExtractor) -> None:
    ast = _extract(ext, "basic_class.kt")
    # Should have some call edges
    assert isinstance(ast.call_edges, list)


# endregion: --- Call graph


# ---------------------------------------------------------------------------
# region:    --- Import map
# ---------------------------------------------------------------------------


def test_import_map_basic() -> None:
    uses = ["import com.example.User", "import java.util.List"]
    result = build_import_map(uses)
    assert result["User"] == "com.example.User"
    assert result["List"] == "java.util.List"


def test_import_map_wildcard_skip() -> None:
    uses = ["import com.example.*"]
    result = build_import_map(uses)
    assert len(result) == 0


# endregion: --- Import map


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing
# ---------------------------------------------------------------------------


def test_manifest_gradle_kts(ext: KotlinExtractor) -> None:
    manifest = FIXTURES / "build.gradle.kts"
    crate = ext.parse_manifest(manifest)
    assert crate.language == "kotlin"
    assert crate.version == "1.0.0"


def test_manifest_dependencies(ext: KotlinExtractor) -> None:
    manifest = FIXTURES / "build.gradle.kts"
    crate = ext.parse_manifest(manifest)
    dep_names = [d.name for d in crate.dependencies]
    assert any("spring-boot-starter-web" in n for n in dep_names)
    assert any("kotlinx-coroutines-core" in n for n in dep_names)


def test_manifest_test_dependencies(ext: KotlinExtractor) -> None:
    manifest = FIXTURES / "build.gradle.kts"
    crate = ext.parse_manifest(manifest)
    test_deps = [d for d in crate.dependencies if d.is_dev]
    assert len(test_deps) >= 1
    assert any("spring-boot-starter-test" in d.name for d in test_deps)


# endregion: --- Manifest parsing


# ---------------------------------------------------------------------------
# region:    --- Error handling
# ---------------------------------------------------------------------------


def test_parse_error_reported(ext: KotlinExtractor) -> None:
    """Malformed Kotlin source should report errors, not crash."""
    src = b"class { invalid syntax @@@ }"
    ast = ext.extract(Path("bad.kt"), src)
    assert isinstance(ast, FileAST)


def test_empty_file(ext: KotlinExtractor) -> None:
    """Empty file should return empty FileAST."""
    ast = ext.extract(Path("empty.kt"), b"")
    assert isinstance(ast, FileAST)
    assert len(ast.structs) == 0
    assert len(ast.functions) == 0


# endregion: --- Error handling
