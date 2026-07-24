"""Tests for API contract parsing — Feature 19.

Tests the OpenAPI/Swagger parser, the ContractExtractor, and the
graph builder integration for contract-sourced routes and schemas.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import FIXTURES_DIR

CONTRACTS_DIR = FIXTURES_DIR / "contracts"


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _parse_yaml(filename: str) -> tuple[list, list]:
    from ast_intel.extractors._openapi import parse_openapi
    source = (CONTRACTS_DIR / filename).read_bytes()
    return parse_openapi(source, filename)


def _parse_json(filename: str) -> tuple[list, list]:
    from ast_intel.extractors._openapi import parse_openapi
    source = (CONTRACTS_DIR / filename).read_bytes()
    return parse_openapi(source, filename)


def _routes_as_tuples(routes: list) -> set[tuple[str, str, str]]:
    """Convert routes to a set of (method, path, handler)."""
    return {(r.method, r.path, r.handler) for r in routes}


def _struct_names(structs: list) -> set[str]:
    """Extract struct names as a set."""
    return {s.name for s in structs}


def _build_graph_from_contract(source: bytes, filename: str):
    """Build a CodeGraph from a contract file."""
    import ast_intel
    from ast_intel.core.graph_builder import GraphBuilder
    from ast_intel.extractors.contract import ContractExtractor
    from ast_intel.models.workspace_model import (
        CrateModel,
        WorkspaceAST,
        WorkspaceMeta,
    )

    ext = ContractExtractor()
    file_ast = ext.extract(Path(filename), source)
    file_ast.file = filename
    file_ast.module_path = f"contracts::{filename}"

    crate = CrateModel(
        name="contracts",
        language="contract",
        manifest_path=".",
    )
    crate.files.append(file_ast)

    workspace = WorkspaceAST(
        meta=WorkspaceMeta(
            schema_version=ast_intel.SCHEMA_VERSION,
            tool_version=ast_intel.TOOL_VERSION,
            workspace_root="/tmp/test",
        ),
    )
    workspace.crates["contracts"] = crate

    builder = GraphBuilder()
    return builder.build(workspace)


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- OpenAPI 3.x YAML Tests
# ---------------------------------------------------------------------------


class TestOpenAPIv3Yaml:
    """Tests for OpenAPI 3.0 YAML parsing."""

    def test_route_count(self) -> None:
        routes, _ = _parse_yaml("petstore_openapi_v3.yaml")
        assert len(routes) == 6

    def test_route_methods(self) -> None:
        routes, _ = _parse_yaml("petstore_openapi_v3.yaml")
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/pets", "listPets") in tuples
        assert ("POST", "/pets", "createPet") in tuples
        assert ("GET", "/pets/{petId}", "getPetById") in tuples
        assert ("PUT", "/pets/{petId}", "updatePet") in tuples
        assert ("DELETE", "/pets/{petId}", "deletePet") in tuples
        assert ("GET", "/health", "healthCheck") in tuples

    def test_all_framework_openapi(self) -> None:
        routes, _ = _parse_yaml("petstore_openapi_v3.yaml")
        assert all(r.framework == "openapi" for r in routes)

    def test_all_have_no_span(self) -> None:
        """Contract-extracted routes have no source span."""
        routes, _ = _parse_yaml("petstore_openapi_v3.yaml")
        assert all(r.span is None for r in routes)

    def test_schema_count(self) -> None:
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        names = _struct_names(structs)
        assert "Pet" in names
        assert "Owner" in names
        assert "Error" in names
        assert len(structs) == 3

    def test_pet_schema_fields(self) -> None:
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        pet = next(s for s in structs if s.name == "Pet")
        field_names = {f.name for f in pet.fields}
        assert field_names == {"id", "name", "tag", "owner"}

    def test_pet_field_types(self) -> None:
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        pet = next(s for s in structs if s.name == "Pet")
        field_map = {f.name: f.type for f in pet.fields}
        assert field_map["id"] == "integer(int64)"
        assert field_map["name"] == "string"
        assert field_map["owner"] == "Owner"  # $ref resolved to basename

    def test_pet_schema_doc(self) -> None:
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        pet = next(s for s in structs if s.name == "Pet")
        assert pet.doc == "A pet in the store."

    def test_schema_visibility_public(self) -> None:
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        assert all(s.visibility == "pub" for s in structs)


# endregion: --- OpenAPI 3.x YAML Tests


# ---------------------------------------------------------------------------
# region:    --- OpenAPI 3.x JSON Tests
# ---------------------------------------------------------------------------


class TestOpenAPIv3Json:
    """Tests for OpenAPI 3.0 JSON parsing."""

    def test_route_count(self) -> None:
        routes, _ = _parse_json("petstore_openapi_v3.json")
        assert len(routes) == 6

    def test_route_methods(self) -> None:
        routes, _ = _parse_json("petstore_openapi_v3.json")
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/pets", "listPets") in tuples
        assert ("POST", "/pets", "createPet") in tuples
        assert ("GET", "/pets/{petId}", "getPetById") in tuples
        assert ("PUT", "/pets/{petId}", "updatePet") in tuples
        assert ("DELETE", "/pets/{petId}", "deletePet") in tuples
        assert ("GET", "/health", "healthCheck") in tuples

    def test_schema_count(self) -> None:
        _, structs = _parse_json("petstore_openapi_v3.json")
        names = _struct_names(structs)
        assert "Pet" in names
        assert "Error" in names
        assert len(structs) == 2

    def test_pet_schema_fields(self) -> None:
        _, structs = _parse_json("petstore_openapi_v3.json")
        pet = next(s for s in structs if s.name == "Pet")
        field_names = {f.name for f in pet.fields}
        assert field_names == {"id", "name", "tag"}

    def test_pet_field_types(self) -> None:
        _, structs = _parse_json("petstore_openapi_v3.json")
        pet = next(s for s in structs if s.name == "Pet")
        field_map = {f.name: f.type for f in pet.fields}
        assert field_map["id"] == "integer(int64)"
        assert field_map["name"] == "string"


# endregion: --- OpenAPI 3.x JSON Tests


# ---------------------------------------------------------------------------
# region:    --- Swagger 2.0 Tests
# ---------------------------------------------------------------------------


class TestSwaggerV2:
    """Tests for Swagger 2.0 YAML parsing."""

    def test_route_count(self) -> None:
        routes, _ = _parse_yaml("swagger_v2.yaml")
        assert len(routes) == 3

    def test_route_methods(self) -> None:
        routes, _ = _parse_yaml("swagger_v2.yaml")
        tuples = _routes_as_tuples(routes)
        assert ("GET", "/users", "listUsers") in tuples
        assert ("POST", "/users", "createUser") in tuples
        assert ("DELETE", "/users/{userId}", "deleteUser") in tuples

    def test_all_framework_openapi(self) -> None:
        routes, _ = _parse_yaml("swagger_v2.yaml")
        assert all(r.framework == "openapi" for r in routes)

    def test_definition_schema(self) -> None:
        _, structs = _parse_yaml("swagger_v2.yaml")
        assert len(structs) == 1
        user = structs[0]
        assert user.name == "User"
        assert user.doc == "A user account."

    def test_user_fields(self) -> None:
        _, structs = _parse_yaml("swagger_v2.yaml")
        user = structs[0]
        field_names = {f.name for f in user.fields}
        assert field_names == {"id", "username", "email"}


# endregion: --- Swagger 2.0 Tests


# ---------------------------------------------------------------------------
# region:    --- Non-OpenAPI Tests
# ---------------------------------------------------------------------------


class TestNonOpenAPIYaml:
    """Tests that non-OpenAPI YAML files produce empty results."""

    def test_empty_routes(self) -> None:
        routes, structs = _parse_yaml("not_openapi.yaml")
        assert routes == []
        assert structs == []

    def test_invalid_json(self) -> None:
        from ast_intel.extractors._openapi import parse_openapi
        routes, structs = parse_openapi(b"this is not json or yaml {{{{", "bad.json")
        assert routes == []
        assert structs == []

    def test_empty_bytes(self) -> None:
        from ast_intel.extractors._openapi import parse_openapi
        routes, structs = parse_openapi(b"", "empty.yaml")
        assert routes == []
        assert structs == []

    def test_json_array(self) -> None:
        """JSON array (not dict) is not an OpenAPI spec."""
        from ast_intel.extractors._openapi import parse_openapi
        routes, structs = parse_openapi(b'[1, 2, 3]', "array.json")
        assert routes == []
        assert structs == []


# endregion: --- Non-OpenAPI Tests


# ---------------------------------------------------------------------------
# region:    --- Schema Extraction Detail Tests
# ---------------------------------------------------------------------------


class TestOpenAPISchemaExtraction:
    """Detailed tests for schema → StructNode/FieldNode mapping."""

    def test_ref_type_resolved(self) -> None:
        """$ref properties resolve to the schema basename."""
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        pet = next(s for s in structs if s.name == "Pet")
        owner_field = next(f for f in pet.fields if f.name == "owner")
        assert owner_field.type == "Owner"

    def test_array_type(self) -> None:
        """Array items with $ref produce 'array<TypeName>'."""
        from ast_intel.extractors._openapi import parse_openapi
        spec = b"""{
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "List": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    },
                    "Item": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}}
                    }
                }
            }
        }"""
        _, structs = parse_openapi(spec, "test.json")
        list_struct = next(s for s in structs if s.name == "List")
        items_field = next(f for f in list_struct.fields if f.name == "items")
        assert items_field.type == "array<Item>"

    def test_allof_type(self) -> None:
        """allOf resolves to the first concrete type."""
        from ast_intel.extractors._openapi import parse_openapi
        spec = b"""{
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Extended": {
                        "type": "object",
                        "properties": {
                            "base": {
                                "allOf": [
                                    {"$ref": "#/components/schemas/Base"},
                                    {"type": "object"}
                                ]
                            }
                        }
                    },
                    "Base": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}}
                    }
                }
            }
        }"""
        _, structs = parse_openapi(spec, "test.json")
        ext = next(s for s in structs if s.name == "Extended")
        base_field = next(f for f in ext.fields if f.name == "base")
        assert base_field.type == "Base"

    def test_format_type(self) -> None:
        """Integer with format produces 'integer(int64)'."""
        _, structs = _parse_yaml("petstore_openapi_v3.yaml")
        pet = next(s for s in structs if s.name == "Pet")
        id_field = next(f for f in pet.fields if f.name == "id")
        assert id_field.type == "integer(int64)"

    def test_primitive_schemas_skipped(self) -> None:
        """Schemas with type != 'object' and no properties are skipped."""
        from ast_intel.extractors._openapi import parse_openapi
        spec = b"""{
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Id": {"type": "string"},
                    "Count": {"type": "integer"},
                    "Payload": {
                        "type": "object",
                        "properties": {"data": {"type": "string"}}
                    }
                }
            }
        }"""
        _, structs = parse_openapi(spec, "test.json")
        assert len(structs) == 1
        assert structs[0].name == "Payload"


# endregion: --- Schema Extraction Detail Tests


# ---------------------------------------------------------------------------
# region:    --- ContractExtractor Tests
# ---------------------------------------------------------------------------


class TestContractExtractor:
    """Tests for the ContractExtractor class."""

    def test_extract_openapi_yaml(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        source = (CONTRACTS_DIR / "petstore_openapi_v3.yaml").read_bytes()
        ast = ext.extract(Path("openapi.yaml"), source)
        assert len(ast.routes) == 6
        assert len(ast.structs) == 3

    def test_extract_openapi_json(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        source = (CONTRACTS_DIR / "petstore_openapi_v3.json").read_bytes()
        ast = ext.extract(Path("openapi.json"), source)
        assert len(ast.routes) == 6
        assert len(ast.structs) == 2

    def test_extract_non_openapi(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        source = (CONTRACTS_DIR / "not_openapi.yaml").read_bytes()
        ast = ext.extract(Path("not_openapi.yaml"), source)
        assert ast.routes == []
        assert ast.structs == []

    def test_extract_proto(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        source = (CONTRACTS_DIR / "users.proto").read_bytes()
        ast = ext.extract(Path("users.proto"), source)
        assert len(ast.routes) == 3
        assert len(ast.structs) == 5
        assert len(ast.traits) == 1

    def test_extract_graphql(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        source = (CONTRACTS_DIR / "schema.graphql").read_bytes()
        ast = ext.extract(Path("schema.graphql"), source)
        assert len(ast.routes) == 4  # 2 queries + 2 mutations
        assert len(ast.structs) == 3  # User, Post, CreateUserInput

    def test_language_id(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        assert ContractExtractor.language_id == "contract"

    def test_file_extensions(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        exts = ContractExtractor.file_extensions
        assert ".yaml" in exts
        assert ".yml" in exts
        assert ".json" in exts
        assert ".proto" in exts
        assert ".graphql" in exts
        assert ".gql" in exts

    def test_parse_manifest(self) -> None:
        from ast_intel.extractors.contract import ContractExtractor
        ext = ContractExtractor()
        crate = ext.parse_manifest(Path("openapi.yaml"))
        assert crate.name == "contracts"
        assert crate.language == "contract"


# endregion: --- ContractExtractor Tests


# ---------------------------------------------------------------------------
# region:    --- Graph Builder Integration Tests
# ---------------------------------------------------------------------------


class TestContractGraphIntegration:
    """Tests that contract-sourced routes and structs appear in the graph."""

    def test_route_nodes_from_contract(self) -> None:
        source = (CONTRACTS_DIR / "petstore_openapi_v3.json").read_bytes()
        graph = _build_graph_from_contract(source, "openapi.json")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert len(route_nodes) == 6
        labels = {n.label for n in route_nodes}
        assert "GET /pets" in labels
        assert "POST /pets" in labels
        assert "DELETE /pets/{petId}" in labels

    def test_route_properties(self) -> None:
        source = (CONTRACTS_DIR / "petstore_openapi_v3.json").read_bytes()
        graph = _build_graph_from_contract(source, "openapi.json")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        get_pets = next(n for n in route_nodes if n.label == "GET /pets")
        assert get_pets.properties["method"] == "GET"
        assert get_pets.properties["path"] == "/pets"
        assert get_pets.properties["framework"] == "openapi"

    def test_struct_nodes_from_contract(self) -> None:
        source = (CONTRACTS_DIR / "petstore_openapi_v3.json").read_bytes()
        graph = _build_graph_from_contract(source, "openapi.json")
        struct_nodes = [n for n in graph.nodes if n.kind == "struct"]
        names = {n.label for n in struct_nodes}
        assert "Pet" in names
        assert "Error" in names

    def test_exposes_edges_exist(self) -> None:
        source = (CONTRACTS_DIR / "petstore_openapi_v3.json").read_bytes()
        graph = _build_graph_from_contract(source, "openapi.json")
        exposes = [e for e in graph.edges if e.relation == "exposes"]
        assert len(exposes) >= 6

    def test_no_routes_no_route_nodes(self) -> None:
        spec = b'{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}'
        source = spec
        graph = _build_graph_from_contract(source, "empty.json")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert route_nodes == []

    def test_non_openapi_no_nodes(self) -> None:
        source = (CONTRACTS_DIR / "not_openapi.yaml").read_bytes()
        graph = _build_graph_from_contract(source, "not_openapi.yaml")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        struct_nodes = [n for n in graph.nodes if n.kind == "struct"]
        assert route_nodes == []
        assert struct_nodes == []


# endregion: --- Graph Builder Integration Tests


# ---------------------------------------------------------------------------
# region:    --- Protobuf Parser Tests
# ---------------------------------------------------------------------------


def _parse_proto(filename: str) -> tuple[list, list, list]:
    from ast_intel.extractors._proto import parse_proto
    source = (CONTRACTS_DIR / filename).read_bytes()
    return parse_proto(source, filename)


class TestProtoParserUsers:
    """Tests for the users.proto fixture (single service + package)."""

    def test_rpc_count(self) -> None:
        routes, _structs, _traits = _parse_proto("users.proto")
        assert len(routes) == 3

    def test_rpc_paths_include_package(self) -> None:
        routes, _structs, _traits = _parse_proto("users.proto")
        paths = {r.path for r in routes}
        assert "/users.UserService/GetUser" in paths
        assert "/users.UserService/ListUsers" in paths
        assert "/users.UserService/CreateUser" in paths

    def test_rpc_method_is_grpc(self) -> None:
        routes, _structs, _traits = _parse_proto("users.proto")
        assert all(r.method == "GRPC" for r in routes)

    def test_rpc_framework_is_grpc(self) -> None:
        routes, _structs, _traits = _parse_proto("users.proto")
        assert all(r.framework == "grpc" for r in routes)

    def test_rpc_handler_names(self) -> None:
        routes, _structs, _traits = _parse_proto("users.proto")
        handlers = {r.handler for r in routes}
        assert handlers == {"GetUser", "ListUsers", "CreateUser"}

    def test_message_count(self) -> None:
        _routes, structs, _traits = _parse_proto("users.proto")
        assert len(structs) == 5

    def test_message_names(self) -> None:
        _routes, structs, _traits = _parse_proto("users.proto")
        names = {s.name for s in structs}
        assert names == {
            "GetUserRequest",
            "ListUsersRequest",
            "ListUsersResponse",
            "CreateUserRequest",
            "User",
        }

    def test_user_message_fields(self) -> None:
        _routes, structs, _traits = _parse_proto("users.proto")
        user = next(s for s in structs if s.name == "User")
        field_names = {f.name for f in user.fields}
        assert field_names == {"id", "name", "email"}

    def test_repeated_field_type(self) -> None:
        _routes, structs, _traits = _parse_proto("users.proto")
        resp = next(s for s in structs if s.name == "ListUsersResponse")
        users_field = next(f for f in resp.fields if f.name == "users")
        assert users_field.type == "repeated User"

    def test_service_trait(self) -> None:
        _routes, _structs, traits = _parse_proto("users.proto")
        assert len(traits) == 1
        assert traits[0].name == "UserService"

    def test_service_trait_items(self) -> None:
        _routes, _structs, traits = _parse_proto("users.proto")
        trait = traits[0]
        item_names = {it.name for it in trait.items}
        assert item_names == {"GetUser", "ListUsers", "CreateUser"}

    def test_service_trait_return_types(self) -> None:
        _routes, _structs, traits = _parse_proto("users.proto")
        trait = traits[0]
        returns = {it.name: it.return_type for it in trait.items}
        assert returns["GetUser"] == "User"
        assert returns["ListUsers"] == "ListUsersResponse"
        assert returns["CreateUser"] == "User"


class TestProtoParserMultiService:
    """Tests for multi_service.proto (multiple services, no package)."""

    def test_rpc_count(self) -> None:
        routes, _structs, _traits = _parse_proto("multi_service.proto")
        assert len(routes) == 3  # 2 OrderService + 1 PaymentService

    def test_no_package_paths(self) -> None:
        routes, _structs, _traits = _parse_proto("multi_service.proto")
        paths = {r.path for r in routes}
        assert "/OrderService/PlaceOrder" in paths
        assert "/OrderService/GetOrder" in paths
        assert "/PaymentService/ProcessPayment" in paths

    def test_message_count(self) -> None:
        _routes, structs, _traits = _parse_proto("multi_service.proto")
        assert len(structs) == 5

    def test_two_service_traits(self) -> None:
        _routes, _structs, traits = _parse_proto("multi_service.proto")
        assert len(traits) == 2
        names = {t.name for t in traits}
        assert names == {"OrderService", "PaymentService"}

    def test_order_fields(self) -> None:
        _routes, structs, _traits = _parse_proto("multi_service.proto")
        order = next(s for s in structs if s.name == "Order")
        field_names = {f.name for f in order.fields}
        assert field_names == {"id", "product_id", "quantity", "status"}


class TestProtoEdgeCases:
    """Edge cases for the proto parser."""

    def test_empty_file(self) -> None:
        from ast_intel.extractors._proto import parse_proto
        routes, structs, traits = parse_proto(b"", "empty.proto")
        assert routes == []
        assert structs == []
        assert traits == []

    def test_comments_stripped(self) -> None:
        from ast_intel.extractors._proto import parse_proto
        source = b"""
            syntax = "proto3";
            package test;
            // service Commented { rpc Foo (Bar) returns (Baz); }
            service Real {
              rpc Hello (Req) returns (Resp);
            }
            message Req { string name = 1; }
            message Resp { string greeting = 1; }
        """
        routes, _structs, traits = parse_proto(source, "comment.proto")
        assert len(routes) == 1
        assert routes[0].handler == "Hello"
        assert len(traits) == 1
        assert traits[0].name == "Real"

    def test_message_only_no_service(self) -> None:
        from ast_intel.extractors._proto import parse_proto
        source = b"""
            syntax = "proto3";
            message Standalone { string value = 1; }
        """
        routes, structs, traits = parse_proto(source, "msg.proto")
        assert routes == []
        assert traits == []
        assert len(structs) == 1
        assert structs[0].name == "Standalone"


# endregion: --- Protobuf Parser Tests


# ---------------------------------------------------------------------------
# region:    --- GraphQL Parser Tests
# ---------------------------------------------------------------------------


def _parse_gql(filename: str) -> tuple[list, list]:
    from ast_intel.extractors._graphql import parse_graphql
    source = (CONTRACTS_DIR / filename).read_bytes()
    return parse_graphql(source, filename)


class TestGraphQLParserSchema:
    """Tests for the schema.graphql fixture."""

    def test_query_routes(self) -> None:
        routes, _structs = _parse_gql("schema.graphql")
        query_routes = [r for r in routes if r.method == "QUERY"]
        assert len(query_routes) == 2
        names = {r.path for r in query_routes}
        assert names == {"users", "user"}

    def test_mutation_routes(self) -> None:
        routes, _structs = _parse_gql("schema.graphql")
        mutation_routes = [r for r in routes if r.method == "MUTATION"]
        assert len(mutation_routes) == 2
        names = {r.path for r in mutation_routes}
        assert names == {"createUser", "deleteUser"}

    def test_framework_is_graphql(self) -> None:
        routes, _structs = _parse_gql("schema.graphql")
        assert all(r.framework == "graphql" for r in routes)

    def test_handler_equals_field_name(self) -> None:
        routes, _structs = _parse_gql("schema.graphql")
        for r in routes:
            assert r.handler == r.path

    def test_struct_count(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        assert len(structs) == 3  # User, Post, CreateUserInput

    def test_struct_names(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        names = {s.name for s in structs}
        assert names == {"User", "Post", "CreateUserInput"}

    def test_user_fields(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        user = next(s for s in structs if s.name == "User")
        field_names = {f.name for f in user.fields}
        assert field_names == {"id", "name", "email", "posts"}

    def test_array_type_normalized(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        user = next(s for s in structs if s.name == "User")
        posts_field = next(f for f in user.fields if f.name == "posts")
        assert posts_field.type == "array<Post>"

    def test_scalar_type_cleaned(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        user = next(s for s in structs if s.name == "User")
        name_field = next(f for f in user.fields if f.name == "name")
        assert name_field.type == "String"

    def test_input_type_as_struct(self) -> None:
        _routes, structs = _parse_gql("schema.graphql")
        create_input = next(s for s in structs if s.name == "CreateUserInput")
        field_names = {f.name for f in create_input.fields}
        assert field_names == {"name", "email"}


class TestGraphQLParserOrders:
    """Tests for orders.graphql (with subscriptions)."""

    def test_subscription_routes(self) -> None:
        routes, _structs = _parse_gql("orders.graphql")
        subs = [r for r in routes if r.method == "SUBSCRIPTION"]
        assert len(subs) == 1
        assert subs[0].path == "orderStatusChanged"

    def test_all_route_count(self) -> None:
        routes, _structs = _parse_gql("orders.graphql")
        assert len(routes) == 4  # 2 queries + 1 mutation + 1 subscription

    def test_struct_count(self) -> None:
        _routes, structs = _parse_gql("orders.graphql")
        names = {s.name for s in structs}
        assert names == {"Order", "OrderItem", "PlaceOrderInput", "OrderItemInput"}

    def test_order_item_fields(self) -> None:
        _routes, structs = _parse_gql("orders.graphql")
        item = next(s for s in structs if s.name == "OrderItem")
        field_names = {f.name for f in item.fields}
        assert field_names == {"productId", "quantity", "price"}


class TestGraphQLEdgeCases:
    """Edge cases for the GraphQL parser."""

    def test_empty_file(self) -> None:
        from ast_intel.extractors._graphql import parse_graphql
        routes, structs = parse_graphql(b"", "empty.graphql")
        assert routes == []
        assert structs == []

    def test_comments_stripped(self) -> None:
        from ast_intel.extractors._graphql import parse_graphql
        source = b"""
            # This is a comment
            type Query {
              hello: String!
            }
            # type Mutation { ... }
        """
        routes, _structs = parse_graphql(source, "comment.graphql")
        assert len(routes) == 1
        assert routes[0].path == "hello"

    def test_type_only_no_operations(self) -> None:
        from ast_intel.extractors._graphql import parse_graphql
        source = b"""
            type Widget {
              id: ID!
              label: String!
            }
        """
        routes, structs = parse_graphql(source, "widget.graphql")
        assert routes == []
        assert len(structs) == 1
        assert structs[0].name == "Widget"

    def test_interface_as_struct(self) -> None:
        from ast_intel.extractors._graphql import parse_graphql
        source = b"""
            interface Node {
              id: ID!
            }
            type User implements Node {
              id: ID!
              name: String!
            }
        """
        _routes, structs = parse_graphql(source, "iface.graphql")
        assert len(structs) == 2
        names = {s.name for s in structs}
        assert "Node" in names
        assert "User" in names


# endregion: --- GraphQL Parser Tests


# ---------------------------------------------------------------------------
# region:    --- Proto Graph Integration Tests
# ---------------------------------------------------------------------------


def _build_graph_from_proto(source: bytes, filename: str):
    """Build a CodeGraph from a proto file."""
    import ast_intel
    from ast_intel.core.graph_builder import GraphBuilder
    from ast_intel.extractors.contract import ContractExtractor
    from ast_intel.models.workspace_model import (
        CrateModel,
        WorkspaceAST,
        WorkspaceMeta,
    )

    ext = ContractExtractor()
    file_ast = ext.extract(Path(filename), source)
    file_ast.file = filename
    file_ast.module_path = f"contracts::{filename}"

    crate = CrateModel(
        name="contracts",
        language="contract",
        manifest_path=".",
    )
    crate.files.append(file_ast)

    workspace = WorkspaceAST(
        meta=WorkspaceMeta(
            schema_version=ast_intel.SCHEMA_VERSION,
            tool_version=ast_intel.TOOL_VERSION,
            workspace_root="/tmp/test",
        ),
    )
    workspace.crates["contracts"] = crate

    builder = GraphBuilder()
    return builder.build(workspace)


class TestProtoGraphIntegration:
    """Tests that proto-sourced nodes appear correctly in the graph."""

    def test_route_nodes_from_proto(self) -> None:
        source = (CONTRACTS_DIR / "users.proto").read_bytes()
        graph = _build_graph_from_proto(source, "users.proto")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert len(route_nodes) == 3
        labels = {n.label for n in route_nodes}
        assert "GRPC /users.UserService/GetUser" in labels

    def test_route_properties_grpc(self) -> None:
        source = (CONTRACTS_DIR / "users.proto").read_bytes()
        graph = _build_graph_from_proto(source, "users.proto")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        for rn in route_nodes:
            assert rn.properties["method"] == "GRPC"
            assert rn.properties["framework"] == "grpc"

    def test_struct_nodes_from_proto(self) -> None:
        source = (CONTRACTS_DIR / "users.proto").read_bytes()
        graph = _build_graph_from_proto(source, "users.proto")
        struct_nodes = [n for n in graph.nodes if n.kind == "struct"]
        names = {n.label for n in struct_nodes}
        assert "User" in names
        assert "GetUserRequest" in names

    def test_trait_nodes_from_proto(self) -> None:
        source = (CONTRACTS_DIR / "users.proto").read_bytes()
        graph = _build_graph_from_proto(source, "users.proto")
        trait_nodes = [n for n in graph.nodes if n.kind == "trait"]
        assert len(trait_nodes) == 1
        assert trait_nodes[0].label == "UserService"


# endregion: --- Proto Graph Integration Tests


# ---------------------------------------------------------------------------
# region:    --- GraphQL Graph Integration Tests
# ---------------------------------------------------------------------------


def _build_graph_from_gql(source: bytes, filename: str):
    """Build a CodeGraph from a GraphQL file."""
    import ast_intel
    from ast_intel.core.graph_builder import GraphBuilder
    from ast_intel.extractors.contract import ContractExtractor
    from ast_intel.models.workspace_model import (
        CrateModel,
        WorkspaceAST,
        WorkspaceMeta,
    )

    ext = ContractExtractor()
    file_ast = ext.extract(Path(filename), source)
    file_ast.file = filename
    file_ast.module_path = f"contracts::{filename}"

    crate = CrateModel(
        name="contracts",
        language="contract",
        manifest_path=".",
    )
    crate.files.append(file_ast)

    workspace = WorkspaceAST(
        meta=WorkspaceMeta(
            schema_version=ast_intel.SCHEMA_VERSION,
            tool_version=ast_intel.TOOL_VERSION,
            workspace_root="/tmp/test",
        ),
    )
    workspace.crates["contracts"] = crate

    builder = GraphBuilder()
    return builder.build(workspace)


class TestGraphQLGraphIntegration:
    """Tests that GraphQL-sourced nodes appear correctly in the graph."""

    def test_route_nodes_from_graphql(self) -> None:
        source = (CONTRACTS_DIR / "schema.graphql").read_bytes()
        graph = _build_graph_from_gql(source, "schema.graphql")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        assert len(route_nodes) == 4
        labels = {n.label for n in route_nodes}
        assert "QUERY users" in labels
        assert "MUTATION createUser" in labels

    def test_route_properties_graphql(self) -> None:
        source = (CONTRACTS_DIR / "schema.graphql").read_bytes()
        graph = _build_graph_from_gql(source, "schema.graphql")
        route_nodes = [n for n in graph.nodes if n.kind == "route"]
        for rn in route_nodes:
            assert rn.properties["framework"] == "graphql"

    def test_struct_nodes_from_graphql(self) -> None:
        source = (CONTRACTS_DIR / "schema.graphql").read_bytes()
        graph = _build_graph_from_gql(source, "schema.graphql")
        struct_nodes = [n for n in graph.nodes if n.kind == "struct"]
        names = {n.label for n in struct_nodes}
        assert "User" in names
        assert "Post" in names
        assert "CreateUserInput" in names


# endregion: --- GraphQL Graph Integration Tests
