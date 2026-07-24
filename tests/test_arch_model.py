"""Tests for the architecture model (`build_arch_model`).

Covers module derivation (crates vs SERVICE nodes vs folder fallback),
inter-module edge aggregation, route/controller extraction, outbound
calls, cross-service interactions, deployment topology, and robustness
on sparse graphs.
"""

from __future__ import annotations

from ast_intel.formatters._service_graph import build_arch_model
from ast_intel.models.ast_node import Confidence
from ast_intel.models.graph_model import (
    CodeGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    NodeKind,
)

# ---------------------------------------------------------------------------
# region:    --- Factories
# ---------------------------------------------------------------------------


def _n(
    nid: str,
    label: str,
    kind: NodeKind,
    file: str = "",
    *,
    service: str = "",
    **props: str,
) -> GraphNode:
    return GraphNode(
        id=nid, label=label, kind=kind, file=file,
        properties=dict(props), service=service,
    )


def _e(
    src: str,
    tgt: str,
    rel: EdgeRelation,
    score: float = 1.0,
    **props: str,
) -> GraphEdge:
    return GraphEdge(
        source=src, target=tgt, relation=rel,
        confidence=Confidence.EXTRACTED, confidence_score=score,
        properties=dict(props),
    )


def _single_repo_graph() -> CodeGraph:
    """A 2-project C# repo: `api` (controller + route + http call) and `core`.

    Topology:
      crate::api  --contains--> src/api/users.cs (file)
      crate::core --contains--> src/core/db.cs (file)
      UsersController (struct) in api; Db (struct) in core
      GetUsers (method) --method_of--> UsersController
      route GET /api/users --handles--> GetUsers ; file --exposes--> route
      GetUsers --calls--> Db                       (inter-module edge)
      http_call GET http://ext/v1 in api           (outbound -> External)
      docker_image + k8s_deployment + DEPLOYS edge (IaC topology)
    """
    api_file = "src/api/users.cs"
    core_file = "src/core/db.cs"
    nodes = [
        _n("crate::api", "api", NodeKind.CRATE, "api.csproj", language="csharp"),
        _n("crate::core", "core", NodeKind.CRATE, "core.csproj", language="csharp"),
        _n(f"{api_file}::<file>", api_file, NodeKind.FILE, api_file),
        _n(f"{core_file}::<file>", core_file, NodeKind.FILE, core_file),
        _n(f"{api_file}::UsersController", "UsersController", NodeKind.STRUCT, api_file),
        _n(f"{api_file}::GetUsers", "GetUsers", NodeKind.METHOD, api_file),
        _n(f"{core_file}::Db", "Db", NodeKind.STRUCT, core_file),
        _n(
            f"{api_file}::route:GET:/api/users", "GET /api/users",
            NodeKind.ROUTE, api_file,
            method="GET", path="/api/users", handler="GetUsers",
            framework="aspnet",
        ),
        _n(
            f"{api_file}::http_call:GET:http://ext/v1", "GET http://ext/v1",
            NodeKind.HTTP_CALL, api_file,
            url="http://ext/v1", method="GET", library="httpclient",
            caller="GetUsers",
        ),
        _n("img::api", "api:latest", NodeKind.DOCKER_IMAGE, "Dockerfile"),
        _n("k8s::api-deploy", "api-deploy", NodeKind.K8S_DEPLOYMENT, "k8s/api.yaml"),
    ]
    edges = [
        _e("crate::api", f"{api_file}::<file>", EdgeRelation.CONTAINS),
        _e("crate::core", f"{core_file}::<file>", EdgeRelation.CONTAINS),
        _e(f"{api_file}::<file>", f"{api_file}::UsersController", EdgeRelation.CONTAINS),
        _e(f"{api_file}::<file>", f"{api_file}::GetUsers", EdgeRelation.CONTAINS),
        _e(f"{core_file}::<file>", f"{core_file}::Db", EdgeRelation.CONTAINS),
        _e(f"{api_file}::GetUsers", f"{api_file}::UsersController", EdgeRelation.METHOD_OF),
        _e(f"{api_file}::route:GET:/api/users", f"{api_file}::GetUsers", EdgeRelation.HANDLES),
        _e(f"{api_file}::<file>", f"{api_file}::route:GET:/api/users", EdgeRelation.EXPOSES),
        _e(f"{api_file}::GetUsers", f"{core_file}::Db", EdgeRelation.CALLS),
        _e("k8s::api-deploy", "img::api", EdgeRelation.DEPLOYS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


def _merged_graph() -> CodeGraph:
    """A 2-service merged graph with a CALLS_SERVICE edge svc-a -> svc-b."""
    nodes = [
        _n("svc-a", "svc-a", NodeKind.SERVICE, service="svc-a"),
        _n("svc-b", "svc-b", NodeKind.SERVICE, service="svc-b"),
        _n("svc-a::f::call", "GET /api/x", NodeKind.HTTP_CALL, "a.cs",
           service="svc-a", url="http://b/api/x", method="GET"),
        _n("svc-b::f::route", "GET /api/x", NodeKind.ROUTE, "b.cs",
           service="svc-b", method="GET", path="/api/x", handler="X"),
    ]
    edges = [
        _e("svc-a::f::call", "svc-b::f::route", EdgeRelation.CALLS_SERVICE, 0.9,
           client_service="svc-a", server_service="svc-b",
           method="GET", url="http://b/api/x"),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


# endregion: --- Factories


# ---------------------------------------------------------------------------
# region:    --- Module derivation
# ---------------------------------------------------------------------------


class TestModuleDerivation:
    def test_modules_mode_from_crates(self) -> None:
        m = build_arch_model(_single_repo_graph())
        assert m.mode == "modules"
        labels = {mod.label for mod in m.modules}
        assert labels == {"api", "core"}

    def test_module_language_and_kind(self) -> None:
        m = build_arch_model(_single_repo_graph())
        api = next(mod for mod in m.modules if mod.label == "api")
        assert api.language == "csharp"
        assert api.kind == "project"

    def test_file_and_symbol_counts(self) -> None:
        m = build_arch_model(_single_repo_graph())
        api = next(mod for mod in m.modules if mod.label == "api")
        assert api.file_count == 1
        # UsersController + GetUsers = 2 code symbols (route/http_call excluded)
        assert api.symbol_count == 2
        assert api.route_count == 1

    def test_services_mode_from_service_nodes(self) -> None:
        m = build_arch_model(_merged_graph())
        assert m.mode == "services"
        labels = {mod.label for mod in m.modules}
        assert labels == {"svc-a", "svc-b"}
        assert all(mod.kind == "service" for mod in m.modules)

    def test_folder_fallback_when_no_crate(self) -> None:
        nodes = [
            _n("app/x.py::<file>", "app/x.py", NodeKind.FILE, "app/x.py"),
            _n("app/x.py::Foo", "Foo", NodeKind.STRUCT, "app/x.py"),
        ]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=[]))
        labels = {mod.label for mod in m.modules}
        assert labels == {"app"}
        assert next(iter(m.modules)).kind == "folder"

    def test_generic_root_uses_two_segments(self) -> None:
        nodes = [
            _n("src/svc/x.py::<file>", "src/svc/x.py", NodeKind.FILE, "src/svc/x.py"),
        ]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=[]))
        assert {mod.label for mod in m.modules} == {"src/svc"}


