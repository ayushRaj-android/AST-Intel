"""Score benchmark results against ground truth.

Reads a benchmark-*.json results file and scores each task:
  - Navigation: did the response mention the ground truth symbols/files?
  - Implementation: did the agent reuse existing code or rewrite?

Usage:
    python benchmarks/scorers/score.py benchmarks/results/benchmark-20260629-*.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"


def load_ground_truth() -> dict[str, dict]:
    """Load all task definitions indexed by ID."""
    tasks = {}
    for f in TASKS_DIR.glob("*.yaml"):
        with f.open() as fh:
            data = yaml.safe_load(fh)
        for t in data.get("tasks", []):
            tasks[t["id"]] = t
    return tasks


def score_navigation(result: dict, task: dict) -> dict:
    """Score a navigation task result against ground truth."""
    response = result.get("response_text", "").lower()
    gt = task.get("ground_truth", {})
    scores = {"task_id": task["id"], "arm": result["arm"]}

    # Check if ground truth file/symbol is mentioned
    if "file" in gt:
        scores["file_found"] = gt["file"].lower() in response
    if "symbol" in gt:
        scores["symbol_found"] = gt["symbol"].lower() in response

    # Check must_mention list
    if "must_mention" in gt:
        mentioned = sum(
            1 for item in gt["must_mention"]
            if item.lower() in response
        )
        total = len(gt["must_mention"])
        scores["recall"] = round(mentioned / total, 2) if total > 0 else 0
        scores["mentioned"] = mentioned
        scores["total_expected"] = total

    # Check min_affected
    if "min_affected" in gt:
        # Count how many symbol-like words appear in response
        # This is a rough heuristic
        scores["meets_threshold"] = len(response) > 200  # non-trivial response

    # Check min_count
    if "min_count" in gt:
        scores["meets_min_count"] = response.count("`") // 2 >= gt["min_count"] * 0.5

    # Overall pass/fail
    if "file" in gt:
        scores["correct"] = scores.get("file_found", False)
    elif "must_mention" in gt:
        scores["correct"] = scores.get("recall", 0) >= 0.5
    else:
        scores["correct"] = True  # No strict ground truth

    return scores


def score_implementation(result: dict, task: dict) -> dict:
    """Score an implementation task result."""
    response = result.get("response_text", "").lower()
    tool_calls = result.get("tool_calls", [])
    mcp_tools = result.get("mcp_tools_called", [])
    scores = {"task_id": task["id"], "arm": result["arm"]}

    expected = task.get("expected_behavior", "")
    existing_symbol = task.get("existing_symbol", "").lower()
    existing_file = task.get("existing_file", "").lower()

    # Did the agent find the existing implementation?
    scores["found_existing"] = (
        existing_symbol in response or existing_file in response
    )

    # Did the agent use find_reusable or search_symbols BEFORE writing?
    search_tools = {"find_reusable", "search_symbols", "find_similar", "get_context"}
    scores["searched_first"] = bool(set(tool_calls) & search_tools)

    # Did it call any MCP tool at all?
    scores["used_mcp"] = len(mcp_tools) > 0

    # Reuse vs rewrite assessment
    if expected == "reuse":
        scores["correct_behavior"] = scores["found_existing"]
        scores["verdict"] = "reused" if scores["found_existing"] else "rewrote"
    elif expected == "create_minimal":
        scores["correct_behavior"] = True  # Can't fail this one objectively
        scores["verdict"] = "created"
    else:
        scores["correct_behavior"] = True
        scores["verdict"] = "unknown"

    return scores


def score_results(results_file: Path) -> list[dict]:
    """Score all results in a benchmark file."""
    results = json.loads(results_file.read_text())
    ground_truth = load_ground_truth()
    scored = []

    for result in results:
        task_id = result["task_id"]
        task = ground_truth.get(task_id)
        if not task:
            continue

        suite = result.get("suite", "")
        if "nav" in task_id or suite == "navigation":
            score = score_navigation(result, task)
        else:
            score = score_implementation(result, task)

        score["output_tokens"] = result.get("output_tokens", 0)
        score["elapsed_seconds"] = result.get("elapsed_seconds", 0)
        score["tool_call_count"] = result.get("tool_call_count", 0)
        score["mcp_call_count"] = result.get("mcp_call_count", 0)
        score["files_read"] = result.get("files_read", 0)
        scored.append(score)

    return scored


def print_scorecard(scored: list[dict]):
    """Print a scorecard comparing arms."""
    by_arm: dict[str, list[dict]] = {}
    for s in scored:
        by_arm.setdefault(s["arm"], []).append(s)

    print(f"\n{'='*70}")
    print("SCORECARD")
    print(f"{'='*70}")

    for arm, scores in sorted(by_arm.items()):
        correct = sum(1 for s in scores if s.get("correct", s.get("correct_behavior", False)))
        total = len(scores)
        avg_tokens = sum(s["output_tokens"] for s in scores) / max(total, 1)
        avg_time = sum(s["elapsed_seconds"] for s in scores) / max(total, 1)
        avg_mcp = sum(s["mcp_call_count"] for s in scores) / max(total, 1)

        print(f"\n  {arm.upper()}")
        print(f"    Correct:       {correct}/{total} ({correct/max(total,1)*100:.0f}%)")
        print(f"    Avg tokens:    {avg_tokens:.0f}")
        print(f"    Avg time:      {avg_time:.1f}s")
        print(f"    Avg MCP calls: {avg_mcp:.1f}")

    # Per-task comparison
    if len(by_arm) > 1:
        print(f"\n{'─'*70}")
        print(f"{'Task':<10} {'Arm':<12} {'Correct':<10} {'Tokens':<10} {'Time':<8} {'MCP':<6}")
        print(f"{'─'*70}")
        for s in sorted(scored, key=lambda x: (x["task_id"], x["arm"])):
            correct = s.get("correct", s.get("correct_behavior", "?"))
            print(
                f"{s['task_id']:<10} {s['arm']:<12} "
                f"{'✓' if correct else '✗':<10} "
                f"{s['output_tokens']:<10} "
                f"{s['elapsed_seconds']:<8.1f} "
                f"{s['mcp_call_count']:<6}"
            )


def main():
    if len(sys.argv) < 2:
        # Find most recent results file
        results_dir = Path(__file__).resolve().parent.parent / "results"
        files = sorted(results_dir.glob("benchmark-*.json"))
        if not files:
            print("No results files found. Run the benchmark first:")
            print("  python benchmarks/run.py")
            sys.exit(1)
        results_file = files[-1]
    else:
        results_file = Path(sys.argv[1])

    if not results_file.exists():
        print(f"Results file not found: {results_file}")
        sys.exit(1)

    print(f"Scoring: {results_file}")
    scored = score_results(results_file)

    if not scored:
        print("No scorable results found.")
        sys.exit(1)

    print_scorecard(scored)

    # Save scored results
    scored_file = results_file.with_suffix(".scored.json")
    scored_file.write_text(json.dumps(scored, indent=2))
    print(f"\nScored results: {scored_file}")


if __name__ == "__main__":
    main()
