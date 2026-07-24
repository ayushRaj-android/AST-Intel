"""Architecture model — derive a service/module-level view of a CodeGraph.

This is the pure-Python "brain" behind the Service Architecture Viewer
(``-f arch``).  It distils a full :class:`~ast_intel.models.graph_model.CodeGraph`
into a small, render-ready :class:`ArchModel` describing:

- **modules** (one per project/crate, or per SERVICE node in a merged graph),
- **inter-module dependency edges** (aggregated ``calls``/``imports``/…),
- **routes** and **controllers** per module (the HTTP API surface),
- **outbound HTTP calls** per module,
- **cross-service interactions** (for sequence diagrams),
- **deployment topology** (IaC nodes/edges).

All derivation happens here so it can be unit-tested without any HTML or
JavaScript.  The formatter (Phase 3) serialises this model and the Mermaid
builders (Phase 2) turn parts of it into diagram source.

The function is intentionally **robust to missing data**: a graph with no
routes, no HTTP calls, no SERVICE nodes, or no IaC still yields a valid
(smaller) model.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ast_intel.models.graph_model import (
    EdgeRelation,
    NodeKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ast_intel.models.graph_model import CodeGraph, GraphEdge, GraphNode

__all__: list[str] = [
    "ArchModel",
    "Controller",
    "DeployEdge",
    "DeployNode",
    "Flow",
    "FlowEdge",
    "FlowNode",
    "Interaction",
    "Module",
    "ModuleEdge",
    "OutboundCall",
    "RouteInfo",
    "build_arch_model",
]


# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

# Node kinds that count as "code symbols" when sizing a module.
_CODE_SYMBOL_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.STRUCT,
    NodeKind.ENUM,
    NodeKind.TRAIT,
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CONSTANT,
    NodeKind.TYPE_ALIAS,
    NodeKind.MACRO,
})

# Edge relations that represent a dependency between two code symbols
# (used to aggregate inter-module edges in the overview).
_DEP_RELATIONS: frozenset[EdgeRelation] = frozenset({
    EdgeRelation.CALLS,
    EdgeRelation.IMPORTS,
    EdgeRelation.DEPENDS_ON,
    EdgeRelation.USES_METHOD,
})

# IaC node kinds for the deployment topology view.
_IAC_NODE_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.SERVICE,
    NodeKind.DOCKER_IMAGE,
    NodeKind.DOCKER_STAGE,
    NodeKind.DOCKER_SERVICE,
    NodeKind.K8S_DEPLOYMENT,
    NodeKind.K8S_SERVICE,
    NodeKind.K8S_INGRESS,
    NodeKind.K8S_CONFIGMAP,
    NodeKind.K8S_SECRET,
    NodeKind.K8S_NAMESPACE,
    NodeKind.K8S_CRONJOB,
    NodeKind.K8S_JOB,
    NodeKind.K8S_PVC,
    NodeKind.K8S_RBAC,
    NodeKind.K8S_GENERIC,
    NodeKind.HELM_CHART,
    NodeKind.HELM_TEMPLATE,
})

# IaC edge relations for the deployment topology view.
_IAC_EDGE_RELATIONS: frozenset[EdgeRelation] = frozenset({
    EdgeRelation.DEPLOYS,
    EdgeRelation.IMAGE_OF,
    EdgeRelation.ROUTES_TO,
    EdgeRelation.RENDERS_TO,
    EdgeRelation.TEMPLATES_TO,
    EdgeRelation.BUILDS_IMAGE,
    EdgeRelation.REFERENCES_IMAGE,
    EdgeRelation.CONFIGURES,
    EdgeRelation.USES_SECRET,
    EdgeRelation.COPIES_FROM,
    EdgeRelation.DEPLOYS_VIA,
    EdgeRelation.PROVISIONS,
})

# Segments (case-insensitive) that mark a module as test-only.
_TEST_SEGMENTS: frozenset[str] = frozenset({
    "test", "tests", "e2e", "e2etests", "integrationtests",
    "unittests", "spec", "specs", "testing", "mock", "mocks",
})

_EXTERNAL = "External"

# Frameworks whose route handlers live in a controller *class* (so a route
# can be grouped under a UML class). Function-based frameworks (fastapi,
# flask, express, axum, actix) have no controller class.
_CLASS_FRAMEWORKS: frozenset[str] = frozenset({"aspnet", "spring"})

# Bound on returned inter-module edges (kept readable in the overview).
_MAX_MODULE_EDGES = 200

# Request-flow tracing bounds (per module).
_FLOW_MAX_NODES = 45
_FLOW_MAX_DEPTH = 3
_FLOW_FANOUT = 6
# Entry-point seeds for a route-less module's call-graph flow.
_FLOW_ROOTS_CAP = 8

# Universal object / lifecycle / await method names. These collide across
# modules (e.g. an overridden ``ToString``) and never represent a meaningful
# step in a request flow, so they are dropped during call resolution.
_FLOW_NOISE_NAMES: frozenset[str] = frozenset({
    "ToString", "Equals", "GetHashCode", "GetType", "ReferenceEquals",
    "MemberwiseClone", "Clone", "Dispose", "DisposeAsync",
    "ConfigureAwait", "GetAwaiter", "GetResult", "GetEnumerator",
    "Finalize", "CompareTo",
})


# endregion: --- Constants


# ---------------------------------------------------------------------------
# region:    --- Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """A single HTTP endpoint exposed by a module."""

    method: str
    path: str
    handler: str
    framework: str
    controller: str  # owning class, resolved via HANDLES→METHOD_OF, else ""
    file: str
    node_id: str


@dataclass(frozen=True, slots=True)
class Controller:
    """A controller class grouping several routes (for the UML view)."""

    name: str
    base: str  # base class (e.g. "ControllerBase") or ""
    file: str
    routes: tuple[RouteInfo, ...]


@dataclass(frozen=True, slots=True)
class OutboundCall:
    """An outgoing HTTP client call made by a module."""

    method: str
    url: str
    library: str
    caller: str
    target_module: str  # resolved server module, or "External"
    node_id: str


@dataclass(frozen=True, slots=True)
class Module:
    """A service or project/crate-level module."""

    id: str
    label: str
    language: str
    kind: str  # "service" | "project" | "folder"
    is_test: bool
    file_count: int
    symbol_count: int
    route_count: int


@dataclass(frozen=True, slots=True)
class ModuleEdge:
    """An aggregated dependency between two modules (overview graph)."""

    source: str  # module label
    target: str  # module label
    weight: int
    kind: str  # "depends" | "calls_service"
    label: str = ""


@dataclass(frozen=True, slots=True)
class Interaction:
    """A cross-service / external interaction (sequence diagram)."""

    client: str  # module label
    server: str  # module label or "External"
    method: str
    path: str
    confidence: float
    count: int = 1


@dataclass(frozen=True, slots=True)
class DeployNode:
    """An IaC resource in the deployment topology."""

    id: str
    label: str
    kind: str


@dataclass(frozen=True, slots=True)
class DeployEdge:
    """An edge in the deployment topology."""

    source: str
    target: str
    relation: str


@dataclass(frozen=True, slots=True)
class FlowNode:
    """A node in a per-module request-flow graph."""

    key: str
    label: str
    kind: str  # route | handler | method | service | external


@dataclass(frozen=True, slots=True)
class FlowEdge:
    """A directed edge in a request-flow graph."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class Flow:
    """A module's request-flow graph (routes → handlers → call chain)."""

    nodes: tuple[FlowNode, ...]
    edges: tuple[FlowEdge, ...]