class TestTestModuleDetection:
    def test_test_projects_flagged(self) -> None:
        nodes = [
            _n("crate::Api", "Api", NodeKind.CRATE, language="csharp"),
            _n("crate::Api.Tests", "Api.Tests", NodeKind.CRATE, language="csharp"),
            _n("crate::Api.E2ETests", "Api.E2ETests", NodeKind.CRATE, language="csharp"),
        ]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=[]))
        by = {mod.label: mod.is_test for mod in m.modules}
        assert by["Api"] is False
        assert by["Api.Tests"] is True
        assert by["Api.E2ETests"] is True

    def test_test_modules_sorted_last(self) -> None:
        nodes = [
            _n("crate::Api.Tests", "Api.Tests", NodeKind.CRATE, language="csharp"),
            _n("crate::Api", "Api", NodeKind.CRATE, language="csharp"),
        ]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=[]))
        assert m.modules[-1].label == "Api.Tests"


# endregion: --- Module derivation


# ---------------------------------------------------------------------------
# region:    --- Inter-module edges
# ---------------------------------------------------------------------------


class TestModuleEdges:
    def test_cross_module_call_aggregated(self) -> None:
        m = build_arch_model(_single_repo_graph())
        # GetUsers (api) calls Db (core)
        assert any(
            e.source == "api" and e.target == "core" and e.weight == 1
            for e in m.module_edges
        )

    def test_intra_module_excluded(self) -> None:
        m = build_arch_model(_single_repo_graph())
        assert all(e.source != e.target for e in m.module_edges)

    def test_edges_sorted_by_weight(self) -> None:
        nodes = [
            _n("crate::a", "a", NodeKind.CRATE, language="x"),
            _n("crate::b", "b", NodeKind.CRATE, language="x"),
            _n("a/f.x::<file>", "a/f.x", NodeKind.FILE, "a/f.x"),
            _n("b/f.x::<file>", "b/f.x", NodeKind.FILE, "b/f.x"),
            _n("a/f.x::f1", "f1", NodeKind.FUNCTION, "a/f.x"),
            _n("a/f.x::f2", "f2", NodeKind.FUNCTION, "a/f.x"),
            _n("b/f.x::g", "g", NodeKind.FUNCTION, "b/f.x"),
        ]
        edges = [
            _e("crate::a", "a/f.x::<file>", EdgeRelation.CONTAINS),
            _e("crate::b", "b/f.x::<file>", EdgeRelation.CONTAINS),
            _e("a/f.x::f1", "b/f.x::g", EdgeRelation.CALLS),
            _e("a/f.x::f2", "b/f.x::g", EdgeRelation.CALLS),
        ]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=edges))
        top = m.module_edges[0]
        assert (top.source, top.target, top.weight) == ("a", "b", 2)


