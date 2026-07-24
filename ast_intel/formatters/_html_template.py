"""HTML template for the interactive code-graph visualization.

This module contains the complete HTML/CSS/JavaScript for a self-contained
force-directed graph viewer.  The formatter injects serialized graph data
into the ``{graph_data}``, ``{vis_js_script}``, and ``{title}`` placeholders
before writing the final HTML file.
"""

from __future__ import annotations

__all__: list[str] = ["HTML_TEMPLATE"]

# ---------------------------------------------------------------------------
# The template intentionally uses single braces for JS code.  Python
# placeholders use the pattern ``{graph_data}`` etc. — they are replaced
# by the formatter with ``str.replace()``, **not** ``str.format()``.
# ---------------------------------------------------------------------------

HTML_TEMPLATE: str = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{vis_js_script}
<style>
/* ------------------------------------------------------------------ */
/* region:    --- CSS Reset & Variables                                */
/* ------------------------------------------------------------------ */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #1e1e2e;
  --surface:   #292940;
  --surface2:  #33334d;
  --border:    #44446a;
  --text:      #cdd6f4;
  --text-dim:  #8888aa;
  --accent:    #89b4fa;
  --accent2:   #a6e3a1;
  --danger:    #f38ba8;
  --warn:      #f9e2af;
  --radius:    6px;
  --font:      'Segoe UI', system-ui, -apple-system, sans-serif;
  --mono:      'Cascadia Code', 'Fira Code', 'Consolas', monospace;

  /* Node kind colours */
  --c-crate:            #f9e2af;
  --c-file:             #89b4fa;
  --c-struct:           #a6e3a1;
  --c-enum:             #cba6f7;
  --c-trait:            #fab387;
  --c-function:         #74c7ec;
  --c-method:           #89dceb;
  --c-impl_block:       #94e2d5;
  --c-type_alias:       #b4befe;
  --c-constant:         #f5c2e7;
  --c-macro:            #eba0ac;
  --c-module:           #a6adc8;
  --c-rationale:        #6c7086;
  --c-import:           #585b70;
  --c-external_method:  #7f849c;
  --c-route:            #f38ba8;
  --c-http_call:        #fab387;
}

body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* ------------------------------------------------------------------ */
/* endregion: --- CSS Reset & Variables                                */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* region:    --- Left Panel (Controls)                                */
/* ------------------------------------------------------------------ */
#controls {
  width: 260px;
  min-width: 260px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 12px;
  gap: 12px;
}

#controls h2 {
  font-size: 14px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 4px;
}

/* Scope selector dropdown */
#scope-selector {
  width: 100%;
  padding: 7px 10px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 13px;
  outline: none;
  cursor: pointer;
}
#scope-selector:focus { border-color: var(--accent); }
#scope-selector option { background: var(--surface2); color: var(--text); }

/* Breadcrumb navigation */
#breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  font-size: 12px;
  min-height: 24px;
}
#breadcrumb .crumb {
  color: var(--accent);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 3px;
  white-space: nowrap;
}
#breadcrumb .crumb:hover { background: var(--surface2); }
#breadcrumb .crumb.current {
  color: var(--text);
  cursor: default;
  font-weight: 600;
}
#breadcrumb .crumb.current:hover { background: transparent; }
#breadcrumb .sep { color: var(--text-dim); font-size: 10px; }

#search {
  width: 100%;
  padding: 8px 10px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 13px;
  outline: none;
}
#search:focus { border-color: var(--accent); }
#search::placeholder { color: var(--text-dim); }

.filter-section { margin-bottom: 8px; }
.filter-section summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  user-select: none;
  padding: 4px 0;
}
.filter-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 8px;
  margin-top: 4px;
}
.filter-grid label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.filter-grid input[type="checkbox"] {
  accent-color: var(--accent);
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.color-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.btn {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface2);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  text-align: center;
}
.btn:hover { background: var(--border); }
.btn.active { border-color: var(--accent); color: var(--accent); }

.btn-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* Spacing slider */
.slider-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0;
}
.slider-row label {
  font-size: 11px;
  color: var(--text-dim);
  white-space: nowrap;
  min-width: 52px;
}
.slider-row input[type=range] {
  flex: 1;
  accent-color: var(--accent);
  height: 4px;
  cursor: pointer;
}
.slider-row .slider-val {
  font-size: 10px;
  color: var(--text-dim);
  font-family: var(--mono);
  min-width: 20px;
  text-align: right;
}

/* Stats bar */
#stats {
  font-size: 11px;
  color: var(--text-dim);
  padding: 4px 0;
  border-top: 1px solid var(--border);
}

/* ------------------------------------------------------------------ */
/* endregion: --- Left Panel (Controls)                               */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* region:    --- Graph Canvas                                        */
/* ------------------------------------------------------------------ */
#graph-container {
  flex: 1;
  position: relative;
  background: var(--bg);
}

#loading {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: var(--accent);
  font-size: 16px;
}

/* ------------------------------------------------------------------ */
/* endregion: --- Graph Canvas                                        */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* region:    --- Right Panel (Detail Sidebar)                        */
/* ------------------------------------------------------------------ */
#detail {
  width: 320px;
  min-width: 320px;
  background: var(--surface);
  border-left: 1px solid var(--border);
  overflow-y: auto;
  padding: 12px;
  display: none;  /* shown on node click */
}
#detail.open { display: block; }

#detail h3 {
  font-size: 15px;
  color: var(--accent);
  margin-bottom: 8px;
  word-break: break-all;
}
#detail .meta-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--border);
}
#detail .meta-row .key { color: var(--text-dim); }
#detail .meta-row .val { color: var(--text); font-family: var(--mono); font-size: 11px; }

