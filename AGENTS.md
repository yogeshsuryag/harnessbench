# Repository Instructions

## Current State

- This repository runs the 370-trial Terminal-Bench 3.0 pilot (74 tasks x 5 harnesses, 1 replication per task-harness) with Harbor controller scripts, pinned agent configurations, custom Harbor adapters for OpenCode 2 and Codex, the TB3 task catalog at `config/tb3_task_catalog.json` (v3.0.0, 74 digests with domain, subcategory, coding relevance, and capabilities), JSON schemas (`config/schemas/experiment.schema.json`, `config/schemas/tb3_task_catalog.schema.json`, `results/schema/pilot-trial.schema.json`), and the locked experiment contract at `config/experiment.json` with `study.phase=pilot`.
- `README.md` is the operational source of truth for setup, smoke tests, benchmark runs, and result handling.
- Harbor is a project dependency managed by `pyproject.toml` and `uv.lock`. Use `uv run --locked` rather than a globally installed Harbor command.
- Before relying on Harbor flags or custom-agent APIs, run `uv run --locked harbor run --help` and inspect the installed Harbor version. Do not copy an old adapter or CLI spelling blindly.

## Architecture And Controls

- The Windows machine is the controller. Harbor orchestrates jobs; Daytona provides independent Linux task sandboxes where agents, commands, and verifiers run.
- The pilot is descriptive: `study.phase=pilot`, `scheduled_trials=370`, `replications_per_task_harness=1`, `dataset.scope=full` with 74 tasks. It identifies which harness solves which task and capability category and does not claim statistical significance from one trial per task.
- All agents must call the same exact OpenRouter `MODEL_ID` `stealth/ox-alpha` through the exclusive `stealth` provider. Use `provider_order=["stealth"]`, `allow_fallbacks=false`, `require_parameters=true`, `openrouter_base_url=https://openrouter.ai/api/v1`, and `reasoning_effort=max`. Never use OpenRouter Auto, a moving alias, a native provider endpoint, or a silent fallback model.
- Keep dataset/version, task IDs, task image/resources, timeout, attempts, concurrency, prompt, model settings, and OpenRouter routing identical across agents. The harness is the experimental variable. `study.scheduled_trials` must equal `agents x tasks` and the full 74-task suite must be used for the pilot.
- Never modify Terminal-Bench task instructions, dependencies, expected outputs, or verifiers. Preserve the task prompt when adapting it to a CLI.
- Test one task before scaling. Use the same task IDs for every agent, then increase concurrency only after measuring stability at `1`, `2`, `4`, `8`, and `16`.
- Keep agent, model/API, infrastructure, and verifier failures separate. Preserve non-zero exit codes, `opencode2.stderr.txt`, and `harbor_process_ok`/`status=process-complete|process-error` rather than relabeling agent or infrastructure failures as task failures.

## Bootstrap And Smoke Tests

- Install `uv` globally with `winget install --id=astral-sh.uv -e`, then run `uv sync --locked`. Verify with `uv --version` and `uv run --locked harbor --version`.
- Inject secrets into the current PowerShell session or the ignored `.env` file only, for example `$env:OPENROUTER_API_KEY="<key>"` and `$env:DAYTONA_API_KEY="<key>"`. Never print or commit their values.
- Run `uv run --locked harbor dataset list` before selecting a dataset. The locked registry name is `terminal-bench/terminal-bench` at `sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3`.
- In the installed Harbor CLI, `-n` means concurrent trials, `-l` means maximum tasks, and `-k` means attempts. Keep all relevant values identical across agent runs; do not confuse concurrency with attempts.
- Validate the local contract without launching a job with `uv run --locked python scripts/preflight.py`. Preflight now verifies Harbor `0.22.0`, required env keys, OpenRouter model metadata (`tools`, `tool_choice`, `reasoning`, `reasoning_effort`), exclusive single-provider routing through `stealth`, TB3 catalog integrity (`task_count=74`, `release=v3.0.0`, 74 tasks), `dataset.scope=full`, agent pinned versions and native configs, `pi.model_api=openai-completions`, `opencode2.model_argument=openrouter/stealth/ox-alpha`, `codex.harbor_agent=custom_agents.codex:OpenRouterCodex`, `study.scheduled_trials`, and importability of `OpenRouterCodex` and `OpenCode2`.
- Verify the current Oracle smoke command with `uv run --locked harbor run --help` before running it. Browse recorded jobs with Harbor's current viewer command.
- Run one task for each agent with the same dataset, `-n`, `-l`, `-k`, and model settings. Confirm the request appears in OpenRouter, resolves to `stealth/ox-alpha` via `stealth`, and reaches the Terminal-Bench verifier before attempting a full comparison.
- Before interpreting the pilot, validate the released suite with the five-pass Oracle job (separate from the 370 agent trials per TB 3.0 task-health guidance): `uv run --locked harbor run -d terminal-bench/terminal-bench@sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3 -a oracle -n 1 -k 5 -l 74 --env daytona --env-file .env --yes`, then `uv run --locked python scripts/validate_oracle.py jobs/<oracle-job-directory>` which requires 5 passing observations per task and reports missing, unexpected, insufficient, and non-passing tasks.
- Check Daytona capacity/credits and OpenRouter usage after the smoke test. Do not launch a large or high-concurrency run before estimating cost and confirming the environment works.

