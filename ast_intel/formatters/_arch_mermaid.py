"""Mermaid diagram builders for the Service Architecture Viewer.

Pure string functions that turn an :class:`~ast_intel.formatters._service_graph.ArchModel`
(or part of it) into Mermaid source for the five architecture views:

- :func:`service_map_mermaid`   — module dependency map (flowchart)
- :func:`detail_flowchart`      — one module's routes + outbound calls (flowchart)
- :func:`class_diagram`         — one module's controllers/routes (classDiagram, UML)
- :func:`cross_service_sequence`— interactions across modules (sequenceDiagram)
- :func:`deployment_topology`   — IaC resources (flowchart)

Every builder is **bounded** (caps + a synthetic "+N more" node) so a large
repository never produces an unreadable diagram, and all output is escaped to
keep the Mermaid parser happy (paths with ``{id}``, names with dots, etc.).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ast_intel.formatters._service_graph import ArchModel

__all__: list[str] = [
    "class_diagram",
    "cloud_infra_diagram",
    "cross_service_sequence",
    "deployment_topology",
    "detail_flowchart",
    "service_map_mermaid",
]

# ---------------------------------------------------------------------------
# region:    --- Bounds
# ---------------------------------------------------------------------------

_MAX_MAP_MODULES = 40
_MAX_MAP_EDGES = 60
_MAX_DETAIL_ROUTES = 40
_MAX_CLASS_MEMBERS = 40
_MAX_SEQ_MESSAGES = 50
_MAX_DEPLOY_NODES = 80

_EXTERNAL = "External"

# endregion: --- Bounds


# ---------------------------------------------------------------------------
# region:    --- Escaping & id helpers
# ---------------------------------------------------------------------------


class _IdGen:
    """Assign short, stable, Mermaid-safe ids to arbitrary string keys."""

    def __init__(self, prefix: str = "n") -> None:
        self._map: dict[str, str] = {}
        self._prefix = prefix

    def get(self, key: str) -> str:
        if key not in self._map:
            self._map[key] = f"{self._prefix}{len(self._map)}"
        return self._map[key]


def _flow_label(text: str) -> str:
    """Escape text for a quoted flowchart label ``id["..."]``."""
    return (
        text.replace("\\", "/")
        .replace('"', "#quot;")
        .replace("[", "#91;")
        .replace("]", "#93;")
        .replace("\n", " ")
        .strip()
    )


def _cd_text(text: str) -> str:
    """Sanitize text for a classDiagram member line.

    classDiagram is strict: ``:`` splits name/type, ``()`` marks methods,
    ``{}`` delimits the class body, ``~`` marks generics.  Route params
    like ``{id}`` are flattened to ``id`` and all such chars are removed
    so the route renders as a plain attribute (``/`` is safe to keep).
    """
    text = re.sub(r"\{([^}]*)\}", r"\1", text)  # {id} -> id
    for ch in "{}()~:<>,|":
        text = text.replace(ch, "")
    return text.replace("\n", " ").strip()


def _ident(name: str) -> str:
    """A safe Mermaid identifier (alphanumeric + underscore)."""
    out = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_")
    if not out:
        out = "x"
    if out[0].isdigit():
        out = f"c_{out}"
    return out


def _seq_text(text: str) -> str:
    """Escape a sequenceDiagram message (free text after ``:``)."""
    return (
        text.replace("\n", " ").replace(";", ",")
        .replace("<", "(").replace(">", ")")
        .strip()
        or "-"
    )


# endregion: --- Escaping & id helpers


# ---------------------------------------------------------------------------
# region:    --- 1. Service map (flowchart)
# ---------------------------------------------------------------------------


def service_map_mermaid(  # noqa: C901, PLR0912
    model: ArchModel, *, direction: str = "LR",
) -> str:
    """Module dependency map: modules + aggregated edges + External."""
    lines = [f"flowchart {direction}"]
    ids = _IdGen("m")

    modules = list(model.modules)[:_MAX_MAP_MODULES]
    shown = {m.label for m in modules}
    test_ids: list[str] = []
    for mod in modules:
        nid = ids.get(mod.label)
        meta = f"{mod.symbol_count} sym"
        if mod.route_count:
            meta += f" · {mod.route_count} routes"
        lines.append(
            f'    {nid}["{_flow_label(mod.label)}<br/><small>{meta}</small>"]',
        )
        if mod.is_test:
            test_ids.append(nid)

    has_ext = any(
        i.server == _EXTERNAL and i.client in shown for i in model.interactions
    )
    if has_ext:
        lines.append(f'    {ids.get(_EXTERNAL)}(["{_EXTERNAL}"])')

    count = 0
    for e in model.module_edges:
        if count >= _MAX_MAP_EDGES:
            break
        if e.source in shown and e.target in shown:
            lines.append(
                f'    {ids.get(e.source)} -->|"{e.weight}"| {ids.get(e.target)}',
            )
            count += 1

    seen_http: set[tuple[str, str]] = set()
    for i in model.interactions:
        if count >= _MAX_MAP_EDGES:
            break
        if i.client not in shown:
            continue
        if i.server != _EXTERNAL and i.server not in shown:
            continue
        pair = (i.client, i.server)
        if pair in seen_http:
            continue
        seen_http.add(pair)
        lines.append(
            f'    {ids.get(i.client)} -.->|"http"| {ids.get(i.server)}',
        )
        count += 1

    if len(model.modules) > _MAX_MAP_MODULES:
        extra = len(model.modules) - _MAX_MAP_MODULES
        lines.append(f'    more["+{extra} more modules"]')

    if test_ids:
        lines.append(
            "    classDef testmod fill:#33343f,stroke:#6c7086,"
            "color:#9399b2,stroke-dasharray:4 2",
        )
        lines.append(f"    class {','.join(test_ids)} testmod")
    return "\n".join(lines)


# endregion: --- 1. Service map


# ---------------------------------------------------------------------------
# region:    --- 2. Per-module detail (flowchart)
# ---------------------------------------------------------------------------


def detail_flowchart(model: ArchModel, module: str, *, direction: str = "LR") -> str:
    """One module's request / call-graph flow.

    Renders the pre-traced :class:`Flow` for *module*.  Modules with HTTP
    routes flow **route → handler → call chain → service/external boundary**;
    route-less modules (workers, libraries) instead show a **call graph**
    rooted at their entry-point functions.
    """
    flow = model.flows_by_module.get(module)
    if flow is None or not flow.nodes:
        msg = f"{module}: no functions or routes to display"
        return f'flowchart {direction}\n    empty["{_flow_label(msg)}"]'

    lines = [f"flowchart {direction}"]
    ids = _IdGen("f")
    for fn in flow.nodes:
        nid = ids.get(fn.key)
        lbl = _flow_label(fn.label)
        if fn.kind == "route":
            lines.append(f'    {nid}["{lbl}"]:::route')
        elif fn.kind == "handler":
            lines.append(f'    {nid}(["{lbl}"]):::handler')
        elif fn.kind == "service":
            lines.append(f'    {nid}[["{lbl}"]]:::svc')
        elif fn.kind == "external":
            lines.append(f'    {nid}>"{lbl}"]:::ext')
        else:
            lines.append(f'    {nid}(["{lbl}"]):::method')

    lines.extend(
        f"    {ids.get(fe.source)} --> {ids.get(fe.target)}"
        for fe in flow.edges
    )

    lines.append("    classDef route fill:#89b4fa,stroke:#1e66f5,color:#11111b")
    lines.append("    classDef handler fill:#a6e3a1,stroke:#40a02b,color:#11111b")
    lines.append("    classDef method fill:#313244,stroke:#6c7086,color:#cdd6f4")
    lines.append("    classDef svc fill:#f9e2af,stroke:#df8e1d,color:#11111b")
    lines.append("    classDef ext fill:#f38ba8,stroke:#d20f39,color:#11111b")
    return "\n".join(lines)


# endregion: --- 2. Per-module detail


# ---------------------------------------------------------------------------
# region:    --- 3. Per-module API (classDiagram / UML)
# ---------------------------------------------------------------------------


def class_diagram(model: ArchModel, module: str) -> str:
    """One module's controllers as UML classes, routes as members."""
    lines = ["classDiagram"]
    controllers = list(model.controllers_by_module.get(module, ()))
    if not controllers:
        return 'classDiagram\n    class NoApi["no HTTP routes in this module"]'

    members_total = 0
    for c in controllers:
        cname = "Endpoints" if c.name == "(routes)" else _ident(c.name)
        lines.append(f"    class {cname} {{")
        for r in c.routes:
            if members_total >= _MAX_CLASS_MEMBERS:
                break
            member = _cd_text(f"{r.method} {r.path} {r.handler}").strip()
            lines.append(f"        +{member}")
            members_total += 1
        lines.append("    }")
        if c.base:
            lines.append(f"    {_ident(c.base)} <|-- {cname}")

    if members_total >= _MAX_CLASS_MEMBERS:
        lines.append('    note "route list truncated"')
    return "\n".join(lines)


