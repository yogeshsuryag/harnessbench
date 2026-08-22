"""Run one or all configured agents with an immutable run manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from common import (
    CONFIG_PATH,
    ENV_PATH,
    ROOT,
    endpoint_metadata,
    harbor_command,
    load_json,
    model_metadata,
    parse_dotenv,
    relative_path,
    tool_versions,
    utc_now,
    write_json,
)


def run_id(agent_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{agent_id}-{uuid.uuid4().hex[:8]}"


def agent_environment(agent_id: str) -> dict[str, str]:
    common = {"OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}"}
    if agent_id in {"opencode", "opencode2", "pi"}:
        common["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"
    elif agent_id == "codex":
        common.update(
            {
                "OPENAI_API_KEY": "${OPENROUTER_API_KEY}",
                "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            }
        )
    elif agent_id == "claude-code":
        common.update(
            {
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                "ANTHROPIC_AUTH_TOKEN": "${OPENROUTER_API_KEY}",
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_DEFAULT_FABLE_MODEL": "stealth/ox-alpha",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "stealth/ox-alpha",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "stealth/ox-alpha",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "stealth/ox-alpha",
                "CLAUDE_CODE_SUBAGENT_MODEL": "stealth/ox-alpha",
            }
        )
    return common


def build_job_config(
    config: dict,
    agent: dict,
    run_directory: Path,
    concurrency: int,
    attempts: int,
    task_names: list[str] | None,
) -> dict:
    execution = config["execution"]
    resources = execution.get("resources", {})
    environment = {
        "type": execution["environment"],
        "delete": execution.get("delete_environment_after_trial", True),
    }
    resource_keys = {
        "cpus": "override_cpus",
        "memory_mb": "override_memory_mb",
        "storage_mb": "override_storage_mb",
        "gpus": "override_gpus",
    }
    for source, target in resource_keys.items():
        if resources.get(source) is not None:
            environment[target] = resources[source]

    native_config = ROOT / agent["native_config"]
    kwargs: dict = {}
    if agent["id"] in {"opencode", "opencode2"}:
        kwargs["opencode_config"] = load_json(ROOT / "config" / "agents" / "opencode.json")
        kwargs["variant"] = config["model"]["reasoning_effort"]
    elif agent["id"] == "codex":
        kwargs["config"] = str(native_config.resolve())
    elif agent["id"] == "claude-code":
        kwargs["config"] = str(native_config.resolve())
        kwargs["reasoning_effort"] = config["model"]["reasoning_effort"]
    elif agent["id"] == "pi":
        kwargs["thinking"] = "xhigh"

    kwargs["version"] = agent["version"]

    agent_config = {
        "name": agent["harbor_agent"],
        "model_name": agent["model_argument"],
        "kwargs": kwargs,
        "env": agent_environment(agent["id"]),
    }
    dataset = {
        "name": config["dataset"]["name"],
        "ref": config["dataset"]["ref"],
    }
    if task_names:
        dataset["task_names"] = task_names

    return {
        "job_name": run_directory.name,
        "jobs_dir": str((run_directory / "harbor-job").resolve()),
        "n_attempts": attempts,
        "n_concurrent_trials": concurrency,
        "timeout_multiplier": config["execution"]["timeout_multiplier"],
        "agent_timeout_multiplier": config["execution"].get("agent_timeout_multiplier"),
        "verifier_timeout_multiplier": config["execution"].get("verifier_timeout_multiplier"),
        "environment_build_timeout_multiplier": config["execution"].get(
            "environment_build_timeout_multiplier"
        ),
        "debug": True,
        "quiet": False,
        "retry": {"max_retries": config["execution"]["max_retries"]},
        "environment": environment,
        "agents": [agent_config],
        "datasets": [dataset],
        "extra_instructions": config["prompt_policy"]["extra_instructions"],
        "extra_instruction_paths": config["prompt_policy"]["extra_instruction_paths"],
    }


def run_one(
    config: dict,
    agent: dict,
    env_file: Path,
    concurrency: int,
    attempts: int,
    task_names: list[str] | None,
    dry_run: bool,
) -> int:
    run_directory = ROOT / "results" / "runs" / run_id(agent["id"])
    run_directory.mkdir(parents=True, exist_ok=False)
    job_config = build_job_config(
        config, agent, run_directory, concurrency, attempts, task_names
    )
    job_config_path = run_directory / "job-config.json"
    write_json(job_config_path, job_config)

    manifest = {
        "schema_version": "1.0",
        "run_id": run_directory.name,
        "started_at": utc_now(),
        "status": "prepared",
        "agent": deepcopy(agent),
        "experiment": deepcopy(config),
        "overrides": {
            "concurrency": concurrency,
            "attempts": attempts,
            "task_names": task_names,
        },
        "tool_versions": tool_versions(),
        "environment_presence": {
            key: bool(parse_dotenv(env_file).get(key))
            for key in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY")
        },
        "model_metadata": model_metadata(config["model"]["id"]),
        "endpoint_metadata": endpoint_metadata(config["model"]["id"]),
        "job_config_path": relative_path(job_config_path),
    }
    write_json(run_directory / "manifest.json", manifest)

    command = harbor_command(
        "run",
        "--config",
        str(job_config_path.resolve()),
        "--env-file",
        str(env_file.resolve()),
        "--yes",
    )
    write_json(run_directory / "command.json", {"argv": command})

    if dry_run:
        manifest["status"] = "dry-run"
        manifest["finished_at"] = utc_now()
        write_json(run_directory / "manifest.json", manifest)
        return 0

    with (run_directory / "stdout.log").open("w", encoding="utf-8", newline="\n") as stdout, (
        run_directory / "stderr.log"
    ).open("w", encoding="utf-8", newline="\n") as stderr:
        process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, check=False)

    manifest["status"] = "passed" if process.returncode == 0 else "process-error"
    manifest["exit_code"] = process.returncode
    manifest["finished_at"] = utc_now()
    write_json(run_directory / "manifest.json", manifest)
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["all", "opencode", "opencode2", "codex", "claude-code", "pi"], default="all")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--task", action="append", dest="task_names")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    execution = config["execution"]
    concurrency = args.concurrency or execution["concurrency"]
    attempts = args.attempts or execution["attempts"]
    task_names = args.task_names if args.task_names else config["dataset"].get("task_names")

    if not args.dry_run and not config["dataset"].get("ref"):
        raise SystemExit("Refusing to run with an unpinned dataset. Set dataset.ref first.")
    if not args.dry_run and not args.env_file.exists():
        raise SystemExit(f"Missing environment file: {args.env_file}")

    selected = config["agents"] if args.agent == "all" else [
        next(agent for agent in config["agents"] if agent["id"] == args.agent)
    ]
    exit_codes = [
        run_one(config, agent, args.env_file, concurrency, attempts, task_names, args.dry_run)
        for agent in selected
    ]
    return 0 if all(code == 0 for code in exit_codes) else 1


if __name__ == "__main__":
    sys.exit(main())
