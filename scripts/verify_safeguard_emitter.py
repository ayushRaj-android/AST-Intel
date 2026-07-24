#!/usr/bin/env python3
"""Safeguard codebase end-to-end verification for Phase 3 — Emitters.

Runs the full pipeline (discover → extract → index → emit) against the
Safeguard workspace and verifies all Phase 3 checkpoints:

1. ast.json is valid JSON (python -m json.tool exits 0)
2. ast.json contains meta.schema_version and meta.generated_at
3. ast.json round-trips: json.loads(json.dumps(…)) is identical
4. summary.md renders without broken Markdown tables
5. --format json produces only ast.json, --format md only summary.md
6. Two runs on identical input produce byte-identical output
7. File sizes are reasonable (ast.json > 100 KB, summary.md > 5 KB)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ast_intel.core.emitter import AST_JSON_FILENAME, SUMMARY_MD_FILENAME, Emitter
from ast_intel.core.indexer import Indexer
from ast_intel.extractors.rust import RustExtractor
from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

SAFEGUARD_ROOT = Path("/home/rajayush/Safeguard")


def discover_and_extract() -> WorkspaceAST:
    """Discover crates, extract all .rs files, return populated workspace."""
    extractor = RustExtractor()

    cargo_files = sorted(SAFEGUARD_ROOT.rglob("Cargo.toml"))
    cargo_files = [
        p for p in cargo_files
        if "target/" not in str(p) and "/tools/" not in str(p)
    ]

    crates: dict[str, CrateModel] = {}
    for cargo in cargo_files:
        try:
            data = tomllib.loads(cargo.read_text())
        except Exception:
            continue
        if "package" not in data:
            continue

        crate = extractor.parse_manifest(cargo)
        crate_dir = cargo.parent
        rs_files = sorted(crate_dir.rglob("*.rs"))
        rs_files = [f for f in rs_files if "target/" not in str(f)]

        for rs_file in rs_files:
            try:
                source = rs_file.read_bytes()
                file_ast = extractor.extract(rs_file, source)
                file_ast.file = str(rs_file.relative_to(SAFEGUARD_ROOT))
                rel = rs_file.relative_to(crate_dir)
                mod_parts = list(rel.with_suffix("").parts)
                if mod_parts and mod_parts[0] == "src":
                    mod_parts = mod_parts[1:]
                if mod_parts and mod_parts[-1] in ("lib", "main", "mod"):
                    mod_parts = mod_parts[:-1]
                if mod_parts:
                    file_ast.module_path = f"{crate.name}::{'::'.join(mod_parts)}"
                else:
                    file_ast.module_path = crate.name
                crate.files.append(file_ast)
            except Exception as exc:
                print(f"  [WARN] Failed to extract {rs_file}: {exc}")

        crates[crate.name] = crate
        print(f"  Crate: {crate.name} ({len(crate.files)} files)")

    return WorkspaceAST(crates=crates)


def build_indexed_workspace() -> WorkspaceAST:
    """Full pipeline: discover → extract → index."""
    print("Phase 1: Discovering and extracting crates...")
    ws = discover_and_extract()
    total_files = sum(len(c.files) for c in ws.crates.values())
    print(f"  Total: {len(ws.crates)} crates, {total_files} files\n")

    print("Phase 2: Building cross-references...")
    indexer = Indexer()
    indexer.build_cross_references(ws)
    xref = ws.cross_references
    print(f"  struct_index:          {len(xref.struct_index)} entries")
    print(f"  enum_index:            {len(xref.enum_index)} entries")
    print(f"  trait_index:           {len(xref.trait_index)} entries")
    print(f"  function_file_index:   {len(xref.function_file_index)} entries")
    print(f"  impl_map:              {len(xref.impl_map)} entries")
    return ws


def check(label: str, condition: bool, detail: str = "") -> bool:
    """Print a checkpoint result and return whether it passed."""
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main() -> int:
    """Run all Phase 3 verification checkpoints."""
    print("=" * 60)
    print("  Safeguard Emitter Verification — Phase 3")
    print("=" * 60 + "\n")

    ws = build_indexed_workspace()

    with tempfile.TemporaryDirectory(prefix="ast_verify_") as tmp:
        tmp_path = Path(tmp)
        failures = 0

        # -----------------------------------------------------------
        # Checkpoint 1: ast.json is valid JSON
        # -----------------------------------------------------------
        print("\n--- Checkpoint 1: ast.json is valid JSON ---")
        json_dir = tmp_path / "json_only"
        emitter_json = Emitter(output_dir=json_dir, output_format="json")
        json_paths = emitter_json.emit(ws)
        ast_json_path = json_dir / AST_JSON_FILENAME
        try:
            data = json.loads(ast_json_path.read_text(encoding="utf-8"))
            if not check("Valid JSON", True, f"{ast_json_path.stat().st_size:,} bytes"):
                failures += 1
        except json.JSONDecodeError as exc:
            check("Valid JSON", False, str(exc))
            failures += 1
            print("  FATAL — cannot continue without valid JSON")
            return 1

        # -----------------------------------------------------------
        # Checkpoint 2: meta.schema_version and meta.generated_at
        # -----------------------------------------------------------
        print("\n--- Checkpoint 2: meta fields present ---")
        meta = data.get("meta", {})
        if not check("meta.schema_version", "schema_version" in meta, repr(meta.get("schema_version"))):
            failures += 1
        if not check("meta.generated_at", "generated_at" in meta, repr(meta.get("generated_at"))):
            failures += 1

        # Also verify statistics are populated
        stats_keys = [
            "total_crates", "total_files", "total_structs",
            "total_enums", "total_traits", "total_functions",
        ]
        for key in stats_keys:
            val = meta.get(key)
            if not check(f"meta.{key}", val is not None and val > 0, str(val)):
                failures += 1

        # -----------------------------------------------------------
        # Checkpoint 3: JSON round-trip
        # -----------------------------------------------------------
        print("\n--- Checkpoint 3: JSON round-trip ---")
        raw = ast_json_path.read_text(encoding="utf-8")
        reserial = json.dumps(json.loads(raw), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        if not check("Round-trip identical", raw == reserial, f"len(original)={len(raw)}, len(reserialized)={len(reserial)}"):
            # Show first diff
            for i, (a, b) in enumerate(zip(raw, reserial)):
                if a != b:
                    print(f"    First diff at char {i}: {a!r} vs {b!r}")
                    break
            failures += 1

        # -----------------------------------------------------------
        # Checkpoint 4: summary.md Markdown tables are well-formed
        # -----------------------------------------------------------
        print("\n--- Checkpoint 4: summary.md tables ---")
        md_dir = tmp_path / "md_only"
        emitter_md = Emitter(output_dir=md_dir, output_format="md")
        md_paths = emitter_md.emit(ws)
        summary_path = md_dir / SUMMARY_MD_FILENAME
        md_text = summary_path.read_text(encoding="utf-8")

        # Find all Markdown tables (lines starting with |)
        table_lines = [
            (i + 1, line)
            for i, line in enumerate(md_text.splitlines())
            if line.startswith("|")
        ]
        pipe_mismatch = 0
        for lineno, line in table_lines:
            pipes = line.count("|")
            # Each table row should have consistent pipe counts
            # Minimum: | col | col | → 3 pipes
            if pipes < 3:
                print(f"    Line {lineno}: only {pipes} pipes → {line[:80]}")
                pipe_mismatch += 1
        if not check("Markdown tables well-formed", pipe_mismatch == 0, f"{len(table_lines)} table rows, {pipe_mismatch} malformed"):
            failures += 1

        # Verify separator rows use dashes
        sep_pattern = re.compile(r"^\|[\s\-:|]+\|$")
        sep_rows = [line for _, line in table_lines if "---" in line]
        bad_seps = [r for r in sep_rows if not sep_pattern.match(r)]
        if not check("Table separators valid", len(bad_seps) == 0, f"{len(sep_rows)} separator rows"):
            for r in bad_seps[:3]:
                print(f"    Bad separator: {r}")
            failures += 1

        # -----------------------------------------------------------
        # Checkpoint 5: format-specific output
        # -----------------------------------------------------------
        print("\n--- Checkpoint 5: format-specific outputs ---")
        json_only_files = list(json_dir.iterdir())
        md_only_files = list(md_dir.iterdir())

        json_names = {f.name for f in json_only_files}
        md_names = {f.name for f in md_only_files}

        if not check("--format json → only ast.json", json_names == {AST_JSON_FILENAME}, str(json_names)):
            failures += 1
        if not check("--format md → only summary.md", md_names == {SUMMARY_MD_FILENAME}, str(md_names)):
            failures += 1

        # --format both → both files
        both_dir = tmp_path / "both"
        emitter_both = Emitter(output_dir=both_dir, output_format="both")
        emitter_both.emit(ws)
        both_names = {f.name for f in both_dir.iterdir()}
        if not check("--format both → both files", both_names == {AST_JSON_FILENAME, SUMMARY_MD_FILENAME}, str(both_names)):
            failures += 1

        # -----------------------------------------------------------
        # Checkpoint 6: determinism (byte-identical output)
        # -----------------------------------------------------------
        print("\n--- Checkpoint 6: deterministic output ---")
        det1_dir = tmp_path / "det1"
        det2_dir = tmp_path / "det2"
        # Build fresh indexed workspaces for each run
        ws2 = build_indexed_workspace()

        # We need to force identical timestamps for determinism
        from ast_intel.models.workspace_model import WorkspaceMeta

        # Use the meta from ws (already populated by first emit) as a template
        existing_meta = ws.meta
        if existing_meta is not None:
            # Re-use exact same meta for both to ensure timestamp match
            object.__setattr__(ws2, "meta", existing_meta)

        em1 = Emitter(output_dir=det1_dir, output_format="both")
        em2 = Emitter(output_dir=det2_dir, output_format="both")
        em1.emit(ws)
        em2.emit(ws2)

        json1 = (det1_dir / AST_JSON_FILENAME).read_bytes()
        json2 = (det2_dir / AST_JSON_FILENAME).read_bytes()
        md1 = (det1_dir / SUMMARY_MD_FILENAME).read_bytes()
        md2 = (det2_dir / SUMMARY_MD_FILENAME).read_bytes()

        if not check("ast.json byte-identical", json1 == json2, f"len={len(json1):,} vs {len(json2):,}"):
            failures += 1
        if not check("summary.md byte-identical", md1 == md2, f"len={len(md1):,} vs {len(md2):,}"):
            failures += 1

        # -----------------------------------------------------------
        # Checkpoint 7: file sizes are reasonable
        # -----------------------------------------------------------
        print("\n--- Checkpoint 7: file size sanity ---")
        json_size = len(json1)
        md_size = len(md1)
        if not check("ast.json > 100 KB", json_size > 100_000, f"{json_size:,} bytes"):
            failures += 1
        if not check("summary.md > 5 KB", md_size > 5_000, f"{md_size:,} bytes"):
            failures += 1

        # -----------------------------------------------------------
        # Bonus: content spot-checks
        # -----------------------------------------------------------
        print("\n--- Bonus: content spot-checks ---")

        # JSON: crates dict has expected crates
        crate_names = set(data.get("crates", {}).keys())
        if not check("JSON has approval-engine", "approval-engine" in crate_names, f"{len(crate_names)} crates"):
            failures += 1
        if not check("JSON has lib-common", "lib-common" in crate_names):
            failures += 1

        # Cross-references present
        xrefs = data.get("cross_references", {})
        if not check("cross_references present", len(xrefs) > 0, f"{len(xrefs)} keys"):
            failures += 1
        if not check("struct_index has entries", len(xrefs.get("struct_index", {})) > 0):
            failures += 1

        # Markdown: section headers
        if not check("MD has overview header", "# AST Intel" in md_text or "# Workspace" in md_text):
            failures += 1
        if not check("MD has crate reference", "## Crate Reference" in md_text or "## Crate:" in md_text or "### Crate:" in md_text):
            failures += 1
        if not check("MD has cross-refs", "Cross" in md_text):
            failures += 1

        # -----------------------------------------------------------
        # Summary
        # -----------------------------------------------------------
        print(f"\n{'=' * 60}")
        if failures:
            print(f"  FAILED: {failures} checkpoint(s) failed")
            return 1
        print("  ALL PHASE 3 CHECKPOINTS PASSED")
        print(f"\n  ast.json:   {json_size:>10,} bytes")
        print(f"  summary.md: {md_size:>10,} bytes")
        print(f"  Crates:     {len(crate_names):>10}")
        print(f"  Files:      {meta.get('total_files', '?'):>10}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
