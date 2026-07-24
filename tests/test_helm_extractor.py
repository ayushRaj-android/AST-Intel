"""Tests for the Helm chart extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.iac.helm import (
    HelmExtractor,
    _flatten_values,
    _is_sensitive_key,
    _truncate,
)
from ast_intel.models.iac_model import IaCContext

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "helm" / "minimal"
SAFEGUARD_CHART = Path("/home/rajayush/Safeguard/deploy/helm-charts/safeguard")


@pytest.fixture()
def extractor() -> HelmExtractor:
    return HelmExtractor()


@pytest.fixture()
def context() -> IaCContext:
    return IaCContext(
        workspace_root=str(FIXTURES_DIR.parent.parent.parent),
        rel_path="fixtures/helm/minimal/Chart.yaml",
    )


@pytest.fixture()
def safeguard_context() -> IaCContext:
    return IaCContext(
        workspace_root="/home/rajayush/Safeguard",
        rel_path="deploy/helm-charts/safeguard/Chart.yaml",
    )


# endregion: --- Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Detection tests
# ---------------------------------------------------------------------------


class TestHelmExtractorDetection:
    """Test can_handle() for Helm chart detection."""

    def test_can_handle_chart_yaml(self, extractor: HelmExtractor) -> None:
        chart_path = FIXTURES_DIR / "Chart.yaml"
        source = chart_path.read_bytes()[:1024]
        assert extractor.can_handle(chart_path, source)

    def test_rejects_non_chart_yaml(self, extractor: HelmExtractor) -> None:
        values_path = FIXTURES_DIR / "values.yaml"
        source = values_path.read_bytes()[:1024]
        assert not extractor.can_handle(values_path, source)

    def test_rejects_k8s_yaml(self, extractor: HelmExtractor) -> None:
        """Plain K8s manifests should not be detected as Helm charts."""
        k8s_path = (
            Path(__file__).parent / "fixtures" / "k8s" / "deployment.yaml"
        )
        if k8s_path.exists():
            source = k8s_path.read_bytes()[:1024]
            assert not extractor.can_handle(k8s_path, source)

    def test_rejects_empty_bytes(self, extractor: HelmExtractor) -> None:
        fake_path = Path("Chart.yaml")
        assert not extractor.can_handle(fake_path, b"")

    def test_rejects_missing_name(self, extractor: HelmExtractor) -> None:
        fake_path = Path("Chart.yaml")
        source = b"apiVersion: v2\nversion: 1.0.0\n"
        assert not extractor.can_handle(fake_path, source)


# endregion: --- Detection tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test helper functions."""

    def test_flatten_simple(self) -> None:
        data = {"a": 1, "b": "hello"}
        result = _flatten_values(data)
        assert len(result) == 2
        assert ("a", 1) in result
        assert ("b", "hello") in result

    def test_flatten_nested(self) -> None:
        data = {"outer": {"inner": 42}}
        result = _flatten_values(data)
        # single-child dict: only the leaf
        assert ("outer.inner", 42) in result

    def test_flatten_depth_limit(self) -> None:
        data = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        result = _flatten_values(data, max_depth=2)
        keys = [k for k, _ in result]
        # Should not go past depth 2
        assert "a.b.c.d.e" not in keys

    def test_flatten_intermediate_large_dict(self) -> None:
        data = {"parent": {"a": 1, "b": 2, "c": 3, "d": 4}}
        result = _flatten_values(data)
        keys = [k for k, _ in result]
        # parent has >3 children → included as intermediate
        assert "parent" in keys
        assert "parent.a" in keys

    def test_truncate_short(self) -> None:
        assert _truncate("hello") == "hello"

    def test_truncate_long(self) -> None:
        val = "x" * 100
        result = _truncate(val, max_len=20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_sensitive_key_detection(self) -> None:
        assert _is_sensitive_key("redis.password")
        assert _is_sensitive_key("tls.secretName")
        assert _is_sensitive_key("auth.token")
        assert not _is_sensitive_key("replicas")
        assert not _is_sensitive_key("image.repository")


# endregion: --- Helper tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Chart.yaml parsing tests
# ---------------------------------------------------------------------------


class TestHelmChartParsing:
    """Test Chart.yaml parsing."""

    def test_parse_minimal_chart(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        charts = [r for r in graph.resources if r.kind == "HelmChart"]
        assert len(charts) == 1
        assert charts[0].name == "test-chart"

    def test_chart_properties(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        chart = next(r for r in graph.resources if r.kind == "HelmChart")
        assert chart.properties["version"] == "0.1.0"
        assert chart.properties["description"] == "A minimal test chart"

    @pytest.mark.skipif(
        not SAFEGUARD_CHART.exists(),
        reason="Safeguard repo not available",
    )
    def test_parse_safeguard_chart(
        self, extractor: HelmExtractor, safeguard_context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(
            SAFEGUARD_CHART, safeguard_context,
        )
        charts = [r for r in graph.resources if r.kind == "HelmChart"]
        assert len(charts) == 1
        assert charts[0].name == "safeguard"
        assert charts[0].properties["version"] == "1.0.0"
        assert charts[0].properties["type"] == "application"


# endregion: --- Chart.yaml parsing tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- values.yaml parsing tests
# ---------------------------------------------------------------------------


class TestHelmValuesParsing:
    """Test values.yaml flattening and value extraction."""

    def test_parse_values(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        values = [r for r in graph.resources if r.kind == "HelmValue"]
        assert len(values) > 0
        names = {v.name for v in values}
        assert "replicaCount" in names
        assert "image.repository" in names
        assert "image.tag" in names

    def test_sensitive_key_redacted(
        self, extractor: HelmExtractor,
    ) -> None:
        """Sensitive keys should have [REDACTED] as default_value."""
        from ast_intel.models.iac_model import IaCContext as Ctx

        # Create a temp chart with a password value
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "Chart.yaml").write_text(
                "apiVersion: v2\nname: test\nversion: 0.1.0\n",
            )
            (tmp_path / "values.yaml").write_text(
                "database:\n  password: supersecret\n  host: localhost\n",
            )
            ctx = Ctx(
                workspace_root=tmp,
                rel_path="Chart.yaml",
            )
            graph = extractor.extract_directory(tmp_path, ctx)
            values = [r for r in graph.resources if r.kind == "HelmValue"]
            pw = next(
                v for v in values if v.name == "database.password"
            )
            assert pw.properties["default_value"] == "[REDACTED]"

    def test_value_type_property(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        values = {
            v.name: v
            for v in graph.resources
            if v.kind == "HelmValue"
        }
        assert values["replicaCount"].properties["value_type"] == "int"
        assert (
            values["image.repository"].properties["value_type"] == "str"
        )

    @pytest.mark.skipif(
        not SAFEGUARD_CHART.exists(),
        reason="Safeguard repo not available",
    )
    def test_safeguard_values(
        self, extractor: HelmExtractor, safeguard_context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(
            SAFEGUARD_CHART, safeguard_context,
        )
        values = [r for r in graph.resources if r.kind == "HelmValue"]
        # Safeguard values.yaml should produce many value nodes
        assert len(values) >= 10


# endregion: --- values.yaml parsing tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Template parsing tests
# ---------------------------------------------------------------------------


class TestHelmTemplateParsing:
    """Test Helm template file parsing."""

    def test_template_count(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        templates = [
            r for r in graph.resources if r.kind == "HelmTemplate"
        ]
        # deployment.yaml, service.yaml, _helpers.tpl
        assert len(templates) == 3

    def test_extract_values_refs(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        deploy_tmpl = next(
            r for r in graph.resources
            if r.kind == "HelmTemplate" and r.name == "deployment.yaml"
        )
        # deployment.yaml references .Values.replicaCount,
        # .Values.image.repository, .Values.image.tag,
        # .Values.service.port
        assert int(deploy_tmpl.properties["value_refs"]) >= 4

    def test_detect_k8s_kinds(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        deploy_tmpl = next(
            r for r in graph.resources
            if r.kind == "HelmTemplate" and r.name == "deployment.yaml"
        )
        assert "Deployment" in deploy_tmpl.properties.get("k8s_kinds", "")

    def test_helpers_defines(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        helpers = next(
            r for r in graph.resources
            if r.kind == "HelmTemplate" and r.name == "_helpers.tpl"
        )
        defines = helpers.properties.get("defines", "")
        assert "test-chart.labels" in defines
        assert "test-chart.namespace" in defines

    @pytest.mark.skipif(
        not SAFEGUARD_CHART.exists(),
        reason="Safeguard repo not available",
    )
    def test_safeguard_templates(
        self, extractor: HelmExtractor, safeguard_context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(
            SAFEGUARD_CHART, safeguard_context,
        )
        templates = [
            r for r in graph.resources if r.kind == "HelmTemplate"
        ]
        # Safeguard has many templates
        assert len(templates) >= 10


# endregion: --- Template parsing tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Edge building tests
# ---------------------------------------------------------------------------


class TestHelmEdgeBuilding:
    """Test edge construction for Helm charts."""

    def test_contains_edges(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        contains = [e for e in graph.edges if e.relation == "contains"]
        # chart → each template
        assert len(contains) == 3

    def test_value_of_edges(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        value_of = [e for e in graph.edges if e.relation == "value_of"]
        # deployment.yaml and service.yaml reference values
        assert len(value_of) >= 3

    def test_templates_to_edges(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        templates_to = [
            e for e in graph.edges if e.relation == "templates_to"
        ]
        # deployment.yaml → Deployment, service.yaml → Service
        assert len(templates_to) >= 2

    def test_value_key_matching(self) -> None:
        """Test prefix matching for value keys."""
        keys = {"image", "image.repository", "image.tag", "replicaCount"}
        assert HelmExtractor._match_value_key("image.repository", keys) == (
            "image.repository"
        )
        # Nonexistent key with prefix
        assert HelmExtractor._match_value_key(
            "image.pullPolicy", keys,
        ) == "image"
        # No match at all
        assert HelmExtractor._match_value_key("unknown.key", keys) == ""


# endregion: --- Edge building tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Full pipeline tests
# ---------------------------------------------------------------------------


class TestHelmFullPipeline:
    """Integration tests for the full Helm chart pipeline."""

    def test_minimal_chart_pipeline(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(FIXTURES_DIR, context)
        assert graph.resources  # Has resources
        assert graph.edges  # Has edges

        kinds = {r.kind for r in graph.resources}
        assert "HelmChart" in kinds
        assert "HelmValue" in kinds
        assert "HelmTemplate" in kinds

    @pytest.mark.skipif(
        not SAFEGUARD_CHART.exists(),
        reason="Safeguard repo not available",
    )
    def test_safeguard_chart_pipeline(
        self, extractor: HelmExtractor, safeguard_context: IaCContext,
    ) -> None:
        graph = extractor.extract_directory(
            SAFEGUARD_CHART, safeguard_context,
        )
        assert len(graph.resources) >= 20
        assert len(graph.edges) >= 10

        kinds = {r.kind for r in graph.resources}
        assert "HelmChart" in kinds
        assert "HelmValue" in kinds
        assert "HelmTemplate" in kinds

    def test_missing_chart_yaml(self, extractor: HelmExtractor) -> None:
        """Empty directory should return empty graph."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ctx = IaCContext(workspace_root=tmp, rel_path=".")
            graph = extractor.extract_directory(Path(tmp), ctx)
            assert len(graph.resources) == 0

    def test_graph_builder_integration(
        self, extractor: HelmExtractor, context: IaCContext,
    ) -> None:
        """Test that HelmExtractor output goes through IaCGraphBuilder."""
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder

        iac_graph = extractor.extract_directory(FIXTURES_DIR, context)
        builder = IaCGraphBuilder()
        code_graph = builder.build([iac_graph])
        assert len(code_graph.nodes) > 0
        assert len(code_graph.edges) > 0

        # Check node kinds are properly mapped
        from ast_intel.models.graph_model import NodeKind

        node_kinds = {n.kind for n in code_graph.nodes}
        assert NodeKind.HELM_CHART in node_kinds
        assert NodeKind.HELM_VALUE in node_kinds
        assert NodeKind.HELM_TEMPLATE in node_kinds

        # Check node IDs use helm:// prefix
        for node in code_graph.nodes:
            if node.kind in (
                NodeKind.HELM_CHART,
                NodeKind.HELM_VALUE,
                NodeKind.HELM_TEMPLATE,
            ):
                assert node.id.startswith("helm://"), node.id


# endregion: --- Full pipeline tests
# ---------------------------------------------------------------------------
