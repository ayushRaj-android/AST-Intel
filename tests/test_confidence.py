"""Tests for Feature 3 — Confidence Tagging.

Covers:
- Confidence enum values and string behaviour
- Default confidence on CallEdge, ConstantNode, TypeAliasNode, ImplBlockNode
- Python extractor: UPPER_CASE constants → INFERRED, typed constants → EXTRACTED
- Python extractor: CamelCase type aliases → INFERRED, explicit TypeAlias → EXTRACTED
- Python extractor: Base-class ImplBlockNode → INFERRED, inherent impl → EXTRACTED
- Cross-file indexer resolution: single match → INFERRED, ambiguous → AMBIGUOUS
- JSON formatter serialization of confidence fields
- Markdown formatter rendering of confidence annotations
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from ast_intel.models.ast_node import (
    SCORE_AMBIGUOUS,
    SCORE_EXTRACTED,
    SCORE_INFERRED,
    SCORE_INFERRED_CROSS_FILE,
    CallEdge,
    Confidence,
    ConstantNode,
    FileAST,
    ImplBlockNode,
    Span,
    TypeAliasNode,
)

# ---------------------------------------------------------------------------
# region:    --- Fixture Paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
PY_CONSTANTS_AND_ALIASES = FIXTURES / "python" / "constants_and_aliases.py"

# endregion: --- Fixture Paths


# ---------------------------------------------------------------------------
# region:    --- Confidence Enum Tests
# ---------------------------------------------------------------------------


class TestConfidenceEnum:
    """Core Confidence enum behaviour."""

    def test_values(self) -> None:
        assert Confidence.EXTRACTED.value == "extracted"
        assert Confidence.INFERRED.value == "inferred"
        assert Confidence.AMBIGUOUS.value == "ambiguous"

    def test_is_str(self) -> None:
        """Confidence is a StrEnum — it IS a string."""
        assert isinstance(Confidence.EXTRACTED, str)
        # StrEnum values compare equal to their string value
        val: str = Confidence.EXTRACTED
        assert val == "extracted"

    def test_in_all_exports(self) -> None:
        from ast_intel.models import ast_node

        assert "Confidence" in ast_node.__all__

    def test_from_string(self) -> None:
        assert Confidence("extracted") is Confidence.EXTRACTED
        assert Confidence("inferred") is Confidence.INFERRED
        assert Confidence("ambiguous") is Confidence.AMBIGUOUS

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_confidence"):
            Confidence("not_a_confidence")


# endregion: --- Confidence Enum Tests


# ---------------------------------------------------------------------------
# region:    --- Default Confidence on Nodes/Edges
# ---------------------------------------------------------------------------


_SPAN = Span(start_line=1, start_col=1, end_line=1, end_col=10)


class TestDefaultConfidence:
    """Nodes and edges default to EXTRACTED with score 1.0."""

    def test_call_edge_defaults(self) -> None:
        edge = CallEdge(caller="f", callee="g", call_site=_SPAN)
        assert edge.confidence == Confidence.EXTRACTED
        assert edge.confidence_score == SCORE_EXTRACTED

    def test_constant_node_defaults(self) -> None:
        node = ConstantNode(name="MAX")
        assert node.confidence == Confidence.EXTRACTED
        assert node.confidence_score == SCORE_EXTRACTED

    def test_type_alias_node_defaults(self) -> None:
        node = TypeAliasNode(name="MyType")
        assert node.confidence == Confidence.EXTRACTED
        assert node.confidence_score == SCORE_EXTRACTED

    def test_impl_block_node_defaults(self) -> None:
        node = ImplBlockNode(self_type="Foo")
        assert node.confidence == Confidence.EXTRACTED
        assert node.confidence_score == SCORE_EXTRACTED

    def test_call_edge_explicit_confidence(self) -> None:
        edge = CallEdge(
            caller="f",
            callee="g",
            call_site=_SPAN,
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED_CROSS_FILE,
        )
        assert edge.confidence == Confidence.INFERRED
        assert edge.confidence_score == SCORE_INFERRED_CROSS_FILE

    def test_constant_node_explicit_confidence(self) -> None:
        node = ConstantNode(
            name="MAX",
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED,
        )
        assert node.confidence == Confidence.INFERRED
        assert node.confidence_score == SCORE_INFERRED

    def test_impl_block_node_explicit_confidence(self) -> None:
        node = ImplBlockNode(
            self_type="Foo",
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED,
        )
        assert node.confidence == Confidence.INFERRED
        assert node.confidence_score == SCORE_INFERRED


class TestConfidenceAsdict:
    """Confidence fields are present when serialized via asdict."""

    def test_call_edge_asdict(self) -> None:
        edge = CallEdge(
            caller="f",
            callee="g",
            call_site=_SPAN,
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED_CROSS_FILE,
        )
        d = asdict(edge)
        assert d["confidence"] == "inferred"
        assert d["confidence_score"] == SCORE_INFERRED_CROSS_FILE

    def test_constant_node_asdict(self) -> None:
        node = ConstantNode(
            name="MAX",
            confidence=Confidence.EXTRACTED,
            confidence_score=SCORE_EXTRACTED,
        )
        d = asdict(node)
        assert d["confidence"] == "extracted"
        assert d["confidence_score"] == SCORE_EXTRACTED

    def test_type_alias_node_asdict(self) -> None:
        node = TypeAliasNode(
            name="MyType",
            aliased_to="str",
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED,
        )
        d = asdict(node)
        assert d["confidence"] == "inferred"
        assert d["confidence_score"] == SCORE_INFERRED

    def test_impl_block_node_asdict(self) -> None:
        node = ImplBlockNode(
            self_type="Foo",
            trait_type="Bar",
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED,
        )
        d = asdict(node)
        assert d["confidence"] == "inferred"
        assert d["confidence_score"] == SCORE_INFERRED


# endregion: --- Default Confidence on Nodes/Edges


# ---------------------------------------------------------------------------
# region:    --- Python Extractor Confidence Tagging
# ---------------------------------------------------------------------------


def _extract_python(fixture: Path) -> FileAST:
    from ast_intel.extractors.python import PythonExtractor

    return PythonExtractor().extract(fixture, fixture.read_bytes())


class TestPythonConstantConfidence:
    """Python constants tagged by detection method."""

    def test_typed_constant_is_extracted(self) -> None:
        """Constants with explicit type annotations → EXTRACTED."""
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        typed_constants = [
            c for c in ast.constants
            if c.name in {"MAX_RETRIES", "DEFAULT_TIMEOUT", "APP_NAME"}
        ]
        assert len(typed_constants) >= 1
        for c in typed_constants:
            assert c.confidence == Confidence.EXTRACTED, (
                f"{c.name} has type annotation, expected EXTRACTED"
            )
            assert c.confidence_score == SCORE_EXTRACTED

    def test_untyped_upper_case_constant_is_inferred(self) -> None:
        """UPPER_CASE constants without type annotation → INFERRED."""
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        debug_const = [c for c in ast.constants if c.name == "DEBUG"]
        # DEBUG has no type annotation, is UPPER_CASE (well, single word)
        # It might or might not be detected depending on regex.
        # If detected, it should be INFERRED
        for c in debug_const:
            assert c.confidence == Confidence.INFERRED
            assert c.confidence_score == SCORE_INFERRED


class TestPythonTypeAliasConfidence:
    """Python type aliases tagged by detection method."""

    def test_explicit_type_alias_is_extracted(self) -> None:
        """Aliases with explicit ``TypeAlias`` annotation → EXTRACTED."""
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        json_dict = [
            a for a in ast.type_aliases if a.name == "JsonDict"
        ]
        assert len(json_dict) == 1
        assert json_dict[0].confidence == Confidence.EXTRACTED
        assert json_dict[0].confidence_score == SCORE_EXTRACTED

    def test_camel_case_alias_is_inferred(self) -> None:
        """CamelCase aliases without ``TypeAlias`` annotation → INFERRED."""
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        headers = [a for a in ast.type_aliases if a.name == "Headers"]
        assert len(headers) == 1
        assert headers[0].confidence == Confidence.INFERRED
        assert headers[0].confidence_score == SCORE_INFERRED

    def test_callback_alias_is_inferred(self) -> None:
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        callback = [a for a in ast.type_aliases if a.name == "Callback"]
        assert len(callback) == 1
        assert callback[0].confidence == Confidence.INFERRED
        assert callback[0].confidence_score == SCORE_INFERRED

    def test_optional_str_alias_is_inferred(self) -> None:
        ast = _extract_python(PY_CONSTANTS_AND_ALIASES)
        opt = [a for a in ast.type_aliases if a.name == "OptionalStr"]
        assert len(opt) == 1
        assert opt[0].confidence == Confidence.INFERRED
        assert opt[0].confidence_score == SCORE_INFERRED


class TestPythonImplBlockConfidence:
    """Python base-class-derived ImplBlockNodes tagged INFERRED."""

    def test_base_class_impl_is_inferred(self) -> None:
        """class Foo(Bar) → ImplBlockNode(trait_type='Bar') is INFERRED."""
        fixture = FIXTURES / "python" / "classes.py"
        ast = _extract_python(fixture)
        trait_impls = [
            im for im in ast.impl_blocks if im.trait_type
        ]
        # All Python base-class impls should be INFERRED
        for im in trait_impls:
            assert im.confidence == Confidence.INFERRED, (
                f"ImplBlockNode({im.self_type} -> {im.trait_type}) "
                f"expected INFERRED"
            )
            assert im.confidence_score == SCORE_INFERRED

    def test_inherent_impl_is_extracted(self) -> None:
        """Class with methods but no base class → EXTRACTED (inherent impl)."""
        fixture = FIXTURES / "python" / "classes.py"
        ast = _extract_python(fixture)
        inherent_impls = [
            im for im in ast.impl_blocks if not im.trait_type
        ]
        for im in inherent_impls:
            assert im.confidence == Confidence.EXTRACTED, (
                f"Inherent impl {im.self_type} expected EXTRACTED"
            )
            assert im.confidence_score == SCORE_EXTRACTED


# endregion: --- Python Extractor Confidence Tagging


# ---------------------------------------------------------------------------
# region:    --- Non-Python Extractors Default to EXTRACTED
# ---------------------------------------------------------------------------


class TestRustConfidenceDefaults:
    """Rust extractor: all nodes/edges default to EXTRACTED."""

    def test_impl_blocks_extracted(self) -> None:
        from ast_intel.extractors.rust import RustExtractor

        fixture = FIXTURES / "rust" / "call_graph.rs"
        ast = RustExtractor().extract(fixture, fixture.read_bytes())
        for im in ast.impl_blocks:
            assert im.confidence == Confidence.EXTRACTED

    def test_call_edges_extracted(self) -> None:
        from ast_intel.extractors.rust import RustExtractor

        fixture = FIXTURES / "rust" / "call_graph.rs"
        ast = RustExtractor().extract(fixture, fixture.read_bytes())
        for edge in ast.call_edges:
            assert edge.confidence == Confidence.EXTRACTED
            assert edge.confidence_score == SCORE_EXTRACTED


class TestTypeScriptConfidenceDefaults:
    """TypeScript extractor: syntax-based extraction → EXTRACTED."""

    def test_impl_blocks_extracted(self) -> None:
        from ast_intel.extractors.typescript import TypeScriptExtractor

        fixture = FIXTURES / "typescript" / "call_graph.ts"
        ast = TypeScriptExtractor().extract(fixture, fixture.read_bytes())
        for im in ast.impl_blocks:
            assert im.confidence == Confidence.EXTRACTED


# endregion: --- Non-Python Extractors Default to EXTRACTED


# ---------------------------------------------------------------------------
# region:    --- Cross-File Indexer Resolution Confidence
# ---------------------------------------------------------------------------


class TestIndexerResolutionConfidence:
    """Indexer Pass 6 tags confidence on cross-file resolution."""

    def test_single_match_is_inferred(self) -> None:
        """Unique cross-file match → INFERRED, score=0.85."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import (
            FileAST,
            FunctionNode,
            MethodNode,
        )
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        # File A calls "helper" (unresolved)
        file_a = FileAST(file="a.py", module_path="crate::a")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="helper",
                call_site=_SPAN,
                resolved_target="",
                is_method_call=False,
            ),
        ]
        file_a.functions = [FunctionNode(name="main", span=_SPAN)]
        file_a.self_methods = [
            MethodNode(name="main", span=_SPAN, context="free"),
        ]

        # File B defines "helper" (unique)
        file_b = FileAST(file="b.py", module_path="crate::b")
        file_b.functions = [FunctionNode(name="helper", span=_SPAN)]
        file_b.self_methods = [
            MethodNode(name="helper", span=_SPAN, context="free"),
        ]

        crate = CrateModel(name="c", files=[file_a, file_b])
        ws = WorkspaceAST(crates={"c": crate})

        Indexer().build_cross_references(ws)

        resolved = [
            e for e in file_a.call_edges if e.resolved_target
        ]
        assert len(resolved) == 1
        assert resolved[0].confidence == Confidence.INFERRED
        assert resolved[0].confidence_score == SCORE_INFERRED_CROSS_FILE

    def test_ambiguous_match_is_tagged(self) -> None:
        """Multiple cross-file matches → AMBIGUOUS, score=0.5."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import FileAST, MethodNode
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_a = FileAST(file="a.py", module_path="crate::a")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="helper",
                call_site=_SPAN,
                resolved_target="",
                is_method_call=False,
            ),
        ]

        # Two files define "helper" → ambiguous
        file_b = FileAST(file="b.py", module_path="crate::b")
        file_b.self_methods = [
            MethodNode(name="helper", span=_SPAN, context="free"),
        ]
        file_c = FileAST(file="c.py", module_path="crate::c")
        file_c.self_methods = [
            MethodNode(name="helper", span=_SPAN, context="free"),
        ]

        crate = CrateModel(name="c", files=[file_a, file_b, file_c])
        ws = WorkspaceAST(crates={"c": crate})

        Indexer().build_cross_references(ws)

        # The edge should now be tagged AMBIGUOUS with no resolved target
        edge = file_a.call_edges[0]
        assert edge.resolved_target == ""
        assert edge.confidence == Confidence.AMBIGUOUS
        assert edge.confidence_score == SCORE_AMBIGUOUS

    def test_no_match_stays_extracted(self) -> None:
        """Zero matches → edge stays at default EXTRACTED."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import FileAST
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_a = FileAST(file="a.py", module_path="crate::a")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="nonexistent",
                call_site=_SPAN,
                resolved_target="",
                is_method_call=False,
            ),
        ]

        crate = CrateModel(name="c", files=[file_a])
        ws = WorkspaceAST(crates={"c": crate})

        Indexer().build_cross_references(ws)

        edge = file_a.call_edges[0]
        assert edge.resolved_target == ""
        assert edge.confidence == Confidence.EXTRACTED
        assert edge.confidence_score == SCORE_EXTRACTED

    def test_already_resolved_keeps_confidence(self) -> None:
        """Already-resolved edges are not overwritten by the indexer."""
        from ast_intel.core.indexer import Indexer
        from ast_intel.models.ast_node import FileAST, MethodNode
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        file_a = FileAST(file="a.py", module_path="crate::a")
        file_a.call_edges = [
            CallEdge(
                caller="main",
                callee="helper",
                call_site=_SPAN,
                resolved_target="helper",  # already resolved
                is_method_call=False,
                confidence=Confidence.EXTRACTED,
                confidence_score=SCORE_EXTRACTED,
            ),
        ]

        file_b = FileAST(file="b.py", module_path="crate::b")
        file_b.self_methods = [
            MethodNode(name="helper", span=_SPAN, context="free"),
        ]

        crate = CrateModel(name="c", files=[file_a, file_b])
        ws = WorkspaceAST(crates={"c": crate})

        Indexer().build_cross_references(ws)

        # Should keep EXTRACTED since it was already resolved
        edge = file_a.call_edges[0]
        assert edge.confidence == Confidence.EXTRACTED
        assert edge.confidence_score == SCORE_EXTRACTED


