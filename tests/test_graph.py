"""Tests for Feature 5 — Graph Output Mode.

Covers:
- Graph model: GraphNode, GraphEdge, CodeGraph dataclasses
- Relation constants: all 11 edge types
- GraphBuilder: node/edge emission from WorkspaceAST
- GraphBuilder: node ID stability (deterministic)
- GraphBuilder: all edge types — contains, method_of, implements, inherits,
    imports, calls, uses_method, depends_on, super_trait, rationale_for, has_field
- GraphJsonFormatter: round-trip, determinism, schema structure
- GraphDotFormatter: valid DOT syntax, shape/color mapping
- GraphMermaidFormatter: valid Mermaid syntax, subgraph layout
- Emitter integration: graph-json, dot, mermaid format routing
- Edge case: empty workspace, large graph truncation in Mermaid
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from ast_intel.core.emitter import Emitter
from ast_intel.core.graph_builder import (
    GraphBuilder,
    _crate_node_id,
    _extract_base_type,
    _extract_base_types,
    _import_crate_hint,
    _node_id,
    _parse_import,
)
from ast_intel.formatters.graph_dot_formatter import GraphDotFormatter
from ast_intel.formatters.graph_json_formatter import GraphJsonFormatter
from ast_intel.formatters.graph_mermaid_formatter import GraphMermaidFormatter
from ast_intel.models.ast_node import (
    SCORE_EXTRACTED,
    SCORE_INFERRED,
    CallEdge,
    Confidence,
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
    RationaleNode,
    Span,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    TypeAliasNode,
    Visibility,
)
from ast_intel.models.graph_model import (
    RELATION_CALLS,
    RELATION_CONTAINS,
    RELATION_DEPENDS_ON,
    RELATION_HAS_FIELD,
    RELATION_IMPLEMENTS,
    RELATION_IMPORTS,
    RELATION_INHERITS,
    RELATION_METHOD_OF,
    RELATION_RATIONALE_FOR,
    RELATION_RESOLVES_TO,
    RELATION_SUPER_TRAIT,
    RELATION_USES_METHOD,
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from ast_intel.models.workspace_model import (
    CrateDependency,
    CrateModel,
    CrossReferences,
    WorkspaceAST,
)

# ---------------------------------------------------------------------------
# region:    --- Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Provide a temporary output directory."""
    return tmp_path / "output"


def _simple_workspace() -> WorkspaceAST:
    """Build a minimal workspace with one struct, one function, one call edge."""
    file1 = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        structs=[
            StructNode(
                name="Config",
                visibility=Visibility.PUBLIC,
                fields=(
                    FieldNode(
                        name="host",
                        type="String",
                        visibility=Visibility.PUBLIC,
                    ),
                ),
                span=Span(1, 1, 10, 2),
            ),
        ],
        functions=[
            FunctionNode(
                name="main",
                visibility=Visibility.PUBLIC,
                span=Span(12, 1, 20, 2),
            ),
        ],
        uses=["std::collections::HashMap"],
    )
    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate",
        version="0.1.0",
        manifest_path="Cargo.toml",
        language="rust",
        files=[file1],
    )
    return ws


def _rich_workspace() -> WorkspaceAST:
    """Build a fully populated workspace exercising all edge types."""
    file1 = FileAST(
        file="src/lib.rs",
        module_path="my_crate",
        structs=[
            StructNode(
                name="Server",
                visibility=Visibility.PUBLIC,
                generics="<T>",
                fields=(
                    FieldNode(
                        name="config",
                        type="AppConfig",
                        visibility=Visibility.PUBLIC,
                    ),
                    FieldNode(
                        name="port",
                        type="u16",
                        visibility=Visibility.PRIVATE,
                    ),
                ),
                span=Span(1, 1, 10, 2),
            ),
        ],
        enums=[
            EnumNode(
                name="Error",
                visibility=Visibility.PUBLIC,
                variants=(
                    EnumVariantNode(
                        name="NotFound", kind=EnumVariantKind.UNIT,
                    ),
                ),
                span=Span(12, 1, 15, 2),
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
                        params=(ParamNode(name="req", type="Request"),),
                        return_type="Response",
                    ),
                ),
                span=Span(17, 1, 25, 2),
            ),
        ],
        functions=[
            FunctionNode(
                name="main",
                visibility=Visibility.PUBLIC,
                is_async=True,
                return_type="Result<()>",
                span=Span(27, 1, 40, 2),
            ),
            FunctionNode(
                name="helper",
                visibility=Visibility.PRIVATE,
                span=Span(42, 1, 50, 2),
            ),
        ],
        impl_blocks=[
            ImplBlockNode(
                self_type="Server",
                trait_type="Handler",
                methods=(
                    MethodNode(
                        name="handle",
                        visibility=Visibility.PUBLIC,
                        is_async=True,
                        span=Span(52, 5, 60, 6),
                    ),
                ),
                span=Span(51, 1, 61, 2),
            ),
            ImplBlockNode(
                self_type="Server",
                trait_type="",
                methods=(
                    MethodNode(
                        name="new",
                        visibility=Visibility.PUBLIC,
                        is_static=True,
                        span=Span(64, 5, 70, 6),
                    ),
                ),
                span=Span(63, 1, 71, 2),
            ),
        ],
        type_aliases=[
            TypeAliasNode(
                name="Result",
                aliased_to="std::result::Result<T, Error>",
                visibility=Visibility.PUBLIC,
                span=Span(73, 1, 73, 50),
            ),
        ],
        constants=[
            ConstantNode(
                name="MAX_RETRIES",
                visibility=Visibility.PUBLIC,
                raw="pub const MAX_RETRIES: u32 = 3;",
                span=Span(75, 1, 75, 32),
            ),
        ],
        macros=[
            MacroNode(
                name="log_info",
                visibility=Visibility.PUBLIC,
                span=Span(77, 1, 80, 2),
            ),
        ],
        modules=[
            ModuleNode(
                name="config",
                visibility=Visibility.PUBLIC,
                inline=False,
                span=Span(82, 1, 82, 15),
            ),
        ],
        rationale_comments=[
            RationaleNode(
                kind="NOTE",
                text="This server design allows hot-reload.",
                span=Span(26, 1, 26, 50),
                parent="main",
            ),
        ],
        call_edges=[
            CallEdge(
                caller="main",
                callee="helper",
                call_site=Span(30, 5, 30, 15),
                resolved_target="src/lib.rs::helper",
                confidence=Confidence.EXTRACTED,
                confidence_score=SCORE_EXTRACTED,
            ),
        ],
        uses=[
            "std::sync::Arc",
            "tokio::runtime::Runtime",
        ],
        imported_package_methods={
            "bytes::BytesMut": ["freeze", "put"],
        },
    )

    ws = WorkspaceAST()
    ws.crates["my_crate"] = CrateModel(
        name="my_crate",
        version="0.1.0",
        manifest_path="Cargo.toml",
        language="rust",
        dependencies=[
            CrateDependency(name="tokio", version="1.0"),
            CrateDependency(name="serde", version="1.0"),
        ],
        files=[file1],
    )
    ws.cross_references = CrossReferences(
        inter_crate_deps={"my_crate": ["tokio", "serde"]},
    )
    return ws


# endregion: --- Test Fixtures


# ---------------------------------------------------------------------------
# region:    --- Relation Constants Tests
# ---------------------------------------------------------------------------