# endregion: --- 3. Per-module API


# ---------------------------------------------------------------------------
# region:    --- 4. Cross-service sequence (sequenceDiagram)
# ---------------------------------------------------------------------------


def cross_service_sequence(model: ArchModel) -> str:
    """Cross-module / external interactions as a sequence diagram."""
    interactions = list(model.interactions)[:_MAX_SEQ_MESSAGES]
    if not interactions:
        return "sequenceDiagram\n    Note over System: no cross-service calls detected"

    lines = ["sequenceDiagram"]
    ids = _IdGen("p")
    participants: list[str] = []
    seen: set[str] = set()
    for i in interactions:
        for actor in (i.client, i.server):
            if actor not in seen:
                seen.add(actor)
                participants.append(actor)
    lines.extend(
        f"    participant {ids.get(actor)} as {_seq_text(actor)}"
        for actor in participants
    )

    for i in interactions:
        msg = _seq_text(f"{i.method} {i.path}")
        if i.count > 1:
            msg += f" (x{i.count})"
        arrow = "->>" if i.server != _EXTERNAL else "-)"
        lines.append(f"    {ids.get(i.client)}{arrow}{ids.get(i.server)}: {msg}")

    if len(model.interactions) > _MAX_SEQ_MESSAGES:
        extra = len(model.interactions) - _MAX_SEQ_MESSAGES
        lines.append(f"    Note over {ids.get(participants[0])}: +{extra} more interactions")
    return "\n".join(lines)


