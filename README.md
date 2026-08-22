# harnessbench

`harnessbench` runs a 370-trial Terminal-Bench 3.0 pilot across terminal
coding agents. Harbor controls the jobs on the Windows controller, while
Daytona provides independent Linux task sandboxes.

## Locked Experiment

- Model: `stealth/ox-alpha`
- OpenRouter provider: `stealth`
- Dataset: `terminal-bench/terminal-bench`
- Dataset ref: `sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3`
- Dataset size: 74 tasks
- Agents: OpenCode 1, OpenCode 2, Codex CLI, Claude Code, and Pi
- Attempts: 1
- Initial concurrency: 1
- Retries: 0
- Pilot comparison: 370 trials
- Replications per task and harness: 1

The dataset ref is immutable. Do not replace it with `latest` during a
comparison.

## Controller Setup

Install `uv` globally as the environment manager if needed, then create the
locked project environment:

```text
winget install --id=astral-sh.uv -e
uv sync --locked
uv run --locked harbor --version
```

The controller requires Python `>=3.12,<3.14` because Harbor `0.22.0` does not
support Python 3.11.

The project environment contains Harbor and its Daytona/Modal integrations.
Agent CLIs, Node, task dependencies, and verifiers are installed and run in
the remote Daytona sandboxes, not in the controller environment.

Create `.env` from `.env.example` and fill it locally:

```text
OPENROUTER_API_KEY=<your OpenRouter key>
DAYTONA_API_KEY=<your Daytona key>
```

Never commit or print secret values.

## Run Stages

Validate the local contract without launching a job:

```text
uv run --locked python scripts/preflight.py
```

Generate and inspect a job configuration:

```text
uv run --locked python scripts/run_benchmark.py --agent opencode --dry-run
uv run --locked harbor run --print-config --config results/runs/<run-id>/job-config.json
```

Run the one-task Oracle smoke test before agent trials:

```text
uv run --locked harbor run -d terminal-bench/terminal-bench@sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3 -a oracle -n 1 -k 1 -l 1 --env daytona --env-file .env --yes
```

Before interpreting the pilot, validate the released task suite with the
Oracle solution five times. This is separate from the 370 agent comparison
trials and follows the Terminal-Bench 3.0 task-health guidance:

```text
uv run --locked harbor run -d terminal-bench/terminal-bench@sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3 -a oracle -n 1 -k 5 -l 74 --env daytona --env-file .env --yes
```

Validate the completed Oracle job before interpreting agent results:

```text
uv run --locked python scripts/validate_oracle.py jobs/<oracle-job-directory>
```

Run the same task with every agent after the Oracle smoke test passes:

```text
uv run --locked python scripts/run_benchmark.py --agent opencode --task ico-path-patch
uv run --locked python scripts/run_benchmark.py --agent opencode2 --task ico-path-patch
uv run --locked python scripts/run_benchmark.py --agent codex --task ico-path-patch
uv run --locked python scripts/run_benchmark.py --agent claude-code --task ico-path-patch
uv run --locked python scripts/run_benchmark.py --agent pi --task ico-path-patch
```

Confirm that every request uses `stealth/ox-alpha` through the `stealth`
provider and that the Terminal-Bench verifier runs before scaling up.

Run the staged pilot only after all smoke tests pass. The cohort selector uses
the pinned `coding_relevance` field in `config/tb3_task_catalog.json` and keeps
the full 370-trial contract unchanged:

```text
uv run --locked python scripts/run_benchmark.py --agent all --cohort core_swe
uv run --locked python scripts/run_benchmark.py --agent all --cohort adjacent_engineering
uv run --locked python scripts/run_benchmark.py --agent all --cohort domain
```

The three stages contain 25, 27, and 22 tasks respectively, for 125, 135,
and 110 agent trials. Each run manifest records the cohort and resolved task
list. Produce a stage-specific report with the matching cohort filter:

```text
uv run --locked python scripts/pilot_results.py --cohort core_swe --output results/derived/pilot/core_swe
uv run --locked python scripts/pilot_results.py --cohort adjacent_engineering --output results/derived/pilot/adjacent_engineering
uv run --locked python scripts/pilot_results.py --cohort domain --output results/derived/pilot/domain
```

Run `scripts/pilot_results.py` without `--cohort` after all stages to produce
the aggregate 74-task report.

The pilot is descriptive: it identifies which harness solves which task and
capability categories. It does not claim statistical significance from one
trial per task.

## Results

Raw Harbor output is stored in `results/runs/<run-id>/` and ignored by Git.
Manifests record the full experiment contract, `tool_versions`,
`model_metadata`/`endpoint_metadata`, `routing_policy` (`provider`,
`provider_order`, `allow_fallbacks`, `require_parameters`), `source_hashes`
for `config/experiment.json`, native agent configs, `pyproject.toml`,
`uv.lock`, and adapter files, timestamps, job identifiers,
`status=process-complete|process-error`, `harbor_process_ok`, `exit_code`,
and artifact paths. Inventory completed artifacts with:

```text
uv run --locked python scripts/inventory_results.py results/runs/<run-id>
```

Normalize completed Harbor trial results and produce pilot summaries with:

```text
uv run --locked python scripts/pilot_results.py
```

Derived output is written to `results/derived/pilot/`:

- `trial_records.jsonl`: one normalized row per Harbor trial
- `harness_summary.json`: overall pass and failure summaries
- `category_summary.json`: SWE capability and subcategory summaries
- `capability_summary.json`: cross-cutting coding capability summaries
- `task_matrix.json`: task-by-harness outcome matrix
- `pilot_summary.json`: combined pilot report data

Do not manually edit raw jobs, trajectories, logs, or manifests. Keep agent,
model API, infrastructure, verifier, and benchmark failures distinct via
`failure_category` and `outcome=pass|task_fail|graded|error|incomplete`.

## Repository Layout

```text
config/experiment.json       locked experiment contract
config/agents/               native agent configurations
config/schemas/              JSON schemas (experiment, TB3 catalog)
config/tb3_task_catalog.json Terminal-Bench 3.0 task taxonomy and digests
custom_agents/opencode2.py   Harbor adapter for OpenCode 2
custom_agents/codex.py       Full-model OpenRouter Codex adapter
scripts/preflight.py         no-job validation
scripts/validate_oracle.py   five-pass Oracle suite validation
scripts/run_benchmark.py     job launcher and manifest writer
scripts/inventory_results.py raw artifact hashing
scripts/pilot_results.py     pilot result normalization and summaries
results/schema/              pilot trial JSON schema
results/runs/                ignored raw jobs and logs
results/derived/pilot/       normalized pilot summaries
```
