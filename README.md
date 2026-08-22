# harnessbench

`harnessbench` runs reproducible Terminal-Bench comparisons across terminal
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
- Full comparison: 370 trials

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

Run one Oracle task before agent trials:

```text
uv run --locked harbor run -d terminal-bench/terminal-bench@sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3 -a oracle -n 1 -k 1 -l 1 --env daytona --env-file .env --yes
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

Run the full suite only after all smoke tests pass:

```text
uv run --locked python scripts/run_benchmark.py --agent all
```

Measure concurrency separately at `2`, `4`, `8`, and `16`, keeping the model,
dataset, resources, timeout, attempts, retry policy, and prompt unchanged.

## Results

Raw Harbor output is stored in `results/runs/<run-id>/` and ignored by Git.
Manifests record the experiment contract, tool versions, model metadata,
configuration hashes, timestamps, job identifiers, statuses, and artifact
paths. Inventory completed artifacts with:

```text
uv run --locked python scripts/inventory_results.py results/runs/<run-id>
```

Do not manually edit raw jobs, trajectories, logs, or manifests. Keep agent,
model API, infrastructure, verifier, and benchmark failures distinct.

## Repository Layout

```text
config/experiment.json       locked experiment contract
config/agents/               native agent configurations
config/schemas/              JSON schemas
custom_agents/opencode2.py   Harbor adapter for OpenCode 2
scripts/preflight.py         no-job validation
scripts/run_benchmark.py     job launcher and manifest writer
scripts/inventory_results.py raw artifact hashing
results/runs/                ignored raw jobs and logs
```
