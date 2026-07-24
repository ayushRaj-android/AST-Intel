"""Comprehensive tests for Phase 3 — JSON formatter, Markdown formatter, and Emitter.

Covers:
- JSON formatter: round-trip, determinism, schema structure, meta population
- Markdown formatter: section structure, cross-ref tables, edge cases
- Emitter: format routing, file creation, format-specific output
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ast_intel.core.emitter import AST_JSON_FILENAME, SUMMARY_MD_FILENAME, Emitter
from ast_intel.core.indexer import Indexer
from ast_intel.formatters.json_formatter import JsonFormatter
from ast_intel.formatters.markdown_formatter import MarkdownFormatter
from ast_intel.models.ast_node import (
    ConstantNode,
    EnumNode,
    EnumVariantKind,
    EnumVariantNode,
    FieldNode,
    FileAST,
    FunctionNode,
    ImplBlockNode,
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
from ast_intel.models.workspace_model import (
    CrateDependency,
    CrateModel,
    WorkspaceAST,
    WorkspaceMeta,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Provide a temporary output directory."""
    return tmp_path / "output"


def _rich_workspace() -> WorkspaceAST:
    """Build a fully populated workspace for testing."""
    file1 = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        structs=[
            StructNode(
                name="AppConfig",
                visibility=Visibility.PUBLIC,
                generics="<T>",
                fields=(
                    FieldNode(name="host", type="String", visibility=Visibility.PUBLIC),
                    FieldNode(name="port", type="u16", visibility=Visibility.PUBLIC),
                ),
                doc="Main application configuration.",
            ),
        ],
        enums=[
            EnumNode(
                name="Error",
                visibility=Visibility.PUBLIC,
                variants=(
                    EnumVariantNode(name="NotFound", kind=EnumVariantKind.UNIT),
                    EnumVariantNode(name="Timeout", kind=EnumVariantKind.UNIT),
                ),
            ),
        ],
        traits=[
            TraitNode(
                name="Handler",
                visibility=Visibility.PUBLIC,
                super_traits=("Send", "Sync"),
                items=(
                    TraitItemNode(
                        kind=TraitItemKind.REQUIRED_METHOD,
                        name="handle",
                        is_async=True,
                        params=(ParamNode(name="request", type="Request"),),
                        return_type="Response",
                    ),
                    TraitItemNode(
                        kind=TraitItemKind.DEFAULT_METHOD,
                        name="health",
                        params=(),
                        return_type="bool",
                    ),
                    TraitItemNode(
                        kind=TraitItemKind.ASSOCIATED_TYPE,
                        name="Output",
                    ),
                ),
                doc="Request handler trait.",
            ),
        ],
        functions=[
            FunctionNode(
                name="main",
                visibility=Visibility.PUBLIC,
                is_async=True,
                params=(ParamNode(name="args", type="Args"),),
                return_type="Result<()>",
            ),
        ],
        impl_blocks=[
            ImplBlockNode(
                self_type="MyHandler",
                trait_type="Handler",
                methods=(
                    MethodNode(name="handle", visibility=Visibility.PUBLIC, is_async=True),
                ),
            ),
            ImplBlockNode(
                self_type="MyHandler",
                trait_type="",
                methods=(
                    MethodNode(name="new", visibility=Visibility.PUBLIC),
                ),
            ),
        ],
        type_aliases=[TypeAliasNode(name="Result", aliased_to="std::result::Result<T, Error>")],
        constants=[
            ConstantNode(
                name="VERSION",
                visibility=Visibility.PUBLIC,
                raw='const VERSION: &str = "1.0"',
            ),
        ],
        modules=[ModuleNode(name="handlers", visibility=Visibility.PUBLIC)],
        self_methods=[
            MethodNode(name="handle", context="impl:Handler for MyHandler", is_async=True),
            MethodNode(name="new", context="impl:MyHandler"),
            MethodNode(name="main", context="free", is_async=True),
        ],
        imported_package_methods={
            "bytes::BytesMut": ["with_capacity", "from"],
            "tokio::spawn": ["spawn"],
        },
    )

    crate = CrateModel(
        name="my-crate",
        version="0.1.0",
        manifest_path="Cargo.toml",
        language="rust",
        dependencies=[
            CrateDependency(name="lib-common", path="../libs/lib-common"),
            CrateDependency(name="tokio", version="1.32"),
        ],
        files=[file1],
    )
    lib = CrateModel(name="lib-common", language="rust")

    ws = WorkspaceAST(
        crates={"my-crate": crate, "lib-common": lib},
    )

    # Run indexer so cross-references are populated
    indexer = Indexer()
    indexer.build_cross_references(ws)
    return ws


