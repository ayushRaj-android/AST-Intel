"""Tests for the Scala language extractor.

Covers class → StructNode, case class → StructNode, trait → TraitNode,
abstract class → TraitNode, sealed trait → TraitNode, enum → EnumNode,
object → StructNode, companion object, extension methods, visibility,
constants, type aliases, generics, annotations, inheritance, nested classes,
top-level functions, rationale comments, value classes, multi-parameter lists,
package objects, implicit/using params, and build.sbt manifest parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.scala import (
    ScalaExtractor,
    build_import_map,
)
from ast_intel.models.ast_node import (
    FileAST,
    TraitItemKind,
    Visibility,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "scala"


@pytest.fixture
def ext() -> ScalaExtractor:
    """Create a fresh extractor instance."""
    return ScalaExtractor()


def _extract(ext: ScalaExtractor, fixture_name: str) -> FileAST:
    """Helper: read fixture and extract."""
    path = FIXTURES / fixture_name
    src = path.read_bytes()
    return ext.extract(path, src)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor identity
# ---------------------------------------------------------------------------


def test_language_id(ext: ScalaExtractor) -> None:
    assert ext.language_id == "scala"


def test_file_extensions(ext: ScalaExtractor) -> None:
    assert ".scala" in ext.file_extensions
    assert ".sc" in ext.file_extensions


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Basic class
# ---------------------------------------------------------------------------


def test_basic_class_module_path(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    assert ast.module_path == "com.example.models"


def test_basic_class_imports(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    assert len(ast.uses) == 1
    assert "java.util.UUID" in ast.uses[0]


def test_basic_class_struct(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    assert len(ast.structs) == 1
    s = ast.structs[0]
    assert s.name == "User"
    assert s.visibility == Visibility.PUBLIC


def test_basic_class_fields(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    s = ast.structs[0]
    field_names = [f.name for f in s.fields]
    assert "id" in field_names
    assert "name" in field_names
    # "email" has no val/var, so it's just a constructor param, not a field


def test_basic_class_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    assert len(ast.impl_blocks) >= 1
    methods = ast.impl_blocks[0].methods
    method_names = [m.name for m in methods]
    assert "greet" in method_names
    assert "validate" in method_names
    assert "toString" in method_names


def test_basic_class_doc(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "basic_class.scala")
    s = ast.structs[0]
    assert "basic user class" in s.doc.lower()


# endregion: --- Basic class


# ---------------------------------------------------------------------------
# region:    --- Case class
# ---------------------------------------------------------------------------


def test_case_class_attribute(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "case_class.scala")
    names = {s.name: s for s in ast.structs}
    assert "Point" in names
    assert "case" in names["Point"].attributes


def test_case_class_fields(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "case_class.scala")
    names = {s.name: s for s in ast.structs}
    pt = names["Point"]
    field_names = [f.name for f in pt.fields]
    assert "x" in field_names
    assert "y" in field_names


def test_case_class_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "case_class.scala")
    impl = [i for i in ast.impl_blocks if i.self_type == "Point"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "distance" in method_names


def test_case_class_doc(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "case_class.scala")
    names = {s.name: s for s in ast.structs}
    assert "2d point" in names["Point"].doc.lower()


def test_case_class_config(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "case_class.scala")
    names = {s.name: s for s in ast.structs}
    assert "Config" in names
    assert "case" in names["Config"].attributes
    field_names = [f.name for f in names["Config"].fields]
    assert "host" in field_names
    assert "port" in field_names
    assert "debug" in field_names


# endregion: --- Case class


# ---------------------------------------------------------------------------
# region:    --- Trait
# ---------------------------------------------------------------------------


def test_trait_basic(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "trait_basic.scala")
    assert len(ast.traits) == 2
    names = {t.name: t for t in ast.traits}
    assert "Drawable" in names
    assert "Resizable" in names


def test_trait_required_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "trait_basic.scala")
    names = {t.name: t for t in ast.traits}
    drawable = names["Drawable"]
    item_names = [i.name for i in drawable.items]
    assert "draw" in item_names
    # draw() has no body → REQUIRED_METHOD
    draw = next(i for i in drawable.items if i.name == "draw")
    assert draw.kind == TraitItemKind.REQUIRED_METHOD


def test_trait_default_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "trait_basic.scala")
    names = {t.name: t for t in ast.traits}
    drawable = names["Drawable"]
    color = next(i for i in drawable.items if i.name == "color")
    assert color.kind == TraitItemKind.DEFAULT_METHOD


# endregion: --- Trait


# ---------------------------------------------------------------------------
# region:    --- Sealed trait
# ---------------------------------------------------------------------------


def test_sealed_trait(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "sealed_trait.scala")
    traits = {t.name: t for t in ast.traits}
    assert "Shape" in traits
    assert "sealed" in traits["Shape"].attributes


def test_sealed_trait_case_class(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "sealed_trait.scala")
    names = {s.name: s for s in ast.structs}
    assert "Circle" in names
    assert "case" in names["Circle"].attributes
    assert "Rectangle" in names
    assert "case" in names["Rectangle"].attributes


def test_sealed_trait_case_object(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "sealed_trait.scala")
    names = {s.name: s for s in ast.structs}
    assert "Empty" in names
    assert "case" in names["Empty"].attributes
    assert "object" in names["Empty"].attributes


def test_sealed_trait_inheritance(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "sealed_trait.scala")
    impls = {i.self_type: i for i in ast.impl_blocks}
    assert "Circle" in impls
    assert impls["Circle"].trait_type == "Shape"


# endregion: --- Sealed trait


# ---------------------------------------------------------------------------
# region:    --- Enum (Scala 3)
# ---------------------------------------------------------------------------


def test_enum_basic(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "scala3_enum.scala")
    assert len(ast.enums) == 2
    names = {e.name: e for e in ast.enums}
    assert "Color" in names
    assert "Planet" in names


def test_enum_unit_variants(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "scala3_enum.scala")
    names = {e.name: e for e in ast.enums}
    color = names["Color"]
    variant_names = [v.name for v in color.variants]
    assert "Red" in variant_names
    assert "Green" in variant_names
    assert "Blue" in variant_names


def test_enum_parameterized_variants(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "scala3_enum.scala")
    names = {e.name: e for e in ast.enums}
    color = names["Color"]
    variant_names = [v.name for v in color.variants]
    assert "Custom" in variant_names
    assert "Gradient" in variant_names


def test_enum_with_constructor(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "scala3_enum.scala")
    names = {e.name: e for e in ast.enums}
    planet = names["Planet"]
    variant_names = [v.name for v in planet.variants]
    assert "Mercury" in variant_names
    assert "Venus" in variant_names
    assert "Earth" in variant_names


# endregion: --- Enum (Scala 3)


# ---------------------------------------------------------------------------
# region:    --- Object (singleton)
# ---------------------------------------------------------------------------


def test_object_singleton(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "object_singleton.scala")
    names = {s.name: s for s in ast.structs}
    assert "MathUtils" in names
    assert "object" in names["MathUtils"].attributes


def test_object_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "object_singleton.scala")
    impl = [i for i in ast.impl_blocks if i.self_type == "MathUtils"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "add" in method_names
    assert "multiply" in method_names


def test_object_constants(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "object_singleton.scala")
    const_names = [c.name for c in ast.constants]
    assert "PI" in const_names
    assert "E" in const_names


def test_object_private_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "object_singleton.scala")
    impl = [i for i in ast.impl_blocks if i.self_type == "MathUtils"]
    assert len(impl) >= 1
    helper = [m for m in impl[0].methods if m.name == "helper"]
    assert len(helper) == 1
    assert helper[0].visibility == Visibility.PRIVATE


# endregion: --- Object (singleton)


# ---------------------------------------------------------------------------
# region:    --- Companion object
# ---------------------------------------------------------------------------


def test_companion_detected(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "companion_object.scala")
    # Should have Person class and Person object
    persons = [s for s in ast.structs if s.name == "Person"]
    assert len(persons) == 2
    obj = next(s for s in persons if "object" in s.attributes)
    assert "companion" in obj.attributes


def test_companion_object_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "companion_object.scala")
    impl = [i for i in ast.impl_blocks if i.self_type == "Person"]
    assert len(impl) >= 1
    all_methods = []
    for i in impl:
        all_methods.extend(m.name for m in i.methods)
    assert "apply" in all_methods
    assert "fromMap" in all_methods


# endregion: --- Companion object


# ---------------------------------------------------------------------------
# region:    --- Abstract class
# ---------------------------------------------------------------------------


def test_abstract_class_as_trait(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "abstract_class.scala")
    traits = {t.name: t for t in ast.traits}
    assert "Animal" in traits
    assert "abstract" in traits["Animal"].attributes


def test_abstract_class_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "abstract_class.scala")
    traits = {t.name: t for t in ast.traits}
    animal = traits["Animal"]
    item_names = [i.name for i in animal.items]
    assert "speak" in item_names
    assert "describe" in item_names


def test_abstract_required_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "abstract_class.scala")
    traits = {t.name: t for t in ast.traits}
    animal = traits["Animal"]
    speak = next(i for i in animal.items if i.name == "speak")
    assert speak.kind == TraitItemKind.REQUIRED_METHOD


def test_abstract_default_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "abstract_class.scala")
    traits = {t.name: t for t in ast.traits}
    animal = traits["Animal"]
    describe = next(i for i in animal.items if i.name == "describe")
    assert describe.kind == TraitItemKind.DEFAULT_METHOD


def test_abstract_class_doc(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "abstract_class.scala")
    traits = {t.name: t for t in ast.traits}
    assert "abstract animal" in traits["Animal"].doc.lower()


# endregion: --- Abstract class


# ---------------------------------------------------------------------------
# region:    --- Visibility
# ---------------------------------------------------------------------------


def test_visibility_public_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "visibility.scala")
    impl = ast.impl_blocks[0]
    pub = next(m for m in impl.methods if m.name == "publicMethod")
    assert pub.visibility == Visibility.PUBLIC


def test_visibility_private_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "visibility.scala")
    impl = ast.impl_blocks[0]
    priv = next(m for m in impl.methods if m.name == "privateMethod")
    assert priv.visibility == Visibility.PRIVATE


def test_visibility_protected_method(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "visibility.scala")
    impl = ast.impl_blocks[0]
    prot = next(m for m in impl.methods if m.name == "protectedMethod")
    assert prot.visibility == Visibility.PROTECTED


# endregion: --- Visibility


# ---------------------------------------------------------------------------
# region:    --- Generics
# ---------------------------------------------------------------------------


def test_generics_class(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "generics.scala")
    names = {s.name: s for s in ast.structs}
    assert "Container" in names
    assert names["Container"].generics != ""


def test_generics_trait(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "generics.scala")
    traits = {t.name: t for t in ast.traits}
    assert "Comparable" in traits
    assert traits["Comparable"].generics != ""


def test_generics_multi_param(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "generics.scala")
    names = {s.name: s for s in ast.structs}
    assert "Pair" in names
    assert "A" in names["Pair"].generics
    assert "B" in names["Pair"].generics


# endregion: --- Generics


# ---------------------------------------------------------------------------
# region:    --- Imports
# ---------------------------------------------------------------------------


def test_imports(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "imports.scala")
    assert len(ast.uses) >= 4


def test_imports_uuid(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "imports.scala")
    assert any("java.util.UUID" in u for u in ast.uses)


def test_imports_wildcard(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "imports.scala")
    assert any("scala.concurrent" in u for u in ast.uses)


# endregion: --- Imports


# ---------------------------------------------------------------------------
# region:    --- Type aliases
# ---------------------------------------------------------------------------


def test_type_aliases(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "type_alias.scala")
    assert len(ast.type_aliases) >= 3
    ta_names = [ta.name for ta in ast.type_aliases]
    assert "StringList" in ta_names
    assert "Predicate" in ta_names
    assert "Callback" in ta_names


# endregion: --- Type aliases


# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------


def test_constants(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "constants.scala")
    const_names = [c.name for c in ast.constants]
    assert "MAX_SIZE" in const_names
    assert "APP_NAME" in const_names
    assert "VERSION" in const_names


def test_constants_raw(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "constants.scala")
    max_size = next(c for c in ast.constants if c.name == "MAX_SIZE")
    assert "100" in max_size.raw


# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Inheritance
# ---------------------------------------------------------------------------


def test_inheritance_extends_with(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "inheritance.scala")
    impls = [i for i in ast.impl_blocks if i.self_type == "Service"]
    trait_types = {i.trait_type for i in impls}
    assert "Serializable" in trait_types
    assert "Logging" in trait_types
    assert "Drawable" in trait_types


def test_inheritance_abstract_base(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "inheritance.scala")
    impls = [i for i in ast.impl_blocks if i.self_type == "Child"]
    trait_types = {i.trait_type for i in impls}
    assert "Base" in trait_types
    assert "Logging" in trait_types


# endregion: --- Inheritance


# ---------------------------------------------------------------------------
# region:    --- Annotations
# ---------------------------------------------------------------------------


def test_annotations(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "annotations.scala")
    names = {s.name: s for s in ast.structs}
    assert "OldClass" in names
    assert "@deprecated" in names["OldClass"].attributes


def test_annotation_serializable(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "annotations.scala")
    names = {s.name: s for s in ast.structs}
    assert "Versioned" in names
    assert "@SerialVersionUID" in names["Versioned"].attributes


# endregion: --- Annotations


# ---------------------------------------------------------------------------
# region:    --- Top-level definitions
# ---------------------------------------------------------------------------


def test_top_level_functions(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "top_level_defs.scala")
    fn_names = [f.name for f in ast.functions]
    assert "add" in fn_names
    assert "greet" in fn_names


def test_top_level_constant(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "top_level_defs.scala")
    const_names = [c.name for c in ast.constants]
    assert "DefaultTimeout" in const_names


# endregion: --- Top-level definitions


# ---------------------------------------------------------------------------
# region:    --- Extension methods
# ---------------------------------------------------------------------------


def test_extension_methods(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "extension_methods.scala")
    assert len(ast.impl_blocks) >= 2
    # String extensions (may be split across multiple impl_blocks)
    string_impl = [i for i in ast.impl_blocks if i.self_type == "String"]
    assert len(string_impl) >= 1
    all_method_names = [
        m.name for i in string_impl for m in i.methods
    ]
    assert "greetWith" in all_method_names
    assert "shout" in all_method_names


def test_extension_method_attribute(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "extension_methods.scala")
    string_impl = [i for i in ast.impl_blocks if i.self_type == "String"]
    for impl in string_impl:
        for method in impl.methods:
            assert "extension" in method.attributes


def test_extension_int(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "extension_methods.scala")
    int_impl = [i for i in ast.impl_blocks if i.self_type == "Int"]
    assert len(int_impl) >= 1
    method_names = [m.name for m in int_impl[0].methods]
    assert "isEven" in method_names


# endregion: --- Extension methods


# ---------------------------------------------------------------------------
# region:    --- Package object
# ---------------------------------------------------------------------------


def test_package_object(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "package_object.scala")
    # Package objects produce a StructNode
    names = {s.name: s for s in ast.structs}
    assert "utils" in names


def test_package_object_type_alias(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "package_object.scala")
    ta_names = [ta.name for ta in ast.type_aliases]
    assert "Predicate" in ta_names


def test_package_object_constant(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "package_object.scala")
    const_names = [c.name for c in ast.constants]
    assert "DefaultTimeout" in const_names


def test_package_object_function(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "package_object.scala")
    fn_names = [f.name for f in ast.functions]
    assert "identity" in fn_names


# endregion: --- Package object


# ---------------------------------------------------------------------------
# region:    --- Implicit / using parameters
# ---------------------------------------------------------------------------


def test_implicit_params(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "implicit_params.scala")
    fn_names = [f.name for f in ast.functions]
    assert "sorted" in fn_names
    assert "process" in fn_names


def test_given_definition(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "implicit_params.scala")
    const_names = [c.name for c in ast.constants]
    assert "intOrdering" in const_names


# endregion: --- Implicit / using parameters


# ---------------------------------------------------------------------------
# region:    --- Multi-parameter lists
# ---------------------------------------------------------------------------


def test_multi_param_lists(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "multi_param_lists.scala")
    fn_names = [f.name for f in ast.functions]
    assert "fold" in fn_names
    assert "configure" in fn_names


def test_multi_param_list_params(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "multi_param_lists.scala")
    configure = next(f for f in ast.functions if f.name == "configure")
    param_names = [p.name for p in configure.params]
    assert "host" in param_names
    assert "port" in param_names
    assert "debug" in param_names


# endregion: --- Multi-parameter lists


# ---------------------------------------------------------------------------
# region:    --- Value class
# ---------------------------------------------------------------------------


def test_value_class(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "value_class.scala")
    names = {s.name: s for s in ast.structs}
    assert "Meter" in names
    field_names = [f.name for f in names["Meter"].fields]
    assert "value" in field_names


# endregion: --- Value class


# ---------------------------------------------------------------------------
# region:    --- Nested classes
# ---------------------------------------------------------------------------


def test_nested_classes(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "nested_classes.scala")
    names = {s.name for s in ast.structs}
    assert "Outer" in names
    assert "Container" in names
    # Nested classes should be found via _walk_body_declarations
    assert "Inner" in names
    assert "InnerCompanion" in names


# endregion: --- Nested classes


# ---------------------------------------------------------------------------
# region:    --- Rationale comments
# ---------------------------------------------------------------------------


def test_rationale_comments(ext: ScalaExtractor) -> None:
    ast = _extract(ext, "rationale_comments.scala")
    assert len(ast.rationale_comments) >= 1
    texts = [r.text for r in ast.rationale_comments]
    combined = " ".join(texts).lower()
    assert "precision" in combined or "division" in combined or "logging" in combined


# endregion: --- Rationale comments


# ---------------------------------------------------------------------------
# region:    --- Import map
# ---------------------------------------------------------------------------


def test_import_map_basic() -> None:
    uses = ["import java.util.UUID", "import scala.collection.mutable.ListBuffer"]
    result = build_import_map(uses)
    assert result["UUID"] == "java.util.UUID"
    assert result["ListBuffer"] == "scala.collection.mutable.ListBuffer"


def test_import_map_wildcard_skip() -> None:
    uses = ["import scala.concurrent._"]
    result = build_import_map(uses)
    assert len(result) == 0


# endregion: --- Import map


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing (build.sbt)
# ---------------------------------------------------------------------------


def test_manifest_build_sbt(ext: ScalaExtractor) -> None:
    manifest = FIXTURES / "build.sbt"
    crate = ext.parse_manifest(manifest)
    assert crate.language == "scala"
    assert crate.name == "my-scala-app"
    assert crate.version == "1.0.0"


def test_manifest_dependencies(ext: ScalaExtractor) -> None:
    manifest = FIXTURES / "build.sbt"
    crate = ext.parse_manifest(manifest)
    dep_names = [d.name for d in crate.dependencies]
    assert any("cats-core" in n for n in dep_names)
    assert any("akka-actor" in n for n in dep_names)
    assert any("circe-core" in n for n in dep_names)


def test_manifest_test_dependencies(ext: ScalaExtractor) -> None:
    manifest = FIXTURES / "build.sbt"
    crate = ext.parse_manifest(manifest)
    test_deps = [d for d in crate.dependencies if d.is_dev]
    assert len(test_deps) >= 1
    assert any("scalatest" in d.name for d in test_deps)


# endregion: --- Manifest parsing (build.sbt)


# ---------------------------------------------------------------------------
# region:    --- Error handling
# ---------------------------------------------------------------------------


def test_parse_error_reported(ext: ScalaExtractor) -> None:
    """Malformed Scala source should report errors, not crash."""
    src = b"class { invalid syntax @@@ }"
    ast = ext.extract(Path("bad.scala"), src)
    assert isinstance(ast, FileAST)


def test_empty_file(ext: ScalaExtractor) -> None:
    """Empty file should return empty FileAST."""
    ast = ext.extract(Path("empty.scala"), b"")
    assert isinstance(ast, FileAST)
    assert len(ast.structs) == 0
    assert len(ast.functions) == 0


# endregion: --- Error handling