#detail h4 {
  font-size: 12px;
  color: var(--accent2);
  margin: 12px 0 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
#detail ul {
  list-style: none;
  font-size: 12px;
}
#detail ul li {
  padding: 2px 0;
  cursor: pointer;
  color: var(--text);
}
#detail ul li:hover { color: var(--accent); }
#detail ul li .rel-tag {
  color: var(--text-dim);
  font-size: 10px;
  margin-left: 4px;
}

#close-detail {
  position: absolute;
  top: 8px;
  right: 8px;
  background: none;
  border: none;
  color: var(--text-dim);
  font-size: 18px;
  cursor: pointer;
}
#close-detail:hover { color: var(--text); }

/* ------------------------------------------------------------------ */
/* endregion: --- Right Panel (Detail Sidebar)                        */
/* ------------------------------------------------------------------ */
</style>
</head>
<body>

<!-- ================================================================ -->
<!-- Left Panel: Controls                                             -->
<!-- ================================================================ -->
<div id="controls">
  <h2>AST Intel</h2>

  <select id="scope-selector" title="Jump to a crate or file">
    <option value="">All crates</option>
  </select>

  <div id="breadcrumb"></div>

  <input id="search" type="text" placeholder="Search symbols…" autocomplete="off">

  <details class="filter-section" open>
    <summary>Node kinds</summary>
    <div id="kind-filters" class="filter-grid"></div>
  </details>

  <details class="filter-section">
    <summary>Edge relations</summary>
    <div id="edge-filters" class="filter-grid"></div>
  </details>

  <div class="btn-row">
    <button class="btn" id="btn-physics" title="Toggle physics simulation">Physics</button>
    <button class="btn" id="btn-fit" title="Zoom to fit all nodes">Fit</button>
    <button class="btn" id="btn-cluster" title="Toggle file clustering">Cluster</button>
  </div>

  <div class="slider-row">
    <label for="spacing-slider">Spacing</label>
    <input type="range" id="spacing-slider" min="1" max="10" value="5" step="1">
    <span class="slider-val" id="spacing-val">5</span>
  </div>

  <div id="stats"></div>
</div>

<!-- ================================================================ -->
<!-- Graph Canvas                                                     -->
<!-- ================================================================ -->
<div id="graph-container">
  <div id="loading">Loading graph…</div>
</div>

<!-- ================================================================ -->
<!-- Right Panel: Node Detail                                         -->
<!-- ================================================================ -->
<div id="detail">
  <button id="close-detail">&times;</button>
  <div id="detail-content"></div>
</div>