# endregion: --- 4. Cross-service sequence


# ---------------------------------------------------------------------------
# region:    --- 5. Deployment topology (flowchart)
# ---------------------------------------------------------------------------


def deployment_topology(model: ArchModel, *, direction: str = "LR") -> str:
    """IaC resources and their relationships."""
    if not model.deploy_nodes:
        return f'flowchart {direction}\n    none["no Infrastructure-as-Code resources found"]'

    lines = [f"flowchart {direction}"]
    ids = _IdGen("k")
    nodes = list(model.deploy_nodes)[:_MAX_DEPLOY_NODES]
    shown = {n.id for n in nodes}

    # Group nodes by kind into subgraphs for readability.
    by_kind: dict[str, list] = defaultdict(list)
    for n in nodes:
        by_kind[n.kind].append(n)
    for ki, (kind, ns) in enumerate(sorted(by_kind.items())):
        lines.append(f'    subgraph sg{ki}["{_flow_label(kind)}"]')
        lines.extend(
            f'        {ids.get(n.id)}["{_flow_label(n.label)}"]' for n in ns
        )
        lines.append("    end")

    lines.extend(
        f'    {ids.get(e.source)} -->|"{_flow_label(e.relation)}"| '
        f'{ids.get(e.target)}'
        for e in model.deploy_edges
        if e.source in shown and e.target in shown
    )

    if len(model.deploy_nodes) > _MAX_DEPLOY_NODES:
        extra = len(model.deploy_nodes) - _MAX_DEPLOY_NODES
        lines.append(f'    more["+{extra} more resources"]')
    return "\n".join(lines)


# endregion: --- 5. Deployment topology


# ---------------------------------------------------------------------------
# region:    --- Cloud infrastructure diagram
# ---------------------------------------------------------------------------

