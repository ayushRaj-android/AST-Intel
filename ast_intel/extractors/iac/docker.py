"""Docker extractor — parse Dockerfiles and docker-compose files.

Dockerfile parsing is line-based using regex.  docker-compose parsing
uses ``yaml.safe_load``.  Multi-stage Dockerfile builds produce
``DOCKER_STAGE`` nodes with ``COPIES_FROM`` edges.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.ast_node import Span
from ast_intel.models.iac_model import (
    IaCContext,
    IaCEdge,
    IaCGraph,
    IaCResource,
)

__all__: list[str] = ["DockerExtractor"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Regex patterns
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(
    r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?",
    re.IGNORECASE | re.MULTILINE,
)
_EXPOSE_RE = re.compile(
    r"^EXPOSE\s+(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_COPY_FROM_RE = re.compile(
    r"^COPY\s+--from=(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_ARG_RE = re.compile(
    r"^ARG\s+(\w+)(?:=(.*))?$",
    re.IGNORECASE | re.MULTILINE,
)
_ENV_RE = re.compile(
    r"^ENV\s+(\w+)[= ](.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# endregion: --- Regex patterns
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _parse_image_ref(image: str) -> dict[str, str]:
    """Parse a Docker image reference into components."""
    props: dict[str, str] = {"full_ref": image}
    # Split tag
    if ":" in image.rsplit("/", 1)[-1]:
        base, _, tag = image.rpartition(":")
        props["tag"] = tag
    else:
        base = image
    # Detect registry (contains '.' or ':')
    parts = base.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]):
        props["registry"] = parts[0]
    return props


def _compute_line_number(text: str, char_offset: int) -> int:
    """Convert a character offset to a 1-based line number."""
    return text[:char_offset].count("\n") + 1


def _truncate(value: object, max_len: int = 100) -> str:
    """Stringify and truncate a value for properties."""
    s = str(value)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


# endregion: --- Helpers
# ---------------------------------------------------------------------------


class DockerExtractor(IaCExtractorBase):
    """Parse Dockerfiles and docker-compose files."""

    format_id: ClassVar[str] = "docker"
    file_patterns: ClassVar[list[str]] = [
        "**/Dockerfile*",
        "**/docker-compose.yml",
        "**/docker-compose.yaml",
    ]
    file_extensions: ClassVar[list[str]] = [".yml", ".yaml"]

    # ------------------------------------------------------------------ #
    # region:    --- Detection
    # ------------------------------------------------------------------ #

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:
        """Detect Dockerfiles and docker-compose files."""
        name = file_path.name
        try:
            text = source_peek.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False
        if name.startswith("Dockerfile"):
            return "FROM " in text.upper()
        if name in ("docker-compose.yml", "docker-compose.yaml"):
            return "services:" in text
        return False

    # endregion: --- Detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Extraction
    # ------------------------------------------------------------------ #

    def extract(
        self,
        file_path: Path,  # noqa: ARG002
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Route to Dockerfile or docker-compose parser."""
        name = Path(context.rel_path).name
        if name.startswith("Dockerfile"):
            return self._parse_dockerfile(source, context)
        return self._parse_compose(source, context)

    # endregion: --- Extraction
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Dockerfile parsing
    # ------------------------------------------------------------------ #

    def _parse_dockerfile(
        self,
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse a Dockerfile into DOCKER_IMAGE + DOCKER_STAGE nodes."""
        graph = IaCGraph(file=context.rel_path)
        text = source.decode("utf-8", errors="replace")
        total_lines = text.count("\n") + 1

        stages = self._extract_stages(text, context.rel_path)
        images: dict[str, IaCResource] = {}

        for stage in stages:
            graph.resources.append(stage)
            # Create DOCKER_IMAGE node for base image
            base_image = stage.properties.get("base_image", "")
            if base_image and base_image not in images:
                img_res = self._make_image_node(
                    base_image, context.rel_path, total_lines,
                )
                images[base_image] = img_res
                graph.resources.append(img_res)

        graph.edges = self._build_docker_edges(stages)
        return graph

    def _extract_stages(
        self,
        text: str,
        rel_path: str,
    ) -> list[IaCResource]:
        """Extract DOCKER_STAGE nodes from FROM lines."""
        stages: list[IaCResource] = []
        from_matches = list(_FROM_RE.finditer(text))

        for idx, m in enumerate(from_matches):
            image = m.group(1)
            stage_name = m.group(2) or f"stage-{idx}"
            start_line = _compute_line_number(text, m.start())

            # End line = next FROM or EOF
            if idx + 1 < len(from_matches):
                end_line = _compute_line_number(
                    text, from_matches[idx + 1].start(),
                ) - 1
            else:
                end_line = text.count("\n") + 1

            # Extract stage-specific instructions
            stage_text = text[
                m.start(): (
                    from_matches[idx + 1].start()
                    if idx + 1 < len(from_matches)
                    else len(text)
                )
            ]

            props = self._extract_stage_props(stage_text, image)

            stages.append(
                IaCResource(
                    kind="DockerStage",
                    name=stage_name,
                    file=rel_path,
                    span=Span(
                        start_line=start_line,
                        start_col=0,
                        end_line=end_line,
                        end_col=0,
                    ),
                    properties=props,
                ),
            )

        return stages

    @staticmethod
    def _extract_stage_props(
        stage_text: str,
        base_image: str,
    ) -> dict[str, str]:
        """Extract properties from a single stage's text."""
        props: dict[str, str] = {"base_image": base_image}

        # EXPOSE
        expose_ports = _EXPOSE_RE.findall(stage_text)
        if expose_ports:
            ports: list[str] = []
            for p in expose_ports:
                ports.extend(p.strip().split())
            props["exposed_ports"] = ",".join(ports)

        # ARG
        args = _ARG_RE.findall(stage_text)
        if args:
            props["args"] = ",".join(a[0] for a in args)

        # ENV
        envs = _ENV_RE.findall(stage_text)
        if envs:
            props["env_vars"] = ",".join(e[0] for e in envs)

        # COPY --from
        copies = _COPY_FROM_RE.findall(stage_text)
        if copies:
            props["copies_from"] = ",".join(copies)

        return props

    @staticmethod
    def _make_image_node(
        image: str,
        rel_path: str,
        total_lines: int,
    ) -> IaCResource:
        """Create a DOCKER_IMAGE resource from an image reference."""
        props = _parse_image_ref(image)
        # Use basename for display
        display_name = image.rsplit("/", 1)[-1]
        return IaCResource(
            kind="DockerImage",
            name=display_name,
            file=rel_path,
            span=Span(
                start_line=1, start_col=0,
                end_line=total_lines, end_col=0,
            ),
            properties=props,
        )

    def _build_docker_edges(
        self,
        stages: list[IaCResource],
    ) -> list[IaCEdge]:
        """Build COPIES_FROM edges between stages."""
        edges: list[IaCEdge] = []
        stage_names = {s.name for s in stages}

        for stage in stages:
            copies = stage.properties.get("copies_from", "")
            for source_name in copies.split(","):
                source_name = source_name.strip()  # noqa: PLW2901
                if source_name and source_name in stage_names:
                    edges.append(
                        IaCEdge(
                            source_name=stage.name,
                            target_name=source_name,
                            relation="copies_from",
                        ),
                    )

        return edges

    # endregion: --- Dockerfile parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- docker-compose parsing
    # ------------------------------------------------------------------ #

    def _parse_compose(
        self,
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse docker-compose.yml into DOCKER_SERVICE nodes."""
        graph = IaCGraph(file=context.rel_path)
        text = source.decode("utf-8", errors="replace")

        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            graph.errors.append(f"YAML parse error: {exc}")
            return graph

        if not isinstance(doc, dict):
            return graph

        services: dict[str, Any] = doc.get("services", {}) or {}

        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            resource = self._parse_compose_service(
                svc_name, svc_def, context.rel_path,
            )
            graph.resources.append(resource)

            # Image reference edge
            image = svc_def.get("image")
            if image:
                img_res = IaCResource(
                    kind="DockerImage",
                    name=str(image),
                    file=context.rel_path,
                    properties=_parse_image_ref(str(image)),
                )
                graph.resources.append(img_res)
                graph.edges.append(
                    IaCEdge(
                        source_name=svc_name,
                        target_name=str(image),
                        relation="references_image",
                    ),
                )

        # depends_on edges
        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue
            deps = svc_def.get("depends_on", [])
            if isinstance(deps, (list, dict)):
                for dep in deps:
                    graph.edges.append(
                        IaCEdge(
                            source_name=svc_name,
                            target_name=str(dep),
                            relation="depends_on",
                        ),
                    )

        return graph

    @staticmethod
    def _parse_compose_service(
        name: str,
        svc: dict[str, Any],
        rel_path: str,
    ) -> IaCResource:
        """Parse a single docker-compose service."""
        props: dict[str, str] = {}

        # Ports
        ports = svc.get("ports", [])
        if isinstance(ports, list) and ports:
            props["ports"] = ",".join(str(p) for p in ports)

        # Build context
        build = svc.get("build")
        if isinstance(build, str):
            props["build_context"] = build
        elif isinstance(build, dict):
            if "context" in build:
                props["build_context"] = str(build["context"])
            if "dockerfile" in build:
                props["dockerfile"] = str(build["dockerfile"])

        # Image
        image = svc.get("image")
        if image:
            props["image"] = str(image)

        # Volumes
        volumes = svc.get("volumes", [])
        if isinstance(volumes, list) and volumes:
            props["volumes"] = str(len(volumes))

        # Command
        command = svc.get("command")
        if command:
            props["command"] = _truncate(str(command))

        return IaCResource(
            kind="DockerService",
            name=name,
            file=rel_path,
            properties=props,
        )

    # endregion: --- docker-compose parsing
    # ------------------------------------------------------------------ #