## Provider Gotchas

- Codex uses OpenRouter's OpenAI-compatible endpoint `https://openrouter.ai/api/v1` with `wire_api="responses"` via `config/agents/codex.toml`. It must not call OpenAI directly. Use the custom Harbor adapter `custom_agents.codex:OpenRouterCodex` which preserves the full `provider/model` slug (`stealth/ox-alpha`) instead of Harbor's default Codex prefix stripping.
- Claude Code uses OpenRouter's Anthropic-compatible endpoint `https://openrouter.ai/api`, not `/api/v1`. Use `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and an empty `ANTHROPIC_API_KEY`; remove cached direct Anthropic authentication with `/logout` if necessary.
- OpenCode 1 (`opencode`) and Pi must use OpenRouter with the fixed model ID; do not let their defaults select a different model. Pi must use `model_api=openai-completions` with `reasoning_effort=max` and `native_reasoning_setting=xhigh` in `config/agents/pi.json`. OpenCode 2 must use `model=openrouter/stealth/ox-alpha` with `provider.order=["stealth"]`, `allow_fallbacks=false`, and `require_parameters=true` in `config/agents/opencode2.json`.
- OpenCode 1 and OpenCode 2 are separate products. Do not assume their configuration, provider, plugin, or CLI APIs are interchangeable.

## OpenCode 2 Integration

- OpenCode 2 is the custom Harbor integration at `custom_agents/opencode2.py` (subclass of Harbor's `OpenCode`).
- Install it inside the Linux Daytona sandbox with `npm install -g @opencode-ai/cli@beta` (version `0.0.0-beta-17927`), verify with `opencode2 --version`, and run headlessly with `opencode2 run "<instruction>"`. Do not use the interactive TUI for benchmark jobs.
- The installed Harbor CLI accepts a custom agent import through `--agent module.path:ClassName` (`custom_agents.opencode2:OpenCode2` and `custom_agents.codex:OpenRouterCodex`); confirm the current syntax again when registering the adapter.
- Keep installation, configuration, execution, output capture, and error handling distinct.
- The adapter must preserve the original instruction, capture stdout to `opencode2.txt` and stderr to `opencode2.stderr.txt` via `tee` with `stdbuf -oL` and process substitution, return the agent exit status, and let Harbor collect the trajectory for verification.

## Results And Reproducibility

- Do not commit API keys, provider credentials, tokens, cookies, authentication files, raw benchmark outputs, or large generated trajectories.
- Do not manually edit raw jobs, trajectories, logs, or manifests. Raw Harbor output is stored in `results/runs/<run-id>/` and ignored by Git. Manifests now record the full experiment contract, `tool_versions`, `model_metadata`/`endpoint_metadata`, `routing_policy` (`provider`, `provider_order`, `allow_fallbacks`, `require_parameters`), `source_hashes` for `config/experiment.json`, native agent configs, `pyproject.toml`, `uv.lock`, and adapter files, timestamps, job identifiers, `status=process-complete|process-error`, `harbor_process_ok`, `exit_code`, and artifact paths. Inventory completed artifacts with `uv run --locked python scripts/inventory_results.py results/runs/<run-id>` and normalize with `uv run --locked python scripts/pilot_results.py`.
- Derived pilot summaries are written by `scripts/pilot_results.py` to `results/derived/pilot/` (`trial_records.jsonl` per Harbor trial, `harness_summary.json`, `category_summary.json`, `capability_summary.json`, `task_matrix.json`, `pilot_summary.json`) validated against `results/schema/pilot-trial.schema.json`. Keep agent, model API, infrastructure, verifier, and benchmark failures distinct via `failure_category` and `outcome=pass|task_fail|graded|error|incomplete`.
- Before a final comparison, confirm every agent used the same dataset, 74 task IDs with full scope, model ID, routing policy, prompt, resources, timeout, attempts, and concurrency policy.
