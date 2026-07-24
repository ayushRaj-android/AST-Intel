"""Core orchestration modules for AST Intel.

This package contains the pipeline stages:
- **workspace**: Repository discovery, file walking, .gitignore filtering
- **manifest_parser**: Language-specific manifest parsing (Cargo.toml, package.json, etc.)
- **dispatcher**: Routes files to the correct language extractor in parallel
- **indexer**: Builds global cross-reference indexes after extraction
- **emitter**: Serializes results to JSON and Markdown output formats
- **graph_builder**: Transforms WorkspaceAST into CodeGraph
- **analyzer**: Computes graph analysis (god nodes, communities, hyperedges)
"""
