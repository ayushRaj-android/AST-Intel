"""HTML template for the Service Architecture Viewer (``architecture.html``).

A self-contained, dark-themed page with a left rail (module picker + view
tabs) and a main panel that renders either the interactive **vis.js** module
overview or a pre-built **Mermaid** diagram (UML / sequence / detail /
deployment).

The :class:`~ast_intel.formatters.graph_arch_html_formatter.ArchHtmlFormatter`
fills four placeholders via ``str.replace`` (not ``str.format``, so literal
``{`` / ``}`` in the CSS/JS below are fine):

- ``{title}``              — page title
- ``{arch_data}``          — the JSON architecture payload
- ``{vis_js_script}``      — vis-network ``<script>`` (CDN or inlined)
- ``{mermaid_js_script}``  — mermaid ``<script>`` (CDN or inlined)
"""

from __future__ import annotations

__all__: list[str] = ["ARCH_HTML_TEMPLATE"]

ARCH_HTML_TEMPLATE: str = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
{vis_js_script}
{mermaid_js_script}
<style>
  :root {
    --bg:#1e1e2e; --surface:#181825; --surface2:#11111b; --panel:#313244;
    --text:#cdd6f4; --subtext:#a6adc8; --muted:#6c7086;
    --accent:#89b4fa; --accent2:#f9e2af; --green:#a6e3a1; --red:#f38ba8;
    --border:#313244;
  }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; }
  body {
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg); color:var(--text); height:100vh; overflow:hidden;
  }
  #app { display:flex; height:100vh; }
  /* --- sidebar --- */
  #sidebar {
    width:300px; min-width:300px; background:var(--surface);
    border-right:1px solid var(--border); display:flex; flex-direction:column;
    padding:16px; gap:14px; overflow-y:auto;
  }
  #repo-name { font-size:16px; font-weight:700; word-break:break-word; }
  #mode-badge {
    display:inline-block; font-size:11px; background:var(--panel);
    color:var(--subtext); padding:2px 8px; border-radius:10px; margin-top:4px;
  }
  #stats { display:grid; grid-template-columns:1fr 1fr; gap:6px; font-size:12px; color:var(--subtext); }
  #stats b { color:var(--text); font-size:14px; }
  nav#tabs { display:flex; flex-direction:column; gap:4px; }
  nav#tabs button {
    text-align:left; background:transparent; color:var(--subtext);
    border:1px solid transparent; border-radius:8px; padding:9px 12px;
    font-size:13px; cursor:pointer; transition:all .12s;
  }
  nav#tabs button:hover { background:var(--panel); color:var(--text); }
  nav#tabs button.active {
    background:var(--accent); color:var(--surface2); font-weight:600;
  }
  #module-picker { display:none; }
  #module-picker label { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }
  #module-select {
    width:100%; margin-top:4px; background:var(--surface2); color:var(--text);
    border:1px solid var(--border); border-radius:8px; padding:8px; font-size:13px;
  }
  .hint { font-size:11px; color:var(--muted); line-height:1.5; }
  /* --- main --- */
  #main { flex:1; position:relative; overflow:hidden; }
  .view { position:absolute; inset:0; }
  .view[hidden] { display:none; }
  #vis { width:100%; height:100%; }
  #toolbar {
    position:absolute; top:10px; right:14px; z-index:5; display:flex; gap:8px;
  }
  #toolbar button {
    background:var(--panel); color:var(--text); border:1px solid var(--border);
    border-radius:7px; padding:6px 11px; font-size:12px; cursor:pointer;
  }
  #toolbar button:hover { background:var(--accent); color:var(--surface2); }
  #mermaid-scroll { width:100%; height:100%; overflow:auto; padding:24px; }
  #mermaid-container { display:flex; justify-content:center; transform-origin: top left; transition: transform 0.2s; }
  #mermaid-container svg { height:auto; min-width:800px; }
  .err { color:var(--red); padding:16px; }
  #zoom-controls { position:absolute; bottom:16px; right:16px; z-index:10; display:flex; gap:6px; }
  #zoom-controls button { width:36px; height:36px; border-radius:50%; border:1px solid var(--border); background:var(--panel); color:var(--text); font-size:18px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
  #zoom-controls button:hover { background:var(--accent); color:var(--surface2); }
  #mermaid-scroll pre {
    background:var(--surface2); color:var(--subtext); padding:14px;
    border-radius:8px; overflow:auto; font-size:12px; border:1px solid var(--border);
  }
  #view-title { position:absolute; top:12px; left:18px; z-index:5; font-size:13px; color:var(--muted); }
  textarea#current-src { display:none; }
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <div>
      <div id="repo-name">Architecture</div>
      <span id="mode-badge"></span>
    </div>
    <div id="stats"></div>
    <nav id="tabs">
      <button data-view="overview" class="active">&#9783; Overview map</button>
      <button data-view="api">&#9707; API (UML)</button>
      <button data-view="sequence">&#8644; Cross-service sequence</button>
      <button data-view="detail">&#10132; Request flow</button>
      <button data-view="cloud">&#9729; Cloud Infra</button>
      <button data-view="deployment">&#9729; Deployment</button>
    </nav>
    <div id="module-picker">
      <label>Module</label>
      <select id="module-select"></select>
    </div>
    <div class="hint" id="view-hint"></div>
    <div style="flex:1"></div>
    <div class="hint">Click a module in the overview to jump to its detail.</div>
  </aside>
  <main id="main">
    <div id="view-title"></div>
    <div id="toolbar">
      <button id="fit-btn">Fit</button>
      <button id="copy-btn">Copy Mermaid</button>
    </div>
    <div id="view-overview" class="view"><div id="vis"></div></div>
    <div id="view-mermaid" class="view" hidden>
      <div id="mermaid-scroll"><div id="mermaid-container"></div></div>
      <div id="zoom-controls">
        <button id="zoom-in" title="Zoom in">+</button>
        <button id="zoom-out" title="Zoom out">−</button>
        <button id="zoom-reset" title="Reset zoom">⟲</button>
      </div>
    </div>
  </main>
