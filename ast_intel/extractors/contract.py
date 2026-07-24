"""Contract file extractor — parses API contracts as first-class AST sources.

Supports OpenAPI / Swagger (YAML and JSON), Protobuf / gRPC (``.proto``),
and GraphQL (``.graphql`` / ``.gql``).

Contract files are *not* source code and do not use tree-sitter.  Instead,
each format has its own parser that produces the same :class:`FileAST`
output as language extractors.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ast_intel.extractors._graphql import parse_graphql
from ast_intel.extractors._openapi import parse_openapi
from ast_intel.extractors._proto import parse_proto
from ast_intel.extractors.base import ExtractorBase
from ast_intel.models.ast_node import FileAST
from ast_intel.models.workspace_model import CrateModel

__all__: list[str] = [
    "ContractExtractor",
]

logger = logging.getLogger(__name__)

# Extensions that may contain OpenAPI specs.
_OPENAPI_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml", ".json"})


class ContractExtractor(ExtractorBase):
    """Extractor for API contract files.

    Dispatches to format-specific parsers based on file extension and
    populates ``FileAST.routes`` and ``FileAST.structs`` from the
    parsed contract.
    """

    language_id: str = "contract"
    file_extensions: list[str] = [  # noqa: RUF012
        ".yaml", ".yml", ".json",
        ".proto",
        ".graphql", ".gql",
    ]

    def extract(self, file_path: Path, source: bytes) -> FileAST:
        """Parse a contract file and return a populated :class:`FileAST`."""
        ast = FileAST(file=str(file_path))
        suffix = file_path.suffix.lower()

        if suffix in _OPENAPI_SUFFIXES:
            routes, structs = parse_openapi(source, str(file_path))
            ast.routes = routes
            ast.structs = structs
        elif suffix == ".proto":
            routes, structs, traits = parse_proto(source, str(file_path))
            ast.routes = routes
            ast.structs = structs
            ast.traits = traits
        elif suffix in {".graphql", ".gql"}:
            routes, structs = parse_graphql(source, str(file_path))
            ast.routes = routes
            ast.structs = structs

        return ast

    def parse_manifest(self, manifest_path: Path) -> CrateModel:
        """Return a minimal crate model for contract files."""
        return CrateModel(
            name="contracts",
            language="contract",
            manifest_path=str(manifest_path),
        )
