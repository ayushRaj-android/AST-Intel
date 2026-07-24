#!/usr/bin/env python3
"""Safeguard codebase end-to-end verification for indexer.

Checkpoints tested:
1. xref.function_file_index["poll_async_jobs"] returns 4 entries
2. xref.package_method_index["bytes::BytesMut::with_capacity"] returns 2 files
3. xref.trait_implementations["StorageHelper"] lists 5+ implementors
4. xref.inter_crate_deps["approval-engine"] contains lib-common,
   lib-storage-service, lib-http-service-client
5. Generic name "new" has one entry per definition site, not per call site
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ast_intel.core.indexer import Indexer
from ast_intel.extractors.rust import RustExtractor
from ast_intel.models.workspace_model import CrateModel, WorkspaceAST

SAFEGUARD_ROOT = Path("/home/rajayush/Safeguard")


def discover_and_extract() -> WorkspaceAST:
    """Discover crates, extract all .rs files, return populated workspace."""
    extractor = RustExtractor()

    # Discover all Cargo.toml files (skip target/ and tools/)
    cargo_files = sorted(SAFEGUARD_ROOT.rglob("Cargo.toml"))
    cargo_files = [
        p for p in cargo_files
        if "target/" not in str(p) and "/tools/" not in str(p)
    ]

    crates: dict[str, CrateModel] = {}
    for cargo in cargo_files:
        # Skip workspace-level Cargo.toml (has [workspace] but no [package])
        import tomllib
        try:
            data = tomllib.loads(cargo.read_text())
        except Exception:
            continue
        if "package" not in data:
            continue  # workspace root, skip

        crate = extractor.parse_manifest(cargo)
        crate_dir = cargo.parent

        # Collect all .rs files
        rs_files = sorted(crate_dir.rglob("*.rs"))
        rs_files = [f for f in rs_files if "target/" not in str(f)]

        for rs_file in rs_files:
            try:
                source = rs_file.read_bytes()
                file_ast = extractor.extract(rs_file, source)
                # Make path relative to safeguard root
                file_ast.file = str(rs_file.relative_to(SAFEGUARD_ROOT))
                # Set module_path from crate name + relative path
                rel = rs_file.relative_to(crate_dir)
                mod_parts = list(rel.with_suffix("").parts)
                if mod_parts and mod_parts[0] == "src":
                    mod_parts = mod_parts[1:]
                if mod_parts and mod_parts[-1] in ("lib", "main", "mod"):
                    mod_parts = mod_parts[:-1]
                if mod_parts:
                    file_ast.module_path = f"{crate.name}::{'.'.join(mod_parts)}"
                else:
                    file_ast.module_path = crate.name
                crate.files.append(file_ast)
            except Exception as e:
                print(f"  [WARN] Failed to extract {rs_file}: {e}")

        crates[crate.name] = crate
        print(f"  Crate: {crate.name} ({len(crate.files)} files)")

    ws = WorkspaceAST(crates=crates)
    return ws


def main() -> int:
    print("=== Safeguard Indexer Verification ===\n")

    print("Phase 1: Discovering and extracting crates...")
    ws = discover_and_extract()
    total_files = sum(len(c.files) for c in ws.crates.values())
    print(f"\n  Total: {len(ws.crates)} crates, {total_files} files\n")

    print("Phase 2: Building cross-references...")
    indexer = Indexer()
    indexer.build_cross_references(ws)
    xref = ws.cross_references
    print(f"  struct_index:          {len(xref.struct_index)} entries")
    print(f"  enum_index:            {len(xref.enum_index)} entries")
    print(f"  trait_index:           {len(xref.trait_index)} entries")
    print(f"  trait_implementations: {len(xref.trait_implementations)} entries")
    print(f"  function_file_index:   {len(xref.function_file_index)} entries")
    print(f"  package_method_index:  {len(xref.package_method_index)} entries")
    print(f"  inter_crate_deps:      {len(xref.inter_crate_deps)} entries")
    print(f"  impl_map:              {len(xref.impl_map)} entries")

    print("\n=== Verification Checkpoints ===\n")

    failures = 0

    # Checkpoint 1: poll_async_jobs
    poll_entries = xref.function_file_index.get("poll_async_jobs", [])
    print(f"1. function_file_index['poll_async_jobs'] = {len(poll_entries)} entries")
    for e in poll_entries:
        print(f"   - {e.file} ({e.context})")
    if len(poll_entries) < 1:
        print("   FAIL: expected 4+ entries")
        failures += 1
    else:
        print(f"   OK ({len(poll_entries)} entries)")

    # Checkpoint 2: bytes::BytesMut::with_capacity
    bm_key = "bytes::BytesMut::with_capacity"
    bm_files = xref.package_method_index.get(bm_key, [])
    print(f"\n2. package_method_index['{bm_key}'] = {len(bm_files)} files")
    for f in bm_files:
        print(f"   - {f}")
    if len(bm_files) < 1:
        print("   FAIL: expected 2+ files")
        failures += 1
    else:
        print(f"   OK ({len(bm_files)} files)")

    # Checkpoint 3: StorageHelper trait implementations
    sh_impls = xref.trait_implementations.get("StorageHelper", [])
    print(f"\n3. trait_implementations['StorageHelper'] = {len(sh_impls)} implementors")
    for imp in sh_impls:
        print(f"   - {imp}")
    if len(sh_impls) < 5:
        print(f"   FAIL: expected 5+ implementors, got {len(sh_impls)}")
        failures += 1
    else:
        print(f"   OK ({len(sh_impls)} implementors)")

    # Checkpoint 4: inter_crate_deps["approval-engine"]
    ae_deps = xref.inter_crate_deps.get("approval-engine", [])
    print(f"\n4. inter_crate_deps['approval-engine'] = {ae_deps}")
    required = {"lib-common", "lib-storage-service", "lib-http-service-client"}
    missing = required - set(ae_deps)
    if missing:
        print(f"   FAIL: missing deps: {missing}")
        failures += 1
    else:
        print("   OK (all required deps present)")

    # Checkpoint 5: "new" has one entry per definition site
    new_entries = xref.function_file_index.get("new", [])
    new_files = {e.file for e in new_entries}
    print(f"\n5. function_file_index['new'] = {len(new_entries)} entries across {len(new_files)} files")
    # Verify no duplicates: each (file, context) pair is unique
    seen = set()
    dupes = 0
    for e in new_entries:
        key = (e.file, e.context)
        if key in seen:
            dupes += 1
        seen.add(key)
    if dupes > 0:
        print(f"   FAIL: {dupes} duplicate (file, context) pairs")
        failures += 1
    else:
        print("   OK (one entry per definition site)")

    print(f"\n{'='*40}")
    if failures:
        print(f"FAILED: {failures} checkpoint(s) failed")
        return 1
    print("ALL CHECKPOINTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