# endregion: --- Inter-module edges


# ---------------------------------------------------------------------------
# region:    --- Routes & controllers
# ---------------------------------------------------------------------------


class TestRoutesAndControllers:
    def test_route_assigned_to_module(self) -> None:
        m = build_arch_model(_single_repo_graph())
        routes = m.routes_by_module["api"]
        assert len(routes) == 1
        r = routes[0]
        assert (r.method, r.path, r.handler) == ("GET", "/api/users", "GetUsers")

    def test_controller_resolved_via_file_struct(self) -> None:
        m = build_arch_model(_single_repo_graph())
        r = m.routes_by_module["api"][0]
        assert r.controller == "UsersController"

    def test_controllers_grouped(self) -> None:
        m = build_arch_model(_single_repo_graph())
        controllers = m.controllers_by_module["api"]
        assert len(controllers) == 1
        c = controllers[0]
        assert c.name == "UsersController"
        assert len(c.routes) == 1


# endregion: --- Routes & controllers


# ---------------------------------------------------------------------------
# region:    --- Outbound calls & interactions
# ---------------------------------------------------------------------------


class TestOutboundAndInteractions:
    def test_outbound_call_to_external(self) -> None:
        m = build_arch_model(_single_repo_graph())
        calls = m.outbound_by_module["api"]
        assert len(calls) == 1
        assert calls[0].target_module == "External"

    def test_outbound_produces_interaction(self) -> None:
        m = build_arch_model(_single_repo_graph())
        assert any(
            i.client == "api" and i.server == "External" and i.method == "GET"
            for i in m.interactions
        )

    def test_calls_service_interaction_merged(self) -> None:
        m = build_arch_model(_merged_graph())
        match = [
            i for i in m.interactions
            if i.client == "svc-a" and i.server == "svc-b"
        ]
        assert match
        assert match[0].method == "GET"
        assert match[0].confidence == 0.9

    def test_interactions_aggregated(self) -> None:
        g = _single_repo_graph()
        # add a duplicate outbound call
        dup = _n(
            "src/api/users.cs::http_call:GET:http://ext/v1#2", "GET http://ext/v1",
            NodeKind.HTTP_CALL, "src/api/users.cs",
            url="http://ext/v1", method="GET", library="httpclient", caller="GetUsers",
        )
        g.nodes.append(dup)
        m = build_arch_model(g)
        ext = [i for i in m.interactions if i.server == "External"]
        assert len(ext) == 1
        assert ext[0].count == 2


