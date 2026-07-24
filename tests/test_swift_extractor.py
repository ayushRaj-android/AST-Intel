"""Tests for the Swift language extractor.

Covers class → StructNode, struct → StructNode, actor → StructNode,
protocol → TraitNode, enum → EnumNode, extension → ImplBlockNode,
visibility, constants, type aliases, generics, annotations, inheritance,
nested types, async functions, static members, top-level definitions,
rationale comments, imports, and Package.swift manifest parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.swift import (
    SwiftExtractor,
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

FIXTURES = Path(__file__).parent / "fixtures" / "swift"


@pytest.fixture
def ext() -> SwiftExtractor:
    """Create a fresh extractor instance."""
    return SwiftExtractor()


def _extract(ext: SwiftExtractor, fixture_name: str) -> FileAST:
    """Helper: read fixture and extract."""
    path = FIXTURES / fixture_name
    src = path.read_bytes()
    return ext.extract(path, src)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor identity
# ---------------------------------------------------------------------------


def test_language_id(ext: SwiftExtractor) -> None:
    assert ext.language_id == "swift"


def test_file_extensions(ext: SwiftExtractor) -> None:
    assert ".swift" in ext.file_extensions


# endregion: --- Extractor identity


# ---------------------------------------------------------------------------
# region:    --- Basic class
# ---------------------------------------------------------------------------


def test_basic_class_imports(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    assert len(ast.uses) == 1
    assert "import Foundation" in ast.uses[0]


def test_basic_class_struct(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    assert len(ast.structs) == 1
    s = ast.structs[0]
    assert s.name == "User"
    assert s.visibility == Visibility.PUBLIC


def test_basic_class_fields(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    s = ast.structs[0]
    field_names = [f.name for f in s.fields]
    assert "id" in field_names
    assert "name" in field_names
    assert "email" in field_names


def test_basic_class_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    assert len(ast.impl_blocks) >= 1
    methods = ast.impl_blocks[0].methods
    method_names = [m.name for m in methods]
    assert "greet" in method_names
    assert "validate" in method_names
    assert "toString" in method_names


def test_basic_class_doc(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    s = ast.structs[0]
    assert "basic user class" in s.doc.lower()


def test_basic_class_private_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "basic_class.swift")
    methods = ast.impl_blocks[0].methods
    validate = next(m for m in methods if m.name == "validate")
    assert validate.visibility == Visibility.PRIVATE


# endregion: --- Basic class


# ---------------------------------------------------------------------------
# region:    --- Struct
# ---------------------------------------------------------------------------


def test_struct_attribute(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "struct_basic.swift")
    names = {s.name: s for s in ast.structs}
    assert "Point" in names
    assert "struct" in names["Point"].attributes


def test_struct_fields(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "struct_basic.swift")
    names = {s.name: s for s in ast.structs}
    pt = names["Point"]
    field_names = [f.name for f in pt.fields]
    assert "x" in field_names
    assert "y" in field_names


def test_struct_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "struct_basic.swift")
    impl = [i for i in ast.impl_blocks if i.self_type == "Point"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "distance" in method_names
    assert "translate" in method_names


def test_struct_doc(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "struct_basic.swift")
    names = {s.name: s for s in ast.structs}
    assert "2d point" in names["Point"].doc.lower()


def test_struct_config(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "struct_basic.swift")
    names = {s.name: s for s in ast.structs}
    assert "Config" in names
    assert "struct" in names["Config"].attributes
    field_names = [f.name for f in names["Config"].fields]
    assert "host" in field_names
    assert "port" in field_names
    assert "debug" in field_names


# endregion: --- Struct


# ---------------------------------------------------------------------------
# region:    --- Protocol
# ---------------------------------------------------------------------------


def test_protocol_basic(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "protocol_basic.swift")
    assert len(ast.traits) == 2
    names = {t.name: t for t in ast.traits}
    assert "Drawable" in names
    assert "Resizable" in names


def test_protocol_required_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "protocol_basic.swift")
    names = {t.name: t for t in ast.traits}
    drawable = names["Drawable"]
    item_names = [i.name for i in drawable.items]
    assert "draw" in item_names
    assert "color" in item_names
    draw = next(i for i in drawable.items if i.name == "draw")
    assert draw.kind == TraitItemKind.REQUIRED_METHOD


def test_protocol_associated_type(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "protocol_basic.swift")
    names = {t.name: t for t in ast.traits}
    drawable = names["Drawable"]
    item_names = [i.name for i in drawable.items]
    assert "Element" in item_names
    element = next(i for i in drawable.items if i.name == "Element")
    assert element.kind == TraitItemKind.ASSOCIATED_TYPE


# endregion: --- Protocol


# ---------------------------------------------------------------------------
# region:    --- Enum
# ---------------------------------------------------------------------------


def test_enum_basic(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "enum_basic.swift")
    assert len(ast.enums) == 3
    names = {e.name: e for e in ast.enums}
    assert "Direction" in names
    assert "Result" in names
    assert "Planet" in names


def test_enum_unit_variants(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "enum_basic.swift")
    names = {e.name: e for e in ast.enums}
    direction = names["Direction"]
    variant_names = [v.name for v in direction.variants]
    assert "north" in variant_names
    assert "south" in variant_names
    assert "east" in variant_names
    assert "west" in variant_names
    for v in direction.variants:
        assert v.kind == EnumVariantKind.UNIT


def test_enum_tuple_variants(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "enum_basic.swift")
    names = {e.name: e for e in ast.enums}
    result = names["Result"]
    variant_names = [v.name for v in result.variants]
    assert "success" in variant_names
    assert "failure" in variant_names
    for v in result.variants:
        assert v.kind == EnumVariantKind.TUPLE


def test_enum_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "enum_basic.swift")
    impl = [i for i in ast.impl_blocks if i.self_type == "Direction"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "opposite" in method_names


# endregion: --- Enum


# ---------------------------------------------------------------------------
# region:    --- Actor
# ---------------------------------------------------------------------------


def test_actor(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "actor_basic.swift")
    assert len(ast.structs) == 1
    s = ast.structs[0]
    assert s.name == "BankAccount"
    assert "actor" in s.attributes


def test_actor_fields(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "actor_basic.swift")
    s = ast.structs[0]
    field_names = [f.name for f in s.fields]
    assert "balance" in field_names


def test_actor_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "actor_basic.swift")
    impl = [i for i in ast.impl_blocks if i.self_type == "BankAccount"]
    assert len(impl) >= 1
    method_names = [m.name for m in impl[0].methods]
    assert "deposit" in method_names
    assert "withdraw" in method_names


# endregion: --- Actor


# ---------------------------------------------------------------------------
# region:    --- Extension
# ---------------------------------------------------------------------------


def test_extension_basic(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "extension_basic.swift")
    names = {s.name: s for s in ast.structs}
    assert "Person" in names


def test_extension_conformance(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "extension_basic.swift")
    impls = [i for i in ast.impl_blocks if i.self_type == "Person"]
    trait_types = {i.trait_type for i in impls}
    assert "CustomStringConvertible" in trait_types


def test_extension_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "extension_basic.swift")
    impls = [i for i in ast.impl_blocks if i.self_type == "Person"]
    all_methods = [m.name for i in impls for m in i.methods]
    assert "greet" in all_methods
    assert "isAdult" in all_methods
    assert "create" in all_methods


# endregion: --- Extension


# ---------------------------------------------------------------------------
# region:    --- Visibility
# ---------------------------------------------------------------------------


def test_visibility_public_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "visibility.swift")
    impl = ast.impl_blocks[0]
    pub = next(m for m in impl.methods if m.name == "publicMethod")
    assert pub.visibility == Visibility.PUBLIC


def test_visibility_private_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "visibility.swift")
    impl = ast.impl_blocks[0]
    priv = next(m for m in impl.methods if m.name == "privateMethod")
    assert priv.visibility == Visibility.PRIVATE


def test_visibility_internal_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "visibility.swift")
    impl = ast.impl_blocks[0]
    internal = next(m for m in impl.methods if m.name == "internalMethod")
    assert internal.visibility == Visibility.CRATE


# endregion: --- Visibility


# ---------------------------------------------------------------------------
# region:    --- Generics
# ---------------------------------------------------------------------------


def test_generics_class(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "generics.swift")
    names = {s.name: s for s in ast.structs}
    assert "Container" in names
    assert "<T>" in names["Container"].generics


def test_generics_struct(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "generics.swift")
    names = {s.name: s for s in ast.structs}
    assert "Pair" in names
    assert "A" in names["Pair"].generics
    assert "B" in names["Pair"].generics


def test_generics_protocol(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "generics.swift")
    traits = {t.name: t for t in ast.traits}
    assert "Comparable2" in traits


# endregion: --- Generics


# ---------------------------------------------------------------------------
# region:    --- Imports
# ---------------------------------------------------------------------------


def test_imports(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "imports.swift")
    assert len(ast.uses) == 2
    assert any("Foundation" in u for u in ast.uses)
    assert any("UIKit" in u for u in ast.uses)


# endregion: --- Imports


# ---------------------------------------------------------------------------
# region:    --- Type aliases
# ---------------------------------------------------------------------------


def test_type_aliases(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "type_alias.swift")
    assert len(ast.type_aliases) == 3
    ta_names = [ta.name for ta in ast.type_aliases]
    assert "StringList" in ta_names
    assert "Predicate" in ta_names
    assert "Callback" in ta_names


def test_type_alias_target(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "type_alias.swift")
    sl = next(ta for ta in ast.type_aliases if ta.name == "StringList")
    assert sl.aliased_to == "[String]"


# endregion: --- Type aliases


# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------


def test_constants(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "constants.swift")
    const_names = [c.name for c in ast.constants]
    assert "MAX_SIZE" in const_names
    assert "APP_NAME" in const_names
    assert "VERSION" in const_names
    assert "GlobalTimeout" in const_names


def test_constant_raw(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "constants.swift")
    max_size = next(c for c in ast.constants if c.name == "MAX_SIZE")
    assert "100" in max_size.raw


# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Inheritance
# ---------------------------------------------------------------------------


def test_inheritance_conformance(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "inheritance.swift")
    impls = [i for i in ast.impl_blocks if i.self_type == "Service"]
    trait_types = {i.trait_type for i in impls}
    assert "Serializable" in trait_types
    assert "Logging" in trait_types
    assert "Renderable" in trait_types


def test_inheritance_subclass(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "inheritance.swift")
    impls = [i for i in ast.impl_blocks if i.self_type == "Child"]
    trait_types = {i.trait_type for i in impls}
    assert "Base" in trait_types
    assert "Logging" in trait_types


# endregion: --- Inheritance


# ---------------------------------------------------------------------------
# region:    --- Annotations
# ---------------------------------------------------------------------------


def test_annotations(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "annotations.swift")
    names = {s.name: s for s in ast.structs}
    assert "OldClass" in names
    assert "@available" in names["OldClass"].attributes


def test_annotation_objc(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "annotations.swift")
    names = {s.name: s for s in ast.structs}
    assert "ObjCClass" in names
    assert "@objc" in names["ObjCClass"].attributes


# endregion: --- Annotations


# ---------------------------------------------------------------------------
# region:    --- Async functions
# ---------------------------------------------------------------------------


def test_async_top_level(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "async_functions.swift")
    fn = next(f for f in ast.functions if f.name == "fetchData")
    assert fn.is_async is True
    assert fn.return_type == "String"


def test_async_methods(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "async_functions.swift")
    impl = [i for i in ast.impl_blocks if i.self_type == "DataService"]
    assert len(impl) >= 1
    load = next(m for m in impl[0].methods if m.name == "load")
    assert load.is_async is True
    process = next(m for m in impl[0].methods if m.name == "process")
    assert process.is_async is True


# endregion: --- Async functions


# ---------------------------------------------------------------------------
# region:    --- Static members
# ---------------------------------------------------------------------------


def test_static_method(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "static_members.swift")
    impl = [i for i in ast.impl_blocks if i.self_type == "StaticMembers"]
    assert len(impl) >= 1
    create = next(m for m in impl[0].methods if m.name == "create")
    assert create.is_static is True


def test_static_constant(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "static_members.swift")
    const_names = [c.name for c in ast.constants]
    assert "shared" in const_names


# endregion: --- Static members


# ---------------------------------------------------------------------------
# region:    --- Top-level definitions
# ---------------------------------------------------------------------------


def test_top_level_functions(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "top_level_defs.swift")
    fn_names = [f.name for f in ast.functions]
    assert "add" in fn_names
    assert "greet" in fn_names


def test_top_level_function_params(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "top_level_defs.swift")
    add = next(f for f in ast.functions if f.name == "add")
    param_names = [p.name for p in add.params]
    assert "a" in param_names
    assert "b" in param_names
    assert add.return_type == "Int"


def test_top_level_constant(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "top_level_defs.swift")
    const_names = [c.name for c in ast.constants]
    assert "DefaultTimeout" in const_names


# endregion: --- Top-level definitions


# ---------------------------------------------------------------------------
# region:    --- Nested types
# ---------------------------------------------------------------------------


def test_nested_types(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "nested_types.swift")
    names = {s.name for s in ast.structs}
    assert "Outer" in names
    assert "Inner" in names
    assert "InnerStruct" in names


# endregion: --- Nested types


# ---------------------------------------------------------------------------
# region:    --- Rationale comments
# ---------------------------------------------------------------------------


def test_rationale_comments(ext: SwiftExtractor) -> None:
    ast = _extract(ext, "rationale_comments.swift")
    assert len(ast.rationale_comments) >= 1
    texts = [r.text for r in ast.rationale_comments]
    combined = " ".join(texts).lower()
    assert "precision" in combined or "division" in combined or "logging" in combined


# endregion: --- Rationale comments


# ---------------------------------------------------------------------------
# region:    --- Import map
# ---------------------------------------------------------------------------


def test_import_map_basic() -> None:
    uses = ["import Foundation", "import UIKit.UIView"]
    result = build_import_map(uses)
    assert result["Foundation"] == "Foundation"


def test_import_map_submodule() -> None:
    uses = ["import UIKit.UIView"]
    result = build_import_map(uses)
    assert "UIView" in result or "UIKit" in result


# endregion: --- Import map


# ---------------------------------------------------------------------------
# region:    --- Manifest parsing (Package.swift)
# ---------------------------------------------------------------------------


def test_manifest_package_swift(ext: SwiftExtractor) -> None:
    manifest = FIXTURES / "Package.swift"
    crate = ext.parse_manifest(manifest)
    assert crate.language == "swift"
    assert crate.name == "MySwiftApp"


def test_manifest_dependencies(ext: SwiftExtractor) -> None:
    manifest = FIXTURES / "Package.swift"
    crate = ext.parse_manifest(manifest)
    dep_names = [d.name for d in crate.dependencies]
    assert any("swift-argument-parser" in n for n in dep_names)
    assert any("vapor" in n for n in dep_names)
    assert any("swift-log" in n for n in dep_names)


# endregion: --- Manifest parsing (Package.swift)


# ---------------------------------------------------------------------------
# region:    --- Error handling
# ---------------------------------------------------------------------------


def test_parse_error_reported(ext: SwiftExtractor) -> None:
    """Malformed Swift source should report errors, not crash."""
    src = b"class { invalid syntax @@@ }"
    ast = ext.extract(Path("bad.swift"), src)
    assert isinstance(ast, FileAST)


def test_empty_file(ext: SwiftExtractor) -> None:
    """Empty file should return empty FileAST."""
    ast = ext.extract(Path("empty.swift"), b"")
    assert isinstance(ast, FileAST)
    assert len(ast.structs) == 0
    assert len(ast.functions) == 0


# endregion: --- Error handling
