"""Tests for the cloud SDK client detectors.

Covers C#, Python, Java, and TypeScript/Rust cloud client detection
using tree-sitter parsing on synthetic source snippets.
"""

from __future__ import annotations

import pytest

from ast_intel.models.ast_node import CloudResourceNode

# ---------------------------------------------------------------------------
# region:    --- C# detector
# ---------------------------------------------------------------------------


class TestCSharpDetector:
    @pytest.fixture
    def parse_fn(self):
        import tree_sitter_c_sharp as ts_cs
        from tree_sitter import Language, Parser

        from ast_intel.extractors._cloud_clients import detect_cloud_clients_csharp

        lang = Language(ts_cs.language())
        p = Parser(lang)

        def parse(code: str) -> list[CloudResourceNode]:
            tree = p.parse(code.encode())
            return detect_cloud_clients_csharp(tree.root_node, code.encode())

        return parse

    def test_new_secret_client(self, parse_fn) -> None:
        code = 'class Svc { void Init() { var c = new SecretClient(new Uri("x")); } }'
        results = parse_fn(code)
        assert len(results) >= 1
        r = results[0]
        assert r.provider == "azure"
        assert r.category == "secret"
        assert r.client == "SecretClient"
        assert r.caller == "Init"

    def test_new_cosmos_client(self, parse_fn) -> None:
        code = 'void Setup() { var db = new CosmosClient("AccountEndpoint=..."); }'
        results = parse_fn(code)
        assert any(r.client == "CosmosClient" and r.category == "database" for r in results)

    def test_di_add_db_context(self, parse_fn) -> None:
        code = 'void Configure() { services.AddDbContext<AppDb>(o => o.UseSqlServer(cs)); }'
        results = parse_fn(code)
        assert any(r.client == "AddDbContext" and r.category == "database" for r in results)

    def test_unrecognized_class_ignored(self, parse_fn) -> None:
        code = 'void X() { var h = new HttpClient(); }'
        results = parse_fn(code)
        assert len(results) == 0


# endregion


# ---------------------------------------------------------------------------
# region:    --- Python detector
# ---------------------------------------------------------------------------


class TestPythonDetector:
    @pytest.fixture
    def parse_fn(self):
        import tree_sitter_python as ts_py
        from tree_sitter import Language, Parser

        from ast_intel.extractors._cloud_clients import detect_cloud_clients_python

        lang = Language(ts_py.language())
        p = Parser(lang)

        def parse(code: str) -> list[CloudResourceNode]:
            tree = p.parse(code.encode())
            return detect_cloud_clients_python(tree.root_node, code.encode())

        return parse

    def test_boto3_client_sqs(self, parse_fn) -> None:
        code = "def send():\n    client = boto3.client('sqs')\n"
        results = parse_fn(code)
        assert len(results) >= 1
        r = results[0]
        assert r.provider == "aws"
        assert r.category == "queue"
        assert "sqs" in r.client
        assert r.caller == "send"

    def test_boto3_client_dynamodb(self, parse_fn) -> None:
        code = "def read_table():\n    db = boto3.resource('dynamodb')\n"
        results = parse_fn(code)
        assert any(r.category == "database" and r.provider == "aws" for r in results)

    def test_redis_redis(self, parse_fn) -> None:
        code = "def cache():\n    r = redis.Redis('localhost')\n"
        results = parse_fn(code)
        assert any(r.category == "cache" and r.client == "redis.Redis" for r in results)

    def test_psycopg2_connect(self, parse_fn) -> None:
        code = "def get_conn():\n    c = psycopg2.connect('host=db')\n"
        results = parse_fn(code)
        assert any(r.category == "database" and "psycopg2" in r.client for r in results)

    def test_unrecognized_call_ignored(self, parse_fn) -> None:
        code = "def x():\n    y = json.loads(data)\n"
        results = parse_fn(code)
        assert len(results) == 0


# endregion


# ---------------------------------------------------------------------------
# region:    --- TypeScript / Rust detector
# ---------------------------------------------------------------------------


