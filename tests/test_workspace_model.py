"""Tests for workspace-level models."""

from __future__ import annotations

from ast_intel.models.workspace_model import (
    CrateDependency,
    CrateModel,
    CrossReferences,
    FunctionIndexEntry,
    ImplMapEntry,
    WorkspaceAST,
    WorkspaceMeta,
)


class TestCrateDependency:
    def test_defaults(self) -> None:
        dep = CrateDependency(name="tokio")
        assert dep.name == "tokio"
        assert dep.version == ""
        assert dep.is_workspace is False
        assert dep.is_dev is False

    def test_workspace_dep(self) -> None:
        dep = CrateDependency(
            name="lib-common",
            version="workspace",
            path="../libs/lib-common",
            is_workspace=True,
        )
        assert dep.is_workspace is True
        assert dep.path == "../libs/lib-common"


class TestCrateModel:
    def test_defaults(self) -> None:
        c = CrateModel()
        assert c.name == ""
        assert c.files == []
        assert c.dependencies == []


class TestCrossReferences:
    def test_defaults(self) -> None:
        xref = CrossReferences()
        assert xref.struct_index == {}
        assert xref.function_file_index == {}
        assert xref.impl_map == []


class TestFunctionIndexEntry:
    def test_entry(self) -> None:
        entry = FunctionIndexEntry(
            file="src/lib.rs",
            module_path="my_crate",
            visibility="pub",
            is_async=True,
            return_type="Result<()>",
            context="impl:Trait for Type",
        )
        assert entry.is_async is True
        assert entry.context == "impl:Trait for Type"


class TestImplMapEntry:
    def test_entry(self) -> None:
        entry = ImplMapEntry(
            trait_name="StorageHelper",
            for_type="DiskStorage",
            in_module="lib_storage::disk",
            methods=("retrieve", "store"),
        )
        assert len(entry.methods) == 2


class TestWorkspaceMeta:
    def test_defaults(self) -> None:
        meta = WorkspaceMeta()
        assert meta.total_files == 0
        assert meta.schema_version == ""


class TestWorkspaceAST:
    def test_defaults(self) -> None:
        ws = WorkspaceAST()
        assert ws.crates == {}
        assert ws.meta.total_files == 0

    def test_with_crate(self) -> None:
        ws = WorkspaceAST()
        ws.crates["my-crate"] = CrateModel(name="my-crate", language="rust")
        assert len(ws.crates) == 1
