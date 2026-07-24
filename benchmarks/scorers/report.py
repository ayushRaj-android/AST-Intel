"""Generate a markdown report from scored benchmark results.

Usage:
    python benchmarks/scorers/report.py
    python benchmarks/scorers/report.py benchmarks/results/benchmark-*.scored.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def generate_report(scored_file: Path) -> str:
    """Generate a markdown benchmark report."""
    scored = json.loads(scored_file.read_text())

    by_arm: dict[str, list[dict]] = {}
    for s in scored:
        by_arm.setdefault(s["arm"], []).append(s)

    # Compute aggregates
    arm_stats: dict[str, dict] = {}
    for arm, results in by_arm.items():
        n = len(results)
        arm_stats[arm] = {
            "n": n,
            "correct": sum(1 for r in results if r.get("correct", r.get("correct_behavior", False))),
            "avg_tokens": sum(r["output_tokens"] for r in results) / max(n, 1),
            "avg_time": sum(r["elapsed_seconds"] for r in results) / max(n, 1),
            "avg_mcp": sum(r["mcp_call_count"] for r in results) / max(n, 1),
            "avg_files_read": sum(r["files_read"] for r in results) / max(n, 1),
            "avg_tool_calls": sum(r["tool_call_count"] for r in results) / max(n, 1),
        }

    # Build markdown
    lines = []
    lines.append("# AST-Intel Benchmark Results")
    lines.append("")
    lines.append(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")
    lines.append("## Summary")
    lines.append("")

    # Comparison table
    arms = sorted(arm_stats.keys())
    lines.append("| Metric | " + " | ".join(f"**{a}**" for a in arms) + " |")
    lines.append("|--------|" + "|".join("---:" for _ in arms) + "|")

    for label, key, fmt in [
        ("Tasks correct", "correct", lambda v, s: f"{v}/{s['n']} ({v/max(s['n'],1)*100:.0f}%)"),
        ("Avg output tokens", "avg_tokens", lambda v, _: f"{v:.0f}"),
        ("Avg time (s)", "avg_time", lambda v, _: f"{v:.1f}"),
        ("Avg MCP tool calls", "avg_mcp", lambda v, _: f"{v:.1f}"),
        ("Avg file reads", "avg_files_read", lambda v, _: f"{v:.1f}"),
        ("Avg total tool calls", "avg_tool_calls", lambda v, _: f"{v:.1f}"),
    ]:
        row = f"| {label} |"
        for arm in arms:
            stats = arm_stats[arm]
            val = stats[key]
            row += f" {fmt(val, stats)} |"
        lines.append(row)

    # Reduction percentages
    if "baseline" in arm_stats and "ast-intel" in arm_stats:
        base = arm_stats["baseline"]
        intel = arm_stats["ast-intel"]
        lines.append("")
        lines.append("## Improvement (ast-intel vs baseline)")
        lines.append("")
        lines.append("| Metric | Reduction |")
        lines.append("|--------|----------:|")
        for label, key in [
            ("Output tokens", "avg_tokens"),
            ("Response time", "avg_time"),
            ("File reads", "avg_files_read"),
        ]:
            if base[key] > 0:
                pct = (1 - intel[key] / base[key]) * 100
                lines.append(f"| {label} | **{pct:+.0f}%** |")

        accuracy_delta = (intel["correct"] / max(intel["n"], 1)) - (base["correct"] / max(base["n"], 1))
        lines.append(f"| Accuracy | **{accuracy_delta*100:+.0f}pp** |")

    # Per-task breakdown
    lines.append("")
    lines.append("## Per-Task Results")
    lines.append("")
    lines.append("| Task | Arm | Correct | Tokens | Time (s) | MCP Calls | Files Read |")
    lines.append("|------|-----|---------|-------:|----------:|----------:|-----------:|")
    for s in sorted(scored, key=lambda x: (x["task_id"], x["arm"])):
        correct = s.get("correct", s.get("correct_behavior", False))
        mark = "✓" if correct else "✗"
        lines.append(
            f"| {s['task_id']} | {s['arm']} | {mark} | "
            f"{s['output_tokens']} | {s['elapsed_seconds']:.1f} | "
            f"{s['mcp_call_count']} | {s['files_read']} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("- **Engine:** GitHub Copilot CLI (`copilot -p`, non-interactive)")
    lines.append("- **Model:** As assigned by Copilot (logged per response)")
    lines.append("- **Arms:**")
    lines.append("  - `baseline`: No ast-intel MCP server (agent reads files directly)")
    lines.append("  - `ast-intel`: ast-intel MCP server connected (graph queries available)")
    lines.append("- **Isolation:** Each task runs in a fresh CLI session (no shared history)")
    lines.append("- **Metrics:** Tokens and model from Copilot JSONL output; time measured wall-clock")
    lines.append("- **Scoring:** Ground truth defined in `benchmarks/tasks/*.yaml`")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("cd /path/to/ast-intel")
    lines.append("python benchmarks/run.py --runs 3")
    lines.append("python benchmarks/scorers/score.py")
    lines.append("python benchmarks/scorers/report.py")
    lines.append("```")

    return "\n".join(lines)


def main():
    results_dir = Path(__file__).resolve().parent.parent / "results"

    if len(sys.argv) > 1:
        scored_file = Path(sys.argv[1])
    else:
        files = sorted(results_dir.glob("*.scored.json"))
        if not files:
            print("No scored results found. Run:")
            print("  python benchmarks/run.py && python benchmarks/scorers/score.py")
            sys.exit(1)
        scored_file = files[-1]

    report = generate_report(scored_file)

    # Write report
    report_file = results_dir / "REPORT.md"
    report_file.write_text(report)
    print(f"Report written to: {report_file}")
    print(report)


if __name__ == "__main__":
    main()