# Category → Mermaid node shape
_CLOUD_SHAPES: dict[str, tuple[str, str]] = {
    "database": ('[("`', '`")]'),       # cylinder
    "cache": ('[("`', '`")]'),          # cylinder
    "queue": ('[/"', '"/]'),            # parallelogram
    "topic": ('[/"', '"/]'),            # parallelogram
    "stream": ('[/"', '"/]'),           # parallelogram
    "storage": ('[["', '"]]'),          # subroutine
    "secret": ('(["', '"])'),           # stadium
    "other": ('["', '"]'),              # box
}


def cloud_infra_diagram(
    cloud_nodes: list[tuple[str, str, str, str, str]],
    module: str,
    *,
    direction: str = "LR",
) -> str:
    """Build a Mermaid flowchart of cloud resources for one module.

    Args:
        cloud_nodes: List of (service, category, client, caller, file) tuples
            for this module.
        module: Module label (for the empty-state message).
        direction: Mermaid direction (LR, TB).

    Returns:
        Mermaid flowchart source string.
    """
    if not cloud_nodes:
        msg = f"{module}: no cloud resources detected"
        return f'flowchart {direction}\n    empty["{_flow_label(msg)}"]'

    lines = [f"flowchart {direction}"]
    ids = _IdGen("c")

    # Deduplicate callers and resources
    callers: dict[str, str] = {}  # caller_name → mermaid_id
    resources: dict[str, tuple[str, str, str]] = {}  # key → (service, category, id)
    edges: list[tuple[str, str]] = []

    for service, category, _client, caller, _file in cloud_nodes:
        # Caller node (green handler style)
        if caller and caller != "<module>" and caller not in callers:
            cid = ids.get(f"caller:{caller}")
            callers[caller] = cid
            lines.append(f'    {cid}(["{_flow_label(caller)}"]):::handler')
        # Resource node (shaped by category)
        rkey = f"{service}:{category}"
        if rkey not in resources:
            rid = ids.get(f"res:{rkey}")
            resources[rkey] = (service, category, rid)
            lbl = _flow_label(f"{service}")
            open_s, close_s = _CLOUD_SHAPES.get(category, ('["', '"]'))
            lines.append(f"    {rid}{open_s}{lbl}{close_s}:::{category}")
        # Edge
        src = callers.get(caller)
        if src is None and caller == "<module>":
            if "<module>" not in callers:
                mid = ids.get("caller:module")
                callers["<module>"] = mid
                lines.append(f'    {mid}(["{_flow_label("(module-level)")}"]):::handler')
            src = callers["<module>"]
        if src:
            _, _, rid = resources[rkey]
            edges.append((src, rid))

    # Deduplicate edges
    seen: set[tuple[str, str]] = set()
    for s, t in edges:
        if (s, t) not in seen:
            seen.add((s, t))
            lines.append(f"    {s} --> {t}")

    # Style definitions
    lines.append("    classDef handler fill:#a6e3a1,stroke:#40a02b,color:#11111b")
    lines.append("    classDef database fill:#89b4fa,stroke:#1e66f5,color:#11111b")
    lines.append("    classDef cache fill:#a6e3a1,stroke:#40a02b,color:#11111b")
    lines.append("    classDef queue fill:#f9e2af,stroke:#df8e1d,color:#11111b")
    lines.append("    classDef topic fill:#f9e2af,stroke:#df8e1d,color:#11111b")
    lines.append("    classDef stream fill:#fab387,stroke:#fe640b,color:#11111b")
    lines.append("    classDef storage fill:#cba6f7,stroke:#8839ef,color:#11111b")
    lines.append("    classDef secret fill:#f38ba8,stroke:#d20f39,color:#11111b")
    lines.append("    classDef other fill:#9399b2,stroke:#6c7086,color:#11111b")

    return "\n".join(lines)


# endregion: --- Cloud infrastructure diagram


# ---------------------------------------------------------------------------
# region:    --- Internal helpers
# ---------------------------------------------------------------------------


def _prefix_of(path: str) -> str:
    """First two path segments, e.g. ``/api/users/{id}`` → ``/api/users``."""
    segs = [s for s in path.split("/") if s]
    if not segs:
        return "/"
    return "/" + "/".join(segs[:2])


def _short_path(url: str) -> str:
    """Trim a URL/path for an edge label."""
    u = re.sub(r"^[a-zA-Z]+://[^/]+", "", url).split("?", 1)[0]
    return (u or url)[:48]


# endregion: --- Internal helpers
