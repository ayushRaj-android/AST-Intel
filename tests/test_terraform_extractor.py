"""Tests for the Terraform extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.iac.terraform import (
    TerraformExtractor,
    _collect_refs,
    _ref_to_resource_id,
    _strip_quotes,
)
from ast_intel.models.iac_model import IaCContext

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def extractor() -> TerraformExtractor:
    return TerraformExtractor()


@pytest.fixture
def ctx() -> IaCContext:
    return IaCContext(workspace_root="/tmp", rel_path="infra/main.tf")


def _extract(
    extractor: TerraformExtractor,
    source: str,
    ctx: IaCContext,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Helper: extract and return (resource names, edges as tuples)."""
    result = extractor.extract(Path("/tmp/main.tf"), source.encode(), ctx)
    names = [r.name for r in result.resources]
    edges = [
        (e.source_name, e.relation, e.target_name) for e in result.edges
    ]
    return names, edges


# endregion: --- Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Unit tests: helpers
# ---------------------------------------------------------------------------


class TestStripQuotes:
    """Test _strip_quotes helper."""

    def test_quoted_string(self) -> None:
        assert _strip_quotes('"hello"') == "hello"

    def test_unquoted_string(self) -> None:
        assert _strip_quotes("hello") == "hello"

    def test_empty_quotes(self) -> None:
        assert _strip_quotes('""') == ""

    def test_partial_quotes(self) -> None:
        assert _strip_quotes('"hello') == '"hello'


class TestCollectRefs:
    """Test _collect_refs from values."""

    def test_interpolation_ref(self) -> None:
        refs = _collect_refs("${aws_instance.web.id}")
        assert "aws_instance.web.id" in refs

    def test_var_ref(self) -> None:
        refs = _collect_refs("${var.region}")
        assert "var.region" in refs

    def test_module_ref(self) -> None:
        refs = _collect_refs("${module.vpc.subnet_id}")
        assert "module.vpc.subnet_id" in refs

    def test_nested_dict(self) -> None:
        refs = _collect_refs({"a": "${aws_s3_bucket.data.arn}"})
        assert "aws_s3_bucket.data.arn" in refs

    def test_list(self) -> None:
        refs = _collect_refs(["${aws_instance.web.id}", "literal"])
        assert "aws_instance.web.id" in refs

    def test_no_refs(self) -> None:
        assert _collect_refs("simple string") == set()


class TestRefToResourceId:
    """Test _ref_to_resource_id."""

    def test_resource_ref(self) -> None:
        assert _ref_to_resource_id("aws_instance.web.id") == "aws_instance.web"

    def test_data_ref(self) -> None:
        assert (
            _ref_to_resource_id("data.aws_ami.ubuntu.id")
            == "data.aws_ami.ubuntu"
        )

    def test_var_ref(self) -> None:
        assert _ref_to_resource_id("var.region") == "var.region"

    def test_module_ref(self) -> None:
        assert _ref_to_resource_id("module.vpc") == "module.vpc"

    def test_single_part(self) -> None:
        assert _ref_to_resource_id("local") is None


# endregion: --- Unit tests: helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Unit tests: can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    """Test file detection."""

    def test_tf_file(self, extractor: TerraformExtractor) -> None:
        assert extractor.can_handle(Path("main.tf"), b"resource")

    def test_tfvars_file(self, extractor: TerraformExtractor) -> None:
        assert extractor.can_handle(Path("vars.tfvars"), b"region")

    def test_yaml_rejected(self, extractor: TerraformExtractor) -> None:
        assert not extractor.can_handle(Path("k8s.yaml"), b"apiVersion")

    def test_case_insensitive(self, extractor: TerraformExtractor) -> None:
        assert extractor.can_handle(Path("Main.TF"), b"")


# endregion: --- Unit tests: can_handle
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Unit tests: resource extraction
# ---------------------------------------------------------------------------


