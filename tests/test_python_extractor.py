"""Comprehensive tests for the Python extractor.

Covers:
- Class extraction (regular, inheritance, __init__ fields, class annotations)
- ABC and Protocol → TraitNode mapping
- @dataclass → StructNode with fields
- Function extraction (sync, async, decorators, visibility, *args, **kwargs)
- Method extraction (instance, static, classmethod)
- Import parsing
- Constant and type alias detection
- Import map building
- Scoped method call resolution
- self_methods aggregation
- pyproject.toml manifest parsing
- Edge cases (empty files, parse errors, empty classes)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.python import (
    PythonExtractor,
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

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "python"


@pytest.fixture
def extractor() -> PythonExtractor:
    """Provide a fresh PythonExtractor instance."""
    return PythonExtractor()


def _parse_fixture(extractor: PythonExtractor, name: str) -> FileAST:
    """Helper to parse a fixture file and return the FileAST."""
    path = FIXTURE_DIR / name
    source = path.read_bytes()
    return extractor.extract(path, source)


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Extractor Identity Tests
# ---------------------------------------------------------------------------


class TestExtractorIdentity:
    """Verify extractor metadata and contract compliance."""

    def test_language_id(self, extractor: PythonExtractor) -> None:
        """Language ID must be 'python'."""
        assert extractor.language_id == "python"

    def test_file_extensions(self, extractor: PythonExtractor) -> None:
        """Must handle .py and .pyi files."""
        assert extractor.file_extensions == [".py", ".pyi"]

    def test_is_extractor_base_subclass(self) -> None:
        """PythonExtractor must inherit from ExtractorBase."""
        from ast_intel.extractors.base import ExtractorBase

        assert issubclass(PythonExtractor, ExtractorBase)


# endregion: --- Extractor Identity Tests


# ---------------------------------------------------------------------------
# region:    --- Class Extraction Tests
# ---------------------------------------------------------------------------


class TestClassExtraction:
    """Verify class parsing from classes.py fixture."""

    def test_struct_count(self, extractor: PythonExtractor) -> None:
        """Must extract all 5 classes as structs (no ABCs in this fixture)."""
        ast = _parse_fixture(extractor, "classes.py")
        assert len(ast.structs) == 5

    def test_simple_class(self, extractor: PythonExtractor) -> None:
        """SimpleClass has no fields and no bases."""
        ast = _parse_fixture(extractor, "classes.py")
        simple = next(s for s in ast.structs if s.name == "SimpleClass")
        assert simple.visibility == Visibility.PUBLIC
        assert simple.fields == ()
        assert simple.doc == "A simple class with no bases."

    def test_animal_fields(self, extractor: PythonExtractor) -> None:
        """Animal must have 3 fields: name, sound (from annotations), _alive (from __init__)."""
        ast = _parse_fixture(extractor, "classes.py")
        animal = next(s for s in ast.structs if s.name == "Animal")
        field_names = [f.name for f in animal.fields]
        assert "name" in field_names
        assert "sound" in field_names
        assert "_alive" in field_names

    def test_animal_field_types(self, extractor: PythonExtractor) -> None:
        """Animal name and sound must have str type from annotations."""
        ast = _parse_fixture(extractor, "classes.py")
        animal = next(s for s in ast.structs if s.name == "Animal")
        fname_field = next(f for f in animal.fields if f.name == "name")
        assert fname_field.type == "str"
        assert fname_field.visibility == Visibility.PUBLIC

    def test_private_field_visibility(self, extractor: PythonExtractor) -> None:
        """Fields starting with _ must be private."""
        ast = _parse_fixture(extractor, "classes.py")
        animal = next(s for s in ast.structs if s.name == "Animal")
        alive = next(f for f in animal.fields if f.name == "_alive")
        assert alive.visibility == Visibility.PRIVATE

    def test_private_class_visibility(self, extractor: PythonExtractor) -> None:
        """Classes starting with _ must be private."""
        ast = _parse_fixture(extractor, "classes.py")
        helper = next(s for s in ast.structs if s.name == "_PrivateHelper")
        assert helper.visibility == Visibility.PRIVATE

    def test_inheritance_impl_blocks(self, extractor: PythonExtractor) -> None:
        """Dog(Animal) must create an ImplBlockNode with trait_type='Animal'."""
        ast = _parse_fixture(extractor, "classes.py")
        dog_impls = [ib for ib in ast.impl_blocks if ib.self_type == "Dog"]
        assert len(dog_impls) == 1
        assert dog_impls[0].trait_type == "Animal"

    def test_deep_inheritance(self, extractor: PythonExtractor) -> None:
        """GuideDog(Dog) must create an ImplBlockNode with trait_type='Dog'."""
        ast = _parse_fixture(extractor, "classes.py")
        guide_impls = [ib for ib in ast.impl_blocks if ib.self_type == "GuideDog"]
        assert len(guide_impls) == 1
        assert guide_impls[0].trait_type == "Dog"

    def test_inherent_impl_block(self, extractor: PythonExtractor) -> None:
        """Animal (no bases) must have an inherent impl block."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impls = [ib for ib in ast.impl_blocks if ib.self_type == "Animal"]
        assert len(animal_impls) == 1
        assert animal_impls[0].trait_type == ""

    def test_method_count(self, extractor: PythonExtractor) -> None:
        """Animal inherent impl must have 5 methods."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Animal")
        assert len(animal_impl.methods) == 5

    def test_static_method(self, extractor: PythonExtractor) -> None:
        """Animal.kingdom() must be marked as static."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Animal")
        kingdom = next(m for m in animal_impl.methods if m.name == "kingdom")
        assert kingdom.is_static is True

    def test_classmethod(self, extractor: PythonExtractor) -> None:
        """Animal.from_dict() is a classmethod (has cls param, NOT static)."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Animal")
        from_dict = next(m for m in animal_impl.methods if m.name == "from_dict")
        # classmethods take cls, so they are not static
        assert from_dict.is_static is False
        assert any("classmethod" in a for a in from_dict.attributes)

    def test_async_method(self, extractor: PythonExtractor) -> None:
        """Animal.feed() must be async."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Animal")
        feed = next(m for m in animal_impl.methods if m.name == "feed")
        assert feed.is_async is True

    def test_method_decorators(self, extractor: PythonExtractor) -> None:
        """Static/classmethod decorators must appear in attributes."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Animal")
        kingdom = next(m for m in animal_impl.methods if m.name == "kingdom")
        assert any("staticmethod" in a for a in kingdom.attributes)


# endregion: --- Class Extraction Tests


# ---------------------------------------------------------------------------
# region:    --- Abstract Class / Protocol Tests
# ---------------------------------------------------------------------------


class TestAbstractClassExtraction:
    """Verify ABC and Protocol classes → TraitNode mapping."""

    def test_trait_count(self, extractor: PythonExtractor) -> None:
        """Must extract 4 traits (2 ABCs + 2 Protocols)."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        assert len(ast.traits) == 4

    def test_abc_trait(self, extractor: PythonExtractor) -> None:
        """Serializable(ABC) must become a TraitNode with ABC super-trait."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        serializable = next(t for t in ast.traits if t.name == "Serializable")
        assert "ABC" in serializable.super_traits
        assert serializable.doc == "Abstract base for serializable objects."

    def test_abc_required_methods(self, extractor: PythonExtractor) -> None:
        """@abstractmethod decorated methods must be REQUIRED_METHOD."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        serializable = next(t for t in ast.traits if t.name == "Serializable")
        required = [i for i in serializable.items if i.kind == TraitItemKind.REQUIRED_METHOD]
        assert len(required) == 2
        assert {i.name for i in required} == {"serialize", "deserialize"}

    def test_abc_default_methods(self, extractor: PythonExtractor) -> None:
        """Non-abstract methods in ABC must be DEFAULT_METHOD."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        serializable = next(t for t in ast.traits if t.name == "Serializable")
        default = [i for i in serializable.items if i.kind == TraitItemKind.DEFAULT_METHOD]
        assert len(default) == 1
        assert default[0].name == "to_json"

    def test_abc_async_method(self, extractor: PythonExtractor) -> None:
        """Comparable.async_compare must be marked async and required."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        comparable = next(t for t in ast.traits if t.name == "Comparable")
        async_cmp = next(i for i in comparable.items if i.name == "async_compare")
        assert async_cmp.is_async is True
        assert async_cmp.kind == TraitItemKind.REQUIRED_METHOD

    def test_protocol_trait(self, extractor: PythonExtractor) -> None:
        """Drawable(Protocol) must become a TraitNode."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        drawable = next(t for t in ast.traits if t.name == "Drawable")
        assert "Protocol" in drawable.super_traits

    def test_protocol_runtime_checkable(self, extractor: PythonExtractor) -> None:
        """@runtime_checkable must appear in attributes."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        drawable = next(t for t in ast.traits if t.name == "Drawable")
        assert any("runtime_checkable" in a for a in drawable.attributes)

    def test_concrete_subclass_is_struct(self, extractor: PythonExtractor) -> None:
        """ConcreteSerializer(Serializable) must be a StructNode, not TraitNode."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        assert len(ast.structs) == 1
        assert ast.structs[0].name == "ConcreteSerializer"

    def test_concrete_subclass_impl(self, extractor: PythonExtractor) -> None:
        """ConcreteSerializer must have ImplBlockNode for Serializable."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        concrete_impls = [
            ib for ib in ast.impl_blocks if ib.self_type == "ConcreteSerializer"
        ]
        assert len(concrete_impls) == 1
        assert concrete_impls[0].trait_type == "Serializable"

    def test_trait_item_params(self, extractor: PythonExtractor) -> None:
        """Trait method parameters must be extracted (excluding self)."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        serializable = next(t for t in ast.traits if t.name == "Serializable")
        deser = next(i for i in serializable.items if i.name == "deserialize")
        assert len(deser.params) == 1
        assert deser.params[0].name == "data"
        assert deser.params[0].type == "bytes"

    def test_trait_item_return_type(self, extractor: PythonExtractor) -> None:
        """Trait methods must capture return types."""
        ast = _parse_fixture(extractor, "abstract_classes.py")
        serializable = next(t for t in ast.traits if t.name == "Serializable")
        serialize = next(i for i in serializable.items if i.name == "serialize")
        assert serialize.return_type == "bytes"


# endregion: --- Abstract Class / Protocol Tests


# ---------------------------------------------------------------------------
# region:    --- Dataclass Tests
# ---------------------------------------------------------------------------


class TestDataclassExtraction:
    """Verify @dataclass classes → StructNode with fields."""

    def test_struct_count(self, extractor: PythonExtractor) -> None:
        """Must extract all 4 dataclass structs."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        assert len(ast.structs) == 4

    def test_point_fields(self, extractor: PythonExtractor) -> None:
        """Point must have x: float and y: float fields."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        point = next(s for s in ast.structs if s.name == "Point")
        assert len(point.fields) == 2
        assert point.fields[0].name == "x"
        assert point.fields[0].type == "float"
        assert point.fields[1].name == "y"
        assert point.fields[1].type == "float"

    def test_config_fields(self, extractor: PythonExtractor) -> None:
        """Config must have 5 fields including defaults."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        config = next(s for s in ast.structs if s.name == "Config")
        assert len(config.fields) == 5
        field_names = [f.name for f in config.fields]
        assert field_names == ["name", "version", "debug", "features", "tags"]

    def test_config_field_types(self, extractor: PythonExtractor) -> None:
        """Config field types must be correctly extracted."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        config = next(s for s in ast.structs if s.name == "Config")
        type_map = {f.name: f.type for f in config.fields}
        assert type_map["name"] == "str"
        assert type_map["version"] == "str"
        assert type_map["debug"] == "bool"
        assert type_map["features"] == "list[str]"
        assert type_map["tags"] == "tuple[str, ...]"

    def test_dataclass_decorator_in_attributes(self, extractor: PythonExtractor) -> None:
        """Config must have @dataclass(...) in attributes."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        config = next(s for s in ast.structs if s.name == "Config")
        assert any("dataclass" in a for a in config.attributes)

    def test_classvar_excluded(self, extractor: PythonExtractor) -> None:
        """ServerSettings must not include ClassVar fields."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        settings = next(s for s in ast.structs if s.name == "ServerSettings")
        field_names = [f.name for f in settings.fields]
        assert "DEFAULT_PORT" not in field_names
        assert "host" in field_names

    def test_private_dataclass(self, extractor: PythonExtractor) -> None:
        """_InternalEntry must be private visibility."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        internal = next(s for s in ast.structs if s.name == "_InternalEntry")
        assert internal.visibility == Visibility.PRIVATE

    def test_dataclass_docstring(self, extractor: PythonExtractor) -> None:
        """Point must have its docstring extracted."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        point = next(s for s in ast.structs if s.name == "Point")
        assert point.doc == "A 2D point."

    def test_dataclass_with_method(self, extractor: PythonExtractor) -> None:
        """ServerSettings has a bind_address method that should be in impl block."""
        ast = _parse_fixture(extractor, "dataclasses_sample.py")
        impl = next(
            (ib for ib in ast.impl_blocks if ib.self_type == "ServerSettings"),
            None,
        )
        assert impl is not None
        assert any(m.name == "bind_address" for m in impl.methods)


# endregion: --- Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- Function Tests
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    """Verify top-level function parsing from functions.py fixture."""

    def test_function_count(self, extractor: PythonExtractor) -> None:
        """Must extract all 9 top-level functions."""
        ast = _parse_fixture(extractor, "functions.py")
        assert len(ast.functions) == 9

    def test_simple_function(self, extractor: PythonExtractor) -> None:
        """simple_add must have correct params and return type."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "simple_add")
        assert fn.return_type == "int"
        assert len(fn.params) == 2
        assert fn.params[0].name == "a"
        assert fn.params[0].type == "int"
        assert fn.is_async is False

    def test_no_annotations(self, extractor: PythonExtractor) -> None:
        """no_annotations must have parameterless ParamNodes (no type info)."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "no_annotations")
        assert len(fn.params) == 2
        assert fn.params[0].name == "x"
        assert fn.params[0].type == ""

    def test_async_function(self, extractor: PythonExtractor) -> None:
        """fetch_data must be marked async."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "fetch_data")
        assert fn.is_async is True
        assert fn.return_type == "bytes"

    def test_decorated_function(self, extractor: PythonExtractor) -> None:
        """cached_value must have @cache in attributes."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "cached_value")
        assert any("cache" in a for a in fn.attributes)

    def test_decorated_function_with_args(self, extractor: PythonExtractor) -> None:
        """lru_lookup must have @lru_cache(maxsize=128) in attributes."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "lru_lookup")
        assert any("lru_cache" in a for a in fn.attributes)

    def test_private_function(self, extractor: PythonExtractor) -> None:
        """_private_helper must have private visibility."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "_private_helper")
        assert fn.visibility == Visibility.PRIVATE

    def test_function_docstring(self, extractor: PythonExtractor) -> None:
        """simple_add must have its docstring extracted."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "simple_add")
        assert fn.doc == "Add two numbers."

    def test_star_args_params(self, extractor: PythonExtractor) -> None:
        """complex_signature must have *args and **kwargs with names and types."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "complex_signature")
        assert len(fn.params) == 5
        param_names = [p.name for p in fn.params]
        assert "*args" in param_names
        assert "**kwargs" in param_names
        # Check types of splat params
        args_param = next(p for p in fn.params if p.name == "*args")
        assert args_param.type == "str"
        kwargs_param = next(p for p in fn.params if p.name == "**kwargs")
        assert kwargs_param.type == "Any"

    def test_default_param(self, extractor: PythonExtractor) -> None:
        """fetch_data timeout param must be present with type."""
        ast = _parse_fixture(extractor, "functions.py")
        fn = next(f for f in ast.functions if f.name == "fetch_data")
        timeout = next(p for p in fn.params if p.name == "timeout")
        assert timeout.type == "float"


# endregion: --- Function Tests


# ---------------------------------------------------------------------------
# region:    --- Import Tests
# ---------------------------------------------------------------------------


class TestImportExtraction:
    """Verify import parsing from imports.py fixture."""

    def test_import_count(self, extractor: PythonExtractor) -> None:
        """Must extract all 10 import statements."""
        ast = _parse_fixture(extractor, "imports.py")
        assert len(ast.uses) == 10

    def test_simple_import(self, extractor: PythonExtractor) -> None:
        """'import os' must be in uses."""
        ast = _parse_fixture(extractor, "imports.py")
        assert "import os" in ast.uses

    def test_from_import(self, extractor: PythonExtractor) -> None:
        """'from pathlib import Path' must be in uses."""
        ast = _parse_fixture(extractor, "imports.py")
        assert "from pathlib import Path" in ast.uses

    def test_multi_from_import(self, extractor: PythonExtractor) -> None:
        """'from collections import ...' must be in uses."""
        ast = _parse_fixture(extractor, "imports.py")
        assert any("collections" in u and "defaultdict" in u for u in ast.uses)

    def test_relative_import(self, extractor: PythonExtractor) -> None:
        """Relative imports must be captured."""
        ast = _parse_fixture(extractor, "imports.py")
        assert any(u.startswith("from .") for u in ast.uses)

    def test_aliased_import(self, extractor: PythonExtractor) -> None:
        """'import importlib as il' must be in uses."""
        ast = _parse_fixture(extractor, "imports.py")
        assert any("importlib" in u and "as il" in u for u in ast.uses)


# endregion: --- Import Tests


# ---------------------------------------------------------------------------
# region:    --- Constant and Type Alias Tests
# ---------------------------------------------------------------------------


class TestConstantAndAliasExtraction:
    """Verify constant and type alias detection from constants_and_aliases.py."""

    def test_constant_count(self, extractor: PythonExtractor) -> None:
        """Must extract 5 constants."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        assert len(ast.constants) == 5

    def test_typed_constant(self, extractor: PythonExtractor) -> None:
        """MAX_RETRIES must be a constant with raw text."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        c = next(c for c in ast.constants if c.name == "MAX_RETRIES")
        assert "3" in c.raw
        assert c.visibility == Visibility.PUBLIC

    def test_private_constant(self, extractor: PythonExtractor) -> None:
        """_INTERNAL_FLAG must be private."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        c = next(c for c in ast.constants if c.name == "_INTERNAL_FLAG")
        assert c.visibility == Visibility.PRIVATE

    def test_untyped_constant(self, extractor: PythonExtractor) -> None:
        """DEBUG (UPPER_CASE, no type) must be a constant."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        c = next(c for c in ast.constants if c.name == "DEBUG")
        assert c is not None

    def test_type_alias_count(self, extractor: PythonExtractor) -> None:
        """Must extract 4 type aliases."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        assert len(ast.type_aliases) == 4

    def test_explicit_type_alias(self, extractor: PythonExtractor) -> None:
        """JsonDict with TypeAlias annotation must be detected."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        j = next(ta for ta in ast.type_aliases if ta.name == "JsonDict")
        assert j.aliased_to == "dict[str, object]"

    def test_camelcase_type_alias(self, extractor: PythonExtractor) -> None:
        """Headers (CamelCase) must be a type alias."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        h = next(ta for ta in ast.type_aliases if ta.name == "Headers")
        assert h.aliased_to == "dict[str, str]"

    def test_union_type_alias(self, extractor: PythonExtractor) -> None:
        """OptionalStr = str | None must be detected."""
        ast = _parse_fixture(extractor, "constants_and_aliases.py")
        opt = next(ta for ta in ast.type_aliases if ta.name == "OptionalStr")
        assert "None" in opt.aliased_to