# endregion: --- Outbound calls & interactions


# ---------------------------------------------------------------------------
# region:    --- Deployment topology
# ---------------------------------------------------------------------------


class TestDeployTopology:
    def test_iac_nodes_collected(self) -> None:
        m = build_arch_model(_single_repo_graph())
        kinds = {d.kind for d in m.deploy_nodes}
        assert "docker_image" in kinds
        assert "k8s_deployment" in kinds

    def test_iac_edge_collected(self) -> None:
        m = build_arch_model(_single_repo_graph())
        assert any(d.relation == "deploys" for d in m.deploy_edges)

    def test_code_nodes_excluded_from_topology(self) -> None:
        m = build_arch_model(_single_repo_graph())
        ids = {d.id for d in m.deploy_nodes}
        assert "src/api/users.cs::UsersController" not in ids


# endregion: --- Deployment topology


# ---------------------------------------------------------------------------
# region:    --- Robustness
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_empty_graph(self) -> None:
        m = build_arch_model(CodeGraph(nodes=[], edges=[]))
        assert m.modules == ()
        assert m.module_edges == ()
        assert m.interactions == ()
        assert m.stats["nodes"] == 0

    def test_graph_without_routes_or_iac(self) -> None:
        nodes = [
            _n("crate::a", "a", NodeKind.CRATE, language="go"),
            _n("a/x.go::<file>", "a/x.go", NodeKind.FILE, "a/x.go"),
            _n("a/x.go::Run", "Run", NodeKind.FUNCTION, "a/x.go"),
        ]
        edges = [_e("crate::a", "a/x.go::<file>", EdgeRelation.CONTAINS)]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=edges))
        assert {mod.label for mod in m.modules} == {"a"}
        assert m.stats["routes"] == 0
        assert m.deploy_nodes == ()

    def test_repo_name_passthrough(self) -> None:
        m = build_arch_model(CodeGraph(nodes=[], edges=[]), repo_name="myrepo")
        assert m.repo_name == "myrepo"


# endregion: --- Robustness


# ---------------------------------------------------------------------------
# region:    --- Request flows
# ---------------------------------------------------------------------------


def _flow_graph(*, call_targets: list[str]) -> CodeGraph:
    """A 1-controller module where Handle() makes name-based calls."""
    f = "src/api/svc.cs"
    nodes = [
        _n("crate::api", "api", NodeKind.CRATE, language="csharp"),
        _n(f"{f}::<file>", f, NodeKind.FILE, f),
        _n(f"{f}::Ctl", "Ctl", NodeKind.STRUCT, f),
        _n(f"{f}::Ctl.Handle", "Handle", NodeKind.METHOD, f),
        _n(f"{f}::Svc.DoWork", "DoWork", NodeKind.METHOD, f),
        _n(
            f"{f}::route:GET:/x", "GET /x", NodeKind.ROUTE, f,
            method="GET", path="/x", handler="Handle", framework="aspnet",
        ),
    ]
    edges = [_e("crate::api", f"{f}::<file>", EdgeRelation.CONTAINS)]
    # Name-based calls from the handler (target id = file::Name, dangling).
    edges += [
        _e(f"{f}::Ctl.Handle", f"{f}::{name}", EdgeRelation.CALLS)
        for name in call_targets
    ]
    return CodeGraph(nodes=nodes, edges=edges)