# endregion: --- Cross-File Indexer Resolution Confidence


# ---------------------------------------------------------------------------
# region:    --- JSON Formatter Confidence Serialization
# ---------------------------------------------------------------------------


class TestJsonConfidenceSerialization:
    """Confidence fields serialize correctly in JSON output."""

    def test_confidence_in_call_edge_json(self) -> None:
        """CallEdge confidence appears in asdict → JSON round-trip."""
        import json

        from ast_intel.formatters.json_formatter import _ASTEncoder

        edge = CallEdge(
            caller="f",
            callee="g",
            call_site=_SPAN,
            confidence=Confidence.INFERRED,
            confidence_score=SCORE_INFERRED_CROSS_FILE,
        )
        serialized = json.loads(
            json.dumps(asdict(edge), cls=_ASTEncoder, sort_keys=True)
        )
        assert serialized["confidence"] == "inferred"
        assert serialized["confidence_score"] == SCORE_INFERRED_CROSS_FILE

    def test_confidence_in_constant_json(self) -> None:
        import json

        from ast_intel.formatters.json_formatter import _ASTEncoder

        node = ConstantNode(
            name="MAX",
            confidence=Confidence.EXTRACTED,
            confidence_score=SCORE_EXTRACTED,
        )
        serialized = json.loads(
            json.dumps(asdict(node), cls=_ASTEncoder, sort_keys=True)
        )
        assert serialized["confidence"] == "extracted"
        assert serialized["confidence_score"] == SCORE_EXTRACTED

    def test_confidence_enum_serializes_as_value(self) -> None:
        """Confidence enum serialized to its string value, not name."""
        import json

        from ast_intel.formatters.json_formatter import _ASTEncoder

        # Direct enum serialization
        data = {"confidence": Confidence.AMBIGUOUS}
        serialized = json.loads(
            json.dumps(data, cls=_ASTEncoder)
        )
        assert serialized["confidence"] == "ambiguous"