# endregion: --- Constant and Type Alias Tests


# ---------------------------------------------------------------------------
# region:    --- Import Map Tests
# ---------------------------------------------------------------------------


class TestImportMap:
    """Verify build_import_map handles all Python import styles."""

    def test_simple_import(self) -> None:
        """'import os' maps os → os."""
        result = build_import_map(["import os"])
        assert result["os"] == "os"

    def test_from_import(self) -> None:
        """'from pathlib import Path' maps Path → pathlib.Path."""
        result = build_import_map(["from pathlib import Path"])
        assert result["Path"] == "pathlib.Path"

    def test_aliased_import(self) -> None:
        """'import importlib as il' maps il → importlib."""
        result = build_import_map(["import importlib as il"])
        assert result["il"] == "importlib"

    def test_from_aliased(self) -> None:
        """'from os.path import join as pjoin' maps pjoin → os.path.join."""
        result = build_import_map(["from os.path import join as pjoin"])
        assert result["pjoin"] == "os.path.join"

    def test_multi_from_import(self) -> None:
        """'from collections import defaultdict, OrderedDict' maps both."""
        result = build_import_map(["from collections import defaultdict, OrderedDict"])
        assert result["defaultdict"] == "collections.defaultdict"
        assert result["OrderedDict"] == "collections.OrderedDict"

    def test_empty_input(self) -> None:
        """Empty list returns empty map."""
        assert build_import_map([]) == {}


