# Repository Instructions

## Current State

- This repository contains the Harbor controller scripts, pinned agent configurations, OpenCode 2 adapter, JSON schemas, and the locked Terminal-Bench experiment contract.
- `README.md` is the operational source of truth for setup, smoke tests, benchmark runs, and result handling.
- Harbor is a project dependency managed by `pyproject.toml` and `uv.lock`. Use `uv run --locked` rather than a globally installed Harbor command.
- Before relying on Harbor flags or custom-agent APIs, run `uv run --locked harbor run --help` and inspect the installed Harbor version. Do not copy an old adapter or CLI spelling blindly.

## Architecture And Controls

- The Windows machine is the controller. Harbor orchestrates jobs; Daytona provides independent Linux task sandboxes where agents, commands, and verifiers run.
- All agents must call the same exact OpenRouter `MODEL_ID`. Never use OpenRouter Auto, a moving alias, a native provider endpoint, or a silent fallback model.
- Keep dataset/version, task IDs, task image/resources, timeout, attempts, concurrency, prompt, model settings, and OpenRouter routing identical across agents. The harness is the experimental variable.
- Never modify Terminal-Bench task instructions, dependencies, expected outputs, or verifiers. Preserve the task prompt when adapting it to a CLI.
- Test one task before scaling. Use the same task IDs for every agent, then increase concurrency only after measuring stability at `1`, `2`, `4`, `8`, and `16`.
- Keep agent, model/API, infrastructure, and verifier failures separate. Preserve non-zero exit codes and stderr rather than relabeling agent or infrastructure failures as task failures.

## Bootstrap And Smoke Tests

- Install `uv` globally with `winget install --id=astral-sh.uv -e`, then run `uv sync --locked`. Verify with `uv --version` and `uv run --locked harbor --version`.
- Inject secrets into the current PowerShell session or the ignored `.env` file only, for example `$env:OPENROUTER_API_KEY="<key>"` and `$env:DAYTONA_API_KEY="<key>"`. Never print or commit their values.
- Run `uv run --locked harbor dataset list` before selecting a dataset. The locked registry name is `terminal-bench/terminal-bench`.
- In the installed Harbor CLI, `-n` means concurrent trials, `-l` means maximum tasks, and `-k` means attempts. Keep all relevant values identical across agent runs; do not confuse concurrency with attempts.
- Verify the current Oracle smoke command with `uv run --locked harbor run --help` before running it. Browse recorded jobs with Harbor's current viewer command.
- Run one task for each agent with the same dataset, `-n`, `-l`, `-k`, and model settings. Confirm the request appears in OpenRouter, resolves to `stealth/ox-alpha`, and reaches the Terminal-Bench verifier before attempting a full comparison.
- Check Daytona capacity/credits and OpenRouter usage after the smoke test. Do not launch a large or high-concurrency run before estimating cost and confirming the environment works.

## Provider Gotchas

- Codex uses OpenRouter's OpenAI-compatible endpoint `https://openrouter.ai/api/v1` and the Responses wire mode. It must not call OpenAI directly.
- Claude Code uses OpenRouter's Anthropic-compatible endpoint `https://openrouter.ai/api`, not `/api/v1`. Use `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and an empty `ANTHROPIC_API_KEY`; remove cached direct Anthropic authentication with `/logout` if necessary.
- OpenCode 1 (`opencode`) and Pi must use OpenRouter with the fixed model ID; do not let their defaults select a different model.
- OpenCode 1 and OpenCode 2 are separate products. Do not assume their configuration, provider, plugin, or CLI APIs are interchangeable.

## OpenCode 2 Integration

- OpenCode 2 is the custom Harbor integration at `custom_agents/opencode2.py`.
- Install it inside the Linux Daytona sandbox with `npm install -g @opencode-ai/cli@beta`, verify with `opencode2 --version`, and run headlessly with `opencode2 run "<instruction>"`. Do not use the interactive TUI for benchmark jobs.
- The installed Harbor CLI accepts a custom agent import through `--agent module.path:ClassName`; confirm the current syntax again when registering the adapter.
- Keep installation, configuration, execution, output capture, and error handling distinct.
- The adapter must preserve the original instruction, capture stdout and stderr, return the agent exit status, and let Harbor collect the trajectory for verification.

## Results And Reproducibility

- Do not commit API keys, provider credentials, tokens, cookies, authentication files, raw benchmark outputs, or large generated trajectories.
- Do not manually edit raw results. Keep derived summaries separate and record Harbor, dataset, agent, Node/Python, model, routing, reasoning settings, resources, concurrency, attempts, and timestamps for comparison runs.
- Before a final comparison, confirm every agent used the same dataset, task IDs, model ID, routing policy, prompt, resources, timeout, attempts, and concurrency policy.
