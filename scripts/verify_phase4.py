"""Phase 4 end-to-end verification on a live repository.

Builds the full Phase 4 stack (HistoryBuilder → EnrichmentPipeline →
SignalAggregator) against the target file:line range and emits a JSON
summary of the fused context.

Usage::

    python scripts/verify_phase4.py \
        --repo /home/rajayush/Safeguard \
        --file deploy/helm-charts/safeguard/values/environments/cyn-egress-string-1.yaml \
        --start 1 --end 80 \
        --symbol cyn-egress-string-1.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ast_intel.history.enrichment import EnrichmentPipeline
from ast_intel.history.history_builder import HistoryBuilder
from ast_intel.history.models import SymbolKind, SymbolRef
from ast_intel.history.providers.registry import build_provider
from ast_intel.history.signals.signal_aggregator import SignalAggregator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--symbol", default="")
    args = parser.parse_args()

    repo: Path = args.repo.resolve()
    label = args.symbol or Path(args.file).name

    ref = SymbolRef(
        id=f"{args.file}::L{args.start}-L{args.end}",
        label=label,
        kind=SymbolKind.OTHER,
        file=args.file,
        start_line=args.start,
        end_line=args.end,
    )

    started = time.perf_counter()
    builder = HistoryBuilder(repo_root=repo)
    provider = build_provider(repo)
    pipeline = EnrichmentPipeline(provider)
    aggregator = SignalAggregator(
        repo,
        history_builder=builder,
        enrichment=pipeline,
    )
    ctx = aggregator.aggregate(ref, symbol_id=label, symbol_label=label)
    elapsed = time.perf_counter() - started

    summary = {
        "elapsed_seconds": round(elapsed, 3),
        "head_sha": ctx.enriched.record.head_sha,
        "commits": len(ctx.enriched.record.commits),
        "prs": len(
            {
                ec.pr.id
                for ec in ctx.enriched.enriched_commits
                if ec.pr is not None
            },
        ),
        "rfcs": len(ctx.rfcs),
        "incidents": len(ctx.incidents),
        "tribal_notes": len(ctx.tribal),
        "signals": len(ctx.signals),
        "conflicts": len(ctx.conflicts),
        "hidden_signal_count": ctx.hidden_signal_count,
        "top_signals": [
            {
                "id": s.id,
                "type": s.signal_type.value,
                "confidence": round(s.confidence, 3),
                "summary": s.summary[:120],
            }
            for s in list(ctx.signals)[:10]
        ],
        "warnings": list(ctx.enriched.warnings),
    }
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