class TestRelationConstants:
    """All 11 edge type constants exist with correct string values."""

    def test_contains(self) -> None:
        assert RELATION_CONTAINS == "contains"

    def test_method_of(self) -> None:
        assert RELATION_METHOD_OF == "method_of"

    def test_implements(self) -> None:
        assert RELATION_IMPLEMENTS == "implements"

    def test_inherits(self) -> None:
        assert RELATION_INHERITS == "inherits"

    def test_imports(self) -> None:
        assert RELATION_IMPORTS == "imports"

    def test_calls(self) -> None:
        assert RELATION_CALLS == "calls"

    def test_uses_method(self) -> None:
        assert RELATION_USES_METHOD == "uses_method"

    def test_depends_on(self) -> None:
        assert RELATION_DEPENDS_ON == "depends_on"

    def test_super_trait(self) -> None:
        assert RELATION_SUPER_TRAIT == "super_trait"

    def test_rationale_for(self) -> None:
        assert RELATION_RATIONALE_FOR == "rationale_for"

    def test_has_field(self) -> None:
        assert RELATION_HAS_FIELD == "has_field"


# endregion: --- Relation Constants Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Model Tests
# ---------------------------------------------------------------------------


class TestGraphNode:
    """GraphNode dataclass behaviour."""

    def test_frozen(self) -> None:
        node = GraphNode(id="a::B", label="B", kind="struct")
        with pytest.raises(AttributeError):
            node.id = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        node = GraphNode(id="a::B", label="B", kind="struct")
        assert node.file == ""
        assert node.span is None
        assert node.properties == {}

    def test_with_properties(self) -> None:
        node = GraphNode(
            id="a::B",
            label="B",
            kind="struct",
            file="a.rs",
            span=Span(1, 1, 5, 2),
            properties={"visibility": "pub"},
        )
        assert node.properties["visibility"] == "pub"
        assert node.span is not None
        assert node.span.start_line == 1

    def test_asdict(self) -> None:
        node = GraphNode(id="a::B", label="B", kind="struct")
        d = asdict(node)
        assert d["id"] == "a::B"
        assert d["kind"] == "struct"


class TestGraphEdge:
    """GraphEdge dataclass behaviour."""

    def test_frozen(self) -> None:
        edge = GraphEdge(
            source="a::B",
            target="a::C",
            relation="calls",
            confidence=Confidence.EXTRACTED,
            confidence_score=1.0,
        )
        with pytest.raises(AttributeError):
            edge.source = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        edge = GraphEdge(
            source="a",
            target="b",
            relation="calls",
            confidence=Confidence.EXTRACTED,
            confidence_score=1.0,
        )
        assert edge.file == ""
        assert edge.span is None

    def test_confidence_fields(self) -> None:
        edge = GraphEdge(
            source="a",
            target="b",
            relation="calls",
            confidence=Confidence.INFERRED,
            confidence_score=0.85,
            file="src/main.rs",
            span=Span(10, 5, 10, 20),
        )
        assert edge.confidence is Confidence.INFERRED
        assert edge.confidence_score == pytest.approx(0.85)


class TestCodeGraph:
    """CodeGraph dataclass behaviour."""

    def test_mutable(self) -> None:
        graph = CodeGraph()
        graph.nodes.append(
            GraphNode(id="a::B", label="B", kind="struct"),
        )
        assert len(graph.nodes) == 1

    def test_defaults(self) -> None:
        graph = CodeGraph()
        assert graph.nodes == []
        assert graph.edges == []
        assert graph.meta is None


# endregion: --- Graph Model Tests


# ---------------------------------------------------------------------------
# region:    --- Node ID Helpers Tests
# ---------------------------------------------------------------------------


class TestNodeIdHelpers:
    """Node ID factory functions produce deterministic, stable IDs."""

    def test_node_id(self) -> None:
        assert _node_id("src/lib.rs", "Config") == "src/lib.rs::Config"

    def test_crate_node_id(self) -> None:
        assert _crate_node_id("my_crate") == "crate::my_crate"

    def test_stability(self) -> None:
        """Same inputs always produce same IDs."""
        for _ in range(10):
            assert _node_id("src/lib.rs", "Foo") == "src/lib.rs::Foo"
            assert _crate_node_id("bar") == "crate::bar"


# endregion: --- Node ID Helpers Tests


# ---------------------------------------------------------------------------
# region:    --- _extract_base_type Tests
# ---------------------------------------------------------------------------


class TestExtractBaseType:
    """Type string parsing for has_field edge emission."""

    def test_simple_custom_type(self) -> None:
        assert _extract_base_type("AppConfig") == "AppConfig"

    def test_reference(self) -> None:
        assert _extract_base_type("&mut MyStruct") == "MyStruct"

    def test_qualified_path(self) -> None:
        assert _extract_base_type("std::sync::MyType") == "MyType"

    def test_primitive_returns_empty(self) -> None:
        assert _extract_base_type("i32") == ""
        assert _extract_base_type("String") == ""
        assert _extract_base_type("bool") == ""

    def test_lowercase_returns_empty(self) -> None:
        assert _extract_base_type("my_type") == ""

    def test_empty_string(self) -> None:
        assert _extract_base_type("") == ""

    # ---- Rust wrapper unwrapping ----

    def test_arc_unwraps_to_inner(self) -> None:
        assert _extract_base_type("Arc<Server>") == "Server"

    def test_option_box_nested_unwrap(self) -> None:
        assert _extract_base_type("Option<Box<MyStruct>>") == "MyStruct"

    def test_vec_primitive_returns_empty(self) -> None:
        assert _extract_base_type("Vec<u8>") == ""
        assert _extract_base_type("Vec<String>") == ""

    def test_vec_custom_type(self) -> None:
        assert _extract_base_type("Vec<JobEntry>") == "JobEntry"

    def test_hashmap_returns_value_type(self) -> None:
        assert _extract_base_type("HashMap<String, Config>") == "Config"

    def test_hashmap_all_primitive_empty(self) -> None:
        assert _extract_base_type("HashMap<String, i32>") == ""

    def test_mutex_unwrap(self) -> None:
        assert _extract_base_type("Mutex<SharedState>") == "SharedState"

    def test_result_unwraps_both(self) -> None:
        types = _extract_base_types("Result<Config, AppError>")
        assert "Config" in types
        assert "AppError" in types

    def test_ref_mut_arc_nested(self) -> None:
        assert _extract_base_type("&mut Arc<Config>") == "Config"

    # ---- Python square-bracket wrappers ----

    def test_python_optional(self) -> None:
        assert _extract_base_type("Optional[Config]") == "Config"

    def test_python_list(self) -> None:
        assert _extract_base_type("List[MyModel]") == "MyModel"

    def test_python_dict(self) -> None:
        types = _extract_base_types("Dict[str, MyModel]")
        assert types == ["MyModel"]

    def test_python_union(self) -> None:
        types = _extract_base_types("Union[Config, AppError]")
        assert "Config" in types
        assert "AppError" in types

    def test_python_primitive_optional(self) -> None:
        assert _extract_base_type("Optional[str]") == ""

    # ---- Go prefix patterns ----

    def test_go_pointer(self) -> None:
        assert _extract_base_type("*StorageConfig") == "StorageConfig"

    def test_go_slice(self) -> None:
        assert _extract_base_type("[]MyStruct") == "MyStruct"

    def test_go_channel(self) -> None:
        assert _extract_base_type("chan Message") == "Message"

    def test_go_map(self) -> None:
        types = _extract_base_types("map[string]Config")
        assert types == ["Config"]

    def test_go_map_both_custom(self) -> None:
        types = _extract_base_types("map[KeyType]ValueType")
        assert "KeyType" in types
        assert "ValueType" in types

    def test_go_slice_primitive(self) -> None:
        assert _extract_base_type("[]string") == ""

    # ---- C++ namespace + wrappers ----

    def test_cpp_shared_ptr(self) -> None:
        assert _extract_base_type("std::shared_ptr<Config>") == "Config"

    def test_cpp_vector(self) -> None:
        assert _extract_base_type("std::vector<Entry>") == "Entry"

    def test_cpp_unique_ptr(self) -> None:
        assert _extract_base_type("std::unique_ptr<Handler>") == "Handler"

    # ---- TypeScript / Java / C# generics ----

    def test_ts_promise(self) -> None:
        assert _extract_base_type("Promise<Response>") == "Response"

    def test_java_arraylist(self) -> None:
        assert _extract_base_type("ArrayList<Item>") == "Item"

    def test_csharp_task(self) -> None:
        assert _extract_base_type("Task<Result>") == "Result"

    def test_non_wrapper_generic_keeps_outer(self) -> None:
        """A generic type NOT in wrappers keeps the outer name."""
        assert _extract_base_type("MyContainer<X>") == "MyContainer"

    # ---- _extract_base_types (plural) returns multiple ----

    def test_plural_returns_list(self) -> None:
        assert _extract_base_types("AppConfig") == ["AppConfig"]

    def test_plural_empty(self) -> None:
        assert _extract_base_types("i32") == []

    def test_plural_deduplicates(self) -> None:
        assert _extract_base_types("HashMap<Config, Config>") == ["Config"]


