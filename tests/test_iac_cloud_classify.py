"""Tests for Phase 3: TF_RESOURCE classification + Bicep/ARM extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.core.iac_graph_builder import IaCGraphBuilder
from ast_intel.extractors.iac.bicep import BicepExtractor
from ast_intel.models.graph_model import EdgeRelation, NodeKind
from ast_intel.models.iac_model import IaCContext, IaCGraph, IaCResource

# ---------------------------------------------------------------------------
# region:    --- TF classify pass
# ---------------------------------------------------------------------------


class TestTerraformClassify:
    """The IaCGraphBuilder promotes TF_RESOURCE → CLOUD_RESOURCE via taxonomy."""

    def _build(self, resources: list[IaCResource]) -> list:
        graph = IaCGraph(file="main.tf", resources=resources)
        builder = IaCGraphBuilder()
        return builder.build([graph])

    def test_sqs_queue_classified(self) -> None:
        res = IaCResource(
            kind="TfResource", name="order-queue",
            file="main.tf", properties={"resource_type": "aws_sqs_queue"},
        )
        g = self._build([res])
        cr = [n for n in g.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr) == 1
        assert cr[0].properties["category"] == "queue"
        assert cr[0].properties["provider"] == "aws"
        assert cr[0].properties["service"] == "SQS"
        assert cr[0].properties["source"] == "iac"
        # PROVISIONS edge exists
        prov = [e for e in g.edges if e.relation == EdgeRelation.PROVISIONS]
        assert len(prov) == 1

    def test_cosmosdb_classified(self) -> None:
        res = IaCResource(
            kind="TfResource", name="main-db",
            file="infra.tf", properties={"resource_type": "azurerm_cosmosdb_account"},
        )
        g = self._build([res])
        cr = [n for n in g.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr) == 1
        assert cr[0].properties["category"] == "database"
        assert cr[0].properties["provider"] == "azure"

    def test_unrecognized_tf_not_promoted(self) -> None:
        res = IaCResource(
            kind="TfResource", name="vm1",
            file="main.tf", properties={"resource_type": "aws_instance"},
        )
        g = self._build([res])
        cr = [n for n in g.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr) == 0

    def test_multiple_resources_classified(self) -> None:
        resources = [
            IaCResource(
                kind="TfResource", name="cache",
                file="main.tf", properties={"resource_type": "azurerm_redis_cache"},
            ),
            IaCResource(
                kind="TfResource", name="vault",
                file="main.tf", properties={"resource_type": "azurerm_key_vault"},
            ),
            IaCResource(
                kind="TfResource", name="vm",
                file="main.tf", properties={"resource_type": "azurerm_linux_virtual_machine"},
            ),
        ]
        g = self._build(resources)
        cr = [n for n in g.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr) == 2  # only cache + vault, not vm
        cats = {n.properties["category"] for n in cr}
        assert cats == {"cache", "secret"}


# endregion


# ---------------------------------------------------------------------------
# region:    --- Bicep extractor
# ---------------------------------------------------------------------------


class TestBicepExtractor:
    @pytest.fixture
    def extractor(self) -> BicepExtractor:
        return BicepExtractor()

    def test_can_handle_bicep(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        f = tmp_path / "main.bicep"
        f.write_text("")
        assert extractor.can_handle(f, b"resource foo 'Microsoft.Cache/redis")

    def test_cannot_handle_random_json(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text("{}")
        assert not extractor.can_handle(f, b'{"key": "value"}')

    def test_can_handle_arm_json(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        f = tmp_path / "azuredeploy.json"
        f.write_text("")
        peek = b'{"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json"}'
        assert extractor.can_handle(f, peek)

    def test_extract_bicep_redis(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        code = "resource redisCache 'Microsoft.Cache/redis@2023-08-01' = {\n  name: 'myRedis'\n}"
        f = tmp_path / "main.bicep"
        f.write_bytes(code.encode())
        ctx = IaCContext(workspace_root=str(tmp_path), rel_path="main.bicep")
        result = extractor.extract(f, code.encode(), ctx)
        assert len(result.resources) == 1
        r = result.resources[0]
        assert r.name == "redisCache"
        assert r.properties["category"] == "cache"
        assert r.properties["provider"] == "azure"

    def test_extract_bicep_multiple(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        code = (
            "resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' = {}\n"
            "resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {}\n"
            "resource vm 'Microsoft.Compute/virtualMachines@2023-03-01' = {}\n"
        )
        f = tmp_path / "infra.bicep"
        f.write_bytes(code.encode())
        ctx = IaCContext(workspace_root=str(tmp_path), rel_path="infra.bicep")
        result = extractor.extract(f, code.encode(), ctx)
        # Only cosmosDb (database) + kv (secret) — vm is unrecognized
        assert len(result.resources) == 2
        cats = {r.properties["category"] for r in result.resources}
        assert cats == {"database", "secret"}

    def test_extract_arm_json(self, extractor: BicepExtractor, tmp_path: Path) -> None:
        import json

        arm = {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "resources": [
                {"type": "Microsoft.Storage/storageAccounts", "name": "mystg"},
                {"type": "Microsoft.ServiceBus/namespaces/queues", "name": "myqueue"},
                {"type": "Microsoft.Network/virtualNetworks", "name": "vnet"},
            ],
        }
        f = tmp_path / "azuredeploy.json"
        f.write_text(json.dumps(arm))
        ctx = IaCContext(workspace_root=str(tmp_path), rel_path="azuredeploy.json")
        result = extractor.extract(f, f.read_bytes(), ctx)
        # Only storage + queue — vnet is unrecognized
        assert len(result.resources) == 2
        cats = {r.properties["category"] for r in result.resources}
        assert cats == {"storage", "queue"}


# endregion


# ---------------------------------------------------------------------------
# region:    --- End-to-end through graph builder
# ---------------------------------------------------------------------------


class TestBicepGraphEmission:
    """Bicep resources emit as CLOUD_RESOURCE via the IaCGraphBuilder."""

    def test_bicep_resources_become_cloud_resource_nodes(self) -> None:
        from ast_intel.core.iac_graph_builder import IaCGraphBuilder

        graph = IaCGraph(
            file="main.bicep",
            resources=[
                IaCResource(
                    kind="CloudResource", name="redisCache",
                    file="main.bicep",
                    properties={
                        "resource_type": "Microsoft.Cache/redis",
                        "provider": "azure",
                        "service": "Redis",
                        "category": "cache",
                        "source": "iac",
                    },
                ),
            ],
        )
        builder = IaCGraphBuilder()
        g = builder.build([graph])
        cr = [n for n in g.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr) == 1
        assert cr[0].label == "redisCache"
        assert cr[0].properties["category"] == "cache"


# endregion


# ---------------------------------------------------------------------------
# region:    --- Phase 4: BACKED_BY resolution
# ---------------------------------------------------------------------------


class TestCloudCodeToIacResolver:
    """Resolver links code-detected → IaC-provisioned CLOUD_RESOURCE via BACKED_BY."""

    def test_backed_by_same_category(self) -> None:
        from ast_intel.core._iac_resolver import IaCResolver
        from ast_intel.models.graph_model import CodeGraph, GraphNode

        # code_graph has a code-detected Key Vault resource
        code_node = GraphNode(
            id="src/app.cs::cloud:azure:secret:SecretClient:Init",
            label="Key Vault (SecretClient)",
            kind=NodeKind.CLOUD_RESOURCE,
            file="src/app.cs",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "code",
                "client": "SecretClient", "caller": "Init",
            },
        )
        code_graph = CodeGraph(nodes=[code_node])

        # iac_graph has a Bicep-provisioned Key Vault
        iac_node = GraphNode(
            id="cloud://main.bicep::secret/kv",
            label="kv",
            kind=NodeKind.CLOUD_RESOURCE,
            file="main.bicep",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "iac",
            },
        )
        iac_graph = CodeGraph(nodes=[iac_node])

        resolver = IaCResolver()
        edges = resolver._resolve_cloud_code_to_iac(code_graph, iac_graph)
        assert len(edges) == 1
        assert edges[0].relation == EdgeRelation.BACKED_BY
        assert edges[0].source == code_node.id
        assert edges[0].target == iac_node.id

    def test_no_match_different_category(self) -> None:
        from ast_intel.core._iac_resolver import IaCResolver
        from ast_intel.models.graph_model import CodeGraph, GraphNode

        code_node = GraphNode(
            id="x::cloud:azure:cache:Redis:f",
            label="Redis (RedisClient)", kind=NodeKind.CLOUD_RESOURCE,
            file="x.cs",
            properties={
                "provider": "azure", "service": "Redis",
                "category": "cache", "source": "code",
            },
        )
        iac_node = GraphNode(
            id="cloud://main.bicep::secret/kv",
            label="kv", kind=NodeKind.CLOUD_RESOURCE,
            file="main.bicep",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "iac",
            },
        )
        resolver = IaCResolver()
        edges = resolver._resolve_cloud_code_to_iac(
            CodeGraph(nodes=[code_node]), CodeGraph(nodes=[iac_node]),
        )
        assert len(edges) == 0

    def test_multiple_iac_same_category(self) -> None:
        from ast_intel.core._iac_resolver import IaCResolver
        from ast_intel.models.graph_model import CodeGraph, GraphNode

        code_node = GraphNode(
            id="x::cloud:azure:secret:SecretClient:f",
            label="KV (SecretClient)", kind=NodeKind.CLOUD_RESOURCE,
            file="x.cs",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "code",
            },
        )
        iac1 = GraphNode(
            id="cloud://a.bicep::secret/kv1",
            label="kv1", kind=NodeKind.CLOUD_RESOURCE, file="a.bicep",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "iac",
            },
        )
        iac2 = GraphNode(
            id="cloud://b.bicep::secret/kv2",
            label="kv2", kind=NodeKind.CLOUD_RESOURCE, file="b.bicep",
            properties={
                "provider": "azure", "service": "Key Vault",
                "category": "secret", "source": "iac",
            },
        )
        resolver = IaCResolver()
        edges = resolver._resolve_cloud_code_to_iac(
            CodeGraph(nodes=[code_node]), CodeGraph(nodes=[iac1, iac2]),
        )
        # Links to both IaC resources
        assert len(edges) == 2
        targets = {e.target for e in edges}
        assert iac1.id in targets
        assert iac2.id in targets


# endregion
