"""Workspace-level data models — crate metadata, cross-references, and workspace AST.

These models represent the top-level output structure of AST Intel.
The ``WorkspaceAST`` is the root container serialized to ``ast.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ast_intel.models.ast_node import FileAST

__all__: list[str] = [
    "CrateDependency",
    "CrateModel",
    "CrossReferences",
    "FunctionIndexEntry",
    "ImplMapEntry",
    "WorkspaceAST",
    "WorkspaceMeta",
]


# ---------------------------------------------------------------------------
# region:    --- Crate / Package Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrateDependency:
    """A dependency declared in a crate's manifest.

    Attributes:
        name: Dependency name (e.g., ``tokio``, ``lib-common``).
        version: Version requirement string (e.g., ``">=1.0"``, ``"workspace"``).
        path: Local path if a workspace member dependency.
        features: Enabled features for this dependency.
        is_workspace: Whether this dependency uses workspace inheritance.
        is_dev: Whether this is a dev / test-only dependency.
        is_transitive: Whether this dependency is transitive (not declared in the
            manifest, only present in the resolved lockfile).
    """

    name: str
    version: str = ""
    path: str = ""
    features: tuple[str, ...] = ()
    is_workspace: bool = False
    is_dev: bool = False
    is_transitive: bool = False


@dataclass(slots=True)
class CrateModel:
    """Metadata for a single crate / package / module group.

    Corresponds to one ``Cargo.toml``, ``package.json``, ``go.mod``, etc.

    Attributes:
        name: Crate / package name.
        version: Version string from manifest.
        manifest_path: Workspace-relative path to the manifest file.
        language: Primary language of this crate (e.g., ``rust``, ``go``).
        dependencies: Declared dependencies.
        files: AST extractions for every source file in this crate.
    """

    name: str = ""
    version: str = ""
    manifest_path: str = ""
    language: str = ""
    dependencies: list[CrateDependency] = field(default_factory=list)
    files: list[FileAST] = field(default_factory=list)


# endregion: --- Crate / Package Models


# ---------------------------------------------------------------------------
# region:    --- Cross-Reference Indexes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FunctionIndexEntry:
    """An entry in the function file index — locates a function definition.

    Attributes:
        file: Workspace-relative file path.
        module_path: Fully qualified module path.
        visibility: Access visibility as string.
        is_async: Whether the function is async.
        return_type: Return type as source text.
        context: Definition context (e.g., ``"impl:Trait for Type"`` or ``"free"``).
    """

    file: str
    module_path: str = ""
    visibility: str = ""
    is_async: bool = False
    return_type: str = ""
    context: str = ""


@dataclass(frozen=True, slots=True)
class ImplMapEntry:
    """An entry in the impl map — records a trait implementation.

    Attributes:
        trait_name: Name of the trait being implemented.
        for_type: The concrete type implementing the trait.
        in_module: Module path where the impl is defined.
        methods: Method names implemented.
    """

    trait_name: str
    for_type: str
    in_module: str = ""
    methods: tuple[str, ...] = ()


@dataclass(slots=True)
class CrossReferences:
    """Global cross-reference indexes built after all files are parsed.

    Each index provides O(1) lookup for a specific type of query.

    Attributes:
        struct_index: Struct name → list of qualified module paths.
        enum_index: Enum name → list of qualified module paths.
        trait_index: Trait name → list of qualified module paths.
        trait_implementations: Trait name → list of implementing types (``module::Type``).
        function_file_index: Function name → list of ``FunctionIndexEntry``.
        package_method_index: ``pkg::Type::method`` → list of file paths that call it.
        inter_crate_deps: Crate name → list of dependency crate names.
        impl_map: List of all trait implementations across the workspace.
    """

    struct_index: dict[str, list[str]] = field(default_factory=dict)
    enum_index: dict[str, list[str]] = field(default_factory=dict)
    trait_index: dict[str, list[str]] = field(default_factory=dict)
    trait_implementations: dict[str, list[str]] = field(default_factory=dict)
    function_file_index: dict[str, list[FunctionIndexEntry]] = field(default_factory=dict)
    package_method_index: dict[str, list[str]] = field(default_factory=dict)
    inter_crate_deps: dict[str, list[str]] = field(default_factory=dict)
    impl_map: list[ImplMapEntry] = field(default_factory=list)


# endregion: --- Cross-Reference Indexes


# ---------------------------------------------------------------------------
# region:    --- Workspace-Level Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceMeta:
    """Metadata about the entire analysis run.

    Serialized as the ``meta`` block at the top of ``ast.json``.

    Attributes:
        schema_version: Version of the output JSON schema.
        tool_version: Version of the ast-intel tool.
        generated_at: ISO 8601 timestamp of generation.
        workspace_root: Absolute path to the analyzed workspace.
        total_crates: Number of crates / packages discovered.
        total_files: Total source files processed.
        total_structs: Total struct/class definitions.
        total_enums: Total enum definitions.
        total_traits: Total trait/interface definitions.
        total_functions: Total free function definitions.
        total_impl_blocks: Total implementation blocks.
        total_self_methods: Total self methods across all files.
        total_pkg_method_call_sites: Total scoped package method call sites.
        total_call_edges: Total call edges extracted across all files.
        total_rationale_comments: Total rationale comments extracted.
        total_errors: Total files with parse errors.
    """

    schema_version: str = ""
    tool_version: str = ""
    generated_at: str = ""
    workspace_root: str = ""
    total_crates: int = 0
    total_files: int = 0
    total_structs: int = 0
    total_enums: int = 0
    total_traits: int = 0
    total_functions: int = 0
    total_impl_blocks: int = 0
    total_self_methods: int = 0
    total_pkg_method_call_sites: int = 0
    total_call_edges: int = 0
    total_rationale_comments: int = 0
    total_errors: int = 0


@dataclass(slots=True)
class WorkspaceAST:
    """Root container for the entire workspace analysis.

    This is the in-memory representation that gets serialized to ``ast.json``
    and ``summary.md``.

    Attributes:
        meta: Analysis metadata and statistics.
        crates: Crate name → ``CrateModel`` mapping.
        cross_references: Global cross-reference indexes.
    """

    meta: WorkspaceMeta = field(default_factory=WorkspaceMeta)
    crates: dict[str, CrateModel] = field(default_factory=dict)
    cross_references: CrossReferences = field(default_factory=CrossReferences)


# endregion: --- Workspace-Level Models
