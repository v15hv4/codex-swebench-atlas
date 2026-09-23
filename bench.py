#!/usr/bin/env python3
"""Run a CLI coding agent on SWE-bench Atlas and grade its patches."""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from datasets import load_dataset


DEFAULT_DATASET = "swebenchatlas/swe-bench-atlas-anon-public"
DEFAULT_COMMAND = "codex exec --json --dangerously-bypass-approvals-and-sandbox -"


def run(command, *, cwd=None, stdin=None, stdout=None, timeout=None):
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=stdout,
        stderr=subprocess.STDOUT if stdout else None,
        text=True,
        start_new_session=True,
        shell=isinstance(command, str),
    )
    try:
        process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return 124
    return process.returncode


def token_usage(path):
    """Read Codex-style JSONL. A custom harness can write ATLAS_METRICS_PATH."""
    best = {}

    def visit(value):
        nonlocal best
        if isinstance(value, dict):
            found = {
                key: int(value[key])
                for key in ("input_tokens", "cached_input_tokens", "output_tokens")
                if isinstance(value.get(key), (int, float))
            }
            if sum(found.values()) > sum(best.values()):
                best = found
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            try:
                visit(json.loads(line))
            except json.JSONDecodeError:
                pass
    best["total_tokens"] = best.get("input_tokens", 0) + best.get("output_tokens", 0)
    return best


def checkout(task, target):
    repo = task["repo"]
    url = repo if "://" in repo else f"https://github.com/{repo}.git"
    target.mkdir(parents=True)
    commands = [
        ["git", "init", "-q"],
        ["git", "remote", "add", "origin", url],
        ["git", "fetch", "-q", "--depth", "1", "origin", task["base_commit"]],
        ["git", "checkout", "-q", "FETCH_HEAD"],
    ]
    for command in commands:
        subprocess.run(command, cwd=target, check=True)


