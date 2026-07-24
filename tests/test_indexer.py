"""Comprehensive tests for the Indexer — cross-reference builder.

Covers all five indexer passes:
- Pass 1: struct_index, enum_index, trait_index
- Pass 2: trait_implementations, impl_map
- Pass 3: function_file_index
- Pass 4: package_method_index
- Pass 5: inter_crate_deps

Also tests edge cases: empty workspace, duplicate names, multi-crate workspaces.
"""

from __future__ import annotations

import pytest

from ast_intel.core.indexer import Indexer
from ast_intel.models.ast_node import (
    EnumNode,
    EnumVariantKind,
    EnumVariantNode,
    FileAST,
    ImplBlockNode,
    MethodNode,
    StructNode,
    TraitItemKind,
    TraitItemNode,
    TraitNode,
    Visibility,
)
from ast_intel.models.workspace_model import (
    CrateDependency,
    CrateModel,
    FunctionIndexEntry,
    WorkspaceAST,
)

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def indexer() -> Indexer:
    """Provide a fresh Indexer instance."""
    return Indexer()


def _make_file_ast(  # noqa: C901, PLR0912, PLR0913
    file: str = "src/lib.rs",
    module_path: str = "my_crate",
    *,
    is_test: bool = False,
    uses: list[str] | None = None,
    modules: list[object] | None = None,
    structs: list[StructNode] | None = None,
    enums: list[EnumNode] | None = None,
    traits: list[TraitNode] | None = None,
    functions: list[object] | None = None,
    impl_blocks: list[ImplBlockNode] | None = None,
    type_aliases: list[object] | None = None,
    constants: list[object] | None = None,
    macros: list[object] | None = None,
    self_methods: list[MethodNode] | None = None,
    imported_package_methods: dict[str, list[str]] | None = None,
    errors: list[str] | None = None,
) -> FileAST:
    """Helper to create a FileAST with defaults.

    Uses explicit keyword args instead of ``**kwargs`` so that
    misspelled field names are caught at the call site.
    """
    ast = FileAST(file=file, module_path=module_path, is_test=is_test)
    if uses is not None:
        ast.uses = uses
    if modules is not None:
        ast.modules = modules  # type: ignore[assignment]
    if structs is not None:
        ast.structs = structs
    if enums is not None:
        ast.enums = enums
    if traits is not None:
        ast.traits = traits
    if functions is not None:
        ast.functions = functions  # type: ignore[assignment]
    if impl_blocks is not None:
        ast.impl_blocks = impl_blocks
    if type_aliases is not None:
        ast.type_aliases = type_aliases  # type: ignore[assignment]
    if constants is not None:
        ast.constants = constants  # type: ignore[assignment]
    if macros is not None:
        ast.macros = macros  # type: ignore[assignment]
    if self_methods is not None:
        ast.self_methods = self_methods
    if imported_package_methods is not None:
        ast.imported_package_methods = imported_package_methods
    if errors is not None:
        ast.errors = errors
    return ast


def _make_workspace(*crates: tuple[str, CrateModel]) -> WorkspaceAST:
    """Helper to assemble a WorkspaceAST from name-model pairs."""
    return WorkspaceAST(crates=dict(crates))


def _single_crate_workspace(
    crate_name: str,
    files: list[FileAST],
    dependencies: list[CrateDependency] | None = None,
) -> WorkspaceAST:
    """Helper to create a workspace with one crate."""
    crate = CrateModel(
        name=crate_name,
        language="rust",
        files=files,
        dependencies=dependencies or [],
    )
    return WorkspaceAST(crates={crate_name: crate})


# endregion: --- Fixtures


# ---------------------------------------------------------------------------
# region:    --- Pass 1: Type Index Tests
# ---------------------------------------------------------------------------