# endregion: --- Import Map Tests


# ---------------------------------------------------------------------------
# region:    --- self_methods Tests
# ---------------------------------------------------------------------------


class TestSelfMethods:
    """Verify self_methods aggregation."""

    def test_self_methods_include_free_functions(self, extractor: PythonExtractor) -> None:
        """Free functions must be in self_methods with context='free'."""
        ast = _parse_fixture(extractor, "functions.py")
        free = [m for m in ast.self_methods if m.context == "free"]
        assert len(free) == 9
        names = {m.name for m in free}
        assert "simple_add" in names
        assert "_private_helper" in names

    def test_self_methods_include_class_methods(self, extractor: PythonExtractor) -> None:
        """Class methods must be in self_methods with correct context."""
        ast = _parse_fixture(extractor, "classes.py")
        animal_methods = [m for m in ast.self_methods if "Animal" in m.context]
        assert len(animal_methods) > 0
        names = {m.name for m in animal_methods}
        assert "speak" in names


# endregion: --- self_methods Tests


# ---------------------------------------------------------------------------
# region:    --- imported_package_methods Tests
# ---------------------------------------------------------------------------


class TestImportedPackageMethods:
    """Verify scoped method call detection."""

    def test_scoped_calls(self, extractor: PythonExtractor) -> None:
        """comprehensive.py must detect logging.getLogger call."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert "logging" in ast.imported_package_methods
        assert "getLogger" in ast.imported_package_methods["logging"]


# endregion: --- imported_package_methods Tests


# ---------------------------------------------------------------------------
# region:    --- Manifest Parsing Tests
# ---------------------------------------------------------------------------


class TestManifestParsing:
    """Verify pyproject.toml parsing."""

    def test_project_name(self, extractor: PythonExtractor) -> None:
        """Must extract project name."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        assert crate.name == "my-sample-project"

    def test_project_version(self, extractor: PythonExtractor) -> None:
        """Must extract version."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        assert crate.version == "1.2.3"

    def test_language(self, extractor: PythonExtractor) -> None:
        """Language must be 'python'."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        assert crate.language == "python"

    def test_dependencies(self, extractor: PythonExtractor) -> None:
        """Must extract 3 runtime dependencies."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        runtime_deps = [d for d in crate.dependencies if not d.is_dev]
        assert len(runtime_deps) == 3
        names = {d.name for d in runtime_deps}
        assert names == {"requests", "click", "pydantic"}

    def test_dev_dependencies(self, extractor: PythonExtractor) -> None:
        """Must extract dev/optional dependencies."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        dev_deps = [d for d in crate.dependencies if d.is_dev]
        assert len(dev_deps) == 4
        names = {d.name for d in dev_deps}
        assert "pytest" in names
        assert "sphinx" in names

    def test_dep_version(self, extractor: PythonExtractor) -> None:
        """Dependencies must have version specifiers."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        requests = next(d for d in crate.dependencies if d.name == "requests")
        assert requests.version == ">=2.28.0"

    def test_invalid_manifest(self, extractor: PythonExtractor) -> None:
        """Invalid manifest must return fallback CrateModel."""
        fake = Path("/nonexistent/pyproject.toml")
        crate = extractor.parse_manifest(fake)
        assert crate.language == "python"
        assert crate.name == "nonexistent"

    def test_manifest_path(self, extractor: PythonExtractor) -> None:
        """Manifest path must be stored."""
        crate = extractor.parse_manifest(FIXTURE_DIR / "pyproject_sample.toml")
        assert "pyproject_sample.toml" in crate.manifest_path


# endregion: --- Manifest Parsing Tests


# ---------------------------------------------------------------------------
# region:    --- Comprehensive Integration Tests
# ---------------------------------------------------------------------------


class TestComprehensiveFixture:
    """Verify comprehensive.py fixture captures all construct types together."""

    def test_struct_count(self, extractor: PythonExtractor) -> None:
        """Must extract 5 structs."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.structs) == 5

    def test_trait_count(self, extractor: PythonExtractor) -> None:
        """Must extract 2 traits (StorageBackend ABC + EventHandler Protocol)."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.traits) == 2

    def test_function_count(self, extractor: PythonExtractor) -> None:
        """Must extract 3 top-level functions."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.functions) == 3

    def test_constant_count(self, extractor: PythonExtractor) -> None:
        """Must extract 3 constants."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.constants) == 3

    def test_type_alias_count(self, extractor: PythonExtractor) -> None:
        """Must extract 2 type aliases."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.type_aliases) == 2

    def test_import_count(self, extractor: PythonExtractor) -> None:
        """Must capture all imports."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.uses) == 5

    def test_no_errors(self, extractor: PythonExtractor) -> None:
        """Must have no parse errors."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert ast.errors == []

    def test_disk_storage_fields(self, extractor: PythonExtractor) -> None:
        """DiskStorage must have both annotation + __init__ fields."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        disk = next(s for s in ast.structs if s.name == "DiskStorage")
        field_names = [f.name for f in disk.fields]
        assert "base_path" in field_names
        assert "_cache" in field_names

    def test_private_cache_class(self, extractor: PythonExtractor) -> None:
        """_InternalCache must be private."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        cache = next(s for s in ast.structs if s.name == "_InternalCache")
        assert cache.visibility == Visibility.PRIVATE

    def test_storage_backend_trait(self, extractor: PythonExtractor) -> None:
        """StorageBackend must have 4 items (3 required + 1 default)."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        sb = next(t for t in ast.traits if t.name == "StorageBackend")
        required = [i for i in sb.items if i.kind == TraitItemKind.REQUIRED_METHOD]
        default = [i for i in sb.items if i.kind == TraitItemKind.DEFAULT_METHOD]
        assert len(required) == 3
        assert len(default) == 1

    def test_dataclass_struct(self, extractor: PythonExtractor) -> None:
        """ServerConfig @dataclass must have 6 fields."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        config = next(s for s in ast.structs if s.name == "ServerConfig")
        assert len(config.fields) == 6

    def test_impl_block_count(self, extractor: PythonExtractor) -> None:
        """Must have impl blocks for all concrete classes."""
        ast = _parse_fixture(extractor, "comprehensive.py")
        assert len(ast.impl_blocks) >= 4


