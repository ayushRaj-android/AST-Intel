"""Tests for the Docker extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.iac.docker import (
    DockerExtractor,
    _compute_line_number,
    _parse_image_ref,
)
from ast_intel.models.iac_model import IaCContext

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

DOCKER_FIXTURES = Path(__file__).parent / "fixtures" / "docker"
SAFEGUARD_DOCKERFILE = Path("/home/rajayush/Safeguard/Dockerfile")
SAFEGUARD_COMPOSE = Path(
    "/home/rajayush/Safeguard/.devcontainer/docker-compose.yml",
)


@pytest.fixture()
def extractor() -> DockerExtractor:
    return DockerExtractor()


def _make_context(rel_path: str, workspace: str = "/fake") -> IaCContext:
    return IaCContext(workspace_root=workspace, rel_path=rel_path)


# endregion: --- Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Detection tests
# ---------------------------------------------------------------------------


class TestDockerExtractorDetection:
    """Test can_handle() detection logic."""

    def test_can_handle_dockerfile(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()[:1024]
        assert extractor.can_handle(path, source)

    def test_can_handle_compose(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()[:1024]
        assert extractor.can_handle(path, source)

    def test_rejects_random_yaml(self, extractor: DockerExtractor) -> None:
        path = Path("random.yaml")
        source = b"key: value\nother: stuff\n"
        assert not extractor.can_handle(path, source)

    def test_rejects_non_dockerfile(
        self, extractor: DockerExtractor,
    ) -> None:
        path = Path("README.md")
        source = b"# Docker README\nFROM the beginning..."
        assert not extractor.can_handle(path, source)

    def test_rejects_empty_dockerfile(
        self, extractor: DockerExtractor,
    ) -> None:
        path = Path("Dockerfile")
        assert not extractor.can_handle(path, b"")


# endregion: --- Detection tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helper tests
# ---------------------------------------------------------------------------


class TestImageRefParsing:
    """Test Docker image reference parsing."""

    def test_simple_image(self) -> None:
        result = _parse_image_ref("nginx")
        assert result["full_ref"] == "nginx"
        assert "registry" not in result

    def test_image_with_tag(self) -> None:
        result = _parse_image_ref("python:3.13-slim")
        assert result["tag"] == "3.13-slim"
        assert result["full_ref"] == "python:3.13-slim"

    def test_image_with_registry(self) -> None:
        result = _parse_image_ref(
            "mcr.microsoft.com/mirror/docker/library/ubuntu:24.04",
        )
        assert result["registry"] == "mcr.microsoft.com"
        assert result["tag"] == "24.04"

    def test_image_without_tag(self) -> None:
        result = _parse_image_ref("redis")
        assert "tag" not in result

    def test_compute_line_number(self) -> None:
        text = "line1\nline2\nline3\n"
        assert _compute_line_number(text, 0) == 1
        assert _compute_line_number(text, 6) == 2
        assert _compute_line_number(text, 12) == 3


# endregion: --- Helper tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Dockerfile parsing tests
# ---------------------------------------------------------------------------


class TestDockerfileParsing:
    """Test Dockerfile parsing."""

    def test_multi_stage_build(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        stages = [r for r in graph.resources if r.kind == "DockerStage"]
        assert len(stages) == 2
        assert stages[0].name == "builder"
        assert stages[1].name == "runtime"

    def test_named_stages(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        stage_names = {r.name for r in graph.resources if r.kind == "DockerStage"}
        assert "builder" in stage_names
        assert "runtime" in stage_names

    def test_image_deduplication(self, extractor: DockerExtractor) -> None:
        """Same base image should produce only one DOCKER_IMAGE node."""
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        images = [r for r in graph.resources if r.kind == "DockerImage"]
        # Both stages use python:3.13-slim → 1 image
        assert len(images) == 1
        assert images[0].name == "python:3.13-slim"

    def test_expose_ports(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        runtime = next(
            r for r in graph.resources
            if r.kind == "DockerStage" and r.name == "runtime"
        )
        assert "8080" in runtime.properties.get("exposed_ports", "")

    def test_args_extraction(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        builder = next(
            r for r in graph.resources
            if r.kind == "DockerStage" and r.name == "builder"
        )
        assert "version" in builder.properties.get("args", "")

    def test_copy_from_edges(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        graph = extractor.extract(path, source, ctx)

        copies_from = [
            e for e in graph.edges if e.relation == "copies_from"
        ]
        assert len(copies_from) == 1
        assert copies_from[0].source_name == "runtime"
        assert copies_from[0].target_name == "builder"

    @pytest.mark.skipif(
        not SAFEGUARD_DOCKERFILE.exists(),
        reason="Safeguard repo not available",
    )
    def test_safeguard_dockerfile(
        self, extractor: DockerExtractor,
    ) -> None:
        source = SAFEGUARD_DOCKERFILE.read_bytes()
        ctx = _make_context(
            "Dockerfile",
            workspace="/home/rajayush/Safeguard",
        )
        graph = extractor.extract(SAFEGUARD_DOCKERFILE, source, ctx)

        stages = [r for r in graph.resources if r.kind == "DockerStage"]
        assert len(stages) == 2
        stage_names = {s.name for s in stages}
        assert "build" in stage_names
        assert "runtime" in stage_names

        # Check COPY --from=build
        copies_from = [
            e for e in graph.edges if e.relation == "copies_from"
        ]
        assert len(copies_from) >= 1

        # Check build stage has args
        build_stage = next(s for s in stages if s.name == "build")
        assert "build_number" in build_stage.properties.get("args", "")


# endregion: --- Dockerfile parsing tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- docker-compose tests
# ---------------------------------------------------------------------------


class TestComposeParsing:
    """Test docker-compose.yml parsing."""

    def test_simple_compose(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        services = [
            r for r in graph.resources if r.kind == "DockerService"
        ]
        assert len(services) == 2
        names = {s.name for s in services}
        assert "web" in names
        assert "redis" in names

    def test_service_with_image(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        images = [r for r in graph.resources if r.kind == "DockerImage"]
        assert len(images) >= 1
        image_names = {i.name for i in images}
        assert "redis:7-alpine" in image_names

    def test_service_with_build(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        web = next(
            r for r in graph.resources
            if r.kind == "DockerService" and r.name == "web"
        )
        assert web.properties.get("build_context") == "."

    def test_depends_on_edges(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        depends_on = [
            e for e in graph.edges if e.relation == "depends_on"
        ]
        assert len(depends_on) == 1
        assert depends_on[0].source_name == "web"
        assert depends_on[0].target_name == "redis"

    def test_ports_extraction(self, extractor: DockerExtractor) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        web = next(
            r for r in graph.resources
            if r.kind == "DockerService" and r.name == "web"
        )
        assert "8080:8080" in web.properties.get("ports", "")

    def test_references_image_edge(
        self, extractor: DockerExtractor,
    ) -> None:
        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        graph = extractor.extract(path, source, ctx)

        refs = [
            e for e in graph.edges if e.relation == "references_image"
        ]
        assert len(refs) >= 1
        assert refs[0].source_name == "redis"
        assert refs[0].target_name == "redis:7-alpine"

    @pytest.mark.skipif(
        not SAFEGUARD_COMPOSE.exists(),
        reason="Safeguard devcontainer compose not available",
    )
    def test_safeguard_compose(
        self, extractor: DockerExtractor,
    ) -> None:
        source = SAFEGUARD_COMPOSE.read_bytes()
        ctx = _make_context(
            ".devcontainer/docker-compose.yml",
            workspace="/home/rajayush/Safeguard",
        )
        graph = extractor.extract(SAFEGUARD_COMPOSE, source, ctx)
        services = [
            r for r in graph.resources if r.kind == "DockerService"
        ]
        assert len(services) >= 1


# endregion: --- docker-compose tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Full pipeline tests
# ---------------------------------------------------------------------------


class TestDockerFullPipeline:
    """Integration tests for Docker extraction pipeline."""

    def test_dockerfile_to_graph(self, extractor: DockerExtractor) -> None:
        """Test that Dockerfile output goes through IaCGraphBuilder."""
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder
        from ast_intel.models.graph_model import NodeKind

        path = DOCKER_FIXTURES / "Dockerfile.multistage"
        source = path.read_bytes()
        ctx = _make_context("Dockerfile.multistage")
        iac_graph = extractor.extract(path, source, ctx)

        builder = IaCGraphBuilder()
        code_graph = builder.build([iac_graph])
        assert len(code_graph.nodes) > 0

        node_kinds = {n.kind for n in code_graph.nodes}
        assert NodeKind.DOCKER_STAGE in node_kinds
        assert NodeKind.DOCKER_IMAGE in node_kinds

        # Check node IDs use docker:// prefix
        for node in code_graph.nodes:
            if node.kind in (
                NodeKind.DOCKER_STAGE,
                NodeKind.DOCKER_IMAGE,
            ):
                assert node.id.startswith("docker://"), node.id

    def test_compose_to_graph(self, extractor: DockerExtractor) -> None:
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder
        from ast_intel.models.graph_model import NodeKind

        path = DOCKER_FIXTURES / "docker-compose.yml"
        source = path.read_bytes()
        ctx = _make_context("docker-compose.yml")
        iac_graph = extractor.extract(path, source, ctx)

        builder = IaCGraphBuilder()
        code_graph = builder.build([iac_graph])
        assert len(code_graph.nodes) > 0

        node_kinds = {n.kind for n in code_graph.nodes}
        assert NodeKind.DOCKER_SERVICE in node_kinds

    def test_empty_dockerfile(self, extractor: DockerExtractor) -> None:
        """Empty Dockerfile should return empty graph."""
        ctx = _make_context("Dockerfile")
        graph = extractor.extract(Path("Dockerfile"), b"", ctx)
        assert len(graph.resources) == 0


# endregion: --- Full pipeline tests
# ---------------------------------------------------------------------------
