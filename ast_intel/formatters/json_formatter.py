"""JSON formatter — serializes WorkspaceAST to deterministic JSON.

Produces ``ast.json`` with:
- Sorted keys for deterministic output
- Custom encoding for dataclasses, Enums, Paths, sets
- ``meta`` block with schema version, tool version, and statistics

Same input always produces **byte-identical** output.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ast_intel import SCHEMA_VERSION, TOOL_VERSION
from ast_intel.core.cache import compute_file_hash
from ast_intel.models.graph_model import CodeGraph
from ast_intel.models.workspace_model import (
    CrossReferences,
    WorkspaceAST,
    WorkspaceMeta,
)

__all__: list[str] = ["JsonFormatter"]

logger = logging.getLogger(__name__)


class _ASTEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles AST dataclasses and special types."""

    def default(self, o: Any) -> Any:  # noqa: ANN401, PLR0911
        """Encode non-standard types."""
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, SimpleNamespace):
            return o.__dict__
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, set | frozenset):
            return sorted(o)
        if isinstance(o, tuple):
            return list(o)
        return super().default(o)


class JsonFormatter:
    """Serialize a ``WorkspaceAST`` to a JSON file.

    The output is deterministic: sorted keys, consistent formatting,
    and stable ordering of all collections.
    """

    def write(
        self,
        workspace: WorkspaceAST,
        output_path: Path,
        *,
        iac_graph: CodeGraph | None = None,
    ) -> None:
        """Write the workspace AST to a JSON file.

        Args:
            workspace: The fully indexed workspace AST.
            output_path: Absolute path to write ``ast.json``.
            iac_graph: Optional IaC-only ``CodeGraph`` to include as
                a top-level ``iac`` section.
        """
        logger.info("Writing JSON to %s", output_path)

        data = self._serialize(workspace)

        if iac_graph is not None and (iac_graph.nodes or iac_graph.edges):
            data["iac"] = {
                "nodes": sorted(
                    [asdict(n) for n in iac_graph.nodes],
                    key=lambda n: n.get("id", ""),
                ),
                "edges": sorted(
                    [asdict(e) for e in iac_graph.edges],
                    key=lambda e: (
                        e.get("source", ""),
                        e.get("target", ""),
                    ),
                ),
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                cls=_ASTEncoder,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            f.write("\n")  # Trailing newline

        size_kb = output_path.stat().st_size / 1024
        logger.info("Wrote %s (%.1f KB)", output_path.name, size_kb)

    @staticmethod
    def _serialize(workspace: WorkspaceAST) -> dict[str, Any]:
        """Convert WorkspaceAST to a JSON-serializable dict.

        Builds the top-level structure::

            {
                "meta": { ... },
                "crates": { "name": { ... }, ... },
                "cross_references": { ... }
            }

        Args:
            workspace: The workspace AST to serialize.

        Returns:
            A nested dict structure matching the output schema.
        """
        meta = _build_meta(workspace)
        meta_dict = asdict(meta)

        # Compute file hashes for the meta block
        root = workspace.meta.workspace_root
        repo_root = Path(root) if root else Path.cwd()
        file_hashes: dict[str, str] = {}
        for crate in workspace.crates.values():
            for f in crate.files:
                abs_path = repo_root / f.file
                file_hashes[f.file] = compute_file_hash(abs_path)
        meta_dict["file_hashes"] = dict(sorted(file_hashes.items()))

        # Serialize crates — each crate becomes a dict with files
        crates_dict: dict[str, Any] = {}
        for crate_name, crate in sorted(workspace.crates.items()):
            crate_data = asdict(crate)
            # Sort files by file path for determinism
            crate_data["files"] = sorted(
                crate_data.get("files", []),
                key=lambda f: f.get("file", ""),
            )
            crates_dict[crate_name] = crate_data

        # Serialize cross-references
        xref = workspace.cross_references
        xref_dict = _serialize_cross_references(xref)

        return {
            "meta": meta_dict,
            "crates": crates_dict,
            "cross_references": xref_dict,
        }


# ---------------------------------------------------------------------------
# region:    --- Meta Builder
# ---------------------------------------------------------------------------


def _build_meta(workspace: WorkspaceAST) -> WorkspaceMeta:
    """Compute statistics and return a populated WorkspaceMeta.

    If the workspace already has a populated meta (non-empty ``generated_at``),
    returns it as-is to support deterministic re-serialization.
    """
    if workspace.meta.generated_at:
        return workspace.meta

    all_files = [
        f for crate in workspace.crates.values() for f in crate.files
    ]

    meta = WorkspaceMeta(
        schema_version=SCHEMA_VERSION,
        tool_version=TOOL_VERSION,
        generated_at=datetime.now(tz=UTC).isoformat(),
        workspace_root=workspace.meta.workspace_root,
        total_crates=len(workspace.crates),
        total_files=len(all_files),
        total_structs=sum(len(f.structs) for f in all_files),
        total_enums=sum(len(f.enums) for f in all_files),
        total_traits=sum(len(f.traits) for f in all_files),
        total_functions=sum(len(f.functions) for f in all_files),
        total_impl_blocks=sum(len(f.impl_blocks) for f in all_files),
        total_self_methods=sum(len(f.self_methods) for f in all_files),
        total_pkg_method_call_sites=sum(
            len(f.imported_package_methods) for f in all_files
        ),
        total_call_edges=sum(len(f.call_edges) for f in all_files),
        total_rationale_comments=sum(
            len(f.rationale_comments) for f in all_files
        ),
        total_errors=sum(1 for f in all_files if f.errors),
    )
    # Attach back to workspace so downstream emitters see it
    workspace.meta = meta
    return meta


# endregion: --- Meta Builder


# ---------------------------------------------------------------------------
# region:    --- Cross-Reference Serializer
# ---------------------------------------------------------------------------


def _serialize_cross_references(xref: CrossReferences) -> dict[str, Any]:
    """Serialize CrossReferences to a plain dict.

    Produces sorted, deterministic output for each index.
    ``FunctionIndexEntry`` and ``ImplMapEntry`` are converted to dicts.
    """
    # function_file_index: sort by name, entries by file
    fn_index: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(xref.function_file_index):
        entries = xref.function_file_index[name]
        fn_index[name] = sorted(
            [asdict(e) for e in entries],
            key=lambda d: (d.get("file", ""), d.get("context", "")),
        )

    # impl_map: sort by trait_name, for_type
    impl_map = sorted(
        [asdict(e) for e in xref.impl_map],
        key=lambda d: (d.get("trait_name", ""), d.get("for_type", "")),
    )

    return {
        "struct_index": dict(sorted(xref.struct_index.items())),
        "enum_index": dict(sorted(xref.enum_index.items())),
        "trait_index": dict(sorted(xref.trait_index.items())),
        "trait_implementations": dict(sorted(xref.trait_implementations.items())),
        "function_file_index": fn_index,
        "package_method_index": dict(sorted(xref.package_method_index.items())),
        "inter_crate_deps": dict(sorted(xref.inter_crate_deps.items())),
        "impl_map": impl_map,
    }


# endregion: --- Cross-Reference Serializer