# endregion: --- Comprehensive Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Verify edge case handling."""

    def test_empty_file(self, extractor: PythonExtractor) -> None:
        """Empty file must return empty FileAST."""
        ast = extractor.extract(Path("empty.py"), b"")
        assert ast.structs == []
        assert ast.functions == []
        assert ast.uses == []

    def test_whitespace_only(self, extractor: PythonExtractor) -> None:
        """Whitespace-only file must return empty FileAST."""
        ast = extractor.extract(Path("whitespace.py"), b"\n\n  \n")
        assert ast.structs == []
        assert ast.functions == []

    def test_syntax_error(self, extractor: PythonExtractor) -> None:
        """File with syntax error must have errors populated."""
        source = b"def broken(\nclass"
        ast = extractor.extract(Path("broken.py"), source)
        assert len(ast.errors) > 0

    def test_module_docstring_not_constant(self, extractor: PythonExtractor) -> None:
        """Module docstring must not be mistakenly extracted as a constant."""
        source = b'"""Module docstring."""\n\ndef foo() -> None:\n    pass\n'
        ast = extractor.extract(Path("has_docstring.py"), source)
        assert len(ast.constants) == 0
        assert len(ast.functions) == 1

    def test_class_with_pass_only(self, extractor: PythonExtractor) -> None:
        """A class with only 'pass' must have no fields."""
        source = b"class Empty:\n    pass\n"
        ast = extractor.extract(Path("empty_class.py"), source)
        assert len(ast.structs) == 1
        assert ast.structs[0].fields == ()

    def test_dunder_method_is_public(self, extractor: PythonExtractor) -> None:
        """__init__ and other dunder methods must be PUBLIC."""
        source = b"class Foo:\n    def __init__(self) -> None:\n        pass\n"
        ast = extractor.extract(Path("dunder.py"), source)
        impl = next(ib for ib in ast.impl_blocks if ib.self_type == "Foo")
        init = next(m for m in impl.methods if m.name == "__init__")
        assert init.visibility == Visibility.PUBLIC


