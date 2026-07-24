#!/usr/bin/env python3
"""Phase 3 end-to-end verification against the Safeguard repository.

Mirrors ``code-history-intel/verify-enrichment.mjs`` (Phase 2) so the
Python engine and the TypeScript extension can be compared apples-to-
apples.  The target line-range was chosen because it is dense with
real PR activity (16 unique PRs over ~26 commits at time of writing).

Usage::

    AZURE_DEVOPS_PAT=... python scripts/verify_history_engine.py [/path/to/Safeguard]

Exits non-zero if (a) no commits are returned, or (b) the live ADO
provider produces zero decision signals despite a PAT being set.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from ast_intel.history.enrichment import EnrichmentPipeline
from ast_intel.history.history_builder import HistoryBuilder
from ast_intel.history.models import SymbolKind, SymbolRef
from ast_intel.history.ownership_scorer import OwnershipScorer
from ast_intel.history.providers.registry import build_provider

DEFAULT_REPO = Path("/home/rajayush/Safeguard")
TARGET_FILE = (
    "deploy/helm-charts/safeguard/values/environments/"
    "cyn-egress-string-1.yaml"
)
TARGET_RANGE = (1, 80)


def main(argv: list[str]) -> int:
    repo = Path(argv[1]) if len(argv) > 1 else DEFAULT_REPO
    if not (repo / ".git").exists():
        print(f"ERROR: {repo} is not a git repo", file=sys.stderr)
        return 2

    start_line, end_line = TARGET_RANGE
    symbol = SymbolRef(
        id=f"{TARGET_FILE}::L{start_line}-L{end_line}",
        label=Path(TARGET_FILE).name,
        kind=SymbolKind.OTHER,
        file=TARGET_FILE,
        start_line=start_line,
        end_line=end_line,
    )

    print(f"[1/3] Building history for {symbol.file} L{start_line}-{end_line}…")
    t0 = time.perf_counter()
    builder = HistoryBuilder(repo_root=repo)
    record = builder.build(symbol)
    print(
        f"      → {len(record.commits)} commits, "
        f"{len(record.blame)} blame hunks "
        f"(HEAD={record.head_sha[:12]}) in {time.perf_counter() - t0:.2f}s",
    )
    if not record.commits:
        print("ERROR: no commits returned", file=sys.stderr)
        return 1

    print("[2/3] Scoring ownership…")
    own = OwnershipScorer().score(
        symbol=record.symbol,
        head_sha=record.head_sha,
        commits=list(record.commits),
        blame=list(record.blame),
    )
    for s in own.scores[:3]:
        print(
            f"      • {s.author_name:<28} "
            f"score={s.score:.3f} "
            f"(commits={s.commit_count}, blame={s.blame_lines})",
        )

    print("[3/3] Enriching with provider…")
    has_pat = bool(os.environ.get("AZURE_DEVOPS_PAT"))
    provider = build_provider(repo)
    pipeline = EnrichmentPipeline(provider, max_pr_threads=5)
    t0 = time.perf_counter()
    enriched = pipeline.enrich(record)
    elapsed = time.perf_counter() - t0
    pr_ids = {
        ec.pr.id for ec in enriched.enriched_commits if ec.pr is not None
    }
    print(
        f"      → {len(pr_ids)} unique PRs, "
        f"{len(enriched.signals)} decision signals in {elapsed:.2f}s",
    )
    if enriched.warnings:
        for w in enriched.warnings:
            print(f"      ! {w}")
    for sig in enriched.signals[:5]:
        print(
            f"      • [{sig.kind.value}] {sig.summary[:80]} "
            f"— {sig.citation.author}",
        )

    if has_pat and not enriched.signals:
        print(
            "ERROR: PAT present but no signals produced",
            file=sys.stderr,
        )
        return 1

    print(
        "\nOK — Phase 3 verified: "
        f"{len(record.commits)} commits / {len(pr_ids)} PRs / "
        f"{len(enriched.signals)} signals",
    )
    # Emit a compact summary line for CI parsing.
    print(json.dumps({
        "commits": len(record.commits),
        "prs": len(pr_ids),
        "signals": len(enriched.signals),
        "warnings": list(enriched.warnings),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
