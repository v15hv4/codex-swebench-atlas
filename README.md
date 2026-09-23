# SWE-bench Atlas custom Codex runner

This runs a custom Codex CLI in an isolated outer Docker container. Atlas grading uses nested Docker containers. The outer container is privileged, but it does not use the host Docker socket.

## Start and configure the harness

```sh
docker compose up -d --build
docker compose exec bench bash
```

Inside the container, install your Codex CLI, sign in with OAuth, and edit `/root/.codex` as needed. This directory is a persistent volume. Confirm that your harness can run from a repository and accept the task prompt on standard input.

The default command is:

```sh
codex exec --json --dangerously-bypass-approvals-and-sandbox -
```

Set `HARNESS_CMD` when your custom harness uses a different command. Shell quoting is supported. The runner sets `ATLAS_INSTANCE_ID` and `ATLAS_METRICS_PATH` for every task. Codex JSONL token usage is detected automatically. A custom harness can instead write a JSON object such as `{"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}` to `ATLAS_METRICS_PATH`.

## Run

Start with one task:

```sh
atlas-bench run --run-id smoke --limit 1
```

Then run a larger sample or selected tasks:

```sh
atlas-bench run --run-id sample --limit 20 --generate-workers 2 --workers 2
atlas-bench run --run-id selected --ids owner__repo-123 owner__repo-456
```

Grading makes one attempt per task by default. This gives pass@1 results and avoids rebuilding a task after a normal test failure. Use `--eval-retries 3` only when you want retries for flaky tasks.

Select the same deterministic 50-task sample on every run with:

```sh
atlas-bench run --run-id seeded-50 --seed my-experiment-v1 --limit 50 --generate-workers 2 --workers 2
```

The seed can be any text. Keep the same dataset, split, seed, and limit to get the same task IDs. Use `--ids` when you need an explicit predetermined list.

`generate`, `evaluate`, and `report` are also separate actions. Generation is resumable. Use the same run ID and selection arguments when you resume.

`--generate-workers` runs agent tasks concurrently. `--workers` controls concurrent Atlas grading tasks. They apply to separate phases of `run`. Both default to 1. Start with 2 of each on a four-core machine. More workers can increase memory, disk, and model API use. After editing `bench.py`, copy it into a running container with `docker compose cp bench.py bench:/usr/local/bin/atlas-bench` and run `docker compose exec bench chmod +x /usr/local/bin/atlas-bench`. A future `docker compose up -d --build` also includes the edit, but replaces the outer container.

For a quick grading check of patches that already exist, run `atlas-bench evaluate --run-id <run-id> --limit <same-limit> --workers 2`, then `atlas-bench report` with the same selection options. Keep the `docker-data` volume between runs so Atlas can reuse base and environment images. `--cache-level instance` can also reuse task-specific images across runs, but uses more disk space.

Results are available on the host under `runs/<run-id>/`:

- `summary.json`: success rate and per-task result, wall time, token usage, exit status, timeout status, and patch size.
- `selected_ids.json`: the exact ordered task selection.
- `generation.jsonl`: raw generation metrics.
- `predictions.jsonl`: Atlas-compatible patches.
- `tasks/<instance>/harness.jsonl`: harness output.
- `logs/`: official Atlas grader logs and test output.

The default dataset is `swebenchatlas/swe-bench-atlas-anon-public`. Its license limits use to non-commercial research, academic, or educational purposes. Atlas can require substantial disk space. The persistent `docker-data` volume keeps environment images between runs.
