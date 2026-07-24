"""Tests for the Mermaid architecture-diagram builders."""

from __future__ import annotations

from ast_intel.formatters._arch_mermaid import (
    class_diagram,
    cross_service_sequence,
    deployment_topology,
    detail_flowchart,
    service_map_mermaid,
)
from ast_intel.formatters._service_graph import (
    ArchModel,
    Controller,
    DeployEdge,
    DeployNode,
    Flow,
    FlowEdge,
    FlowNode,
    Interaction,
    Module,
    ModuleEdge,
    RouteInfo,
)

# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _model(**kw: object) -> ArchModel:
    base: dict[str, object] = {
        "repo_name": "r",
        "mode": "modules",
        "modules": (),
        "module_edges": (),
        "routes_by_module": {},
        "controllers_by_module": {},
        "outbound_by_module": {},
        "interactions": (),
        "deploy_nodes": (),
        "deploy_edges": (),
        "stats": {},
    }
    base.update(kw)
    return ArchModel(**base)  # type: ignore[arg-type]


def _mod(label: str, *, test: bool = False, routes: int = 0, syms: int = 5) -> Module:
    return Module(
        id=label, label=label, language="csharp", kind="project",
        is_test=test, file_count=1, symbol_count=syms, route_count=routes,
    )


def _flowchart_balanced(mmd: str) -> bool:
    sg = mmd.count("subgraph ")
    en = len([ln for ln in mmd.splitlines() if ln.strip() == "end"])
    return sg == en


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Service map
# ---------------------------------------------------------------------------


class TestServiceMap:
    def test_header_and_modules(self) -> None:
        m = _model(
            modules=(_mod("api", syms=10), _mod("core", syms=4)),
            module_edges=(ModuleEdge("api", "core", 3, "depends"),),
        )
        out = service_map_mermaid(m)
        assert out.startswith("flowchart")
        assert "api" in out
        assert "core" in out
        assert '-->|"3"|' in out

    def test_external_node_for_interactions(self) -> None:
        m = _model(
            modules=(_mod("api"),),
            interactions=(Interaction("api", "External", "GET", "/x", 1.0),),
        )
        out = service_map_mermaid(m)
        assert "External" in out
        assert '-.->|"http"|' in out

    def test_test_module_styled(self) -> None:
        m = _model(modules=(_mod("Api.Tests", test=True),))
        out = service_map_mermaid(m)
        assert "classDef testmod" in out
        assert "class " in out

    def test_module_cap(self) -> None:
        mods = tuple(_mod(f"m{i}", syms=100 - i) for i in range(50))
        out = service_map_mermaid(_model(modules=mods))
        assert "more modules" in out


# endregion: --- Service map


# ---------------------------------------------------------------------------
# region:    --- Detail flowchart
# ---------------------------------------------------------------------------


class TestDetailFlowchart:
    def test_flow_renders_route_handler_chain(self) -> None:
        flow = Flow(
            nodes=(
                FlowNode("r:1", "GET /api/users", "route"),
                FlowNode("m:h", "List", "handler"),
                FlowNode("m:s", "GetAll", "method"),
            ),
            edges=(FlowEdge("r:1", "m:h"), FlowEdge("m:h", "m:s")),
        )
        m = _model(
            modules=(_mod("api", routes=1),),
            flows_by_module={"api": flow},
        )
        out = detail_flowchart(m, "api")
        assert out.startswith("flowchart")
        assert "GET /api/users" in out
        assert "List" in out
        assert "GetAll" in out
        assert "classDef route" in out
        assert _flowchart_balanced(out)

    def test_service_boundary_and_external_styled(self) -> None:
        flow = Flow(
            nodes=(
                FlowNode("m:h", "Handle", "handler"),
                FlowNode("m:s", "Lib (core)", "service"),
                FlowNode("ext:External", "External", "external"),
            ),
            edges=(FlowEdge("m:h", "m:s"), FlowEdge("m:h", "ext:External")),
        )
        m = _model(modules=(_mod("api"),), flows_by_module={"api": flow})
        out = detail_flowchart(m, "api")
        assert ":::svc" in out
        assert ":::ext" in out

    def test_empty_module(self) -> None:
        m = _model(modules=(_mod("api"),))
        out = detail_flowchart(m, "api")
        assert "no functions or routes" in out