@pytest.fixture
def rich_ws() -> WorkspaceAST:
    """Provide a fully populated workspace."""
    return _rich_workspace()


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- JSON Formatter Tests
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    """Tests for the JSON formatter."""

    def test_produces_valid_json(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output file is valid JSON."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_top_level_keys(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """JSON has meta, crates, cross_references at top level."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "meta" in data
        assert "crates" in data
        assert "cross_references" in data

    def test_meta_has_schema_version(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """meta block contains schema_version and generated_at."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        meta = data["meta"]
        assert meta["schema_version"] == "1.0.0"
        assert "generated_at" in meta
        assert len(meta["generated_at"]) > 0

    def test_meta_statistics(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """meta block computes correct statistics."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        meta = data["meta"]
        assert meta["total_crates"] == 2
        assert meta["total_files"] == 1
        assert meta["total_structs"] == 1
        assert meta["total_enums"] == 1
        assert meta["total_traits"] == 1
        assert meta["total_functions"] == 1
        assert meta["total_impl_blocks"] == 2
        assert meta["total_self_methods"] == 3

    def test_round_trip(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """JSON round-trips: loads → dumps → identical."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        raw = json_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        re_encoded = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert raw == re_encoded

    def test_determinism(self, tmp_output: Path) -> None:
        """Two runs on identical input produce byte-identical output."""
        path1 = tmp_output / "run1" / "ast.json"
        path2 = tmp_output / "run2" / "ast.json"

        ws1 = _rich_workspace()
        ws2 = _rich_workspace()

        # Force identical timestamps
        fixed_meta = WorkspaceMeta(
            schema_version="1.0.0",
            tool_version="0.1.0",
            generated_at="2025-01-01T00:00:00+00:00",
            total_crates=2,
            total_files=1,
            total_structs=1,
            total_enums=1,
            total_traits=1,
            total_functions=1,
            total_impl_blocks=2,
            total_self_methods=3,
            total_pkg_method_call_sites=2,
        )
        ws1.meta = fixed_meta
        ws2.meta = fixed_meta

        fmt = JsonFormatter()
        fmt.write(ws1, path1)
        fmt.write(ws2, path2)

        assert path1.read_bytes() == path2.read_bytes()

    def test_crates_sorted_by_name(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crates in JSON are sorted alphabetically."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        crate_names = list(data["crates"].keys())
        assert crate_names == sorted(crate_names)

    def test_cross_references_present(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Cross-references are serialized with all expected keys."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        xref = data["cross_references"]
        expected_keys = {
            "struct_index",
            "enum_index",
            "trait_index",
            "trait_implementations",
            "function_file_index",
            "package_method_index",
            "inter_crate_deps",
            "impl_map",
        }
        assert set(xref.keys()) == expected_keys

    def test_struct_in_cross_references(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Struct index contains the expected entries."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        xref = data["cross_references"]
        assert "AppConfig" in xref["struct_index"]

    def test_function_file_index_entries(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Function file index entries are dicts with expected keys."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        fn_index = data["cross_references"]["function_file_index"]
        assert "handle" in fn_index
        entry = fn_index["handle"][0]
        assert "file" in entry
        assert "module_path" in entry
        assert "is_async" in entry
        assert "context" in entry

    def test_empty_workspace(self, tmp_output: Path) -> None:
        """Empty workspace produces valid JSON with zero stats."""
        ws = WorkspaceAST()
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["meta"]["total_files"] == 0
        assert data["crates"] == {}

    def test_trailing_newline(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """JSON file ends with a newline."""
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(rich_ws, json_path)

        raw = json_path.read_bytes()
        assert raw.endswith(b"\n")

    def test_files_sorted_within_crate(self, tmp_output: Path) -> None:
        """Files within a crate are sorted by file path."""
        crate = CrateModel(
            name="test",
            language="rust",
            files=[
                FileAST(file="src/z.rs"),
                FileAST(file="src/a.rs"),
                FileAST(file="src/m.rs"),
            ],
        )
        ws = WorkspaceAST(crates={"test": crate})
        fmt = JsonFormatter()
        json_path = tmp_output / "ast.json"
        fmt.write(ws, json_path)

        data = json.loads(json_path.read_text(encoding="utf-8"))
        files = [f["file"] for f in data["crates"]["test"]["files"]]
        assert files == ["src/a.rs", "src/m.rs", "src/z.rs"]


# endregion: --- JSON Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- Markdown Formatter Tests
# ---------------------------------------------------------------------------


class TestMarkdownFormatter:
    """Tests for the Markdown formatter."""

    def test_produces_file(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output file is created."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        assert md_path.exists()
        content = md_path.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_has_overview_table(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output contains the overview table with key metrics."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "## Workspace Overview" in content
        assert "| Metric | Count |" in content
        assert "Structs" in content
        assert "Enums" in content
        assert "Traits" in content

    def test_has_crate_reference(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output contains per-crate reference sections."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "## Crate Reference" in content
        assert "### `my-crate`" in content

    def test_has_structs_table(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes structs table."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Structs" in content
        assert "AppConfig" in content
        assert "host: String" in content

    def test_has_enums_table(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes enums table."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Enums" in content
        assert "Error" in content
        assert "NotFound" in content

    def test_has_traits_section(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes detailed traits."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Traits" in content
        assert "Handler" in content
        assert "Send" in content  # super trait
        assert "Required methods:" in content
        assert "async handle" in content

    def test_has_trait_impls_table(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes trait implementation table."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Trait Implementations" in content
        assert "Handler" in content
        assert "MyHandler" in content

    def test_has_public_functions(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes public functions."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Public Functions" in content
        assert "main" in content

    def test_has_inter_crate_deps(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output includes inter-crate dependency graph."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "## Inter-Crate Dependency Graph" in content
        assert "lib-common" in content

    def test_has_global_cross_refs(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Output includes global cross-reference index."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "## Global Cross-Reference Index" in content
        assert "### Trait Implementation Map" in content
        assert "### Type Index (Structs)" in content
        assert "### Type Index (Enums)" in content
        assert "### Trait Index" in content

    def test_module_tree_rendered(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Crate section includes module file tree."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "#### Module File Tree" in content
        assert "src/lib.rs" in content

    def test_workspace_deps_shown(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Workspace deps for a crate are listed."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "**Workspace deps**:" in content

    def test_empty_workspace(self, tmp_output: Path) -> None:
        """Empty workspace produces minimal valid markdown."""
        ws = WorkspaceAST()
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "# AST Intel" in content

    def test_trailing_newline(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Markdown file ends with a newline."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        raw = md_path.read_bytes()
        assert raw.endswith(b"\n")

    def test_associated_types_shown(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Associated types from traits are rendered."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "Associated types:" in content
        assert "Output" in content

    def test_default_methods_shown(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Default methods from traits are rendered."""
        fmt = MarkdownFormatter()
        md_path = tmp_output / "summary.md"
        fmt.write(rich_ws, md_path)

        content = md_path.read_text(encoding="utf-8")
        assert "Default methods:" in content
        assert "health" in content


# endregion: --- Markdown Formatter Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Tests
# ---------------------------------------------------------------------------


class TestEmitter:
    """Tests for the Emitter orchestrator."""

    def test_both_format(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """format='both' creates ast.json, summary.md, and graph.json."""
        emitter = Emitter(output_dir=tmp_output, output_format="both")
        written = emitter.emit(rich_ws)

        assert len(written) == 3
        assert (tmp_output / AST_JSON_FILENAME).exists()
        assert (tmp_output / SUMMARY_MD_FILENAME).exists()
        assert (tmp_output / "graph.json").exists()

    def test_json_only(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """format='json' creates only ast.json."""
        emitter = Emitter(output_dir=tmp_output, output_format="json")
        written = emitter.emit(rich_ws)

        assert len(written) == 1
        assert (tmp_output / AST_JSON_FILENAME).exists()
        assert not (tmp_output / SUMMARY_MD_FILENAME).exists()

    def test_md_only(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """format='md' creates only summary.md."""
        emitter = Emitter(output_dir=tmp_output, output_format="md")
        written = emitter.emit(rich_ws)

        assert len(written) == 1
        assert not (tmp_output / AST_JSON_FILENAME).exists()
        assert (tmp_output / SUMMARY_MD_FILENAME).exists()

    def test_creates_output_dir(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Emitter creates the output directory if it doesn't exist."""
        nested = tmp_output / "deep" / "nested"
        emitter = Emitter(output_dir=nested, output_format="json")
        emitter.emit(rich_ws)

        assert nested.exists()
        assert (nested / AST_JSON_FILENAME).exists()

    def test_returns_written_paths(self, rich_ws: WorkspaceAST, tmp_output: Path) -> None:
        """Emitter returns paths of all files written."""
        emitter = Emitter(output_dir=tmp_output, output_format="both")
        written = emitter.emit(rich_ws)

        assert all(p.exists() for p in written)
        names = {p.name for p in written}
        assert AST_JSON_FILENAME in names
        assert SUMMARY_MD_FILENAME in names

    def test_meta_populated_after_emit(self, tmp_output: Path) -> None:
        """After emit, workspace.meta is populated with statistics."""
        ws = _rich_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="json")
        emitter.emit(ws)

        assert ws.meta.total_files == 1
        assert ws.meta.total_crates == 2
        assert len(ws.meta.generated_at) > 0

    def test_invalid_format_raises(self, tmp_output: Path) -> None:
        """Invalid output_format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid output_format"):
            Emitter(output_dir=tmp_output, output_format="xml")  # type: ignore[arg-type]

    def test_invalid_format_empty_string(self, tmp_output: Path) -> None:
        """Empty string output_format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid output_format"):
            Emitter(output_dir=tmp_output, output_format="")  # type: ignore[arg-type]


# endregion: --- Emitter Tests


# ---------------------------------------------------------------------------
# region:    --- JSON Encoder Edge Cases
# ---------------------------------------------------------------------------


class TestASTEncoder:
    """Direct tests for _ASTEncoder to cover edge-case types."""

    def test_encodes_set(self) -> None:
        """Sets are sorted and converted to lists."""
        from ast_intel.formatters.json_formatter import _ASTEncoder

        result = json.loads(json.dumps({"s": {"c", "a", "b"}}, cls=_ASTEncoder))
        assert result["s"] == ["a", "b", "c"]

    def test_encodes_frozenset(self) -> None:
        """Frozensets are sorted and converted to lists."""
        from ast_intel.formatters.json_formatter import _ASTEncoder

        result = json.loads(
            json.dumps({"fs": frozenset({3, 1, 2})}, cls=_ASTEncoder),
        )
        assert result["fs"] == [1, 2, 3]

    def test_encodes_tuple(self) -> None:
        """Tuples are converted to lists."""
        from ast_intel.formatters.json_formatter import _ASTEncoder

        result = json.loads(json.dumps({"t": (1, 2, 3)}, cls=_ASTEncoder))
        assert result["t"] == [1, 2, 3]

    def test_encodes_path(self) -> None:
        """Path objects are converted to strings."""
        from ast_intel.formatters.json_formatter import _ASTEncoder

        result = json.loads(
            json.dumps({"p": Path("/some/path")}, cls=_ASTEncoder),
        )
        assert result["p"] == "/some/path"

    def test_encodes_enum(self) -> None:
        """Enum values use .value."""
        from ast_intel.formatters.json_formatter import _ASTEncoder

        result = json.loads(
            json.dumps({"v": Visibility.PUBLIC}, cls=_ASTEncoder),
        )
        assert result["v"] == "pub"


# endregion: --- JSON Encoder Edge Cases