class TestPass1TypeIndexes:
    """Verify struct_index, enum_index, and trait_index."""

    def test_struct_index_basic(self, indexer: Indexer) -> None:
        """Structs are indexed by name → qualified path."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(structs=[
                StructNode(name="AppConfig", visibility=Visibility.PUBLIC),
                StructNode(name="Server"),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "AppConfig" in xref.struct_index
        assert "Server" in xref.struct_index
        assert "my_crate::AppConfig" in xref.struct_index["AppConfig"]

    def test_enum_index_basic(self, indexer: Indexer) -> None:
        """Enums are indexed by name → qualified path."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(enums=[
                EnumNode(
                    name="Error",
                    variants=(EnumVariantNode(name="NotFound", kind=EnumVariantKind.UNIT),),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "Error" in xref.enum_index
        assert "my_crate::Error" in xref.enum_index["Error"]

    def test_trait_index_basic(self, indexer: Indexer) -> None:
        """Traits are indexed by name → qualified path."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(traits=[
                TraitNode(name="StorageHelper", visibility=Visibility.PUBLIC),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "StorageHelper" in xref.trait_index
        assert "my_crate::StorageHelper" in xref.trait_index["StorageHelper"]

    def test_duplicate_struct_name_across_modules(self, indexer: Indexer) -> None:
        """Same struct name in different modules → multiple entries."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                module_path="crate::config",
                structs=[StructNode(name="Settings")],
            ),
            _make_file_ast(
                file="src/web.rs",
                module_path="crate::web",
                structs=[StructNode(name="Settings")],
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.struct_index["Settings"]) == 2
        paths = set(xref.struct_index["Settings"])
        assert "crate::config::Settings" in paths
        assert "crate::web::Settings" in paths


# endregion: --- Pass 1: Type Index Tests


# ---------------------------------------------------------------------------
# region:    --- Pass 2: Trait Implementation Tests
# ---------------------------------------------------------------------------


class TestPass2TraitImplementations:
    """Verify trait_implementations and impl_map."""

    def test_trait_impl_basic(self, indexer: Indexer) -> None:
        """impl Trait for Type is captured."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(impl_blocks=[
                ImplBlockNode(
                    self_type="DiskStorage",
                    trait_type="StorageHelper",
                    methods=(
                        MethodNode(name="get", visibility=Visibility.PUBLIC),
                        MethodNode(name="set", visibility=Visibility.PUBLIC),
                    ),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "StorageHelper" in xref.trait_implementations
        implementors = xref.trait_implementations["StorageHelper"]
        assert "my_crate::DiskStorage" in implementors

    def test_impl_map_populated(self, indexer: Indexer) -> None:
        """impl_map contains structured ImplMapEntry."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(impl_blocks=[
                ImplBlockNode(
                    self_type="RedisStorage",
                    trait_type="StorageHelper",
                    methods=(MethodNode(name="get"),),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.impl_map) == 1
        entry = xref.impl_map[0]
        assert entry.trait_name == "StorageHelper"
        assert entry.for_type == "RedisStorage"
        assert "get" in entry.methods

    def test_inherent_impl_skipped(self, indexer: Indexer) -> None:
        """impl Type (no trait) is not included in trait_implementations."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(impl_blocks=[
                ImplBlockNode(
                    self_type="Server",
                    trait_type="",
                    methods=(MethodNode(name="new"),),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.trait_implementations) == 0
        assert len(xref.impl_map) == 0

    def test_multiple_trait_impls(self, indexer: Indexer) -> None:
        """Multiple types implementing same trait → multiple entries."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                module_path="crate::disk",
                impl_blocks=[
                    ImplBlockNode(
                        self_type="DiskStorage",
                        trait_type="StorageHelper",
                        methods=(MethodNode(name="get"),),
                    ),
                ],
            ),
            _make_file_ast(
                file="src/redis.rs",
                module_path="crate::redis",
                impl_blocks=[
                    ImplBlockNode(
                        self_type="RedisStorage",
                        trait_type="StorageHelper",
                        methods=(MethodNode(name="get"),),
                    ),
                ],
            ),
            _make_file_ast(
                file="src/mem.rs",
                module_path="crate::mem",
                impl_blocks=[
                    ImplBlockNode(
                        self_type="InMemoryStorage",
                        trait_type="StorageHelper",
                        methods=(MethodNode(name="get"),),
                    ),
                ],
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.trait_implementations["StorageHelper"]) == 3
        assert len(xref.impl_map) == 3


# endregion: --- Pass 2: Trait Implementation Tests


# ---------------------------------------------------------------------------
# region:    --- Pass 3: Function File Index Tests
# ---------------------------------------------------------------------------


