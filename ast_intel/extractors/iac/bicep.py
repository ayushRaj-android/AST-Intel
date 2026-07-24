"""Bicep / ARM template extractor — parse Azure resource declarations.

Handles two formats:

- **Bicep** (``*.bicep``): ``resource x 'Microsoft.Type/sub@version' = { … }``
- **ARM JSON** (``*.json`` with ``$schema`` containing ``deploymentTemplate``):
  ``"resources": [{ "type": "Microsoft.Type/sub", "name": "…" }]``

Each recognized Azure resource type is emitted directly as a
``CLOUD_RESOURCE`` IaC resource (via the taxonomy classification),
so it bypasses the ``TF_RESOURCE → CLOUD_RESOURCE`` promotion path.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, ClassVar

from ast_intel.extractors._cloud_taxonomy import classify_iac_type
from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.ast_node import Span
from ast_intel.models.iac_model import IaCGraph, IaCResource

if TYPE_CHECKING:
    from pathlib import Path

    from ast_intel.models.iac_model import IaCContext

__all__: list[str] = ["BicepExtractor"]

logger = logging.getLogger(__name__)

# Regex for Bicep resource declarations:
#   resource <name> 'Microsoft.Cache/redis@2023-01-01' = { ... }
_BICEP_RESOURCE_RE: re.Pattern[str] = re.compile(
    r"^\s*resource\s+(\w+)\s+'([^']+)'",
    re.MULTILINE,
)

# ARM JSON deploymentTemplate schema markers
_ARM_SCHEMA_MARKERS: tuple[str, ...] = (
    "deploymenttemplate",
    "deploymentparameters",
)


class BicepExtractor(IaCExtractorBase):
    """Extract Azure cloud resources from Bicep and ARM template files."""

    format_id: ClassVar[str] = "bicep"
    file_patterns: ClassVar[list[str]] = ["*.bicep"]
    file_extensions: ClassVar[list[str]] = [".bicep"]

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:
        """Handle ``.bicep`` files and ARM JSON templates."""
        if file_path.suffix == ".bicep":
            return True
        if file_path.suffix == ".json":
            text = source_peek.decode("utf-8", errors="replace").lower()
            return any(marker in text for marker in _ARM_SCHEMA_MARKERS)
        return False

    def extract(
        self,
        file_path: Path,
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse Bicep or ARM JSON and extract CLOUD_RESOURCE resources."""
        rel_path = context.rel_path or str(file_path.name)

        if file_path.suffix == ".bicep":
            return self._extract_bicep(source, rel_path)
        return self._extract_arm_json(source, rel_path)

    def _extract_bicep(self, source: bytes, rel_path: str) -> IaCGraph:
        """Parse Bicep resource declarations."""
        text = source.decode("utf-8", errors="replace")
        resources: list[IaCResource] = []

        for match in _BICEP_RESOURCE_RE.finditer(text):
            symbolic_name = match.group(1)
            type_with_version = match.group(2)
            # Strip API version: 'Microsoft.Cache/redis@2023-01-01' → 'Microsoft.Cache/redis'
            resource_type = type_with_version.split("@")[0]

            info = classify_iac_type(resource_type)
            if info is None:
                continue

            line = text[: match.start()].count("\n") + 1
            resources.append(
                IaCResource(
                    kind="CloudResource",
                    name=symbolic_name,
                    file=rel_path,
                    span=Span(
                        start_line=line, start_col=0,
                        end_line=line, end_col=len(match.group(0)),
                    ),
                    properties={
                        "resource_type": resource_type,
                        "provider": info.provider,
                        "service": info.service,
                        "category": info.category,
                        "source": "iac",
                    },
                ),
            )

        return IaCGraph(file=rel_path, resources=resources)

    def _extract_arm_json(self, source: bytes, rel_path: str) -> IaCGraph:
        """Parse ARM JSON template resources."""
        resources: list[IaCResource] = []
        try:
            data = json.loads(source)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return IaCGraph(file=rel_path, errors=["Invalid JSON"])

        if not isinstance(data, dict):
            return IaCGraph(file=rel_path)

        for res in data.get("resources", []):
            if not isinstance(res, dict):
                continue
            resource_type = res.get("type", "")
            name = res.get("name", "")
            if not resource_type:
                continue

            info = classify_iac_type(resource_type)
            if info is None:
                continue

            resources.append(
                IaCResource(
                    kind="CloudResource",
                    name=name or resource_type.split("/")[-1],
                    file=rel_path,
                    properties={
                        "resource_type": resource_type,
                        "provider": info.provider,
                        "service": info.service,
                        "category": info.category,
                        "source": "iac",
                    },
                ),
            )

        return IaCGraph(file=rel_path, resources=resources)