@dataclass(frozen=True, slots=True)
class ArchModel:
    """The full render-ready architecture model."""

    repo_name: str
    mode: str  # "services" (merged) | "modules" (single repo)
    modules: tuple[Module, ...]
    module_edges: tuple[ModuleEdge, ...]
    routes_by_module: dict[str, tuple[RouteInfo, ...]] = field(
        default_factory=dict,
    )
    controllers_by_module: dict[str, tuple[Controller, ...]] = field(
        default_factory=dict,
    )
    outbound_by_module: dict[str, tuple[OutboundCall, ...]] = field(
        default_factory=dict,
    )
    interactions: tuple[Interaction, ...] = ()
    deploy_nodes: tuple[DeployNode, ...] = ()
    deploy_edges: tuple[DeployEdge, ...] = ()
    flows_by_module: dict[str, Flow] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


# endregion: --- Data structures


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _is_test_label(label: str) -> bool:
    """Heuristic: does *label* look like a test/mock module?"""
    segments = re.split(r"[.\-_/ ]+", label.lower())
    return any(seg in _TEST_SEGMENTS for seg in segments)


def _folder_module(file_path: str) -> str:
    """Fallback module name from a file path's leading directory.

    ``src/api/users.py`` → ``src/api`` (skips generic roots like ``src``).
    A bare filename → ``"(root)"``.
    """
    parts = [p for p in file_path.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    head = parts[0]
    generic = {"src", "lib", "app", "source", "pkg", "internal"}
    if head.lower() in generic and len(parts) >= 3:  # noqa: PLR2004
        return f"{parts[0]}/{parts[1]}"
    return head


def _short_url(url: str) -> str:
    """Trim a URL to a readable path for diagram labels."""
    u = re.sub(r"^[a-zA-Z]+://[^/]+", "", url)  # strip scheme + host
    u = u.split("?", 1)[0]
    return u or url


def _controller_for(
    route_file: str,
    framework: str,
    structs_by_file: dict[str, list[str]],
) -> str:
    """Resolve a route's controller class (class-based frameworks only).

    Prefers a struct whose name ends in ``Controller`` within the route's
    file; falls back to the first struct.  Returns ``""`` for
    function-based frameworks or when no class is found.
    """
    if framework not in _CLASS_FRAMEWORKS:
        return ""
    cands = structs_by_file.get(route_file, [])
    if not cands:
        return ""
    controllers = [c for c in cands if c.endswith("Controller")]
    return (controllers or cands)[0]


# endregion: --- Helpers


# ---------------------------------------------------------------------------
# region:    --- Module assignment
# ---------------------------------------------------------------------------


def _build_module_index(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    by_id: dict[str, GraphNode],
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve every node to a module label.

    Returns ``(mode, module_of, module_lang)`` where:
      - *mode* is ``"services"`` (merged graph) or ``"modules"``,
      - *module_of* maps node id → module label,
      - *module_lang* maps module label → language.
    """
    service_nodes = [n for n in nodes if n.kind == NodeKind.SERVICE]
    if service_nodes:
        module_of = {
            n.id: (n.service or n.label or "(root)") for n in nodes
        }
        module_lang = {sn.label: "" for sn in service_nodes}
        return "services", module_of, module_lang

    # Single repo: modules = crates (projects). Map file path → crate.
    crate_of_file: dict[str, str] = {}
    module_lang: dict[str, str] = {}
    for e in edges:
        if e.relation != EdgeRelation.CONTAINS:
            continue
        src = by_id.get(e.source)
        tgt = by_id.get(e.target)
        if src and src.kind == NodeKind.CRATE and tgt and tgt.kind == NodeKind.FILE:
            crate_of_file[tgt.file] = src.label
            module_lang[src.label] = src.properties.get("language", "")

    module_of: dict[str, str] = {}
    for n in nodes:
        if n.kind == NodeKind.CRATE:
            module_of[n.id] = n.label
        elif n.file and n.file in crate_of_file:
            module_of[n.id] = crate_of_file[n.file]
        elif n.file:
            module_of[n.id] = _folder_module(n.file)
        else:
            module_of[n.id] = "(root)"
    return "modules", module_of, module_lang


# endregion: --- Module assignment


# ---------------------------------------------------------------------------
# region:    --- Public API
# ---------------------------------------------------------------------------


def build_arch_model(  # noqa: C901, PLR0912, PLR0915
    graph: CodeGraph, *, repo_name: str = "",
) -> ArchModel:
    """Distil a :class:`CodeGraph` into an :class:`ArchModel`.

    Args:
        graph: The full code knowledge graph.
        repo_name: Optional display name for the repository/workspace.

    Returns:
        A render-ready :class:`ArchModel` (safe even on sparse graphs).
    """
    nodes = list(graph.nodes)
    edges = list(graph.edges)
    by_id: dict[str, GraphNode] = {n.id: n for n in nodes}

    mode, module_of, module_lang = _build_module_index(nodes, edges, by_id)

    # --- Module membership counts ---
    file_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    module_labels: set[str] = set()
    for n in nodes:
        mod = module_of.get(n.id, "(root)")
        if n.kind in (NodeKind.SERVICE, NodeKind.CRATE):
            module_labels.add(n.label)
            continue
        if n.kind == NodeKind.FILE:
            file_counts[mod] += 1
            module_labels.add(mod)
        elif n.kind in _CODE_SYMBOL_KINDS:
            symbol_counts[mod] += 1
            module_labels.add(mod)

    # --- Edge lookup: CALLS_SERVICE (cross-service resolution) ---
    route_of_call: dict[str, str] = {}
    for e in edges:
        if e.relation == EdgeRelation.CALLS_SERVICE:
            route_of_call[e.source] = e.target

    # --- Controller resolution (class-based frameworks only) ---
    # ASP.NET / Spring controllers are classes (STRUCT nodes).  The C#
    # extractor models a class's method container as an IMPL_BLOCK
    # labelled "<Base> for <Class>", which also yields the base class.
    # (Note: HANDLES edges are class-unqualified and do not resolve to
    # the qualified method nodes, so we resolve by file instead.)
    structs_by_file: dict[str, list[str]] = defaultdict(list)
    base_of_class: dict[str, str] = {}
    for n in nodes:
        if n.kind == NodeKind.STRUCT:
            structs_by_file[n.file].append(n.label)
        elif n.kind == NodeKind.IMPL_BLOCK and " for " in n.label:
            base, cls = n.label.split(" for ", 1)
            base_of_class.setdefault(cls.strip(), base.strip())
    for e in edges:
        if e.relation == EdgeRelation.INHERITS:
            src = by_id.get(e.source)
            tgt = by_id.get(e.target)
            if src and tgt:
                base_of_class.setdefault(src.label, tgt.label)

    # --- Routes per module + controllers ---
    routes_by_module: dict[str, list[RouteInfo]] = defaultdict(list)
    route_counts: Counter[str] = Counter()
    for n in nodes:
        if n.kind != NodeKind.ROUTE:
            continue
        mod = module_of.get(n.id, "(root)")
        props = n.properties
        controller = _controller_for(
            n.file, props.get("framework", ""), structs_by_file,
        )
        routes_by_module[mod].append(RouteInfo(
            method=props.get("method", ""),
            path=props.get("path", n.label),
            handler=props.get("handler", ""),
            framework=props.get("framework", ""),
            controller=controller,
            file=n.file,
            node_id=n.id,
        ))
        route_counts[mod] += 1
        module_labels.add(mod)

    controllers_by_module = _group_controllers(
        routes_by_module, base_of_class,
    )

    # --- Outbound HTTP calls per module ---
    outbound_by_module: dict[str, list[OutboundCall]] = defaultdict(list)
    interactions_raw: list[Interaction] = []
    route_module = {
        n.id: module_of.get(n.id, "(root)")
        for n in nodes if n.kind == NodeKind.ROUTE
    }
    for n in nodes:
        if n.kind != NodeKind.HTTP_CALL:
            continue
        mod = module_of.get(n.id, "(root)")
        props = n.properties
        route_id = route_of_call.get(n.id)
        target = route_module.get(route_id, _EXTERNAL) if route_id else _EXTERNAL
        method = props.get("method", "") or "UNKNOWN"
        url = props.get("url", n.label)
        outbound_by_module[mod].append(OutboundCall(
            method=method,
            url=url,
            library=props.get("library", ""),
            caller=props.get("caller", ""),
            target_module=target,
            node_id=n.id,
        ))
        # Only unresolved calls become interactions here; resolved
        # (cross-service) calls are emitted from the CALLS_SERVICE loop
        # below with their real match confidence (avoids double-counting).
        if target == _EXTERNAL:
            interactions_raw.append(Interaction(
                client=mod, server=target, method=method,
                path=_short_url(url), confidence=1.0,
            ))
        module_labels.add(mod)

    # --- Explicit cross-service edges (merged graphs) ---
    for e in edges:
        if e.relation != EdgeRelation.CALLS_SERVICE:
            continue
        client = e.properties.get("client_service") or module_of.get(e.source, "")
        server = e.properties.get("server_service") or route_module.get(e.target, "")
        if not client or not server:
            continue
        interactions_raw.append(Interaction(
            client=client, server=server,
            method=e.properties.get("method", "UNKNOWN"),
            path=_short_url(e.properties.get("url", "")),
            confidence=e.confidence_score,
        ))

    interactions = _aggregate_interactions(interactions_raw)

    # --- Inter-module dependency edges (overview) ---
    module_edges = _aggregate_module_edges(edges, module_of, module_labels)

    # --- Deployment topology (IaC) ---
    deploy_nodes, deploy_edges = _build_deploy_topology(nodes, edges, by_id)

    # --- Per-module request-flow (handler call chains) ---
    flows_by_module = _build_flows(
        nodes, edges, module_of, routes_by_module, outbound_by_module,
    )

    # --- Assemble Module list ---
    modules = _assemble_modules(
        mode, module_labels, module_lang,
        file_counts, symbol_counts, route_counts,
    )

    stats = {
        "modules": len(modules),
        "module_edges": len(module_edges),
        "routes": sum(route_counts.values()),
        "outbound_calls": sum(len(v) for v in outbound_by_module.values()),
        "interactions": len(interactions),
        "deploy_nodes": len(deploy_nodes),
        "flow_nodes": sum(len(f.nodes) for f in flows_by_module.values()),
        "nodes": len(nodes),
        "edges": len(edges),
    }

    return ArchModel(
        repo_name=repo_name,
        mode=mode,
        modules=modules,
        module_edges=module_edges,
        routes_by_module={k: tuple(v) for k, v in routes_by_module.items()},
        controllers_by_module=controllers_by_module,
        outbound_by_module={
            k: tuple(v) for k, v in outbound_by_module.items()
        },
        interactions=interactions,
        deploy_nodes=deploy_nodes,
        deploy_edges=deploy_edges,
        flows_by_module=flows_by_module,
        stats=stats,
    )


# endregion: --- Public API


# ---------------------------------------------------------------------------
# region:    --- Derivation helpers
# ---------------------------------------------------------------------------


def _add_flow_node(
    fnodes: dict[str, FlowNode], key: str, label: str, kind: str,
) -> bool:
    """Add a flow node (capped). Returns ``True`` if present or added."""
    if key in fnodes:
        return True
    if len(fnodes) >= _FLOW_MAX_NODES:
        return False
    fnodes[key] = FlowNode(key=key, label=label, kind=kind)
    return True


def _pick_call_roots(
    methods: Sequence[GraphNode],
    calls: dict[str, list[str]],
    resolve: Callable[[str, str], GraphNode | None],
    module: str,
    module_of: dict[str, str],
) -> list[tuple[str, str]]:
    """Choose entry-point methods for a route-less module's call graph.

    Entry points are functions that nothing else in the module calls
    (in-degree zero) yet which themselves call something.  When the call
    graph is fully cyclic the highest out-degree methods are used; when there
    are no internal calls at all the first few methods are shown standalone.
    Returns ``(node_id, label)`` pairs, capped at :data:`_FLOW_ROOTS_CAP`.
    """
    in_module = {m.id for m in methods}
    indeg: dict[str, int] = {m.id: 0 for m in methods}
    outdeg: dict[str, int] = {m.id: 0 for m in methods}
    for m in methods:
        for name in dict.fromkeys(calls.get(m.id, ())):
            if name in _FLOW_NOISE_NAMES:
                continue
            callee = resolve(name, module)
            if callee is None or callee.id == m.id:
                continue
            outdeg[m.id] += 1
            if callee.id in in_module:
                indeg[callee.id] += 1
    label_of = {m.id: m.label for m in methods}
    roots = [mid for mid in indeg if indeg[mid] == 0 and outdeg[mid] > 0]
    if not roots:  # fully cyclic — fall back to busiest callers
        roots = sorted(
            (mid for mid in in_module if outdeg[mid] > 0),
            key=lambda x: -outdeg[x],
        )
    if not roots:  # no internal calls — show a sample of functions
        roots = [m.id for m in methods]
    return [(mid, label_of[mid]) for mid in roots[:_FLOW_ROOTS_CAP]]


def _build_flows(  # noqa: C901, PLR0912, PLR0915
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    module_of: dict[str, str],
    routes_by_module: dict[str, list[RouteInfo]],
    outbound_by_module: dict[str, list[OutboundCall]],
) -> dict[str, Flow]:
    """Build a per-module request- / call-graph flow.

    Modules that expose HTTP routes are traced **route → handler → callees**.
    Modules with *no* routes (workers, libraries, background services) fall
    back to a **call-graph** flow rooted at their entry-point functions, so
    the detail view is never empty when there is code to show.

    Call edges are frequently *name-based* (the target id is the call-site
    name, not a resolved node), so callees are re-resolved by label with a
    same-module preference.  This drops framework / stdlib calls (which match
    no repository method) and keeps genuine business calls.  A cross-module
    callee becomes a service-boundary node and is not expanded further.
    """
    calls: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.relation == EdgeRelation.CALLS:
            calls[e.source].append(e.target.rsplit("::", 1)[-1])

    by_label: dict[str, list[GraphNode]] = defaultdict(list)
    handler_index: dict[tuple[str, str], GraphNode] = {}
    methods_by_module: dict[str, list[GraphNode]] = defaultdict(list)
    for n in nodes:
        if n.kind in (NodeKind.METHOD, NodeKind.FUNCTION):
            by_label[n.label].append(n)
            handler_index.setdefault((n.file, n.label), n)
            mod = module_of.get(n.id)
            if mod:
                methods_by_module[mod].append(n)

    def resolve(name: str, prefer: str) -> GraphNode | None:
        cands = by_label.get(name)
        if not cands:
            return None
        same = [c for c in cands if module_of.get(c.id) == prefer]
        return (same or cands)[0]

    def expand(
        seeds: list[str],
        fnodes: dict[str, FlowNode],
        fedges: list[tuple[str, str]],
        module: str,
    ) -> None:
        frontier: deque[tuple[str, int]] = deque(
            (s, 0) for s in dict.fromkeys(seeds)
        )
        visited: set[str] = set(seeds)
        while frontier:
            nid, depth = frontier.popleft()
            if depth >= _FLOW_MAX_DEPTH or len(fnodes) >= _FLOW_MAX_NODES:
                continue
            skey = "m:" + nid
            fanout = 0
            for name in dict.fromkeys(calls.get(nid, ())):
                if fanout >= _FLOW_FANOUT:
                    break
                if name in _FLOW_NOISE_NAMES:
                    continue
                callee = resolve(name, module)
                if callee is None or callee.id == nid:
                    continue
                cmod = module_of.get(callee.id, module)
                cross = cmod != module
                ckey = "m:" + callee.id
                clabel = f"{callee.label} ({cmod})" if cross else callee.label
                if not _add_flow_node(
                    fnodes, ckey, clabel, "service" if cross else "method",
                ):
                    continue
                fedges.append((skey, ckey))
                fanout += 1
                if not cross and callee.id not in visited:
                    visited.add(callee.id)
                    frontier.append((callee.id, depth + 1))

    flows: dict[str, Flow] = {}
    for module in set(routes_by_module) | set(methods_by_module):
        routes = routes_by_module.get(module) or []
        fnodes: dict[str, FlowNode] = {}
        fedges: list[tuple[str, str]] = []

        if routes:
            seeds: list[str] = []
            for r in routes:
                rkey = "r:" + r.node_id
                _add_flow_node(fnodes, rkey, f"{r.method} {r.path}", "route")
                hn = handler_index.get((r.file, r.handler))
                if hn is None:
                    continue
                hkey = "m:" + hn.id
                if _add_flow_node(fnodes, hkey, r.handler, "handler"):
                    fedges.append((rkey, hkey))
                seeds.append(hn.id)
            expand(seeds, fnodes, fedges, module)
        else:
            # Route-less module → call-graph flow from entry-point functions.
            roots = _pick_call_roots(
                methods_by_module.get(module, ()), calls, resolve,
                module, module_of,
            )
            for rid, rlabel in roots:
                _add_flow_node(fnodes, "m:" + rid, rlabel, "handler")
            expand([rid for rid, _ in roots], fnodes, fedges, module)

        # Outbound HTTP calls → External, linked from their caller.
        label_to_key = {
            fn.label: fn.key
            for fn in fnodes.values()
            if fn.kind in ("handler", "method")
        }
        default_src = next(
            (fn.key for fn in fnodes.values()
             if fn.kind in ("handler", "method")), None,
        )
        for oc in outbound_by_module.get(module, ()):
            tgt = oc.target_module or _EXTERNAL
            ekey = "ext:" + tgt
            if not _add_flow_node(fnodes, ekey, tgt, "external"):
                continue
            src = label_to_key.get(oc.caller, default_src)
            if src:
                fedges.append((src, ekey))

        seen: set[tuple[str, str]] = set()
        uedges: list[FlowEdge] = []
        for s, t in fedges:
            if s in fnodes and t in fnodes and s != t and (s, t) not in seen:
                seen.add((s, t))
                uedges.append(FlowEdge(source=s, target=t))
        if fnodes:
            flows[module] = Flow(
                nodes=tuple(fnodes.values()), edges=tuple(uedges),
            )
    return flows


def _group_controllers(
    routes_by_module: dict[str, list[RouteInfo]],
    base_of_class: dict[str, str],
) -> dict[str, tuple[Controller, ...]]:
    """Group each module's routes by their owning controller class."""
    out: dict[str, tuple[Controller, ...]] = {}
    for mod, routes in routes_by_module.items():
        grouped: dict[str, list[RouteInfo]] = defaultdict(list)
        for r in routes:
            grouped[r.controller or "(routes)"].append(r)
        controllers: list[Controller] = []
        for cls, rs in sorted(grouped.items()):
            controllers.append(Controller(
                name=cls,
                base=base_of_class.get(cls, ""),
                file=rs[0].file,
                routes=tuple(sorted(rs, key=lambda x: (x.path, x.method))),
            ))
        out[mod] = tuple(controllers)
    return out


def _aggregate_interactions(
    raw: list[Interaction],
) -> tuple[Interaction, ...]:
    """Collapse duplicate (client, server, method, path) interactions."""
    counter: Counter[tuple[str, str, str, str]] = Counter()
    conf: dict[tuple[str, str, str, str], float] = {}
    for i in raw:
        key = (i.client, i.server, i.method, i.path)
        counter[key] += 1
        conf[key] = max(conf.get(key, 0.0), i.confidence)
    result = [
        Interaction(
            client=c, server=s, method=m, path=p,
            confidence=conf[(c, s, m, p)], count=n,
        )
        for (c, s, m, p), n in counter.items()
    ]
    result.sort(key=lambda x: (x.client, x.server, x.path, x.method))
    return tuple(result)


def _aggregate_module_edges(
    edges: list[GraphEdge],
    module_of: dict[str, str],
    module_labels: set[str],
) -> tuple[ModuleEdge, ...]:
    """Aggregate symbol-level dependency edges to the module level."""
    counter: Counter[tuple[str, str]] = Counter()
    for e in edges:
        if e.relation not in _DEP_RELATIONS:
            continue
        src = module_of.get(e.source)
        tgt = module_of.get(e.target)
        if not src or not tgt or src == tgt:
            continue
        if src not in module_labels or tgt not in module_labels:
            continue
        counter[(src, tgt)] += 1
    result = [
        ModuleEdge(source=s, target=t, weight=w, kind="depends")
        for (s, t), w in counter.items()
    ]
    result.sort(key=lambda m: (-m.weight, m.source, m.target))
    return tuple(result[:_MAX_MODULE_EDGES])


def _build_deploy_topology(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    by_id: dict[str, GraphNode],
) -> tuple[tuple[DeployNode, ...], tuple[DeployEdge, ...]]:
    """Collect IaC nodes/edges for the deployment topology view."""
    iac_ids: set[str] = set()
    dnodes: list[DeployNode] = []
    for n in nodes:
        if n.kind in _IAC_NODE_KINDS:
            iac_ids.add(n.id)
            dnodes.append(DeployNode(id=n.id, label=n.label, kind=n.kind.value))
    dedges: list[DeployEdge] = []
    for e in edges:
        if e.relation not in _IAC_EDGE_RELATIONS:
            continue
        if e.source in iac_ids and e.target in iac_ids:
            dedges.append(DeployEdge(
                source=e.source, target=e.target, relation=e.relation.value,
            ))
    dnodes.sort(key=lambda d: (d.kind, d.label))
    dedges.sort(key=lambda d: (d.relation, d.source, d.target))
    return tuple(dnodes), tuple(dedges)


def _assemble_modules(  # noqa: PLR0913
    mode: str,
    module_labels: set[str],
    module_lang: dict[str, str],
    file_counts: Counter[str],
    symbol_counts: Counter[str],
    route_counts: Counter[str],
) -> tuple[Module, ...]:
    """Build the sorted :class:`Module` list."""
    kind = "service" if mode == "services" else "project"
    modules: list[Module] = []
    for label in module_labels:
        lang = module_lang.get(label, "")
        mkind = kind
        if mode != "services" and label not in module_lang:
            mkind = "folder"  # came from the folder fallback, not a crate
        modules.append(Module(
            id=label,
            label=label,
            language=lang,
            kind=mkind,
            is_test=_is_test_label(label),
            file_count=file_counts.get(label, 0),
            symbol_count=symbol_counts.get(label, 0),
            route_count=route_counts.get(label, 0),
        ))
    modules.sort(key=lambda m: (m.is_test, -m.symbol_count, m.label))
    return tuple(modules)


# endregion: --- Derivation helpers
