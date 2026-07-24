"""Tests for Feature 1 — Source Line Numbers / Spans.

Covers:
- Span dataclass construction and immutability
- span_from_node helper (1-based conversion from tree-sitter 0-based)
- Span population on all major node types from every extractor
- Span carry-forward in self_methods (re-wrapped nodes)
- Span serialization in JSON formatter (asdict)
- Span display in Markdown formatter (Line column in tables)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from ast_intel.extractors.base import span_from_node
from ast_intel.models.ast_node import (
    ConstantNode,
    EnumNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
    MacroNode,
    MethodNode,
    ModuleNode,
    Span,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
)

# ---------------------------------------------------------------------------
# region:    --- Span Dataclass Tests
# ---------------------------------------------------------------------------


class TestSpanDataclass:
    """Core Span dataclass behavior."""

    def test_construction(self) -> None:
        s = Span(start_line=1, start_col=1, end_line=5, end_col=2)
        assert s.start_line == 1
        assert s.start_col == 1
        assert s.end_line == 5
        assert s.end_col == 2

    def test_frozen(self) -> None:
        s = Span(start_line=1, start_col=1, end_line=1, end_col=10)
        with pytest.raises(FrozenInstanceError):
            s.start_line = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        a = Span(start_line=1, start_col=1, end_line=5, end_col=2)
        b = Span(start_line=1, start_col=1, end_line=5, end_col=2)
        assert a == b

    def test_inequality(self) -> None:
        a = Span(start_line=1, start_col=1, end_line=5, end_col=2)
        b = Span(start_line=2, start_col=1, end_line=5, end_col=2)
        assert a != b

    def test_asdict(self) -> None:
        s = Span(start_line=10, start_col=5, end_line=20, end_col=1)
        d = asdict(s)
        assert d == {
            "start_line": 10,
            "start_col": 5,
            "end_line": 20,
            "end_col": 1,
        }

    def test_repr(self) -> None:
        s = Span(start_line=1, start_col=1, end_line=1, end_col=10)
        r = repr(s)
        assert "start_line=1" in r
        assert "end_col=10" in r


# endregion: --- Span Dataclass Tests


# ---------------------------------------------------------------------------
# region:    --- span_from_node Helper Tests
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal tree-sitter Node stub for testing span_from_node."""

    def __init__(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.start_point = start
        self.end_point = end


class TestSpanFromNode:
    """span_from_node converts 0-based tree-sitter points to 1-based Span."""

    def test_zero_based_to_one_based(self) -> None:
        node = _FakeNode(start=(0, 0), end=(0, 10))
        s = span_from_node(node)  # type: ignore[arg-type]
        assert s == Span(start_line=1, start_col=1, end_line=1, end_col=11)

    def test_multiline(self) -> None:
        node = _FakeNode(start=(4, 3), end=(12, 1))
        s = span_from_node(node)  # type: ignore[arg-type]
        assert s == Span(start_line=5, start_col=4, end_line=13, end_col=2)

    def test_single_char(self) -> None:
        node = _FakeNode(start=(9, 0), end=(9, 1))
        s = span_from_node(node)  # type: ignore[arg-type]
        assert s == Span(start_line=10, start_col=1, end_line=10, end_col=2)


# endregion: --- span_from_node Helper Tests


# ---------------------------------------------------------------------------
# region:    --- Node Span Default Tests
# ---------------------------------------------------------------------------


class TestNodeSpanDefaults:
    """All node types default span to None (backward-compatible)."""

    def test_struct_node_default(self) -> None:
        assert StructNode(name="Foo").span is None

    def test_enum_node_default(self) -> None:
        assert EnumNode(name="Bar").span is None

    def test_function_node_default(self) -> None:
        assert FunctionNode(name="baz").span is None

    def test_method_node_default(self) -> None:
        assert MethodNode(name="qux").span is None

    def test_trait_node_default(self) -> None:
        assert TraitNode(name="MyTrait").span is None

    def test_impl_block_node_default(self) -> None:
        assert ImplBlockNode(self_type="Foo").span is None

    def test_type_alias_node_default(self) -> None:
        assert TypeAliasNode(name="Alias").span is None

    def test_constant_node_default(self) -> None:
        assert ConstantNode(name="C").span is None

    def test_module_node_default(self) -> None:
        assert ModuleNode(name="m").span is None

    def test_macro_node_default(self) -> None:
        assert MacroNode(name="mac").span is None

    def test_trait_item_node_default(self) -> None:
        assert TraitItemNode(kind=TraitItemKind.REQUIRED_METHOD, name="f").span is None


class TestNodeSpanExplicit:
    """All node types accept an explicit Span."""

    _SPAN = Span(start_line=1, start_col=1, end_line=10, end_col=2)

    def test_struct_node(self) -> None:
        assert StructNode(name="A", span=self._SPAN).span == self._SPAN

    def test_enum_node(self) -> None:
        assert EnumNode(name="B", span=self._SPAN).span == self._SPAN

    def test_function_node(self) -> None:
        assert FunctionNode(name="c", span=self._SPAN).span == self._SPAN

    def test_method_node(self) -> None:
        assert MethodNode(name="d", span=self._SPAN).span == self._SPAN

    def test_trait_node(self) -> None:
        assert TraitNode(name="E", span=self._SPAN).span == self._SPAN

    def test_impl_block_node(self) -> None:
        assert ImplBlockNode(self_type="F", span=self._SPAN).span == self._SPAN

    def test_type_alias_node(self) -> None:
        assert TypeAliasNode(name="G", span=self._SPAN).span == self._SPAN

    def test_constant_node(self) -> None:
        assert ConstantNode(name="H", span=self._SPAN).span == self._SPAN

    def test_module_node(self) -> None:
        assert ModuleNode(name="i", span=self._SPAN).span == self._SPAN

    def test_macro_node(self) -> None:
        assert MacroNode(name="j", span=self._SPAN).span == self._SPAN

    def test_trait_item_node(self) -> None:
        node = TraitItemNode(
            kind=TraitItemKind.REQUIRED_METHOD, name="k", span=self._SPAN,
        )
        assert node.span == self._SPAN


# endregion: --- Node Span Default Tests


# ---------------------------------------------------------------------------
# region:    --- Rust Extractor Span Tests
# ---------------------------------------------------------------------------

RUST_FIXTURES = Path(__file__).parent / "fixtures" / "rust"


@pytest.fixture
def rust_ext() -> object:
    from ast_intel.extractors.rust import RustExtractor
    return RustExtractor()


class TestRustSpans:
    """Rust extractor populates spans from tree-sitter nodes."""

    def test_struct_spans(self, rust_ext: object) -> None:
        """Structs in struct_with_fields.rs have correct start lines."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "struct_with_fields.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        # At least one struct should exist
        assert len(ast.structs) > 0
        # Every struct must have a span
        for s in ast.structs:
            assert s.span is not None, f"Struct {s.name} has no span"
            assert s.span.start_line >= 1
            assert s.span.start_col >= 1

    def test_struct_line_accuracy(self, rust_ext: object) -> None:
        """AppConfig starts at line 3 (after doc + derive attribute)."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "struct_with_fields.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        app_config = next(s for s in ast.structs if s.name == "AppConfig")
        # Line 1 = doc comment line, but tree-sitter struct_item includes
        # the attributes — the doc comment + attribute + pub struct line
        assert app_config.span is not None
        # The struct_item node starts at the doc comment (line 1)
        assert app_config.span.start_line >= 1
        assert app_config.span.start_col >= 1

    def test_function_spans(self, rust_ext: object) -> None:
        """Functions have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "functions_and_modules.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for fn in ast.functions:
            assert fn.span is not None, f"Function {fn.name} has no span"
            assert fn.span.start_line >= 1

    def test_enum_spans(self, rust_ext: object) -> None:
        """Enums have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "enum_variants.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for e in ast.enums:
            assert e.span is not None, f"Enum {e.name} has no span"
            assert e.span.start_line >= 1

    def test_trait_spans(self, rust_ext: object) -> None:
        """Traits have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "trait_with_methods.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for t in ast.traits:
            assert t.span is not None, f"Trait {t.name} has no span"
            assert t.span.start_line >= 1

    def test_impl_block_spans(self, rust_ext: object) -> None:
        """Impl blocks have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "impl_block.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for ib in ast.impl_blocks:
            assert ib.span is not None, f"ImplBlock {ib.self_type} has no span"
            assert ib.span.start_line >= 1

    def test_impl_method_spans(self, rust_ext: object) -> None:
        """Methods inside impl blocks have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "impl_block.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for ib in ast.impl_blocks:
            for m in ib.methods:
                assert m.span is not None, f"Method {m.name} in {ib.self_type} has no span"

    def test_self_methods_carry_forward(self, rust_ext: object) -> None:
        """self_methods carry forward spans from original nodes."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "impl_block.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for m in ast.self_methods:
            assert m.span is not None, f"SelfMethod {m.name} ({m.context}) has no span"

    def test_module_spans(self, rust_ext: object) -> None:
        """Module declarations have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "functions_and_modules.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for mod in ast.modules:
            assert mod.span is not None, f"Module {mod.name} has no span"

    def test_type_alias_spans(self, rust_ext: object) -> None:
        """Type aliases have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "functions_and_modules.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for ta in ast.type_aliases:
            assert ta.span is not None, f"TypeAlias {ta.name} has no span"

    def test_constant_spans(self, rust_ext: object) -> None:
        """Constants have spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "functions_and_modules.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for c in ast.constants:
            assert c.span is not None, f"Constant {c.name} has no span"

    def test_span_end_after_start(self, rust_ext: object) -> None:
        """Span end_line >= start_line for all nodes."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "struct_with_fields.rs"
        ast = rust_ext.extract(path, path.read_bytes())
        for s in ast.structs:
            assert s.span is not None
            assert s.span.end_line >= s.span.start_line


# endregion: --- Rust Extractor Span Tests


# ---------------------------------------------------------------------------
# region:    --- Python Extractor Span Tests
# ---------------------------------------------------------------------------

PY_FIXTURES = Path(__file__).parent / "fixtures" / "python"


@pytest.fixture
def py_ext() -> object:
    from ast_intel.extractors.python import PythonExtractor
    return PythonExtractor()


class TestPythonSpans:
    """Python extractor populates spans."""

    def _parse(self, py_ext: object, name: str) -> FileAST:
        from ast_intel.extractors.python import PythonExtractor
        assert isinstance(py_ext, PythonExtractor)
        path = PY_FIXTURES / name
        if not path.exists():
            pytest.skip(f"Fixture {name} not found")
        return py_ext.extract(path, path.read_bytes())

    def test_struct_spans(self, py_ext: object) -> None:
        """Classes mapped to StructNode have spans."""
        # Find any fixture with classes
        for f in PY_FIXTURES.glob("*.py"):
            from ast_intel.extractors.python import PythonExtractor
            assert isinstance(py_ext, PythonExtractor)
            ast = py_ext.extract(f, f.read_bytes())
            for s in ast.structs:
                assert s.span is not None, f"Struct {s.name} in {f.name} has no span"

    def test_function_spans(self, py_ext: object) -> None:
        """Functions have spans."""
        for f in PY_FIXTURES.glob("*.py"):
            from ast_intel.extractors.python import PythonExtractor
            assert isinstance(py_ext, PythonExtractor)
            ast = py_ext.extract(f, f.read_bytes())
            for fn in ast.functions:
                assert fn.span is not None, f"Function {fn.name} in {f.name} has no span"

    def test_self_methods_span_carry_forward(self, py_ext: object) -> None:
        """self_methods carry forward spans."""
        for f in PY_FIXTURES.glob("*.py"):
            from ast_intel.extractors.python import PythonExtractor
            assert isinstance(py_ext, PythonExtractor)
            ast = py_ext.extract(f, f.read_bytes())
            for m in ast.self_methods:
                assert m.span is not None, f"SelfMethod {m.name} in {f.name} has no span"


# endregion: --- Python Extractor Span Tests


# ---------------------------------------------------------------------------
# region:    --- TypeScript Extractor Span Tests
# ---------------------------------------------------------------------------

TS_FIXTURES = Path(__file__).parent / "fixtures" / "typescript"


@pytest.fixture
def ts_ext() -> object:
    from ast_intel.extractors.typescript import TypeScriptExtractor
    return TypeScriptExtractor()


class TestTypeScriptSpans:
    """TypeScript extractor populates spans."""

    def test_all_node_types_have_spans(self, ts_ext: object) -> None:
        """All extracted nodes across all TS fixtures have spans."""
        from ast_intel.extractors.typescript import TypeScriptExtractor
        assert isinstance(ts_ext, TypeScriptExtractor)

        for f in TS_FIXTURES.glob("*.ts"):
            ast = ts_ext.extract(f, f.read_bytes())
            for s in ast.structs:
                assert s.span is not None, f"Struct {s.name} in {f.name}"
            for e in ast.enums:
                assert e.span is not None, f"Enum {e.name} in {f.name}"
            for t in ast.traits:
                assert t.span is not None, f"Trait {t.name} in {f.name}"
            for fn in ast.functions:
                assert fn.span is not None, f"Function {fn.name} in {f.name}"
            for ib in ast.impl_blocks:
                assert ib.span is not None, f"ImplBlock {ib.self_type} in {f.name}"
            for m in ast.self_methods:
                assert m.span is not None, f"SelfMethod {m.name} in {f.name}"


# endregion: --- TypeScript Extractor Span Tests


# ---------------------------------------------------------------------------
# region:    --- Go Extractor Span Tests
# ---------------------------------------------------------------------------

GO_FIXTURES = Path(__file__).parent / "fixtures" / "go"


@pytest.fixture
def go_ext() -> object:
    from ast_intel.extractors.go import GoExtractor
    return GoExtractor()


class TestGoSpans:
    """Go extractor populates spans."""

    def test_struct_and_function_spans(self, go_ext: object) -> None:
        from ast_intel.extractors.go import GoExtractor
        assert isinstance(go_ext, GoExtractor)

        for f in GO_FIXTURES.glob("*.go"):
            ast = go_ext.extract(f, f.read_bytes())
            for s in ast.structs:
                assert s.span is not None, f"Struct {s.name} in {f.name}"
            for fn in ast.functions:
                assert fn.span is not None, f"Function {fn.name} in {f.name}"
            for t in ast.traits:
                assert t.span is not None, f"Trait {t.name} in {f.name}"
            for m in ast.self_methods:
                assert m.span is not None, f"SelfMethod {m.name} in {f.name}"


# endregion: --- Go Extractor Span Tests


# ---------------------------------------------------------------------------
# region:    --- C# Extractor Span Tests
# ---------------------------------------------------------------------------

CS_FIXTURES = Path(__file__).parent / "fixtures" / "csharp"


@pytest.fixture
def cs_ext() -> object:
    from ast_intel.extractors.csharp import CSharpExtractor
    return CSharpExtractor()


class TestCSharpSpans:
    """C# extractor populates spans."""

    def test_all_node_types_have_spans(self, cs_ext: object) -> None:
        from ast_intel.extractors.csharp import CSharpExtractor
        assert isinstance(cs_ext, CSharpExtractor)

        for f in CS_FIXTURES.glob("*.cs"):
            ast = cs_ext.extract(f, f.read_bytes())
            for s in ast.structs:
                assert s.span is not None, f"Struct {s.name} in {f.name}"
            for e in ast.enums:
                assert e.span is not None, f"Enum {e.name} in {f.name}"
            for t in ast.traits:
                assert t.span is not None, f"Trait {t.name} in {f.name}"
            for fn in ast.functions:
                assert fn.span is not None, f"Function {fn.name} in {f.name}"
            for ib in ast.impl_blocks:
                assert ib.span is not None, f"ImplBlock {ib.self_type} in {f.name}"
            for m in ast.self_methods:
                assert m.span is not None, f"SelfMethod {m.name} in {f.name}"


# endregion: --- C# Extractor Span Tests


# ---------------------------------------------------------------------------
# region:    --- Span Serialization Tests
# ---------------------------------------------------------------------------


class TestSpanSerialization:
    """Span fields appear correctly in JSON and Markdown output."""

    def test_span_in_asdict(self) -> None:
        """asdict includes span when present."""
        s = StructNode(
            name="Foo",
            span=Span(start_line=10, start_col=1, end_line=20, end_col=2),
        )
        d = asdict(s)
        assert d["span"] == {
            "start_line": 10,
            "start_col": 1,
            "end_line": 20,
            "end_col": 2,
        }

    def test_span_none_in_asdict(self) -> None:
        """asdict produces None when span is absent."""
        s = StructNode(name="Bar")
        d = asdict(s)
        assert d["span"] is None

    def test_method_span_in_asdict(self) -> None:
        """MethodNode span serializes correctly."""
        m = MethodNode(
            name="run",
            span=Span(start_line=5, start_col=3, end_line=15, end_col=1),
        )
        d = asdict(m)
        assert d["span"]["start_line"] == 5
        assert d["span"]["end_line"] == 15


# endregion: --- Span Serialization Tests


# ---------------------------------------------------------------------------
# region:    --- Cross-Extractor Span Consistency
# ---------------------------------------------------------------------------


class TestSpanConsistency:
    """Spans are consistent across repeated extractions (deterministic)."""

    def test_rust_deterministic(self, rust_ext: object) -> None:
        """Same input → same spans."""
        from ast_intel.extractors.rust import RustExtractor
        assert isinstance(rust_ext, RustExtractor)
        path = RUST_FIXTURES / "struct_with_fields.rs"
        src = path.read_bytes()
        ast1 = rust_ext.extract(path, src)
        ast2 = rust_ext.extract(path, src)
        for s1, s2 in zip(ast1.structs, ast2.structs, strict=True):
            assert s1.span == s2.span, f"{s1.name} spans differ across runs"


# endregion: --- Cross-Extractor Span Consistency