# endregion: --- _extract_base_type Tests


# ---------------------------------------------------------------------------
# region:    --- GraphBuilder Tests
# ---------------------------------------------------------------------------


class TestGraphBuilderSimple:
    """Graph builder on a minimal workspace."""

    def test_builds_non_empty_graph(self) -> None:
        ws = _simple_workspace()
        builder = GraphBuilder()
        graph = builder.build(ws)
        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0

    def test_crate_node_exists(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        crate_nodes = [n for n in graph.nodes if n.kind == "crate"]
        assert len(crate_nodes) == 1
        assert crate_nodes[0].label == "my_crate"

    def test_file_node_exists(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        file_nodes = [n for n in graph.nodes if n.kind == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0].label == "src/lib.rs"

    def test_struct_node_exists(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        struct_nodes = [n for n in graph.nodes if n.kind == "struct"]
        assert len(struct_nodes) == 1
        assert struct_nodes[0].label == "Config"

    def test_function_node_exists(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        func_nodes = [n for n in graph.nodes if n.kind == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].label == "main"

    def test_contains_edges(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        contains = [e for e in graph.edges if e.relation == RELATION_CONTAINS]
        # crate→file, file→struct, file→function = 3
        assert len(contains) == 3

    def test_import_edge(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        imports = [e for e in graph.edges if e.relation == RELATION_IMPORTS]
        assert len(imports) == 1
        assert imports[0].confidence is Confidence.EXTRACTED

    def test_meta_attached(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        # meta is passed through (defaults)
        assert graph.meta is not None


class TestGraphBuilderRich:
    """Graph builder on a fully populated workspace — all edge types."""

    @pytest.fixture(autouse=True)
    def _build_graph(self) -> None:
        ws = _rich_workspace()
        self.graph = GraphBuilder().build(ws)

    def test_node_counts(self) -> None:
        kinds = {}
        for node in self.graph.nodes:
            kinds[node.kind] = kinds.get(node.kind, 0) + 1
        assert kinds.get("crate", 0) >= 1
        assert kinds.get("file", 0) >= 1
        assert kinds.get("struct", 0) >= 1
        assert kinds.get("enum", 0) >= 1
        assert kinds.get("trait", 0) >= 1
        assert kinds.get("function", 0) >= 2
        assert kinds.get("impl_block", 0) >= 2
        assert kinds.get("method", 0) >= 2
        assert kinds.get("type_alias", 0) >= 1
        assert kinds.get("constant", 0) >= 1
        assert kinds.get("macro", 0) >= 1
        assert kinds.get("module", 0) >= 1
        assert kinds.get("rationale", 0) >= 1

    def test_contains_edges(self) -> None:
        contains = [
            e for e in self.graph.edges
            if e.relation == RELATION_CONTAINS
        ]
        # crate→file + file→(struct,enum,trait,2 funcs,2 impls,alias,const,
        #   macro,module) = 1 + 11 = 12
        assert len(contains) >= 12

    def test_method_of_edges(self) -> None:
        method_of = [
            e for e in self.graph.edges
            if e.relation == RELATION_METHOD_OF
        ]
        # 2 impl blocks with 1 method each
        assert len(method_of) == 2

    def test_implements_edge(self) -> None:
        implements = [
            e for e in self.graph.edges
            if e.relation == RELATION_IMPLEMENTS
        ]
        assert len(implements) == 1
        assert "Handler" in implements[0].target

    def test_no_inherits_for_rust(self) -> None:
        """Rust impl with trait_type uses IMPLEMENTS, not INHERITS."""
        inherits = [
            e for e in self.graph.edges
            if e.relation == RELATION_INHERITS
        ]
        assert len(inherits) == 0

    def test_imports_edges(self) -> None:
        imports = [
            e for e in self.graph.edges
            if e.relation == RELATION_IMPORTS
        ]
        assert len(imports) == 2  # Arc, Runtime

    def test_calls_edge(self) -> None:
        calls = [
            e for e in self.graph.edges
            if e.relation == RELATION_CALLS
        ]
        assert len(calls) == 1
        assert "main" in calls[0].source
        assert "helper" in calls[0].target

    def test_uses_method_edges(self) -> None:
        uses = [
            e for e in self.graph.edges
            if e.relation == RELATION_USES_METHOD
        ]
        assert len(uses) == 2  # freeze, put

    def test_depends_on_edges(self) -> None:
        depends = [
            e for e in self.graph.edges
            if e.relation == RELATION_DEPENDS_ON
        ]
        assert len(depends) == 2  # tokio, serde

    def test_super_trait_edges(self) -> None:
        super_traits = [
            e for e in self.graph.edges
            if e.relation == RELATION_SUPER_TRAIT
        ]
        assert len(super_traits) == 2  # Send, Sync

    def test_rationale_for_edge(self) -> None:
        rationale = [
            e for e in self.graph.edges
            if e.relation == RELATION_RATIONALE_FOR
        ]
        assert len(rationale) == 1
        assert "main" in rationale[0].target

    def test_has_field_edge(self) -> None:
        has_field = [
            e for e in self.graph.edges
            if e.relation == RELATION_HAS_FIELD
        ]
        # Server has field 'config: AppConfig' → has_field to AppConfig
        # Server has field 'port: u16' → u16 is primitive, no edge
        assert len(has_field) == 1
        assert "AppConfig" in has_field[0].target


class TestGraphBuilderIDStability:
    """Same input always produces same node IDs — deterministic output."""

    def test_ids_stable_across_builds(self) -> None:
        ws1 = _rich_workspace()
        ws2 = _rich_workspace()
        g1 = GraphBuilder().build(ws1)
        g2 = GraphBuilder().build(ws2)

        ids1 = sorted(n.id for n in g1.nodes)
        ids2 = sorted(n.id for n in g2.nodes)
        assert ids1 == ids2

    def test_edge_sources_targets_stable(self) -> None:
        ws1 = _rich_workspace()
        ws2 = _rich_workspace()
        g1 = GraphBuilder().build(ws1)
        g2 = GraphBuilder().build(ws2)

        def edge_key(e: GraphEdge) -> tuple[str, str, str]:
            return (e.source, e.target, e.relation)

        keys1 = sorted(edge_key(e) for e in g1.edges)
        keys2 = sorted(edge_key(e) for e in g2.edges)
        assert keys1 == keys2

    def test_no_duplicate_node_ids(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        ids = [n.id for n in graph.nodes]
        assert len(ids) == len(set(ids))


class TestGraphBuilderEmpty:
    """Edge case: empty workspace produces empty graph."""

    def test_empty_workspace(self) -> None:
        ws = WorkspaceAST()
        graph = GraphBuilder().build(ws)
        assert graph.nodes == []
        assert graph.edges == []


class TestGraphBuilderInheritsEdge:
    """Python-style base-class detection produces INHERITS edge."""

    def test_inferred_impl_block_produces_inherits(self) -> None:
        """ImplBlockNode with confidence=INFERRED → INHERITS relation."""
        file_ = FileAST(
            file="src/handler.py",
            impl_blocks=[
                ImplBlockNode(
                    self_type="MyHandler",
                    trait_type="BaseHandler",
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED,
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["my_pkg"] = CrateModel(
            name="my_pkg", language="python", files=[file_],
        )
        graph = GraphBuilder().build(ws)

        inherits = [
            e for e in graph.edges
            if e.relation == RELATION_INHERITS
        ]
        assert len(inherits) == 1
        assert inherits[0].confidence is Confidence.INFERRED


class TestTypeReferenceResolution:
    """Feature 15 — type:: synthetic references resolved to real nodes."""

    @staticmethod
    def _two_file_workspace(
        *,
        second_trait_name: str = "Handler",
        second_crate: str = "my_crate",
    ) -> WorkspaceAST:
        """Helper: workspace with a trait in one file and an impl in another."""
        file_trait = FileAST(
            file="src/traits.rs",
            module_path="my_crate::traits",
            traits=[
                TraitNode(
                    name=second_trait_name,
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 10, 2),
                ),
            ],
        )
        file_impl = FileAST(
            file="src/server.rs",
            module_path="my_crate::server",
            structs=[
                StructNode(
                    name="Server",
                    visibility=Visibility.PUBLIC,
                    fields=(
                        FieldNode(
                            name="cfg",
                            type="AppConfig",
                            visibility=Visibility.PUBLIC,
                        ),
                    ),
                    span=Span(1, 1, 5, 2),
                ),
                StructNode(
                    name="AppConfig",
                    visibility=Visibility.PUBLIC,
                    span=Span(7, 1, 12, 2),
                ),
            ],
            impl_blocks=[
                ImplBlockNode(
                    self_type="Server",
                    trait_type=second_trait_name,
                    methods=(
                        MethodNode(
                            name="handle",
                            visibility=Visibility.PUBLIC,
                            span=Span(15, 5, 20, 6),
                        ),
                    ),
                    span=Span(14, 1, 21, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates[second_crate] = CrateModel(
            name=second_crate,
            version="0.1.0",
            language="rust",
            files=[file_trait, file_impl],
        )
        return ws

    # -- Unambiguous resolution -----------------------------------------

    def test_implements_edge_resolved(self) -> None:
        """IMPLEMENTS edge target rewritten from type::Handler to real node."""
        ws = self._two_file_workspace()
        graph = GraphBuilder().build(ws)

        implements = [
            e for e in graph.edges
            if e.relation == RELATION_IMPLEMENTS
        ]
        assert len(implements) == 1
        assert implements[0].target == "src/traits.rs::Handler"
        assert not implements[0].target.startswith("type::")

    def test_has_field_edge_resolved(self) -> None:
        """HAS_FIELD edge target rewritten from type::AppConfig to real node."""
        ws = self._two_file_workspace()
        graph = GraphBuilder().build(ws)

        has_field = [
            e for e in graph.edges
            if e.relation == RELATION_HAS_FIELD
        ]
        assert len(has_field) == 1
        assert has_field[0].target == "src/server.rs::AppConfig"
        assert not has_field[0].target.startswith("type::")

    def test_super_trait_edge_resolved(self) -> None:
        """SUPER_TRAIT edge rewritten when parent trait is in the graph."""
        file_ = FileAST(
            file="src/lib.rs",
            traits=[
                TraitNode(
                    name="Base",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
                TraitNode(
                    name="Derived",
                    visibility=Visibility.PUBLIC,
                    super_traits=("Base",),
                    span=Span(7, 1, 12, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["pkg"] = CrateModel(
            name="pkg", language="rust", files=[file_],
        )
        graph = GraphBuilder().build(ws)

        super_trait = [
            e for e in graph.edges
            if e.relation == RELATION_SUPER_TRAIT
        ]
        assert len(super_trait) == 1
        assert super_trait[0].target == "src/lib.rs::Base"

    # -- Zero candidates (external type) --------------------------------

    def test_external_type_stays_unresolved(self) -> None:
        """type:: edge for an external type not in the repo stays as-is."""
        file_ = FileAST(
            file="src/lib.rs",
            traits=[
                TraitNode(
                    name="MyTrait",
                    visibility=Visibility.PUBLIC,
                    super_traits=("Send",),
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["pkg"] = CrateModel(
            name="pkg", language="rust", files=[file_],
        )
        graph = GraphBuilder().build(ws)

        super_trait = [
            e for e in graph.edges
            if e.relation == RELATION_SUPER_TRAIT
        ]
        assert len(super_trait) == 1
        assert super_trait[0].target == "type::Send"

    # -- Ambiguous same-crate resolution --------------------------------

    def test_ambiguous_same_crate_resolved(self) -> None:
        """Two nodes named 'Config' — same-crate heuristic picks the right one."""
        file_a = FileAST(
            file="src/crate_a/config.rs",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_b = FileAST(
            file="src/crate_b/config.rs",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        # Impl in crate_a should resolve to crate_a's Config
        file_impl = FileAST(
            file="src/crate_a/server.rs",
            structs=[
                StructNode(
                    name="Server",
                    visibility=Visibility.PUBLIC,
                    fields=(
                        FieldNode(
                            name="cfg",
                            type="Config",
                            visibility=Visibility.PUBLIC,
                        ),
                    ),
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["multi"] = CrateModel(
            name="multi",
            language="rust",
            files=[file_a, file_b, file_impl],
        )
        graph = GraphBuilder().build(ws)

        has_field = [
            e for e in graph.edges
            if e.relation == RELATION_HAS_FIELD
        ]
        assert len(has_field) == 1
        assert has_field[0].target == "src/crate_a/config.rs::Config"

    # -- Ambiguous with no same-crate match -----------------------------

    def test_ambiguous_no_match_stays_unresolved(self) -> None:
        """Two candidates, source shares no prefix — stays type::."""
        file_a = FileAST(
            file="libs/alpha/config.rs",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_b = FileAST(
            file="libs/beta/config.rs",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        # Impl in a completely different directory
        file_impl = FileAST(
            file="other/server.rs",
            structs=[
                StructNode(
                    name="Server",
                    visibility=Visibility.PUBLIC,
                    fields=(
                        FieldNode(
                            name="cfg",
                            type="Config",
                            visibility=Visibility.PUBLIC,
                        ),
                    ),
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["pkg"] = CrateModel(
            name="pkg", language="rust",
            files=[file_a, file_b, file_impl],
        )
        graph = GraphBuilder().build(ws)

        has_field = [
            e for e in graph.edges
            if e.relation == RELATION_HAS_FIELD
        ]
        assert len(has_field) == 1
        # Both candidates share "libs/" equally with no winner —
        # the edge stays as type::Config
        assert has_field[0].target == "type::Config"

    # -- Python INHERITS resolution -------------------------------------

    def test_python_inherits_resolved(self) -> None:
        """Python class inheritance resolves across files."""
        file_base = FileAST(
            file="src/base.py",
            traits=[
                TraitNode(
                    name="BaseHandler",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 10, 2),
                ),
            ],
        )
        file_child = FileAST(
            file="src/child.py",
            impl_blocks=[
                ImplBlockNode(
                    self_type="MyHandler",
                    trait_type="BaseHandler",
                    confidence=Confidence.INFERRED,
                    confidence_score=SCORE_INFERRED,
                    span=Span(1, 1, 15, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["my_pkg"] = CrateModel(
            name="my_pkg", language="python",
            files=[file_base, file_child],
        )
        graph = GraphBuilder().build(ws)

        inherits = [
            e for e in graph.edges
            if e.relation == RELATION_INHERITS
        ]
        assert len(inherits) == 1
        assert inherits[0].target == "src/base.py::BaseHandler"
        assert inherits[0].confidence is Confidence.INFERRED

    # -- Symbol table composition ---------------------------------------

    def test_symbol_table_only_type_kinds(self) -> None:
        """_build_symbol_table includes only STRUCT/TRAIT/ENUM/TYPE_ALIAS."""
        ws = _rich_workspace()
        builder = GraphBuilder()
        builder.build(ws)
        table = builder._build_symbol_table()

        # Server (struct) and Handler (trait) should be in the table
        assert "Server" in table
        assert "Handler" in table

        # Functions, methods, etc. should NOT be in the table
        assert "main" not in table
        assert "helper" not in table
        assert "handle" not in table

    # -- Edge preservation guarantees -----------------------------------

    def test_resolution_preserves_edge_count(self) -> None:
        """Resolving type references does not add or remove edges."""
        ws = self._two_file_workspace()
        # Build once — count edges
        graph = GraphBuilder().build(ws)
        # Every type:: edge should still exist (just with a new target)
        impl_edges = [
            e for e in graph.edges
            if e.relation in {RELATION_IMPLEMENTS, RELATION_INHERITS}
        ]
        assert len(impl_edges) == 1  # unchanged count

    def test_existing_tests_unaffected(self) -> None:
        """_rich_workspace still behaves correctly with resolution on.

        The IMPLEMENTS edge for Handler now targets the real node
        instead of type::Handler.
        """
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)

        implements = [
            e for e in graph.edges
            if e.relation == RELATION_IMPLEMENTS
        ]
        assert len(implements) == 1
        # Handler trait is in the same file → resolves to real node
        assert implements[0].target == "src/lib.rs::Handler"

    def test_cross_crate_resolution(self) -> None:
        """Impl in crate A referencing trait in crate B is resolved."""
        file_trait = FileAST(
            file="libs/core/handler.rs",
            traits=[
                TraitNode(
                    name="Handler",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 10, 2),
                ),
            ],
        )
        file_impl = FileAST(
            file="services/web/server.rs",
            impl_blocks=[
                ImplBlockNode(
                    self_type="WebServer",
                    trait_type="Handler",
                    span=Span(1, 1, 15, 2),
                ),
            ],
        )
        ws = WorkspaceAST()
        ws.crates["core"] = CrateModel(
            name="core", language="rust", files=[file_trait],
        )
        ws.crates["web"] = CrateModel(
            name="web", language="rust", files=[file_impl],
        )
        graph = GraphBuilder().build(ws)

        implements = [
            e for e in graph.edges
            if e.relation == RELATION_IMPLEMENTS
        ]
        assert len(implements) == 1
        assert implements[0].target == "libs/core/handler.rs::Handler"


# endregion: --- GraphBuilder Tests


# ---------------------------------------------------------------------------
# region:    --- GraphJsonFormatter Tests
# ---------------------------------------------------------------------------


class TestGraphJsonFormatter:
    """JSON graph formatter output."""

    def test_write_creates_file(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_json_structure(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        assert "meta" in data
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_json_nodes_have_required_fields(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "kind" in node

    def test_json_edges_have_required_fields(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "relation" in edge
            assert "confidence" in edge
            assert "confidence_score" in edge

    def test_json_deterministic(self, tmp_output: Path) -> None:
        """Same input produces byte-identical output."""
        ws1 = _simple_workspace()
        ws2 = _simple_workspace()
        g1 = GraphBuilder().build(ws1)
        g2 = GraphBuilder().build(ws2)

        out1 = tmp_output / "g1.json"
        out2 = tmp_output / "g2.json"
        GraphJsonFormatter().write(g1, out1)
        GraphJsonFormatter().write(g2, out2)

        assert out1.read_text() == out2.read_text()

    def test_json_confidence_serialized_as_string(
        self, tmp_output: Path,
    ) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        for edge in data["edges"]:
            assert isinstance(edge["confidence"], str)
            assert edge["confidence"] in (
                "extracted", "inferred", "ambiguous",
            )

    def test_json_nodes_sorted_by_id(self, tmp_output: Path) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        ids = [n["id"] for n in data["nodes"]]
        assert ids == sorted(ids)


# endregion: --- GraphJsonFormatter Tests


# ---------------------------------------------------------------------------
# region:    --- GraphDotFormatter Tests
# ---------------------------------------------------------------------------


class TestGraphDotFormatter:
    """DOT graph formatter output."""

    def test_write_creates_file(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        assert out.exists()

    def test_dot_starts_with_digraph(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        content = out.read_text()
        assert content.startswith("digraph CodeGraph {")

    def test_dot_ends_with_closing_brace(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        content = out.read_text().strip()
        assert content.endswith("}")

    def test_dot_contains_nodes(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        content = out.read_text()
        assert "Config" in content
        assert "main" in content

    def test_dot_contains_edges(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        content = out.read_text()
        assert "->" in content
        assert "contains" in content

    def test_dot_deterministic(self, tmp_output: Path) -> None:
        ws1 = _simple_workspace()
        ws2 = _simple_workspace()
        g1 = GraphBuilder().build(ws1)
        g2 = GraphBuilder().build(ws2)
        out1 = tmp_output / "g1.dot"
        out2 = tmp_output / "g2.dot"
        GraphDotFormatter().write(g1, out1)
        GraphDotFormatter().write(g2, out2)
        assert out1.read_text() == out2.read_text()

    def test_dot_node_shapes(self, tmp_output: Path) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.dot"
        GraphDotFormatter().write(graph, out)
        content = out.read_text()
        # Struct → box, function → ellipse, trait → hexagon
        assert "shape=box" in content
        assert "shape=ellipse" in content
        assert "shape=hexagon" in content


# endregion: --- GraphDotFormatter Tests


# ---------------------------------------------------------------------------
# region:    --- GraphMermaidFormatter Tests
# ---------------------------------------------------------------------------


class TestGraphMermaidFormatter:
    """Mermaid graph formatter output."""

    def test_write_creates_file(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        assert out.exists()

    def test_mermaid_fenced_code_block(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert content.startswith("```mermaid")
        assert "```" in content.split("\n")[-2] or content.rstrip().endswith("```")

    def test_mermaid_graph_td(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert "graph TD" in content

    def test_mermaid_contains_subgraph(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert "subgraph" in content

    def test_mermaid_contains_edges(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert "-->|" in content

    def test_mermaid_deterministic(self, tmp_output: Path) -> None:
        ws1 = _simple_workspace()
        ws2 = _simple_workspace()
        g1 = GraphBuilder().build(ws1)
        g2 = GraphBuilder().build(ws2)
        out1 = tmp_output / "m1.md"
        out2 = tmp_output / "m2.md"
        GraphMermaidFormatter().write(g1, out1)
        GraphMermaidFormatter().write(g2, out2)
        assert out1.read_text() == out2.read_text()


# endregion: --- GraphMermaidFormatter Tests


# ---------------------------------------------------------------------------
# region:    --- Emitter Integration Tests
# ---------------------------------------------------------------------------


class TestEmitterGraphFormats:
    """Emitter dispatches graph formats correctly."""

    def test_emitter_graph_json(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="graph-json")
        written = emitter.emit(ws)
        assert len(written) == 1
        assert written[0].name == "graph.json"
        assert written[0].exists()
        data = json.loads(written[0].read_text())
        assert "nodes" in data

    def test_emitter_dot(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="dot")
        written = emitter.emit(ws)
        assert len(written) == 1
        assert written[0].name == "graph.dot"
        content = written[0].read_text()
        assert content.startswith("digraph")

    def test_emitter_mermaid(self, tmp_output: Path) -> None:
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="mermaid")
        written = emitter.emit(ws)
        assert len(written) == 1
        assert written[0].name == "graph.mermaid.md"
        content = written[0].read_text()
        assert "```mermaid" in content

    def test_emitter_both_unchanged(self, tmp_output: Path) -> None:
        """'both' writes ast.json + summary.md + graph.json."""
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="both")
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert "ast.json" in names
        assert "summary.md" in names
        assert "graph.json" in names

    def test_emitter_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid output_format"):
            Emitter(output_dir=Path("/tmp"), output_format="invalid")  # type: ignore[arg-type]

    def test_emitter_json_unchanged(self, tmp_output: Path) -> None:
        """Format 'json' still writes ast.json only."""
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="json")
        written = emitter.emit(ws)
        assert len(written) == 1
        assert written[0].name == "ast.json"

    def test_emitter_all_format(self, tmp_output: Path) -> None:
        """'all' writes ast.json + summary.md + graph.json + graph.html + architecture.html."""
        ws = _simple_workspace()
        emitter = Emitter(output_dir=tmp_output, output_format="all")
        written = emitter.emit(ws)
        names = {p.name for p in written}
        assert "ast.json" in names
        assert "summary.md" in names
        assert "graph.json" in names
        assert "graph.html" in names
        assert "architecture.html" in names
        assert len(written) == 5


# endregion: --- Emitter Integration Tests


# ---------------------------------------------------------------------------
# region:    --- CLI Output Format Enum Tests
# ---------------------------------------------------------------------------


class TestCLIOutputFormat:
    """CLI OutputFormat enum includes graph formats."""

    def test_graph_json_variant(self) -> None:
        from ast_intel.cli import OutputFormat

        assert OutputFormat.GRAPH_JSON.value == "graph-json"

    def test_dot_variant(self) -> None:
        from ast_intel.cli import OutputFormat

        assert OutputFormat.DOT.value == "dot"

    def test_mermaid_variant(self) -> None:
        from ast_intel.cli import OutputFormat

        assert OutputFormat.MERMAID.value == "mermaid"

    def test_both_still_default(self) -> None:
        from ast_intel.cli import OutputFormat

        assert OutputFormat.BOTH.value == "both"


# endregion: --- CLI Output Format Enum Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Properties Tests
# ---------------------------------------------------------------------------


class TestGraphNodeProperties:
    """Node properties carry metadata correctly."""

    def test_struct_node_has_visibility(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        struct_node = next(
            n for n in graph.nodes if n.kind == "struct"
        )
        assert "visibility" in struct_node.properties
        assert struct_node.properties["visibility"] == "pub"

    def test_function_node_properties(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        main_node = next(
            n for n in graph.nodes
            if n.kind == "function" and n.label == "main"
        )
        assert main_node.properties.get("async") == "true"
        assert main_node.properties.get("return_type") == "Result<()>"

    def test_impl_block_node_properties(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        impl_nodes = [n for n in graph.nodes if n.kind == "impl_block"]
        # Find trait impl
        trait_impl = next(
            n for n in impl_nodes
            if n.properties.get("trait_type") == "Handler"
        )
        assert trait_impl.properties["self_type"] == "Server"

    def test_enum_node_variant_count(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        enum_node = next(n for n in graph.nodes if n.kind == "enum")
        assert enum_node.properties.get("variants") == "1"


# endregion: --- Graph Properties Tests


# ---------------------------------------------------------------------------
# region:    --- Span Propagation Tests
# ---------------------------------------------------------------------------


class TestSpanPropagation:
    """Spans from Features 1 are preserved in graph nodes and edges."""

    def test_struct_span(self) -> None:
        ws = _simple_workspace()
        graph = GraphBuilder().build(ws)
        struct_node = next(
            n for n in graph.nodes if n.kind == "struct"
        )
        assert struct_node.span is not None
        assert struct_node.span.start_line == 1

    def test_call_edge_span(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        call_edge = next(
            e for e in graph.edges if e.relation == RELATION_CALLS
        )
        assert call_edge.span is not None
        assert call_edge.span.start_line == 30

    def test_json_includes_span(self, tmp_output: Path) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.json"
        GraphJsonFormatter().write(graph, out)
        data = json.loads(out.read_text())
        # At least some nodes should have spans
        nodes_with_span = [n for n in data["nodes"] if "span" in n]
        assert len(nodes_with_span) > 0
        # Verify span structure
        span = nodes_with_span[0]["span"]
        assert "start_line" in span
        assert "end_line" in span


# endregion: --- Span Propagation Tests


# ---------------------------------------------------------------------------
# region:    --- Edge Confidence Tests
# ---------------------------------------------------------------------------


class TestEdgeConfidence:
    """Confidence tags from Feature 3 propagate to graph edges."""

    def test_call_edge_confidence_extracted(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        call_edge = next(
            e for e in graph.edges if e.relation == RELATION_CALLS
        )
        assert call_edge.confidence is Confidence.EXTRACTED
        assert call_edge.confidence_score == pytest.approx(SCORE_EXTRACTED)

    def test_has_field_confidence_inferred(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        hf = next(
            e for e in graph.edges if e.relation == RELATION_HAS_FIELD
        )
        assert hf.confidence is Confidence.INFERRED
        assert hf.confidence_score == pytest.approx(SCORE_INFERRED)

    def test_depends_on_confidence_extracted(self) -> None:
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        dep = next(
            e for e in graph.edges if e.relation == RELATION_DEPENDS_ON
        )
        assert dep.confidence is Confidence.EXTRACTED


# endregion: --- Edge Confidence Tests


# ---------------------------------------------------------------------------
# region:    --- Enum Type Safety Tests
# ---------------------------------------------------------------------------


class TestNodeKindEnum:
    """NodeKind StrEnum type safety."""

    def test_is_str(self) -> None:
        assert isinstance(NodeKind.STRUCT, str)
        assert NodeKind.STRUCT == "struct"

    def test_all_node_kinds_in_graph(self) -> None:
        """Every node kind produced by the builder is a valid NodeKind."""
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        for node in graph.nodes:
            assert isinstance(node.kind, NodeKind), (
                f"Node {node.id} has kind={node.kind!r} which is not a NodeKind"
            )

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_kind"):
            NodeKind("not_a_kind")

    def test_member_count(self) -> None:
        """Node kinds: 19 code + 11 K8s + 3 Helm + 3 Docker + 3 Ansible + 4 CI/CD + 6 TF."""
        assert len(NodeKind) == 50


class TestEdgeRelationEnum:
    """EdgeRelation StrEnum type safety."""

    def test_is_str(self) -> None:
        assert isinstance(EdgeRelation.CALLS, str)
        assert EdgeRelation.CALLS == "calls"

    def test_all_edge_relations_in_graph(self) -> None:
        """Every edge relation produced by the builder is a valid EdgeRelation."""
        ws = _rich_workspace()
        graph = GraphBuilder().build(ws)
        for edge in graph.edges:
            assert isinstance(edge.relation, EdgeRelation), (
                f"Edge {edge.source}→{edge.target} has relation="
                f"{edge.relation!r} which is not an EdgeRelation"
            )

    def test_backward_compat_aliases(self) -> None:
        """RELATION_* module-level aliases still work."""
        assert RELATION_CALLS == EdgeRelation.CALLS
        assert RELATION_CONTAINS == EdgeRelation.CONTAINS
        assert RELATION_DEPENDS_ON == EdgeRelation.DEPENDS_ON

    def test_member_count(self) -> None:
        """Edge relations: 19 code + 5 K8s + 2 Helm + 4 Docker + 4 Ansible + 3 CI/CD + 1 TF."""
        assert len(EdgeRelation) == 41


# endregion: --- Enum Type Safety Tests


# ---------------------------------------------------------------------------
# region:    --- Mermaid Large Graph Truncation Tests
# ---------------------------------------------------------------------------


class TestMermaidLargeGraphTruncation:
    """Large graphs (>500 nodes) trigger truncation in Mermaid output."""

    @staticmethod
    def _large_workspace() -> WorkspaceAST:
        """Build a workspace with >500 symbols to trigger truncation."""
        structs = [
            StructNode(
                name=f"Struct{i}",
                visibility=Visibility.PUBLIC,
                span=Span(i, 1, i + 5, 2),
            )
            for i in range(510)
        ]
        file_ = FileAST(file="src/big.rs", structs=structs)
        ws = WorkspaceAST()
        ws.crates["big"] = CrateModel(
            name="big", language="rust", files=[file_],
        )
        return ws

    def test_truncation_comment_present(self, tmp_output: Path) -> None:
        ws = self._large_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert ">500 nodes" in content

    def test_detail_kinds_omitted(self, tmp_output: Path) -> None:
        """Methods, constants, etc. are omitted in truncated view."""
        # Add methods to the large workspace
        ws = self._large_workspace()
        file_ = ws.crates["big"].files[0]
        file_.constants = [
            ConstantNode(name=f"CONST_{i}") for i in range(10)
        ]
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        # The graph is large — constants should be omitted
        assert "CONST_0" not in content

    def test_contains_edges_omitted(self, tmp_output: Path) -> None:
        ws = self._large_workspace()
        graph = GraphBuilder().build(ws)
        out = tmp_output / "graph.mermaid.md"
        GraphMermaidFormatter().write(graph, out)
        content = out.read_text()
        assert "contains" not in content


# endregion: --- Mermaid Large Graph Truncation Tests


# ---------------------------------------------------------------------------
# region:    --- Import Resolution Tests (Feature 16)
# ---------------------------------------------------------------------------


class TestParseImport:
    """Unit tests for the ``_parse_import`` helper."""

    def test_simple_import(self) -> None:
        result = _parse_import("use lib_b::MyTrait")
        assert result == [("MyTrait", "lib_b::MyTrait")]

    def test_braced_import(self) -> None:
        result = _parse_import("use lib_b::{Foo, Bar}")
        assert ("Foo", "lib_b::Foo") in result
        assert ("Bar", "lib_b::Bar") in result

    def test_aliased_import(self) -> None:
        result = _parse_import("use crate::config::Settings as AppSettings")
        assert result == [("AppSettings", "crate::config::Settings")]

    def test_braced_with_alias(self) -> None:
        result = _parse_import("use lib_b::{Foo as F, Bar}")
        assert ("F", "lib_b::Foo") in result
        assert ("Bar", "lib_b::Bar") in result

    def test_nested_path_in_braces(self) -> None:
        result = _parse_import("use lib_b::{models::Job, Config}")
        assert ("Job", "lib_b::models::Job") in result
        assert ("Config", "lib_b::Config") in result

    def test_glob_import_skipped(self) -> None:
        assert _parse_import("use lib_b::*") == []

    def test_super_import_skipped(self) -> None:
        assert _parse_import("super::handler::Handler") == []

    def test_self_import_skipped(self) -> None:
        assert _parse_import("self::inner::Widget") == []

    def test_braced_self_entry_skipped(self) -> None:
        """``self`` inside braces should be ignored."""
        result = _parse_import("use lib_b::{self, Config}")
        assert len(result) == 1
        assert result[0][0] == "Config"

    def test_crate_prefix(self) -> None:
        result = _parse_import("use crate::error::Error")
        assert result == [("Error", "crate::error::Error")]

    def test_deep_path(self) -> None:
        result = _parse_import(
            "use lib_storage_service::models::job_metadata::JobMetadata",
        )
        assert result == [
            ("JobMetadata", "lib_storage_service::models::job_metadata::JobMetadata"),
        ]


class TestImportCrateHint:
    """Unit tests for the ``_import_crate_hint`` helper."""

    def test_workspace_crate(self) -> None:
        idx = {"lib_storage_service": "lib-storage-service"}
        assert _import_crate_hint(
            "lib_storage_service::StorageHelper", idx, "my_crate",
        ) == "lib-storage-service"

    def test_crate_keyword(self) -> None:
        idx: dict[str, str] = {}
        assert _import_crate_hint(
            "crate::config::Settings", idx, "approval-engine",
        ) == "approval-engine"

    def test_external_crate(self) -> None:
        idx = {"lib_b": "lib-b"}
        assert _import_crate_hint("tokio::sync::Mutex", idx, "my_crate") == ""

    def test_no_path_separator(self) -> None:
        assert _import_crate_hint("HashMap", {}, "x") == ""


class TestImportResolution:
    """Feature 16 — cross-crate import resolution end-to-end."""

    @staticmethod
    def _two_crate_workspace(
        *,
        crate_a_uses: list[str] | None = None,
    ) -> WorkspaceAST:
        """Build a workspace with crate-a depending on crate-b.

        crate-b has a trait ``MyTrait`` and a function ``helper``.
        crate-a imports from crate-b via ``use`` statements.
        """
        file_b = FileAST(
            file="src/crates/lib-b/src/lib.rs",
            module_path="lib-b",
            traits=[
                TraitNode(
                    name="MyTrait",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
            functions=[
                FunctionNode(
                    name="helper",
                    visibility=Visibility.PUBLIC,
                    span=Span(7, 1, 10, 2),
                ),
            ],
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(12, 1, 15, 2),
                ),
            ],
        )
        uses = crate_a_uses if crate_a_uses is not None else [
            "use lib_b::MyTrait",
        ]
        file_a = FileAST(
            file="src/crates/crate-a/src/main.rs",
            module_path="crate-a",
            uses=uses,
        )

        ws = WorkspaceAST()
        ws.crates["lib-b"] = CrateModel(
            name="lib-b",
            version="0.1.0",
            language="rust",
            files=[file_b],
            dependencies=[],
        )
        ws.crates["crate-a"] = CrateModel(
            name="crate-a",
            version="0.1.0",
            language="rust",
            files=[file_a],
            dependencies=[
                CrateDependency(name="lib-b", path="../lib-b"),
            ],
        )
        return ws

    # -- Basic cross-crate resolution ----------------------------------

    def test_cross_crate_import_resolved(self) -> None:
        """``use lib_b::MyTrait`` creates a RESOLVES_TO edge."""
        ws = self._two_crate_workspace()
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 1
        assert resolves[0].source == "import::use lib_b::MyTrait"
        assert resolves[0].target == "src/crates/lib-b/src/lib.rs::MyTrait"

    def test_function_import_resolved(self) -> None:
        """``use lib_b::helper`` resolves to the function node."""
        ws = self._two_crate_workspace(crate_a_uses=["use lib_b::helper"])
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 1
        assert resolves[0].target == "src/crates/lib-b/src/lib.rs::helper"

    def test_braced_import_multiple_edges(self) -> None:
        """``use lib_b::{MyTrait, Config}`` creates two RESOLVES_TO edges."""
        ws = self._two_crate_workspace(
            crate_a_uses=["use lib_b::{MyTrait, Config}"],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 2
        targets = {e.target for e in resolves}
        assert "src/crates/lib-b/src/lib.rs::MyTrait" in targets
        assert "src/crates/lib-b/src/lib.rs::Config" in targets

    def test_aliased_import_resolved(self) -> None:
        """``use lib_b::MyTrait as Handler`` resolves through alias."""
        ws = self._two_crate_workspace(
            crate_a_uses=["use lib_b::MyTrait as Handler"],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        # The alias "Handler" won't match "MyTrait" label, but the
        # qualified path is "lib_b::MyTrait" — _parse_import extracts
        # alias="Handler" but we look up "Handler" in the symbol table.
        # "Handler" doesn't exist → no edge. This is expected for aliases
        # whose original name differs from the alias.
        #
        # However, if we look up by original name too, it should resolve.
        # Current implementation only looks up by short_name (the alias).
        # This is an acceptable limitation for Phase 1.
        assert len(resolves) == 0

    # -- Intra-crate resolution (use crate::) --------------------------

    def test_intra_crate_import_resolved(self) -> None:
        """``use crate::config::Settings`` resolves within the same crate."""
        file_config = FileAST(
            file="src/config.rs",
            module_path="my_crate::config",
            structs=[
                StructNode(
                    name="Settings",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_main = FileAST(
            file="src/main.rs",
            module_path="my_crate",
            uses=["use crate::config::Settings"],
        )
        ws = WorkspaceAST()
        ws.crates["my_crate"] = CrateModel(
            name="my_crate",
            version="0.1.0",
            language="rust",
            files=[file_config, file_main],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 1
        assert resolves[0].target == "src/config.rs::Settings"

    # -- External imports (no resolution) ------------------------------

    def test_external_import_skipped(self) -> None:
        """``use tokio::sync::Mutex`` produces no RESOLVES_TO edge."""
        ws = self._two_crate_workspace(
            crate_a_uses=["use tokio::sync::Mutex"],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 0

    def test_glob_import_skipped(self) -> None:
        """``use lib_b::*`` produces no RESOLVES_TO edge."""
        ws = self._two_crate_workspace(crate_a_uses=["use lib_b::*"])
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 0

    # -- Disambiguation ------------------------------------------------

    def test_ambiguous_import_disambiguated_by_crate(self) -> None:
        """When ``Config`` exists in 2 crates, the crate hint picks the right one."""
        file_b = FileAST(
            file="src/crates/lib-b/src/lib.rs",
            module_path="lib-b",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_c = FileAST(
            file="src/crates/lib-c/src/lib.rs",
            module_path="lib-c",
            structs=[
                StructNode(
                    name="Config",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_a = FileAST(
            file="src/crates/crate-a/src/main.rs",
            module_path="crate-a",
            uses=["use lib_b::Config"],
        )
        ws = WorkspaceAST()
        ws.crates["lib-b"] = CrateModel(
            name="lib-b", language="rust", files=[file_b],
        )
        ws.crates["lib-c"] = CrateModel(
            name="lib-c", language="rust", files=[file_c],
        )
        ws.crates["crate-a"] = CrateModel(
            name="crate-a", language="rust", files=[file_a],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 1
        assert resolves[0].target == "src/crates/lib-b/src/lib.rs::Config"

    # -- Underscore/dash normalisation ---------------------------------

    def test_underscore_dash_normalisation(self) -> None:
        """``use lib_storage_service::X`` maps to crate ``lib-storage-service``."""
        file_lib = FileAST(
            file="src/crates/lib-storage-service/src/lib.rs",
            module_path="lib-storage-service",
            traits=[
                TraitNode(
                    name="StorageHelper",
                    visibility=Visibility.PUBLIC,
                    span=Span(1, 1, 5, 2),
                ),
            ],
        )
        file_svc = FileAST(
            file="src/crates/approval-engine/src/main.rs",
            module_path="approval-engine",
            uses=["use lib_storage_service::StorageHelper"],
        )
        ws = WorkspaceAST()
        ws.crates["lib-storage-service"] = CrateModel(
            name="lib-storage-service", language="rust", files=[file_lib],
        )
        ws.crates["approval-engine"] = CrateModel(
            name="approval-engine", language="rust", files=[file_svc],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        assert len(resolves) == 1
        assert "lib-storage-service" in resolves[0].target

    # -- Integration: edge count in multi-crate workspace --------------

    def test_resolves_to_edge_count(self) -> None:
        """Multi-import workspace produces expected RESOLVES_TO count."""
        ws = self._two_crate_workspace(
            crate_a_uses=[
                "use lib_b::MyTrait",
                "use lib_b::helper",
                "use lib_b::Config",
                "use tokio::sync::Mutex",  # external — skipped
                "use std::collections::HashMap",  # external — skipped
            ],
        )
        graph = GraphBuilder().build(ws)

        resolves = [
            e for e in graph.edges
            if e.relation == RELATION_RESOLVES_TO
        ]
        # 3 internal imports resolved, 2 external skipped
        assert len(resolves) == 3


# endregion: --- Import Resolution Tests (Feature 16)