<!-- ================================================================ -->
<!-- Application JavaScript                                           -->
<!-- ================================================================ -->
<script>
/* global vis */
(function () {
"use strict";

// ------------------------------------------------------------------ //
// region:    --- Graph Data (injected by formatter)                   //
// ------------------------------------------------------------------ //
const RAW = {graph_data};
// endregion

// ------------------------------------------------------------------ //
// region:    --- Colour & Shape Maps                                  //
// ------------------------------------------------------------------ //
const KIND_COLORS = {
  crate:           "#f9e2af",
  file:            "#89b4fa",
  struct:          "#a6e3a1",
  enum:            "#cba6f7",
  trait:           "#fab387",
  function:        "#74c7ec",
  method:          "#89dceb",
  impl_block:      "#94e2d5",
  type_alias:      "#b4befe",
  constant:        "#f5c2e7",
  macro:           "#eba0ac",
  module:          "#a6adc8",
  rationale:       "#6c7086",
  import:          "#585b70",
  external_method: "#7f849c",
  route:           "#f38ba8",
  http_call:       "#fab387",
  sdk_call:        "#cba6f7",
  service:         "#f5e0dc",
  // IaC: Kubernetes
  k8s_deployment:  "#74c7ec",
  k8s_service:     "#89dceb",
  k8s_configmap:   "#94e2d5",
  k8s_secret:      "#f38ba8",
  k8s_ingress:     "#89b4fa",
  k8s_namespace:   "#b4befe",
  k8s_generic:     "#9399b2",
  // IaC: Helm
  helm_chart:      "#f9e2af",
  helm_value:      "#f5c2e7",
  helm_template:   "#cba6f7",
  // IaC: Docker
  docker_image:    "#a6e3a1",
  docker_stage:    "#94e2d5",
  docker_service:  "#89dceb",
  // IaC: Ansible
  ansible_playbook: "#f9e2af",
  ansible_task:     "#fab387",
  ansible_role:     "#cba6f7",
  // IaC: Kubernetes (expanded)
  k8s_cronjob:      "#89dceb",
  k8s_job:          "#74c7ec",
  k8s_pvc:          "#94e2d5",
  k8s_rbac:         "#f5c2e7",
  // IaC: CI/CD
  ci_pipeline:       "#2196F3",
  ci_stage:          "#42A5F5",
  ci_job:            "#64B5F6",
  ci_step:           "#90CAF9",
  // IaC: Terraform
  tf_resource:       "#fab387",
  tf_data:           "#f9e2af",
  tf_module:         "#f5c2e7",
  tf_variable:       "#eba0ac",
  tf_output:         "#f2cdcd",
  tf_provider:       "#f5e0dc",
  // Cloud Infrastructure
  cloud_resource:    "#89b4fa"
};

const KIND_SHAPES = {
  crate:           "diamond",
  file:            "box",
  struct:          "box",
  enum:            "triangle",
  trait:           "hexagon",
  function:        "dot",
  method:          "dot",
  impl_block:      "square",
  type_alias:      "triangleDown",
  constant:        "star",
  macro:           "star",
  module:          "square",
  rationale:       "text",
  import:          "text",
  external_method: "text",
  route:           "hexagon",
  http_call:       "triangleDown",
  sdk_call:        "triangleDown",
  service:         "star",
  // IaC: Kubernetes
  k8s_deployment:  "box",
  k8s_service:     "hexagon",
  k8s_configmap:   "square",
  k8s_secret:      "star",
  k8s_ingress:     "hexagon",
  k8s_namespace:   "diamond",
  k8s_generic:     "dot",
  // IaC: Helm
  helm_chart:      "diamond",
  helm_value:      "dot",
  helm_template:   "box",
  // IaC: Docker
  docker_image:    "box",
  docker_stage:    "square",
  docker_service:  "hexagon",
  // IaC: Ansible
  ansible_playbook: "diamond",
  ansible_task:     "dot",
  ansible_role:     "hexagon",
  // IaC: Kubernetes (expanded)
  k8s_cronjob:      "star",
  k8s_job:          "triangleDown",
  k8s_pvc:          "square",
  k8s_rbac:         "star",
  // IaC: CI/CD
  ci_pipeline:       "hexagon",
  ci_stage:          "hexagon",
  ci_job:            "box",
  ci_step:           "box",
  // IaC: Terraform
  tf_resource:       "box",
  tf_data:           "triangleDown",
  tf_module:         "diamond",
  tf_variable:       "dot",
  tf_output:         "dot",
  tf_provider:       "hexagon"
};

const EDGE_COLORS = {
  contains:      "#888888",
  method_of:     "#4444aa",
  implements:    "#00aa00",
  inherits:      "#00aa00",
  imports:       "#aa8800",
  calls:         "#cc0000",
  uses_method:   "#aa4400",
  depends_on:    "#0000cc",
  super_trait:   "#00aa00",
  rationale_for: "#666666",
  has_field:     "#666688",
  resolves_to:   "#888888",
  similar_to:    "#cba6f7",
  handles:       "#f38ba8",
  exposes:       "#f38ba8",
  calls_http:    "#fab387",
  sends_data:    "#cba6f7",
  belongs_to:    "#585b70",
  calls_service: "#f5c2e7",
  // IaC: Kubernetes
  routes_to:        "#89dceb",
  configures:       "#94e2d5",
  uses_secret:      "#f38ba8",
  deploys:          "#74c7ec",
  references_image: "#a6e3a1",
  // IaC: Helm
  templates_to:    "#cba6f7",
  value_of:        "#f5c2e7",
  // IaC: Docker
  builds_image:    "#a6e3a1",
  exposes_port:    "#f38ba8",
  image_of:        "#89dceb",
  copies_from:     "#94e2d5",
  // IaC: Ansible
  runs_task:         "#fab387",
  uses_role:         "#cba6f7",
  notifies_handler:  "#f38ba8",
  imports_playbook:  "#f9e2af",
  // IaC: CI/CD
  triggers:          "#FF9800",
  uses_template:     "#9C27B0",
  // IaC: Cross-domain
  deploys_via:       "#74c7ec",
  renders_to:        "#cba6f7",
  // IaC: Terraform
  provisions:        "#fab387",
  // Cloud Infrastructure
  uses_resource:     "#89b4fa",
  backed_by:         "#94e2d5"
};

const DASHED = new Set([
  "similar_to", "rationale_for", "belongs_to", "configures",
  "uses_secret", "value_of", "image_of",
  "notifies_handler", "imports_playbook", "uses_template"
]);

// Node kinds that represent symbols (leaf level)
const SYMBOL_KINDS = new Set([
  "struct", "enum", "trait", "function", "method", "impl_block",
  "type_alias", "constant", "macro", "module", "rationale",
  "import", "external_method", "route", "http_call",
  "k8s_deployment", "k8s_service", "k8s_configmap", "k8s_secret",
  "k8s_ingress", "k8s_namespace", "k8s_generic",
  "k8s_cronjob", "k8s_job", "k8s_pvc", "k8s_rbac",
  "helm_chart", "helm_value", "helm_template",
  "docker_image", "docker_stage", "docker_service",
  "ansible_playbook", "ansible_task", "ansible_role",
  "ci_pipeline", "ci_stage", "ci_job", "ci_step"
]);
// endregion

// ------------------------------------------------------------------ //
// region:    --- Hierarchical mode detection                          //
// ------------------------------------------------------------------ //
var HIERARCHICAL = RAW.nodes.length > 500;
// endregion

// ------------------------------------------------------------------ //
// region:    --- Build indexes for hierarchical navigation             //
// ------------------------------------------------------------------ //
var rawNodeMap = {};
var crateNames = [];
var crateToFiles = {};
var fileToSymbols = {};
var nodeIdToCrate = {};
var nodeIdToFile = {};
var fileToCrate = {};

RAW.nodes.forEach(function (n) {
  rawNodeMap[n.id] = n;
  var kind = n.group || n.kind || "";
  if (kind === "crate") {
    crateNames.push(n.label || n.id);
  }
});
crateNames.sort();

// Map files to crates via "contains" edges from crate -> file
RAW.edges.forEach(function (e) {
  var src = rawNodeMap[e.from];
  var tgt = rawNodeMap[e.to];
  if (!src || !tgt) return;
  var srcKind = src.group || src.kind || "";
  var tgtKind = tgt.group || tgt.kind || "";
  if (srcKind === "crate" && tgtKind === "file") {
    var crateName = src.label || src.id;
    fileToCrate[tgt.file || tgt.id] = crateName;
    fileToCrate[tgt.id] = crateName;
  }
});

// Build crateToFiles and fileToSymbols
RAW.nodes.forEach(function (n) {
  var kind = n.group || n.kind || "";
  var file = n.file || "";
  if (kind === "file") {
    var crate = fileToCrate[file] || fileToCrate[n.id] || "__unknown__";
    if (!crateToFiles[crate]) crateToFiles[crate] = [];
    crateToFiles[crate].push(file || n.id);
    nodeIdToCrate[n.id] = crate;
    nodeIdToFile[n.id] = file || n.id;
  } else if (kind === "crate") {
    nodeIdToCrate[n.id] = n.label || n.id;
  } else if (SYMBOL_KINDS.has(kind)) {
    if (file) {
      if (!fileToSymbols[file]) fileToSymbols[file] = [];
      fileToSymbols[file].push(n.id);
      nodeIdToFile[n.id] = file;
      var c = fileToCrate[file];
      if (c) nodeIdToCrate[n.id] = c;
    }
  }
});

// Sort file lists
Object.keys(crateToFiles).forEach(function (c) {
  crateToFiles[c].sort();
});
// endregion

// ------------------------------------------------------------------ //
// region:    --- Build aggregated edges for each view level            //
// ------------------------------------------------------------------ //
function buildCrateEdges() {
  var pairCount = {};
  RAW.edges.forEach(function (e) {
    var rel = e.relation || "";
    if (rel === "contains" || rel === "method_of" || rel === "has_field") return;
    var srcCrate = nodeIdToCrate[e.from];
    var tgtCrate = nodeIdToCrate[e.to];
    if (!srcCrate || !tgtCrate || srcCrate === tgtCrate) return;
    var key = srcCrate + ">>>" + tgtCrate;
    pairCount[key] = (pairCount[key] || 0) + 1;
  });
  var edges = [];
  var idx = 0;
  Object.keys(pairCount).forEach(function (key) {
    var parts = key.split(">>>");
    edges.push({
      id: "ce" + idx++,
      from: "crate:" + parts[0],
      to: "crate:" + parts[1],
      label: String(pairCount[key]),
      title: parts[0] + " \u2192 " + parts[1] + " (" + pairCount[key] + " edges)",
      color: { color: "#555", highlight: "#fff", opacity: 0.7 },
      arrows: "to",
      width: Math.min(Math.max(1, Math.log2(pairCount[key])), 6),
      font: { color: "#888", size: 10, strokeWidth: 0 },
      smooth: { type: "continuous" },
      _rel: "aggregated"
    });
  });
  return edges;
}

function buildFileEdges(crateName) {
  var pairCount = {};
  var files = new Set(crateToFiles[crateName] || []);
  RAW.edges.forEach(function (e) {
    var rel = e.relation || "";
    if (rel === "contains" || rel === "method_of" || rel === "has_field") return;
    var srcFile = nodeIdToFile[e.from];
    var tgtFile = nodeIdToFile[e.to];
    if (!srcFile || !tgtFile || srcFile === tgtFile) return;
    if (!files.has(srcFile) && !files.has(tgtFile)) return;
    var key = srcFile + ">>>" + tgtFile;
    pairCount[key] = (pairCount[key] || 0) + 1;
  });
  var edges = [];
  var idx = 0;
  Object.keys(pairCount).forEach(function (key) {
    var parts = key.split(">>>");
    edges.push({
      id: "fe" + idx++,
      from: "file:" + parts[0],
      to: "file:" + parts[1],
      label: String(pairCount[key]),
      title: parts[0].split("/").pop() + " \u2192 " + parts[1].split("/").pop() +
             " (" + pairCount[key] + " edges)",
      color: { color: "#555", highlight: "#fff", opacity: 0.7 },
      arrows: "to",
      width: Math.min(Math.max(1, Math.log2(pairCount[key])), 6),
      font: { color: "#888", size: 10, strokeWidth: 0 },
      smooth: { type: "continuous" },
      _rel: "aggregated"
    });
  });
  return edges;
}

function buildSymbolEdges(filePath) {
  var symbolSet = new Set(fileToSymbols[filePath] || []);
  var edges = [];
  var idx = 0;
  RAW.edges.forEach(function (e) {
    if (!symbolSet.has(e.from) && !symbolSet.has(e.to)) return;
    var rel = e.relation || "";
    edges.push({
      id: "se" + idx++,
      from: e.from,
      to: e.to,
      label: "",
      title: rel,
      color: { color: EDGE_COLORS[rel] || "#555", highlight: "#fff", opacity: 0.6 },
      arrows: "to",
      dashes: DASHED.has(rel),
      _rel: rel
    });
  });
  return edges;
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- View state                                           //
// ------------------------------------------------------------------ //
var viewLevel = HIERARCHICAL ? "crates" : "flat";
var viewScope = { crate: null, file: null };

var network;
var nodeDS = new vis.DataSet();
var edgeDS = new vis.DataSet();
// endregion

// ------------------------------------------------------------------ //
// region:    --- Flat mode data (small graphs)                        //
// ------------------------------------------------------------------ //
var allNodesFlat = [];
var allEdgesFlat = [];

if (!HIERARCHICAL) {
  allNodesFlat = RAW.nodes.map(function (n) {
    var kind = n.group || n.kind || "";
    return {
      id:    n.id,
      label: n.label,
      group: kind,
      title: _tooltip(n),
      shape: KIND_SHAPES[kind] || "dot",
      color: {
        background: KIND_COLORS[kind] || "#888",
        border:     KIND_COLORS[kind] || "#888",
        highlight:  { background: "#fff", border: KIND_COLORS[kind] || "#888" }
      },
      font: { color: "#cdd6f4", size: 11 },
      _file: n.file || "",
      _kind: kind,
      _props: n.properties || {}
    };
  });

  allEdgesFlat = RAW.edges.map(function (e, i) {
    var rel = e.relation || "";
    return {
      id:     "e" + i,
      from:   e.from,
      to:     e.to,
      label:  "",
      title:  rel,
      color:  { color: EDGE_COLORS[rel] || "#555", highlight: "#fff", opacity: 0.6 },
      arrows: "to",
      dashes: DASHED.has(rel),
      _rel:   rel
    };
  });
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Render views                                         //
// ------------------------------------------------------------------ //
function renderCrateView() {
  viewLevel = "crates";
  viewScope = { crate: null, file: null };
  hideDetail();

  var nodes = crateNames.map(function (name) {
    var fileCount = (crateToFiles[name] || []).length;
    return {
      id: "crate:" + name,
      label: name + "\n(" + fileCount + " files)",
      group: "crate",
      title: name + ": " + fileCount + " files\nClick to expand",
      shape: "diamond",
      size: 18 + Math.min(fileCount, 12),
      color: {
        background: KIND_COLORS.crate,
        border: KIND_COLORS.crate,
        highlight: { background: "#fff", border: KIND_COLORS.crate }
      },
      font: { color: "#cdd6f4", size: 14, bold: { color: KIND_COLORS.crate } },
      _kind: "crate",
      _file: "",
      _props: { files: String(fileCount) }
    };
  });

  var edges = buildCrateEdges();

  nodeDS.clear();
  edgeDS.clear();
  nodeDS.add(nodes);
  edgeDS.add(edges);

  updateBreadcrumb();
  updateScopeSelector();
  updateStats(nodes.length, edges.length);
  resetFilters();
  fitAfterRender();
}

function renderFileView(crateName) {
  viewLevel = "files";
  viewScope = { crate: crateName, file: null };
  hideDetail();

  var files = crateToFiles[crateName] || [];
  var nodes = files.map(function (f) {
    var symbolCount = (fileToSymbols[f] || []).length;
    var shortName = f.split("/").pop();
    return {
      id: "file:" + f,
      label: shortName + "\n(" + symbolCount + ")",
      group: "file",
      title: f + ": " + symbolCount + " symbols\nClick to expand",
      shape: "box",
      size: 18 + Math.min(symbolCount, 20),
      color: {
        background: KIND_COLORS.file,
        border: KIND_COLORS.file,
        highlight: { background: "#fff", border: KIND_COLORS.file }
      },
      font: { color: "#cdd6f4", size: 12 },
      _kind: "file",
      _file: f,
      _props: { symbols: String(symbolCount) }
    };
  });

  var edges = buildFileEdges(crateName);

  nodeDS.clear();
  edgeDS.clear();
  nodeDS.add(nodes);
  edgeDS.add(edges);

  updateBreadcrumb();
  updateScopeSelector();
  updateStats(nodes.length, edges.length);
  resetFilters();
  fitAfterRender();
}

function renderSymbolView(filePath) {
  var crateName = fileToCrate[filePath] || viewScope.crate || "";
  viewLevel = "symbols";
  viewScope = { crate: crateName, file: filePath };
  hideDetail();

  var symbolIds = fileToSymbols[filePath] || [];
  var connectedExternal = new Set();
  var symbolSet = new Set(symbolIds);

  RAW.edges.forEach(function (e) {
    if (symbolSet.has(e.from) && !symbolSet.has(e.to)) connectedExternal.add(e.to);
    if (symbolSet.has(e.to) && !symbolSet.has(e.from)) connectedExternal.add(e.from);
  });

  var nodeIds = symbolIds.concat(Array.from(connectedExternal));
  var nodes = nodeIds.map(function (id) {
    var n = rawNodeMap[id];
    if (!n) return null;
    var kind = n.group || n.kind || "";
    var isExternal = connectedExternal.has(id);
    return {
      id: n.id,
      label: n.label,
      group: kind,
      title: _tooltip(n),
      shape: KIND_SHAPES[kind] || "dot",
      color: {
        background: isExternal ? "#444" : (KIND_COLORS[kind] || "#888"),
        border: KIND_COLORS[kind] || "#888",
        highlight: { background: "#fff", border: KIND_COLORS[kind] || "#888" }
      },
      font: { color: isExternal ? "#777" : "#cdd6f4", size: 12 },
      opacity: isExternal ? 0.5 : 1.0,
      _file: n.file || "",
      _kind: kind,
      _props: n.properties || {}
    };
  }).filter(Boolean);

  var edges = buildSymbolEdges(filePath);

  nodeDS.clear();
  edgeDS.clear();
  nodeDS.add(nodes);
  edgeDS.add(edges);

  updateBreadcrumb();
  updateScopeSelector();
  updateStats(nodes.length, edges.length);
  resetFilters();
  fitAfterRender();
}

function renderFlatView() {
  viewLevel = "flat";
  viewScope = { crate: null, file: null };
  hideDetail();

  nodeDS.clear();
  edgeDS.clear();
  nodeDS.add(allNodesFlat);
  edgeDS.add(allEdgesFlat);

  updateBreadcrumb();
  updateStats(allNodesFlat.length, allEdgesFlat.length);
  resetFilters();
  fitAfterRender();
}

var spacingMultiplier = 1.0;

function getSpacingMultiplier() {
  return spacingMultiplier;
}

function applyViewPhysics() {
  if (!network) return;
  var s = getSpacingMultiplier();
  var p;
  if (viewLevel === "crates") {
    p = { solver: "repulsion", repulsion: {
      nodeDistance: Math.round(300 * s),
      centralGravity: 0.05,
      springLength: Math.round(400 * s),
      springConstant: 0.005,
      damping: 0.09
    }, stabilization: { iterations: 400, fit: true } };
  } else if (viewLevel === "files") {
    p = { solver: "repulsion", repulsion: {
      nodeDistance: Math.round(200 * s),
      centralGravity: 0.1,
      springLength: Math.round(280 * s),
      springConstant: 0.008,
      damping: 0.09
    }, stabilization: { iterations: 300, fit: true } };
  } else {
    p = { solver: "barnesHut", barnesHut: {
      gravitationalConstant: Math.round(-8000 * s),
      centralGravity: 0.15,
      springLength: Math.round(180 * s),
      springConstant: 0.015,
      avoidOverlap: 0.5
    }, stabilization: { iterations: 200, fit: true } };
  }
  p.enabled = true;
  network.setOptions({ physics: p });
  network.stabilize();
}

function fitAfterRender() {
  if (!network) return;
  applyViewPhysics();
  network.once("stabilizationIterationsDone", function () {
    network.fit({ animation: { duration: 400 } });
  });
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Network Initialisation                               //
// ------------------------------------------------------------------ //
function initNetwork() {
  var container = document.getElementById("graph-container");
  document.getElementById("loading").style.display = "none";

  var options = {
    physics: {
      solver: "repulsion",
      repulsion: {
        nodeDistance: 300,
        centralGravity: 0.05,
        springLength: 400,
        springConstant: 0.005,
        damping: 0.09
      },
      stabilization: { iterations: 400, fit: true }
    },
    interaction: {
      hover: true,
      tooltipDelay: 100,
      multiselect: false,
      navigationButtons: false,
      keyboard: false
    },
    edges: {
      smooth: { type: "continuous" },
      width: 0.8,
      hoverWidth: 2,
      selectionWidth: 2.5,
      font: { size: 10, color: "#888", strokeWidth: 0 }
    },
    nodes: {
      borderWidth: 1.5,
      borderWidthSelected: 3,
      size: 16
    },
    layout: { improvedLayout: true }
  };

  network = new vis.Network(container, { nodes: nodeDS, edges: edgeDS }, options);

  network.on("click", function (params) {
    if (params.nodes.length !== 1) { hideDetail(); return; }
    var nodeId = params.nodes[0];

    if (network.isCluster(nodeId)) {
      network.openCluster(nodeId);
      return;
    }

    var node = nodeDS.get(nodeId);
    if (!node) return;

    if (HIERARCHICAL) {
      if (viewLevel === "crates" && typeof nodeId === "string" && nodeId.startsWith("crate:")) {
        renderFileView(nodeId.substring(6));
        return;
      }
      if (viewLevel === "files" && typeof nodeId === "string" && nodeId.startsWith("file:")) {
        renderSymbolView(nodeId.substring(5));
        return;
      }
    }

    showDetail(nodeId);
  });

  network.on("doubleClick", function (params) {
    if (params.nodes.length === 1) {
      var nodeId = params.nodes[0];
      if (network.isCluster(nodeId)) {
        network.openCluster(nodeId);
      }
    }
  });

  if (HIERARCHICAL) {
    renderCrateView();
  } else {
    renderFlatView();
  }
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Breadcrumb Navigation                                //
// ------------------------------------------------------------------ //
var breadcrumbEl = document.getElementById("breadcrumb");

function updateBreadcrumb() {
  breadcrumbEl.innerHTML = "";

  if (!HIERARCHICAL) {
    var flat = document.createElement("span");
    flat.className = "crumb current";
    flat.textContent = "All nodes";
    breadcrumbEl.appendChild(flat);
    return;
  }

  // Root crumb
  var root = document.createElement("span");
  root.className = "crumb" + (viewLevel === "crates" ? " current" : "");
  root.textContent = "Crates";
  if (viewLevel !== "crates") {
    root.addEventListener("click", function () { renderCrateView(); });
  }
  breadcrumbEl.appendChild(root);

  if (viewScope.crate) {
    breadcrumbEl.appendChild(_makeSep());
    var crateCrumb = document.createElement("span");
    crateCrumb.className = "crumb" + (viewLevel === "files" ? " current" : "");
    crateCrumb.textContent = viewScope.crate;
    if (viewLevel !== "files") {
      crateCrumb.addEventListener("click", function () {
        renderFileView(viewScope.crate);
      });
    }
    breadcrumbEl.appendChild(crateCrumb);
  }

  if (viewScope.file) {
    breadcrumbEl.appendChild(_makeSep());
    var fileCrumb = document.createElement("span");
    fileCrumb.className = "crumb current";
    fileCrumb.textContent = viewScope.file.split("/").pop();
    fileCrumb.title = viewScope.file;
    breadcrumbEl.appendChild(fileCrumb);
  }
}

function _makeSep() {
  var sep = document.createElement("span");
  sep.className = "sep";
  sep.textContent = "\u203a";
  return sep;
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Scope Selector                                       //
// ------------------------------------------------------------------ //
var scopeSelector = document.getElementById("scope-selector");

function updateScopeSelector() {
  if (!HIERARCHICAL) {
    scopeSelector.style.display = "none";
    return;
  }
  scopeSelector.style.display = "";
  scopeSelector.innerHTML = "";

  var allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = "All crates (" + crateNames.length + ")";
  scopeSelector.appendChild(allOpt);

  crateNames.forEach(function (c) {
    var group = document.createElement("optgroup");
    group.label = c;

    var crateOpt = document.createElement("option");
    crateOpt.value = "crate:" + c;
    crateOpt.textContent = c + " (crate)";
    if (viewLevel === "files" && viewScope.crate === c && !viewScope.file) {
      crateOpt.selected = true;
    }
    group.appendChild(crateOpt);

    var files = crateToFiles[c] || [];
    files.forEach(function (f) {
      var fOpt = document.createElement("option");
      fOpt.value = "file:" + f;
      fOpt.textContent = "\u00a0\u00a0" + f.split("/").pop();
      fOpt.title = f;
      if (viewLevel === "symbols" && viewScope.file === f) {
        fOpt.selected = true;
      }
      group.appendChild(fOpt);
    });

    scopeSelector.appendChild(group);
  });
}

scopeSelector.addEventListener("change", function () {
  var val = scopeSelector.value;
  if (!val) {
    renderCrateView();
  } else if (val.startsWith("crate:")) {
    renderFileView(val.substring(6));
  } else if (val.startsWith("file:")) {
    renderSymbolView(val.substring(5));
  }
});
// endregion

// ------------------------------------------------------------------ //
// region:    --- Search                                               //
// ------------------------------------------------------------------ //
var searchInput = document.getElementById("search");
searchInput.addEventListener("input", function () {
  var q = searchInput.value.toLowerCase().trim();
  var currentNodes = nodeDS.get();
  if (!q) {
    var updates = [];
    currentNodes.forEach(function (n) {
      updates.push({ id: n.id, opacity: 1.0, font: { color: "#cdd6f4" } });
    });
    nodeDS.update(updates);
    return;
  }
  var matches = [];
  var updates = [];
  currentNodes.forEach(function (n) {
    var label = (n.label || "").toLowerCase();
    var id = String(n.id || "").toLowerCase();
    if (label.indexOf(q) !== -1 || id.indexOf(q) !== -1) {
      matches.push(n.id);
      updates.push({ id: n.id, opacity: 1.0, font: { color: "#fff", size: 14 } });
    } else {
      updates.push({ id: n.id, opacity: 0.15, font: { color: "#555", size: 10 } });
    }
  });
  nodeDS.update(updates);
  if (matches.length > 0 && matches.length <= 20) {
    network.fit({ nodes: matches, animation: { duration: 400 } });
  }
});
// endregion

// ------------------------------------------------------------------ //
// region:    --- Filters                                              //
// ------------------------------------------------------------------ //
var hiddenKinds = new Set();
var hiddenRelations = new Set();
var kindFiltersEl = document.getElementById("kind-filters");
var edgeFiltersEl = document.getElementById("edge-filters");

function resetFilters() {
  hiddenKinds.clear();
  hiddenRelations.clear();
  kindFiltersEl.innerHTML = "";
  edgeFiltersEl.innerHTML = "";
  searchInput.value = "";

  var presentKinds = new Set();
  nodeDS.get().forEach(function (n) { if (n._kind) presentKinds.add(n._kind); });

  Array.from(presentKinds).sort().forEach(function (kind) {
    var lbl = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.dataset.kind = kind;
    var dot = document.createElement("span");
    dot.className = "color-dot";
    dot.style.backgroundColor = KIND_COLORS[kind] || "#888";
    lbl.appendChild(cb);
    lbl.appendChild(dot);
    lbl.appendChild(document.createTextNode(" " + kind));
    kindFiltersEl.appendChild(lbl);

    cb.addEventListener("change", function () {
      if (cb.checked) { hiddenKinds.delete(kind); } else { hiddenKinds.add(kind); }
      applyVisibility();
    });
  });

  var presentRelations = new Set();
  edgeDS.get().forEach(function (e) { if (e._rel) presentRelations.add(e._rel); });

  Array.from(presentRelations).sort().forEach(function (rel) {
    var lbl = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.dataset.rel = rel;
    var dot = document.createElement("span");
    dot.className = "color-dot";
    dot.style.backgroundColor = EDGE_COLORS[rel] || "#555";
    lbl.appendChild(cb);
    lbl.appendChild(dot);
    lbl.appendChild(document.createTextNode(" " + rel));
    edgeFiltersEl.appendChild(lbl);

    cb.addEventListener("change", function () {
      if (cb.checked) { hiddenRelations.delete(rel); } else { hiddenRelations.add(rel); }
      applyVisibility();
    });
  });
}

function applyVisibility() {
  var nodeUpdates = [];
  nodeDS.get().forEach(function (n) {
    nodeUpdates.push({ id: n.id, hidden: hiddenKinds.has(n._kind) });
  });
  nodeDS.update(nodeUpdates);

  var edgeUpdates = [];
  edgeDS.get().forEach(function (e) {
    edgeUpdates.push({ id: e.id, hidden: hiddenRelations.has(e._rel) });
  });
  edgeDS.update(edgeUpdates);
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Toolbar Buttons                                     //
// ------------------------------------------------------------------ //
var physicsOn = true;
document.getElementById("btn-physics").addEventListener("click", function () {
  physicsOn = !physicsOn;
  network.setOptions({ physics: { enabled: physicsOn } });
  this.classList.toggle("active", physicsOn);
});
document.getElementById("btn-physics").classList.add("active");

document.getElementById("btn-fit").addEventListener("click", function () {
  network.fit({ animation: { duration: 500 } });
});

var spacingSlider = document.getElementById("spacing-slider");
var spacingValEl = document.getElementById("spacing-val");
spacingSlider.addEventListener("input", function () {
  var v = parseInt(this.value, 10);
  spacingValEl.textContent = v;
  spacingMultiplier = 0.3 + (v - 1) * 0.25;
  applyViewPhysics();
  network.once("stabilizationIterationsDone", function () {
    network.fit({ animation: { duration: 300 } });
  });
});

var clustered = false;
document.getElementById("btn-cluster").addEventListener("click", function () {
  if (viewLevel !== "flat" && viewLevel !== "symbols") return;
  if (clustered) {
    removeAllClusters();
    clustered = false;
    this.classList.remove("active");
  } else {
    applyFileClustering();
    clustered = true;
    this.classList.add("active");
  }
});

function applyFileClustering() {
  var fileCount = {};
  nodeDS.get().forEach(function (n) {
    if (n._file) { fileCount[n._file] = (fileCount[n._file] || 0) + 1; }
  });
  Object.keys(fileCount).forEach(function (file) {
    if (fileCount[file] < 2) return;
    try {
      network.cluster({
        joinCondition: function (nodeOptions) {
          return nodeOptions._file === file && nodeOptions._kind !== "file";
        },
        clusterNodeProperties: {
          id: "cluster:" + file,
          label: file.split("/").pop() + " (" + fileCount[file] + ")",
          shape: "box",
          color: { background: "#89b4fa", border: "#89b4fa",
                   highlight: { background: "#fff", border: "#89b4fa" }},
          font: { color: "#cdd6f4", size: 12, bold: { color: "#89b4fa" } },
          borderWidth: 2,
          _file: file,
          _kind: "file_cluster"
        }
      });
    } catch(e) { /* skip */ }
  });
}

function removeAllClusters() {
  nodeDS.getIds().forEach(function (id) {
    if (typeof id === "string" && id.startsWith("cluster:")) {
      try { network.openCluster(id); } catch(e) {}
    }
  });
  try {
    network.body.data.nodes.getIds().forEach(function (id) {
      if (network.isCluster(id)) {
        try { network.openCluster(id); } catch(e) {}
      }
    });
  } catch(e) {}
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Detail Sidebar                                      //
// ------------------------------------------------------------------ //
var detailEl = document.getElementById("detail");
var detailContent = document.getElementById("detail-content");

document.getElementById("close-detail").addEventListener("click", hideDetail);

function showDetail(nodeId) {
  var node = nodeDS.get(nodeId);
  if (!node) return;

  var html = '<h3>' + _esc(node.label) + '</h3>';
  html += _metaRow("Kind", node._kind);
  html += _metaRow("File", node._file || "\u2014");
  if (node._props) {
    Object.keys(node._props).forEach(function (k) {
      html += _metaRow(k, node._props[k]);
    });
  }

  var incoming = [];
  var outgoing = [];
  edgeDS.get().forEach(function (e) {
    if (e.to === nodeId) incoming.push(e);
    if (e.from === nodeId) outgoing.push(e);
  });

  if (incoming.length > 0) {
    html += '<h4>Incoming (' + incoming.length + ')</h4><ul>';
    incoming.slice(0, 50).forEach(function (e) {
      var src = nodeDS.get(e.from);
      var label = src ? src.label : e.from;
      html += '<li data-id="' + _esc(String(e.from)) + '">' + _esc(label) +
              '<span class="rel-tag">' + _esc(e._rel) + '</span></li>';
    });
    if (incoming.length > 50) html += '<li>\u2026 and ' + (incoming.length - 50) + ' more</li>';
    html += '</ul>';
  }

  if (outgoing.length > 0) {
    html += '<h4>Outgoing (' + outgoing.length + ')</h4><ul>';
    outgoing.slice(0, 50).forEach(function (e) {
      var tgt = nodeDS.get(e.to);
      var label = tgt ? tgt.label : e.to;
      html += '<li data-id="' + _esc(String(e.to)) + '">' + _esc(label) +
              '<span class="rel-tag">' + _esc(e._rel) + '</span></li>';
    });
    if (outgoing.length > 50) html += '<li>\u2026 and ' + (outgoing.length - 50) + ' more</li>';
    html += '</ul>';
  }

  if (incoming.length === 0 && outgoing.length === 0) {
    html += '<h4>Connections</h4><p style="color:var(--text-dim);font-size:12px">No edges</p>';
  }

  detailContent.innerHTML = html;
  detailEl.classList.add("open");

  detailContent.querySelectorAll("li[data-id]").forEach(function (li) {
    li.addEventListener("click", function () {
      var targetId = li.dataset.id;
      network.selectNodes([targetId]);
      network.focus(targetId, { scale: 1.2, animation: { duration: 400 } });
      showDetail(targetId);
    });
  });
}

function hideDetail() {
  detailEl.classList.remove("open");
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Stats Bar                                           //
// ------------------------------------------------------------------ //
function updateStats(nodeCount, edgeCount) {
  var label = "";
  if (viewLevel === "crates") label = " (crate view)";
  else if (viewLevel === "files") {
    label = " (file view \u2014 " + viewScope.crate + ")";
  } else if (viewLevel === "symbols") {
    var fn = (viewScope.file || "").split("/").pop();
    label = " (symbol view \u2014 " + fn + ")";
  }
  document.getElementById("stats").textContent =
    nodeCount + " nodes \u00b7 " + edgeCount + " edges" + label;
}
// endregion

// ------------------------------------------------------------------ //
// region:    --- Helpers                                              //
// ------------------------------------------------------------------ //
function _tooltip(n) {
  var parts = [n.label, "Kind: " + (n.group || n.kind || "")];
  if (n.file) parts.push("File: " + n.file);
  if (n.properties) {
    Object.keys(n.properties).forEach(function (k) {
      parts.push(k + ": " + n.properties[k]);
    });
  }
  return parts.join("\n");
}

function _metaRow(key, val) {
  return '<div class="meta-row"><span class="key">' + _esc(key) +
         '</span><span class="val">' + _esc(String(val)) + '</span></div>';
}

function _esc(s) {
  var el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}
// endregion

// ------------------------------------------------------------------ //
// Boot                                                               //
// ------------------------------------------------------------------ //
initNetwork();

})();
</script>
</body>
</html>
"""