</div>
<textarea id="current-src"></textarea>
<script>
const ARCH = {arch_data};

const VIEW_HINTS = {
  overview: "Modules sized by symbol count. Solid edges = code dependencies (weight = call/import count); dashed = HTTP calls.",
  api: "UML class diagram of this module's controllers and their routes.",
  sequence: "Who calls whom across services. Async arrows (-->>) target External systems.",
  detail: "Request flow: each HTTP route traced through its handler into the methods it calls. Yellow = cross-module boundary, red = external HTTP.",
  cloud: "Cloud infrastructure (databases, caches, queues, storage, secrets) used by this module's code, with caller attribution.",
  deployment: "Infrastructure-as-Code resources (Docker / K8s / Helm) and their relationships."
};

let currentView = "overview";
let currentModule = (function(){
  const ms = ARCH.modules || [];
  if (!ms.length) return null;
  let best = ms[0];
  for (const m of ms){ if ((m.route_count||0) > (best.route_count||0)) best = m; }
  return best.label;
})();
let network = null;

function el(id){ return document.getElementById(id); }
function escapeHtml(s){ return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

mermaid.initialize({
  startOnLoad:false, theme:"dark", securityLevel:"loose",
  flowchart:{ useMaxWidth:false, htmlLabels:true, curve:"basis" },
  sequence:{ useMaxWidth:false },
  themeVariables:{ fontSize:"16px" }
});

function renderStats(){
  const s = ARCH.stats || {};
  el("repo-name").textContent = ARCH.repo || "Architecture";
  el("mode-badge").textContent = (ARCH.mode === "services" ? "merged services" : "single-repo modules");
  el("stats").innerHTML =
    '<div><b>' + (s.modules||0) + '</b> modules</div>' +
    '<div><b>' + (s.routes||0) + '</b> routes</div>' +
    '<div><b>' + (s.interactions||0) + '</b> interactions</div>' +
    '<div><b>' + (s.deploy_nodes||0) + '</b> infra nodes</div>';
}

function renderModulePicker(){
  const sel = el("module-select");
  let opts = '<option value="_all">All services (repo-wide)</option>';
  opts += (ARCH.modules||[]).map(m =>
    '<option value="' + escapeHtml(m.label) + '">' + escapeHtml(m.label) +
    (m.is_test ? " (test)" : "") + " &middot; " + (m.route_count||0) + " routes</option>"
  ).join("");
  sel.innerHTML = opts;
  if (currentModule) sel.value = currentModule;
  sel.onchange = () => {
    currentModule = sel.value;
    if (currentView === "api" || currentView === "detail" || currentView === "cloud") renderMermaid();
  };
}

function initVis(){
  const ov = (ARCH.overview) || {nodes:[], edges:[]};
  if (typeof vis === "undefined"){ el("vis").innerHTML = '<div class="err">vis-network failed to load.</div>'; return; }
  const data = { nodes:new vis.DataSet(ov.nodes), edges:new vis.DataSet(ov.edges) };
  network = new vis.Network(el("vis"), data, {
    physics:{ stabilization:{iterations:200}, barnesHut:{gravitationalConstant:-9000, springLength:170, springConstant:0.04} },
    nodes:{ shape:"dot", scaling:{min:10, max:46, label:{min:11, max:20}}, font:{color:"#cdd6f4", size:14} },
    edges:{ arrows:"to", color:{color:"#585b70", highlight:"#89b4fa"}, smooth:{type:"continuous"}, font:{color:"#9399b2", size:10, strokeWidth:0, background:"#1e1e2e"} },
    groups:{
      module:{ color:{background:"#89b4fa", border:"#1e66f5"}, font:{color:"#11111b"} },
      test:{ color:{background:"#45475a", border:"#6c7086"}, font:{color:"#bac2de"}, shapeProperties:{borderDashes:[4,3]} },
      external:{ shape:"diamond", color:{background:"#f9e2af", border:"#df8e1d"}, font:{color:"#11111b"} }
    },
    interaction:{ hover:true, tooltipDelay:120 }
  });
  network.on("click", params => {
    if (!params.nodes.length) return;
    const node = ov.nodes.find(n => n.id === params.nodes[0]);
    if (node && node.module){
      currentModule = node.module;
      el("module-select").value = currentModule;
      showView("detail");
    }
  });
}

async function renderMermaid(){
  const c = el("mermaid-container");
  let src = "";
  const d = ARCH.diagrams || {};
  if (currentView === "sequence") src = d.sequence || "";
  else if (currentView === "deployment") src = d.deployment || "";
  else if (currentView === "api") src = (d.class || {})[currentModule] || "classDiagram\n  class None[\"select a module\"]";
  else if (currentView === "detail") src = (d.detail || {})[currentModule] || "flowchart LR\n  none[\"select a module\"]";
  else if (currentView === "cloud") src = (d.cloud || {})[currentModule] || (d.cloud || {})["_all"] || "flowchart LR\n  none[\"no cloud resources detected\"]";
  el("current-src").value = src;
  try {
    const id = "mmd_" + Math.random().toString(36).slice(2);
    const { svg } = await mermaid.render(id, src);
    c.innerHTML = svg;
  } catch (e){
    c.innerHTML = '<div class="err">Diagram render error: ' + escapeHtml(e && e.message ? e.message : e) +
      '</div><pre>' + escapeHtml(src) + '</pre>';
  }
}

function showView(view){
  currentView = view;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  const isOverview = view === "overview";
  el("view-overview").hidden = !isOverview;
  el("view-mermaid").hidden = isOverview;
  el("module-picker").style.display = (view === "api" || view === "detail" || view === "cloud") ? "block" : "none";
  el("fit-btn").style.display = isOverview ? "inline-block" : "none";
  el("copy-btn").style.display = isOverview ? "none" : "inline-block";
  el("view-hint").textContent = VIEW_HINTS[view] || "";
  el("view-title").textContent = (view === "api" || view === "detail" || view === "cloud") && currentModule ? currentModule : "";
  if (isOverview){ if (network) network.fit(); }
  else renderMermaid();
}

renderStats();
renderModulePicker();
initVis();
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => showView(b.dataset.view));
el("fit-btn").onclick = () => { if (network) network.fit(); };
el("copy-btn").onclick = () => {
  const t = el("current-src");
  navigator.clipboard && navigator.clipboard.writeText(t.value);
  el("copy-btn").textContent = "Copied!";
  setTimeout(() => el("copy-btn").textContent = "Copy Mermaid", 1200);
};

// --- Zoom controls for Mermaid diagrams ---
let zoomLevel = 1;
const zoomStep = 0.25;
const zoomMin = 0.3;
const zoomMax = 4;
function applyZoom() {
  const c = el("mermaid-container");
  c.style.transform = "scale(" + zoomLevel + ")";
}
el("zoom-in").onclick = () => { zoomLevel = Math.min(zoomMax, zoomLevel + zoomStep); applyZoom(); };
el("zoom-out").onclick = () => { zoomLevel = Math.max(zoomMin, zoomLevel - zoomStep); applyZoom(); };
el("zoom-reset").onclick = () => { zoomLevel = 1; applyZoom(); };

// Mouse wheel zoom on mermaid area
el("mermaid-scroll").addEventListener("wheel", (e) => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    zoomLevel = Math.max(zoomMin, Math.min(zoomMax, zoomLevel + (e.deltaY < 0 ? zoomStep : -zoomStep)));
    applyZoom();
  }
}, { passive: false });

showView("overview");
</script>
</body>
</html>
"""