class TestPass3FunctionFileIndex:
    """Verify function_file_index from self_methods."""

    def test_function_indexed_by_name(self, indexer: Indexer) -> None:
        """Free functions and impl methods are indexed by name."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(self_methods=[
                MethodNode(
                    name="process_data",
                    visibility=Visibility.PUBLIC,
                    is_async=True,
                    return_type="Result<Vec<u8>>",
                    context="free",
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "process_data" in xref.function_file_index
        entries = xref.function_file_index["process_data"]
        assert len(entries) == 1
        assert isinstance(entries[0], FunctionIndexEntry)
        assert entries[0].file == "src/lib.rs"
        assert entries[0].is_async is True
        assert entries[0].context == "free"

    def test_multiple_definitions_same_name(self, indexer: Indexer) -> None:
        """Same function name in different files → multiple entries."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                file="src/a.rs",
                module_path="crate::a",
                self_methods=[
                    MethodNode(name="new", context="impl:ServerA"),
                ],
            ),
            _make_file_ast(
                file="src/b.rs",
                module_path="crate::b",
                self_methods=[
                    MethodNode(name="new", context="impl:ServerB"),
                ],
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        entries = xref.function_file_index["new"]
        assert len(entries) == 2
        files = {e.file for e in entries}
        assert "src/a.rs" in files
        assert "src/b.rs" in files

    def test_per_definition_site_not_call_site(self, indexer: Indexer) -> None:
        """Generic name 'new' has one entry per definition, not call."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                file="src/server.rs",
                module_path="crate::server",
                self_methods=[
                    MethodNode(name="new", context="impl:Server"),
                ],
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.function_file_index["new"]) == 1

    def test_function_visibility_captured(self, indexer: Indexer) -> None:
        """Visibility is stored in the FunctionIndexEntry."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(self_methods=[
                MethodNode(
                    name="internal_helper",
                    visibility=Visibility.PRIVATE,
                    context="free",
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        entry = xref.function_file_index["internal_helper"][0]
        assert entry.visibility == "private"

    def test_function_return_type_captured(self, indexer: Indexer) -> None:
        """Return type is stored in the FunctionIndexEntry."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(self_methods=[
                MethodNode(
                    name="fetch",
                    return_type="Result<Response>",
                    context="free",
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        entry = xref.function_file_index["fetch"][0]
        assert entry.return_type == "Result<Response>"


# endregion: --- Pass 3: Function File Index Tests


# ---------------------------------------------------------------------------
# region:    --- Pass 4: Package Method Index Tests
# ---------------------------------------------------------------------------


class TestPass4PackageMethodIndex:
    """Verify package_method_index inversion."""

    def test_scoped_call_indexed(self, indexer: Indexer) -> None:
        """BytesMut::with_capacity → from file_ast maps to index."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                file="src/handler.rs",
                imported_package_methods={
                    "bytes::BytesMut": ["with_capacity", "from"],
                },
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "bytes::BytesMut::with_capacity" in xref.package_method_index
        assert "bytes::BytesMut::from" in xref.package_method_index
        assert "src/handler.rs" in xref.package_method_index[
            "bytes::BytesMut::with_capacity"
        ]

    def test_same_method_multiple_files(self, indexer: Indexer) -> None:
        """Same package method called in two files → two entries."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                file="src/a.rs",
                imported_package_methods={"bytes::BytesMut": ["with_capacity"]},
            ),
            _make_file_ast(
                file="src/b.rs",
                module_path="crate::b",
                imported_package_methods={"bytes::BytesMut": ["with_capacity"]},
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        files = xref.package_method_index["bytes::BytesMut::with_capacity"]
        assert len(files) == 2
        assert "src/a.rs" in files
        assert "src/b.rs" in files

    def test_empty_package_methods(self, indexer: Indexer) -> None:
        """File with no scoped calls → nothing in index."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(imported_package_methods={}),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.package_method_index) == 0


# endregion: --- Pass 4: Package Method Index Tests


# ---------------------------------------------------------------------------
# region:    --- Pass 5: Inter-Crate Dependency Tests
# ---------------------------------------------------------------------------


class TestPass5InterCrateDeps:
    """Verify inter_crate_deps from manifest metadata."""

    def test_path_deps_detected(self, indexer: Indexer) -> None:
        """Path dependencies are recognized as workspace-internal."""
        engine = CrateModel(
            name="approval-engine",
            language="rust",
            dependencies=[
                CrateDependency(
                    name="lib-common",
                    path="../libs/lib-common",
                ),
                CrateDependency(
                    name="lib-storage-service",
                    path="../libs/lib-storage-service",
                ),
                CrateDependency(name="tokio", version="1.32"),
            ],
        )
        lib_common = CrateModel(name="lib-common", language="rust")
        lib_storage = CrateModel(name="lib-storage-service", language="rust")

        ws = WorkspaceAST(crates={
            "approval-engine": engine,
            "lib-common": lib_common,
            "lib-storage-service": lib_storage,
        })
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "approval-engine" in xref.inter_crate_deps
        deps = xref.inter_crate_deps["approval-engine"]
        assert "lib-common" in deps
        assert "lib-storage-service" in deps
        # External dep (tokio) should not be included
        assert "tokio" not in deps

    def test_workspace_inherited_deps(self, indexer: Indexer) -> None:
        """Workspace-inherited deps whose name matches a crate → included."""
        engine = CrateModel(
            name="approval-engine",
            language="rust",
            dependencies=[
                CrateDependency(
                    name="lib-common",
                    is_workspace=True,
                ),
            ],
        )
        lib_common = CrateModel(name="lib-common", language="rust")

        ws = WorkspaceAST(crates={
            "approval-engine": engine,
            "lib-common": lib_common,
        })
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "lib-common" in xref.inter_crate_deps["approval-engine"]

    def test_external_deps_excluded(self, indexer: Indexer) -> None:
        """External-only deps are not in inter_crate_deps."""
        engine = CrateModel(
            name="my-service",
            language="rust",
            dependencies=[
                CrateDependency(name="tokio", version="1.32"),
                CrateDependency(name="serde", version="1.0"),
            ],
        )
        ws = WorkspaceAST(crates={"my-service": engine})
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "my-service" not in xref.inter_crate_deps

    def test_no_self_dependency(self, indexer: Indexer) -> None:
        """A crate should not appear as its own dependency."""
        engine = CrateModel(
            name="my-crate",
            language="rust",
            dependencies=[
                CrateDependency(
                    name="lib-common",
                    path="../libs/lib-common",
                ),
            ],
        )
        lib_common = CrateModel(name="lib-common", language="rust")

        ws = WorkspaceAST(crates={
            "my-crate": engine,
            "lib-common": lib_common,
        })
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        # Crate has deps, so it must appear in inter_crate_deps.
        assert "my-crate" in xref.inter_crate_deps
        assert "my-crate" not in xref.inter_crate_deps["my-crate"]


# endregion: --- Pass 5: Inter-Crate Dependency Tests


# ---------------------------------------------------------------------------
# region:    --- Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Verify edge cases and robustness."""

    def test_empty_workspace(self, indexer: Indexer) -> None:
        """Empty workspace produces empty cross-references."""
        ws = WorkspaceAST()
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.struct_index) == 0
        assert len(xref.enum_index) == 0
        assert len(xref.trait_index) == 0
        assert len(xref.function_file_index) == 0
        assert len(xref.package_method_index) == 0
        assert len(xref.inter_crate_deps) == 0
        assert len(xref.impl_map) == 0

    def test_file_without_module_path(self, indexer: Indexer) -> None:
        """File with no module_path falls back to file path."""
        ws = _single_crate_workspace("my-crate", [
            FileAST(
                file="src/orphan.rs",
                module_path="",
                structs=[StructNode(name="Orphan")],
            ),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "Orphan" in xref.struct_index
        assert xref.struct_index["Orphan"][0] == "src/orphan.rs::Orphan"

    def test_multi_crate_indexes_merged(self, indexer: Indexer) -> None:
        """Indexes from multiple crates are merged."""
        crate_a = CrateModel(
            name="crate-a",
            language="rust",
            files=[
                _make_file_ast(
                    module_path="crate_a",
                    structs=[StructNode(name="Alpha")],
                    self_methods=[MethodNode(name="run", context="free")],
                ),
            ],
        )
        crate_b = CrateModel(
            name="crate-b",
            language="rust",
            files=[
                _make_file_ast(
                    file="src/b.rs",
                    module_path="crate_b",
                    structs=[StructNode(name="Beta")],
                    self_methods=[MethodNode(name="run", context="free")],
                ),
            ],
        )
        ws = WorkspaceAST(crates={"crate-a": crate_a, "crate-b": crate_b})
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "Alpha" in xref.struct_index
        assert "Beta" in xref.struct_index
        assert len(xref.function_file_index["run"]) == 2

    def test_workspace_returned_is_same_object(self, indexer: Indexer) -> None:
        """build_cross_references mutates and returns the same workspace."""
        ws = WorkspaceAST()
        result = indexer.build_cross_references(ws)
        assert result is ws

    def test_comprehensive_single_file(self, indexer: Indexer) -> None:
        """A single rich file populates all index types."""
        file_ast = _make_file_ast(
            structs=[StructNode(name="Config")],
            enums=[
                EnumNode(
                    name="Error",
                    variants=(
                        EnumVariantNode(name="NotFound", kind=EnumVariantKind.UNIT),
                    ),
                ),
            ],
            traits=[
                TraitNode(
                    name="Handler",
                    items=(
                        TraitItemNode(
                            kind=TraitItemKind.REQUIRED_METHOD,
                            name="handle",
                        ),
                    ),
                ),
            ],
            impl_blocks=[
                ImplBlockNode(
                    self_type="MyHandler",
                    trait_type="Handler",
                    methods=(MethodNode(name="handle"),),
                ),
            ],
            self_methods=[
                MethodNode(name="handle", context="impl:Handler for MyHandler"),
                MethodNode(name="main", context="free"),
            ],
            imported_package_methods={"tokio::spawn": ["spawn"]},
        )
        ws = _single_crate_workspace("my-crate", [file_ast])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert "Config" in xref.struct_index
        assert "Error" in xref.enum_index
        assert "Handler" in xref.trait_index
        assert "Handler" in xref.trait_implementations
        assert "handle" in xref.function_file_index
        assert "main" in xref.function_file_index
        assert "tokio::spawn::spawn" in xref.package_method_index
        assert len(xref.impl_map) == 1

    def test_impl_block_empty_self_type_skipped(self, indexer: Indexer) -> None:
        """impl block with empty self_type is ignored in trait_implementations."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(impl_blocks=[
                ImplBlockNode(
                    self_type="",
                    trait_type="SomeTrait",
                    methods=(MethodNode(name="go"),),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.trait_implementations) == 0
        assert len(xref.impl_map) == 0

    def test_impl_block_with_no_methods(self, indexer: Indexer) -> None:
        """impl block with empty methods → impl_map entry has empty tuple."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(impl_blocks=[
                ImplBlockNode(
                    self_type="Marker",
                    trait_type="Display",
                    methods=(),
                ),
            ]),
        ])
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.impl_map) == 1
        assert xref.impl_map[0].methods == ()

    def test_crate_with_empty_files_list(self, indexer: Indexer) -> None:
        """Crate with zero files produces no index entries."""
        crate = CrateModel(name="empty", language="rust", files=[])
        ws = WorkspaceAST(crates={"empty": crate})
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        assert len(xref.struct_index) == 0
        assert len(xref.function_file_index) == 0

    def test_function_index_module_path_stored(self, indexer: Indexer) -> None:
        """FunctionIndexEntry.module_path is set correctly."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                module_path="crate::handlers",
                self_methods=[MethodNode(name="call", context="free")],
            ),
        ])
        result = indexer.build_cross_references(ws)
        entry = result.cross_references.function_file_index["call"][0]

        assert entry.module_path == "crate::handlers"

    def test_pass5_name_collision_external_vs_workspace(self, indexer: Indexer) -> None:
        """External dep whose name matches a workspace crate is NOT included.

        If crate-a depends on "serde" (no path, not workspace-inherited)
        and we also have a workspace crate named "serde", the external dep
        must NOT be treated as an internal dependency.
        """
        crate_a = CrateModel(
            name="crate-a",
            language="rust",
            dependencies=[
                CrateDependency(name="serde", version="1.0"),
            ],
        )
        serde_local = CrateModel(name="serde", language="rust")
        ws = WorkspaceAST(crates={"crate-a": crate_a, "serde": serde_local})
        result = indexer.build_cross_references(ws)
        xref = result.cross_references

        # External "serde" dep should be excluded
        assert "crate-a" not in xref.inter_crate_deps

    def test_pass5_dedup_sorted(self, indexer: Indexer) -> None:
        """Duplicate internal deps are deduplicated and sorted."""
        crate = CrateModel(
            name="my-service",
            language="rust",
            dependencies=[
                CrateDependency(name="lib-b", path="../libs/lib-b"),
                CrateDependency(name="lib-a", path="../libs/lib-a"),
                CrateDependency(name="lib-b", is_workspace=True),
            ],
        )
        lib_a = CrateModel(name="lib-a", language="rust")
        lib_b = CrateModel(name="lib-b", language="rust")
        ws = WorkspaceAST(crates={
            "my-service": crate,
            "lib-a": lib_a,
            "lib-b": lib_b,
        })
        result = indexer.build_cross_references(ws)
        deps = result.cross_references.inter_crate_deps["my-service"]

        assert deps == ["lib-a", "lib-b"]

    def test_package_method_dedup_same_file(self, indexer: Indexer) -> None:
        """Same file appearing twice for the same package method is deduped."""
        ws = _single_crate_workspace("my-crate", [
            _make_file_ast(
                file="src/handler.rs",
                imported_package_methods={
                    "bytes::BytesMut": ["with_capacity"],
                    "bytes::BytesMut": ["with_capacity"],  # noqa: F601
                },
            ),
        ])
        result = indexer.build_cross_references(ws)
        files = result.cross_references.package_method_index[
            "bytes::BytesMut::with_capacity"
        ]
        assert len(files) == 1


# endregion: --- Edge Cases