class TestRequestFlows:
    def test_route_handler_present(self) -> None:
        m = build_arch_model(_single_repo_graph())
        flow = m.flows_by_module.get("api")
        assert flow is not None
        kinds = {n.kind for n in flow.nodes}
        assert "route" in kinds
        assert "handler" in kinds

    def test_call_resolved_by_name(self) -> None:
        m = build_arch_model(_flow_graph(call_targets=["DoWork"]))
        flow = m.flows_by_module["api"]
        labels = {n.label for n in flow.nodes}
        assert "Handle" in labels
        assert "DoWork" in labels  # re-resolved from the dangling call edge

    def test_framework_noise_dropped(self) -> None:
        m = build_arch_model(
            _flow_graph(call_targets=["Ok", "Substring", "ConfigureAwait"]),
        )
        flow = m.flows_by_module["api"]
        labels = {n.label for n in flow.nodes}
        assert "Ok" not in labels
        assert "Substring" not in labels
        assert "Handle" in labels  # handler retained even with no real callees

    def test_flow_edges_connect_route_to_chain(self) -> None:
        m = build_arch_model(_flow_graph(call_targets=["DoWork"]))
        flow = m.flows_by_module["api"]
        # at least route->handler and handler->DoWork
        assert len(flow.edges) >= 2


def _worker_graph() -> CodeGraph:
    """A route-less module whose functions form a call chain."""
    f = "src/worker/job.cs"
    nodes = [
        _n("crate::worker", "worker", NodeKind.CRATE, language="csharp"),
        _n(f"{f}::<file>", f, NodeKind.FILE, f),
        _n(f"{f}::Job.Run", "Run", NodeKind.METHOD, f),
        _n(f"{f}::Job.Process", "Process", NodeKind.METHOD, f),
        _n(f"{f}::Repo.Save", "Save", NodeKind.METHOD, f),
    ]
    edges = [
        _e("crate::worker", f"{f}::<file>", EdgeRelation.CONTAINS),
        _e(f"{f}::Job.Run", f"{f}::Process", EdgeRelation.CALLS),
        _e(f"{f}::Job.Process", f"{f}::Save", EdgeRelation.CALLS),
    ]
    return CodeGraph(nodes=nodes, edges=edges)


class TestCallGraphFlows:
    """Route-less modules fall back to a function call-graph flow."""

    def test_routeless_module_gets_flow(self) -> None:
        m = build_arch_model(_worker_graph())
        flow = m.flows_by_module.get("worker")
        assert flow is not None
        labels = {n.label for n in flow.nodes}
        assert {"Run", "Process", "Save"} <= labels
        assert all(n.kind != "route" for n in flow.nodes)

    def test_entry_point_is_root(self) -> None:
        m = build_arch_model(_worker_graph())
        flow = m.flows_by_module["worker"]
        # Run has in-degree 0 -> shown as an entry-point (handler) node.
        run = next(n for n in flow.nodes if n.label == "Run")
        assert run.kind == "handler"
        # Process is reached via expansion -> a plain method node.
        proc = next(n for n in flow.nodes if n.label == "Process")
        assert proc.kind == "method"
        assert len(flow.edges) >= 2  # Run->Process->Save

    def test_leaf_only_module_lists_functions(self) -> None:
        f = "src/models/dto.cs"
        nodes = [
            _n("crate::models", "models", NodeKind.CRATE, language="csharp"),
            _n(f"{f}::<file>", f, NodeKind.FILE, f),
            _n(f"{f}::Dto.ToJson", "ToJson", NodeKind.METHOD, f),
            _n(f"{f}::Dto.FromJson", "FromJson", NodeKind.METHOD, f),
        ]
        edges = [_e("crate::models", f"{f}::<file>", EdgeRelation.CONTAINS)]
        m = build_arch_model(CodeGraph(nodes=nodes, edges=edges))
        flow = m.flows_by_module["models"]
        labels = {n.label for n in flow.nodes}
        assert {"ToJson", "FromJson"} <= labels


# endregion: --- Request flows