def make_patch(repo, base_commit):
    subprocess.run(["git", "add", "-N", "."], cwd=repo, check=False, stdout=subprocess.DEVNULL)
    return subprocess.run(
        ["git", "diff", "--binary", base_commit, "--", "."],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def tasks(args):
    rows = load_dataset(args.dataset, split=args.split)
    selected = set(args.ids or [])
    result = [row for row in rows if not selected or row["instance_id"] in selected]
    if args.seed is not None:
        result.sort(key=lambda row: hashlib.sha256(
            f"{args.seed}\0{row['instance_id']}".encode()
        ).digest())
    return result[: args.limit or None]


def generate_task(args, task, run_dir):
    instance_id = task["instance_id"]
    task_dir = run_dir / "tasks" / instance_id
    repo = task_dir / "repo"
    shutil.rmtree(task_dir, ignore_errors=True)
    checkout(task, repo)
    output = task_dir / "harness.jsonl"
    custom_metrics = task_dir / "metrics.json"
    prompt = (
        "Work on the issue below in this repository. Implement the fix in the working tree. "
        "Do not only describe the fix. Do not read Git history or search for the upstream solution.\n\n"
        + task["problem_statement"]
    )
    env = os.environ.copy()
    env.update({
        "ATLAS_INSTANCE_ID": instance_id,
        "ATLAS_METRICS_PATH": str(custom_metrics),
    })
    print(f"{instance_id}: running", flush=True)
    started = time.monotonic()
    with output.open("w") as log:
        process = subprocess.Popen(
            args.command,
            cwd=repo,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True,
            start_new_session=True,
            env=env,
        )
        timed_out = False
        try:
            process.communicate(prompt, timeout=args.agent_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    elapsed = round(time.monotonic() - started, 3)
    usage = token_usage(output)
    if custom_metrics.exists():
        usage.update(json.loads(custom_metrics.read_text()))
    patch = make_patch(repo, task["base_commit"])
    prediction = {
        "instance_id": instance_id,
        "model_name_or_path": args.model,
        "model_patch": patch,
    }
    metric = {
        "instance_id": instance_id,
        "wall_time_seconds": elapsed,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "patch_bytes": len(patch.encode()),
        **usage,
    }
    return prediction, metric


def generate(args, rows, run_dir):
    prediction_file = run_dir / "predictions.jsonl"
    metrics_file = run_dir / "generation.jsonl"
    completed = {
        json.loads(line)["instance_id"]
        for line in prediction_file.read_text().splitlines()
    } if prediction_file.exists() else set()
    pending = [task for task in rows if task["instance_id"] not in completed]
    print(f"Generating {len(pending)} tasks with {args.generate_workers} workers", flush=True)
    with ThreadPoolExecutor(max_workers=args.generate_workers) as pool:
        futures = {pool.submit(generate_task, args, task, run_dir): task for task in pending}
        for future in as_completed(futures):
            prediction, metric = future.result()
            with prediction_file.open("a") as file:
                file.write(json.dumps(prediction) + "\n")
            with metrics_file.open("a") as file:
                file.write(json.dumps(metric) + "\n")
            print(f"{metric['instance_id']}: {metric['wall_time_seconds']}s, {metric.get('total_tokens', 0)} tokens", flush=True)


def evaluate(args, rows, run_dir):
    ids = [task["instance_id"] for task in rows]
    atlas_config = run_dir / "atlas-config.jsonl"
    with atlas_config.open("w") as file:
        for task in rows:
            config = task["environment_config"]
            if isinstance(config, str):
                config = ast.literal_eval(config)
            file.write(json.dumps({
                "repo": task["repo"],
                "instance_id": task["instance_id"],
                "language": task["language"],
                "spec_dict": config,
            }) + "\n")

    # This Atlas revision reads configuration from --dataset_name at import
    # time, but its evaluator can load the actual tasks from Hugging Face.
    saved_argv = sys.argv
    sys.argv = [saved_argv[0], "--dataset_name", str(atlas_config)]
    try:
        from swebench.harness.run_evaluation import main as run_evaluation
    finally:
        sys.argv = saved_argv

    previous_dir = Path.cwd()
    os.chdir(run_dir)
    try:
        run_evaluation(
            dataset_name=args.dataset,
            split=args.split,
            instance_ids=ids,
            predictions_path=str(run_dir / "predictions.jsonl"),
            max_workers=args.workers,
            force_rebuild=False,
            cache_level=args.cache_level,
            clean=False,
            open_file_limit=4096,
            run_id=args.run_id,
            timeout=args.eval_timeout,
            namespace="",
            rewrite_reports=False,
            atlas_eval=True,
            max_retries=args.eval_retries,
        )
    finally:
        os.chdir(previous_dir)


def report(args, rows, run_dir):
    generation = {
        item["instance_id"]: item
        for item in map(json.loads, (run_dir / "generation.jsonl").read_text().splitlines())
    }
    results = []
    for task in rows:
        instance_id = task["instance_id"]
        reports = list((run_dir / "logs" / "run_evaluation" / args.run_id).glob(f"*/{instance_id}/report.json"))
        resolved = None
        if reports:
            data = json.loads(reports[0].read_text())
            resolved = bool(data[instance_id]["resolved"])
        results.append({**generation.get(instance_id, {"instance_id": instance_id}), "resolved": resolved})
    graded = [row for row in results if row["resolved"] is not None]
    summary = {
        "run_id": args.run_id,
        "tasks": len(results),
        "graded": len(graded),
        "resolved": sum(row["resolved"] for row in graded),
        "success_rate": (sum(row["resolved"] for row in graded) / len(graded)) if graded else None,
        "total_wall_time_seconds": round(sum(row.get("wall_time_seconds", 0) for row in results), 3),
        "total_tokens": sum(row.get("total_tokens", 0) for row in results),
        "results": results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "evaluate", "report", "run"), nargs="?", default="run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default="test")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", help="Select a repeatable sample when used with --limit")
    parser.add_argument("--command", default=os.getenv("HARNESS_CMD", DEFAULT_COMMAND))
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", "custom-codex"))
    parser.add_argument("--agent-timeout", type=int, default=3600)
    parser.add_argument("--eval-timeout", type=int, default=1800)
    parser.add_argument("--eval-retries", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--generate-workers", type=int, default=1)
    parser.add_argument("--cache-level", choices=("none", "base", "env", "instance"), default="env")
    args = parser.parse_args()
    if args.workers < 1 or args.generate_workers < 1:
        parser.error("worker counts must be at least 1")
    run_dir = Path("/bench/runs") / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = tasks(args)
    (run_dir / "selected_ids.json").write_text(json.dumps(
        [row["instance_id"] for row in rows], indent=2
    ) + "\n")
    if args.action in ("generate", "run"):
        generate(args, rows, run_dir)
    if args.action in ("evaluate", "run"):
        evaluate(args, rows, run_dir)
    if args.action in ("report", "run"):
        report(args, rows, run_dir)


if __name__ == "__main__":
    main()
