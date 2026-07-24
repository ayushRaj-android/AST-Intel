"""Output formatters for AST Intel.

Serializes the in-memory WorkspaceAST into consumable output formats:
- **json_formatter**: Deterministic JSON output (ast.json)
- **markdown_formatter**: Human/LLM-readable summary (summary.md)
- **graph_json_formatter**: Code knowledge graph as JSON (graph.json)
- **graph_dot_formatter**: Code knowledge graph as Graphviz DOT (graph.dot)
- **graph_mermaid_formatter**: Code knowledge graph as Mermaid (graph.mermaid.md)
- **report_formatter**: Graph analysis report (GRAPH_REPORT.md + analysis.json)
"""