# endregion: --- Detail flowchart


# ---------------------------------------------------------------------------
# region:    --- Class diagram (UML)
# ---------------------------------------------------------------------------


class TestClassDiagram:
    def _api_model(self) -> ArchModel:
        routes = (
            RouteInfo(
                "GET", "/api/users/{id}", "GetById", "aspnet",
                "UsersController", "u.cs", "r1",
            ),
            RouteInfo("POST", "/api/users", "Create", "aspnet", "UsersController", "u.cs", "r2"),
        )
        ctrl = Controller("UsersController", "ControllerBase", "u.cs", routes)
        return _model(
            modules=(_mod("api", routes=2),),
            routes_by_module={"api": routes},
            controllers_by_module={"api": (ctrl,)},
        )

    def test_class_and_members(self) -> None:
        out = class_diagram(self._api_model(), "api")
        assert out.startswith("classDiagram")
        assert "class UsersController" in out
        assert "+POST /api/users Create" in out

    def test_inheritance(self) -> None:
        out = class_diagram(self._api_model(), "api")
        assert "ControllerBase <|-- UsersController" in out

    def test_path_param_escaped(self) -> None:
        out = class_diagram(self._api_model(), "api")
        # {id} must be flattened (no braces or colon, which break classDiagram)
        assert "/api/users/id" in out
        assert "{id}" not in out
        assert ":id" not in out

    def test_balanced_braces(self) -> None:
        out = class_diagram(self._api_model(), "api")
        assert out.count("{") == out.count("}")

    def test_function_routes_become_endpoints(self) -> None:
        routes = (RouteInfo("GET", "/health", "health", "axum", "(routes)", "m.rs", "r1"),)
        ctrl = Controller("(routes)", "", "m.rs", routes)
        m = _model(
            modules=(_mod("svc", routes=1),),
            routes_by_module={"svc": routes},
            controllers_by_module={"svc": (ctrl,)},
        )
        out = class_diagram(m, "svc")
        assert "class Endpoints" in out

    def test_no_controllers(self) -> None:
        out = class_diagram(_model(modules=(_mod("x"),)), "x")
        assert "no HTTP routes" in out


# endregion: --- Class diagram


# ---------------------------------------------------------------------------
# region:    --- Sequence diagram
# ---------------------------------------------------------------------------


class TestSequence:
    def test_participants_and_messages(self) -> None:
        m = _model(interactions=(
            Interaction("svc-a", "svc-b", "GET", "/api/x", 0.9),
            Interaction("svc-a", "External", "POST", "/v1/y", 1.0, count=3),
        ))
        out = cross_service_sequence(m)
        assert out.startswith("sequenceDiagram")
        assert "participant" in out
        assert "as svc-a" in out
        assert "GET /api/x" in out
        assert "(x3)" in out

    def test_external_uses_async_arrow(self) -> None:
        m = _model(interactions=(
            Interaction("a", "External", "GET", "/x", 1.0),
        ))
        out = cross_service_sequence(m)
        assert "-)" in out

    def test_internal_uses_sync_arrow(self) -> None:
        m = _model(interactions=(
            Interaction("a", "b", "GET", "/x", 1.0),
        ))
        out = cross_service_sequence(m)
        assert "->>" in out

    def test_empty(self) -> None:
        out = cross_service_sequence(_model())
        assert "no cross-service calls" in out


# endregion: --- Sequence diagram


# ---------------------------------------------------------------------------
# region:    --- Deployment topology
# ---------------------------------------------------------------------------


class TestDeployment:
    def test_nodes_grouped_by_kind(self) -> None:
        m = _model(
            deploy_nodes=(
                DeployNode("i1", "api:latest", "docker_image"),
                DeployNode("d1", "api-deploy", "k8s_deployment"),
            ),
            deploy_edges=(DeployEdge("d1", "i1", "deploys"),),
        )
        out = deployment_topology(m)
        assert out.startswith("flowchart")
        assert 'subgraph sg0["docker_image"]' in out
        assert '"deploys"' in out
        assert _flowchart_balanced(out)

    def test_empty(self) -> None:
        out = deployment_topology(_model())
        assert "no Infrastructure-as-Code" in out


# endregion: --- Deployment topology