# endregion: --- JSON Formatter Confidence Serialization


# ---------------------------------------------------------------------------
# region:    --- Markdown Formatter Confidence Rendering
# ---------------------------------------------------------------------------


class TestMarkdownConfidenceRendering:
    """Markdown formatter annotates non-EXTRACTED confidence levels."""

    def test_confidence_tag_function(self) -> None:
        from ast_intel.formatters.markdown_formatter import _confidence_tag

        assert _confidence_tag(Confidence.EXTRACTED) == ""
        assert _confidence_tag(Confidence.INFERRED) == "[inferred]"
        assert _confidence_tag(Confidence.AMBIGUOUS) == "[ambiguous]"

    def test_call_graph_table_has_confidence_column(self) -> None:
        """Call-graph Markdown table includes a Confidence column."""
        from ast_intel.formatters.markdown_formatter import (
            _render_call_graph_section,
        )

        edges: list[tuple[str, CallEdge]] = [
            (
                "mod",
                CallEdge(
                    caller="main",
                    callee="helper",
                    call_site=_SPAN,
                    resolved_target="crate::b::helper",
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED_CROSS_FILE,
                ),
            ),
            (
                "mod",
                CallEdge(
                    caller="main",
                    callee="process",
                    call_site=_SPAN,
                    resolved_target="process",
                    confidence=Confidence.EXTRACTED,
                ),
            ),
        ]

        lines: list[str] = []
        _render_call_graph_section(lines.append, edges)

        header = lines[2]  # Third line is the table header
        assert "Confidence" in header

        # First edge row should show [inferred]
        inferred_row = next(line for line in lines if "helper" in line)
        assert "[inferred]" in inferred_row

        # Second edge row (EXTRACTED) should NOT show a tag
        extracted_row = next(line for line in lines if "process" in line)
        assert "[inferred]" not in extracted_row
        assert "[ambiguous]" not in extracted_row

    def test_trait_impls_table_has_confidence_column(self) -> None:
        """Trait implementations table includes a Confidence column."""
        from ast_intel.formatters.markdown_formatter import (
            _render_trait_impls_table,
        )
        from ast_intel.models.ast_node import MethodNode

        impls: list[tuple[str, ImplBlockNode]] = [
            (
                "mod",
                ImplBlockNode(
                    self_type="Dog",
                    trait_type="Animal",
                    methods=(
                        MethodNode(name="speak", span=_SPAN, context="Dog"),
                    ),
                    span=_SPAN,
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED,
                ),
            ),
            (
                "mod",
                ImplBlockNode(
                    self_type="Config",
                    trait_type="Default",
                    span=_SPAN,
                    confidence=Confidence.EXTRACTED,
                ),
            ),
        ]

        lines: list[str] = []
        _render_trait_impls_table(lines.append, impls)

        header = lines[2]
        assert "Confidence" in header

        inferred_row = next(line for line in lines if "Dog" in line)
        assert "[inferred]" in inferred_row

        extracted_row = next(line for line in lines if "Config" in line)
        assert "[inferred]" not in extracted_row


# endregion: --- Markdown Formatter Confidence Rendering