class TestResourceExtraction:
    """Test extraction of different Terraform block types."""

    def test_single_resource(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_instance" "web" {
  ami           = "ami-12345"
  instance_type = "t3.micro"
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "aws_instance.web" in names

    def test_resource_properties(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_instance" "web" {
  ami           = "ami-12345"
  instance_type = "t3.micro"
}
"""
        result = extractor.extract(Path("/tmp/main.tf"), source.encode(), ctx)
        res = result.resources[0]
        assert res.kind == "TfResource"
        assert res.properties["resource_type"] == "aws_instance"
        assert res.properties["ami"] == "ami-12345"
        assert res.properties["instance_type"] == "t3.micro"

    def test_data_source(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
data "aws_ami" "ubuntu" {
  most_recent = true
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "data.aws_ami.ubuntu" in names

    def test_module(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
module "vpc" {
  source = "./modules/vpc"
  cidr   = "10.0.0.0/16"
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "module.vpc" in names

    def test_variable(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
variable "region" {
  type    = string
  default = "us-east-1"
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "var.region" in names

    def test_output(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
output "instance_ip" {
  value = aws_instance.web.public_ip
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "output.instance_ip" in names

    def test_provider(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
provider "aws" {
  region = "us-east-1"
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "provider.aws" in names

    def test_multiple_resources(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_instance" "web" {
  ami = "ami-123"
}
resource "aws_instance" "api" {
  ami = "ami-456"
}
resource "aws_s3_bucket" "data" {
  bucket = "my-bucket"
}
"""
        names, _ = _extract(extractor, source, ctx)
        assert "aws_instance.web" in names
        assert "aws_instance.api" in names
        assert "aws_s3_bucket.data" in names


# endregion: --- Unit tests: resource extraction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Unit tests: dependency edges
# ---------------------------------------------------------------------------


class TestDependencyEdges:
    """Test implicit dependency edge detection."""

    def test_resource_depends_on_data(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_instance" "web" {
  ami = data.aws_ami.ubuntu.id
}
data "aws_ami" "ubuntu" {
  most_recent = true
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert ("aws_instance.web", "depends_on", "data.aws_ami.ubuntu") in edges

    def test_output_depends_on_resource(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_instance" "web" {
  ami = "ami-123"
}
output "ip" {
  value = aws_instance.web.public_ip
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert ("output.ip", "depends_on", "aws_instance.web") in edges

    def test_provider_depends_on_variable(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
variable "region" {
  default = "us-east-1"
}
provider "aws" {
  region = var.region
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert ("provider.aws", "depends_on", "var.region") in edges

    def test_module_depends_on_resource(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = """
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
module "subnet" {
  source = "./modules/subnet"
  vpc_id = aws_vpc.main.id
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert ("module.subnet", "depends_on", "aws_vpc.main") in edges

    def test_no_self_edge(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        """Resources should not depend on themselves."""
        source = """
resource "aws_instance" "web" {
  ami = "ami-123"
  tags = {
    Name = "web"
  }
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert not any(
            s == t for s, _, t in edges
        )

    def test_no_edge_to_unknown(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        """Edges should not target resources not in the file."""
        source = """
resource "aws_instance" "web" {
  ami = data.aws_ami.missing.id
}
"""
        _, edges = _extract(extractor, source, ctx)
        assert len(edges) == 0


# endregion: --- Unit tests: dependency edges
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Unit tests: error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test graceful error handling."""

    def test_invalid_hcl(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = b"this is not { valid HCL at all }"
        result = extractor.extract(Path("/tmp/bad.tf"), source, ctx)
        assert result.errors
        assert result.resources == []

    def test_empty_file(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        source = b""
        result = extractor.extract(Path("/tmp/empty.tf"), source, ctx)
        assert result.resources == []
        assert result.edges == []


# endregion: --- Unit tests: error handling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Integration: full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Test extraction through IaCGraphBuilder."""

    def test_graph_builder_integration(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder
        from ast_intel.models.graph_model import EdgeRelation, NodeKind

        source = """
resource "aws_instance" "web" {
  ami = data.aws_ami.ubuntu.id
}
data "aws_ami" "ubuntu" {
  most_recent = true
}
"""
        iac_graph = extractor.extract(
            Path("/tmp/main.tf"), source.encode(), ctx,
        )
        builder = IaCGraphBuilder()
        graph = builder.build([iac_graph])

        assert len(graph.nodes) == 2
        kinds = {n.kind for n in graph.nodes}
        assert NodeKind.TF_RESOURCE in kinds
        assert NodeKind.TF_DATA in kinds

        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.relation == EdgeRelation.DEPENDS_ON

    def test_all_node_kinds(
        self, extractor: TerraformExtractor, ctx: IaCContext,
    ) -> None:
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder
        from ast_intel.models.graph_model import NodeKind

        source = """
variable "region" { default = "us-east-1" }
provider "aws" { region = "us-east-1" }
data "aws_ami" "ubuntu" { most_recent = true }
resource "aws_instance" "web" { ami = "123" }
output "ip" { value = "1.2.3.4" }
module "vpc" { source = "./vpc" }
"""
        iac_graph = extractor.extract(
            Path("/tmp/main.tf"), source.encode(), ctx,
        )
        builder = IaCGraphBuilder()
        graph = builder.build([iac_graph])

        kinds = {n.kind for n in graph.nodes}
        assert NodeKind.TF_RESOURCE in kinds
        assert NodeKind.TF_DATA in kinds
        assert NodeKind.TF_MODULE in kinds
        assert NodeKind.TF_VARIABLE in kinds
        assert NodeKind.TF_OUTPUT in kinds
        assert NodeKind.TF_PROVIDER in kinds


# endregion: --- Integration: full pipeline
# ---------------------------------------------------------------------------