# endregion: --- Edge Cases


# ---------------------------------------------------------------------------
# region:    --- Dispatcher & Manifest Integration Tests
# ---------------------------------------------------------------------------


class TestDispatcherIntegration:
    """Verify Python extractor is registered in the dispatcher."""

    def test_python_in_registry(self) -> None:
        """Python extractor must be in the dispatcher registry."""
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert ".py" in registry
        assert ".pyi" in registry

    def test_registry_type(self) -> None:
        """Registry must map to PythonExtractor class."""
        from ast_intel.core.dispatcher import _build_extractor_registry

        registry = _build_extractor_registry()
        assert registry[".py"] is PythonExtractor


class TestManifestParserIntegration:
    """Verify Python manifest routing in manifest_parser."""

    def test_python_manifest_routing(self, tmp_path: Path) -> None:
        """ManifestParser must route pyproject.toml to Python extractor."""
        import shutil

        from ast_intel.core.manifest_parser import ManifestParser

        # Copy fixture to a properly named pyproject.toml
        dest = tmp_path / "pyproject.toml"
        shutil.copy(FIXTURE_DIR / "pyproject_sample.toml", dest)

        parser = ManifestParser()
        crate = parser.parse(dest)
        assert crate.language == "python"
        assert crate.name == "my-sample-project"
        assert crate.version == "1.2.3"


# endregion: --- Dispatcher & Manifest Integration Tests
