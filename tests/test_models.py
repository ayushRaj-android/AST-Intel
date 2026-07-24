"""Tests for AST node dataclasses."""

from __future__ import annotations

from ast_intel.models.ast_node import (
    ConstantNode,
    EnumNode,
    EnumVariantKind,
    EnumVariantNode,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MacroNode,
    MethodNode,
    ModuleNode,
    ParamNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
    Visibility,
)


class TestVisibility:
    """Enum values serialize correctly."""

    def test_values(self) -> None:
        assert Visibility.PUBLIC.value == "pub"
        assert Visibility.PRIVATE.value == "private"
        assert Visibility.CRATE.value == "crate"
        assert Visibility.PROTECTED.value == "protected"


class TestFieldNode:
    def test_defaults(self) -> None:
        f = FieldNode(name="x", type="i32")
        assert f.name == "x"
        assert f.type == "i32"
        assert f.visibility == Visibility.PRIVATE

    def test_with_visibility(self) -> None:
        f = FieldNode(name="y", type="String", visibility=Visibility.PUBLIC)
        assert f.visibility == Visibility.PUBLIC


class TestStructNode:
    def test_minimal(self) -> None:
        s = StructNode(name="Config")
        assert s.name == "Config"
        assert s.fields == ()
        assert s.generics == ""
        assert s.doc == ""

    def test_with_fields(self) -> None:
        s = StructNode(
            name="Config",
            visibility=Visibility.PUBLIC,
            generics="<T>",
            fields=(
                FieldNode(name="name", type="String"),
                FieldNode(name="port", type="u16"),
            ),
            attributes=('#[derive(Debug, Clone)]',),
            doc="Configuration struct",
        )
        assert len(s.fields) == 2
        assert s.fields[0].name == "name"


class TestEnumNode:
    def test_variants(self) -> None:
        e = EnumNode(
            name="Status",
            variants=(
                EnumVariantNode(name="Active", kind=EnumVariantKind.UNIT),
                EnumVariantNode(
                    name="Error",
                    kind=EnumVariantKind.TUPLE,
                    fields=(FieldNode(name="0", type="String"),),
                ),
            ),
        )
        assert len(e.variants) == 2
        assert e.variants[1].kind == EnumVariantKind.TUPLE


class TestFunctionNode:
    def test_async_function(self) -> None:
        f = FunctionNode(
            name="fetch_data",
            visibility=Visibility.PUBLIC,
            is_async=True,
            params=(ParamNode(name="url", type="&str"),),
            return_type="Result<Data>",
        )
        assert f.is_async is True
        assert f.return_type == "Result<Data>"


class TestMethodNode:
    def test_with_context(self) -> None:
        m = MethodNode(
            name="process",
            visibility=Visibility.PUBLIC,
            is_async=True,
            context="impl:Handler for MyHandler",
        )
        assert m.context == "impl:Handler for MyHandler"


class TestTraitNode:
    def test_with_items(self) -> None:
        t = TraitNode(
            name="StorageHelper",
            visibility=Visibility.PUBLIC,
            items=(
                TraitItemNode(
                    kind=TraitItemKind.REQUIRED_METHOD,
                    name="retrieve",
                    is_async=True,
                    return_type="Result<Vec<u8>>",
                ),
            ),
        )
        assert len(t.items) == 1
        assert t.items[0].kind == TraitItemKind.REQUIRED_METHOD


class TestImplBlockNode:
    def test_inherent_impl(self) -> None:
        ib = ImplBlockNode(self_type="Config")
        assert ib.trait_type == ""

    def test_trait_impl(self) -> None:
        ib = ImplBlockNode(
            self_type="DiskStorage",
            trait_type="StorageHelper",
            methods=(
                MethodNode(name="retrieve", visibility=Visibility.PUBLIC),
            ),
        )
        assert ib.trait_type == "StorageHelper"
        assert len(ib.methods) == 1


class TestAuxiliaryNodes:
    def test_type_alias(self) -> None:
        ta = TypeAliasNode(name="Result", aliased_to="core::result::Result<T, Error>")
        assert ta.aliased_to == "core::result::Result<T, Error>"

    def test_constant(self) -> None:
        c = ConstantNode(name="MAX_RETRIES", raw="const MAX_RETRIES: u32 = 3")
        assert c.name == "MAX_RETRIES"

    def test_module(self) -> None:
        m = ModuleNode(name="utils", visibility=Visibility.PUBLIC, inline=False)
        assert m.inline is False

    def test_macro(self) -> None:
        m = MacroNode(name="debug_print", visibility=Visibility.PRIVATE)
        assert m.name == "debug_print"


class TestFileAST:
    def test_defaults(self) -> None:
        f = FileAST()
        assert f.file == ""
        assert f.uses == []
        assert f.structs == []
        assert f.errors == []
        assert f.imported_package_methods == {}

    def test_populated(self) -> None:
        f = FileAST(
            file="src/main.rs",
            module_path="my_crate::main",
            structs=[StructNode(name="App")],
            functions=[FunctionNode(name="main")],
        )
        assert len(f.structs) == 1
        assert len(f.functions) == 1