class TestTypeScriptDetector:
    @pytest.fixture
    def parse_fn(self):
        import tree_sitter_typescript as ts_ts
        from tree_sitter import Language, Parser

        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_typescript,
        )

        lang = Language(ts_ts.language_typescript())
        p = Parser(lang)

        def parse(code: str) -> list[CloudResourceNode]:
            tree = p.parse(code.encode())
            return detect_cloud_clients_typescript(tree.root_node, code.encode())

        return parse

    def test_new_cosmos_client(self, parse_fn) -> None:
        code = 'function init() { const c = new CosmosClient("endpoint"); }'
        results = parse_fn(code)
        assert any(r.client == "CosmosClient" and r.category == "database" for r in results)

    def test_new_s3_client(self, parse_fn) -> None:
        code = "function upload() { const s3 = new S3Client({ region: 'us-east-1' }); }"
        results = parse_fn(code)
        assert any(r.client == "S3Client" and r.category == "storage" for r in results)

    def test_unrecognized_constructor_ignored(self, parse_fn) -> None:
        code = 'function x() { const a = new Array(); }'
        results = parse_fn(code)
        assert len(results) == 0


class TestRustDetector:
    @pytest.fixture
    def parse_fn(self):
        import tree_sitter_rust as ts_rust
        from tree_sitter import Language, Parser

        from ast_intel.extractors._cloud_clients import (
            detect_cloud_clients_typescript,
        )

        lang = Language(ts_rust.language())
        p = Parser(lang)

        def parse(code: str) -> list[CloudResourceNode]:
            tree = p.parse(code.encode())
            return detect_cloud_clients_typescript(tree.root_node, code.encode())

        return parse

    def test_redis_client_new(self, parse_fn) -> None:
        code = 'fn setup() { let r = RedisClient::new("redis://host"); }'
        results = parse_fn(code)
        assert len(results) >= 1
        r = results[0]
        assert r.provider == "generic"
        assert r.category == "cache"
        assert r.client == "RedisClient"
        assert r.caller == "setup"
        assert r.name == "redis://host"

    def test_mongo_client(self, parse_fn) -> None:
        code = 'fn db() { let m = MongoClient::connect("mongodb://host/db"); }'
        results = parse_fn(code)
        assert any(r.client == "MongoClient" and r.category == "database" for r in results)


# endregion


# ---------------------------------------------------------------------------
# region:    --- Graph emission
# ---------------------------------------------------------------------------


class TestGraphEmission:
    """Integration: emit CLOUD_RESOURCE nodes + USES_RESOURCE edges via GraphBuilder."""

    def test_cloud_resource_node_emitted(self, tmp_path) -> None:
        from ast_intel.core.graph_builder import GraphBuilder
        from ast_intel.models.ast_node import CloudResourceNode, FileAST, Span
        from ast_intel.models.graph_model import EdgeRelation, NodeKind
        from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

        f = FileAST(
            file="src/app.cs",
            module_path="api",
            cloud_resources=[
                CloudResourceNode(
                    provider="azure",
                    service="Key Vault",
                    category="secret",
                    client="SecretClient",
                    caller="GetSecret",
                    name="my-vault",
                    span=Span(start_line=10, start_col=4, end_line=10, end_col=40),
                ),
            ],
        )
        crate = CrateModel(name="api", language="csharp", files=[f])
        ws = WorkspaceAST(crates={"api": crate})

        builder = GraphBuilder()
        graph = builder.build(ws)

        cr_nodes = [n for n in graph.nodes if n.kind == NodeKind.CLOUD_RESOURCE]
        assert len(cr_nodes) == 1
        n = cr_nodes[0]
        assert n.properties["provider"] == "azure"
        assert n.properties["category"] == "secret"
        assert n.properties["client"] == "SecretClient"
        assert n.properties["caller"] == "GetSecret"
        assert n.properties["source"] == "code"

        ur = [e for e in graph.edges if e.relation == EdgeRelation.USES_RESOURCE]
        assert len(ur) == 1
        assert ur[0].target == n.id


# endregion
