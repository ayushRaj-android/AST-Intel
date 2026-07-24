"""AST-Intel Benchmark Harness — Copilot CLI Edition.

Runs navigation and implementation tasks against two arms:
  - baseline: Copilot CLI WITHOUT ast-intel MCP
  - ast-intel: Copilot CLI WITH ast-intel MCP

Uses `copilot -p <prompt> --output-format json --allow-all-tools` for
headless execution with full JSONL output (token counts, tool calls, timing).

Usage:
    python benchmarks/run.py                     # Run all tasks, all arms, n=1
    python benchmarks/run.py --runs 3            # 3 runs per (task, arm)
    python benchmarks/run.py --arm ast-intel     # Only one arm
    python benchmarks/run.py --task nav-01       # Only one task
    python benchmarks/run.py --suite navigation  # Only navigation tasks
    python benchmarks/run.py --dry-run           # Print commands without executing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
TASKS_DIR = BENCHMARKS_DIR / "tasks"
RESULTS_DIR = BENCHMARKS_DIR / "results"

# MCP config that EXCLUDES ast-intel (baseline arm)
BASELINE_MCP_CONFIG = {
    "mcpServers": {}
}

# Copilot CLI timeout per task (seconds)
TASK_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


def load_tasks(suite: str | None = None, task_id: str | None = None) -> list[dict]:
    """Load tasks from YAML files."""
    tasks = []
    files = list(TASKS_DIR.glob("*.yaml"))
    for f in sorted(files):
        if suite and suite not in f.stem:
            continue
        with f.open() as fh:
            data = yaml.safe_load(fh)
        for t in data.get("tasks", []):
            t["suite"] = f.stem
            tasks.append(t)

    if task_id:
        tasks = [t for t in tasks if t["id"] == task_id]

    return tasks


# ---------------------------------------------------------------------------
# Arm execution
# ---------------------------------------------------------------------------


def build_copilot_command(
    prompt: str,
    arm: str,
    repo_path: Path,
) -> list[str]:
    """Build the copilot CLI command for a given arm."""
    cmd = [
        "copilot",
        "-p", prompt,
        "--output-format", "json",
        "--allow-all-tools",
        "--add-dir", str(repo_path),
    ]

    if arm == "baseline":
        # Override MCP config to exclude ast-intel
        # Use an empty additional config — the key is that we DON'T load ast-intel
        # Copilot CLI reads ~/.copilot/mcp-config.json by default,
        # so for baseline we pass a flag to disable non-builtin MCP servers
        cmd.extend(["--additional-mcp-config", json.dumps(BASELINE_MCP_CONFIG)])
    # For "ast-intel" arm, we use the default config which already has ast-intel

    return cmd


def run_task(
    task: dict,
    arm: str,
    run_number: int,
    repo_path: Path,
    dry_run: bool = False,
) -> dict | None:
    """Execute a single task and return parsed results."""
    prompt = task["prompt"]
    task_id = task["id"]

    # Add context about the repo for baseline arm
    if arm == "baseline":
        prompt = (
            f"You are working on the ast-intel project at {repo_path}. "
            f"Use file reading and grep to answer. Do NOT use any MCP tools. "
            f"\n\n{task['prompt']}"
        )

    cmd = build_copilot_command(prompt, arm, repo_path)

    if dry_run:
        print(f"  [DRY RUN] {arm}/{task_id}/run{run_number}")
        print(f"    cmd: {' '.join(cmd[:6])}...")
        return None

    print(f"  Running: {arm}/{task_id}/run{run_number}...", end=" ", flush=True)
    start_time = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TASK_TIMEOUT,
            cwd=str(repo_path),
        )
        elapsed = time.time() - start_time
        output = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        elapsed = TASK_TIMEOUT
        output = ""
        stderr = "TIMEOUT"
        exit_code = -1

    print(f"({elapsed:.1f}s, exit={exit_code})")

    # Save raw JSONL output
    run_dir = RESULTS_DIR / f"run-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_file = run_dir / f"{arm}_{task_id}_run{run_number}.jsonl"
    raw_file.write_text(output)

    # Parse the JSONL for metrics
    metrics = parse_jsonl_metrics(output)
    metrics.update({
        "arm": arm,
        "task_id": task_id,
        "suite": task.get("suite", ""),
        "run": run_number,
        "elapsed_seconds": round(elapsed, 2),
        "exit_code": exit_code,
        "raw_file": str(raw_file),
    })

    return metrics


# ---------------------------------------------------------------------------
# JSONL parsing — extract metrics from copilot CLI output
# ---------------------------------------------------------------------------


def parse_jsonl_metrics(jsonl_output: str) -> dict:
    """Parse Copilot CLI JSONL output and extract benchmark metrics."""
    metrics = {
        "output_tokens": 0,
        "tool_calls": [],
        "mcp_tools_called": [],
        "files_read": 0,
        "response_text": "",
        "model": "",
        "reasoning_text": "",
    }

    for line in jsonl_output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")
        data = event.get("data", {})

        # Extract final assistant message (has token counts)
        if event_type == "assistant.message":
            metrics["output_tokens"] = data.get("outputTokens", 0)
            metrics["model"] = data.get("model", "")
            metrics["response_text"] = data.get("content", "")

        # Track tool calls via tool.execution_start events
        if event_type == "tool.execution_start":
            tool_name = data.get("toolName", "")
            metrics["tool_calls"].append(tool_name)
            # MCP tools are prefixed with server name: "ast-intel-<tool>"
            if data.get("mcpServerName") or tool_name.startswith("ast-intel-"):
                metrics["mcp_tools_called"].append(tool_name)
            # Count file-reading tools
            if tool_name in ("read_file", "grep", "file_search", "semantic_search", "list_dir"):
                metrics["files_read"] += 1

        # Collect reasoning
        if event_type == "assistant.reasoning_delta":
            metrics["reasoning_text"] += data.get("deltaContent", "")

    # Summarize
    metrics["tool_call_count"] = len(metrics["tool_calls"])
    metrics["unique_tools"] = list(set(metrics["tool_calls"]))
    metrics["mcp_call_count"] = len(metrics["mcp_tools_called"])

    return metrics


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_benchmark(
    arms: list[str],
    suite: str | None,
    task_id: str | None,
    runs: int,
    repo_path: Path,
    dry_run: bool,
) -> list[dict]:
    """Run the full benchmark suite."""
    tasks = load_tasks(suite=suite, task_id=task_id)
    if not tasks:
        print("No tasks found matching the filter.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"AST-Intel Benchmark")
    print(f"  Arms:  {', '.join(arms)}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Runs:  {runs} per (task, arm)")
    print(f"  Total: {len(tasks) * len(arms) * runs} executions")
    print(f"  Repo:  {repo_path}")
    print(f"{'='*60}\n")

    all_results = []

    for task in tasks:
        print(f"\nTask: {task['id']} — {task['prompt'][:60]}...")
        for arm in arms:
            for run_num in range(1, runs + 1):
                result = run_task(task, arm, run_num, repo_path, dry_run=dry_run)
                if result:
                    all_results.append(result)

    # Save aggregated results
    if all_results:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        summary_file = RESULTS_DIR / f"benchmark-{stamp}.json"
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(all_results, indent=2))
        print(f"\nResults saved to: {summary_file}")

    return all_results


def main():
    parser = argparse.ArgumentParser(description="AST-Intel Benchmark Harness")
    parser.add_argument(
        "--arm", choices=["baseline", "ast-intel", "all"], default="all",
        help="Which arm to run (default: all)",
    )
    parser.add_argument(
        "--suite", choices=["navigation", "implementation"],
        help="Run only one task suite",
    )
    parser.add_argument("--task", help="Run only a specific task ID (e.g. nav-01)")
    parser.add_argument("--runs", type=int, default=1, help="Runs per (task, arm)")
    parser.add_argument(
        "--repo", type=Path, default=REPO_ROOT,
        help="Repository to benchmark against",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands only")
    args = parser.parse_args()

    arms = ["baseline", "ast-intel"] if args.arm == "all" else [args.arm]

    results = run_benchmark(
        arms=arms,
        suite=args.suite,
        task_id=args.task,
        runs=args.runs,
        repo_path=args.repo,
        dry_run=args.dry_run,
    )

    if results:
        print_summary(results)


def print_summary(results: list[dict]):
    """Print a quick comparison table."""
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    # Group by arm
    by_arm: dict[str, list[dict]] = {}
    for r in results:
        by_arm.setdefault(r["arm"], []).append(r)

    print(f"\n{'Metric':<25} ", end="")
    for arm in sorted(by_arm.keys()):
        print(f"{arm:<15}", end="")
    print()
    print("-" * 55)

    for metric_name, metric_key in [
        ("Avg output tokens", "output_tokens"),
        ("Avg time (seconds)", "elapsed_seconds"),
        ("Avg tool calls", "tool_call_count"),
        ("Avg MCP calls", "mcp_call_count"),
        ("Avg files read", "files_read"),
    ]:
        print(f"{metric_name:<25} ", end="")
        for arm in sorted(by_arm.keys()):
            vals = [r[metric_key] for r in by_arm[arm] if metric_key in r]
            avg = sum(vals) / max(len(vals), 1)
            print(f"{avg:<15.1f}", end="")
        print()

    # Reduction calculation
    if "baseline" in by_arm and "ast-intel" in by_arm:
        print(f"\n{'--- Reduction ---':<25}")
        for metric_name, metric_key in [
            ("Token reduction", "output_tokens"),
            ("Time reduction", "elapsed_seconds"),
            ("File reads avoided", "files_read"),
        ]:
            base_vals = [r[metric_key] for r in by_arm["baseline"]]
            intel_vals = [r[metric_key] for r in by_arm["ast-intel"]]
            base_avg = sum(base_vals) / max(len(base_vals), 1)
            intel_avg = sum(intel_vals) / max(len(intel_vals), 1)
            if base_avg > 0:
                reduction = (1 - intel_avg / base_avg) * 100
                print(f"{metric_name:<25}  {reduction:+.0f}%")


if __name__ == "__main__":
    main()
