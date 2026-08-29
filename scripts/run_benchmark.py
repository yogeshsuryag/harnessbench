"""Run one or all configured agents with an immutable run manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import tomllib
import uuid

from common import (
    CATALOG_PATH,
    CONFIG_PATH,
    COHORTS,
    ENV_PATH,
    ROOT,
    endpoint_metadata,
    harbor_command,
    load_json,
    model_metadata,
    parse_dotenv,
    relative_path,
    resolve_model_id,
    resolve_model_placeholders,
    sha256_file,
    task_names_for_cohort,
    tool_versions,
    utc_now,
    write_json,
)


def run_id(agent_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{agent_id}-{uuid.uuid4().hex[:8]}"


def run_streaming(
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def forward(source, target, console) -> None:
        for line in iter(source.readline, ""):
            target.write(line)
            target.flush()
            try:
                console.write(line)
            except UnicodeEncodeError:
                encoding = getattr(console, "encoding", None) or "utf-8"
                safe_line = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
                console.write(safe_line)
            console.flush()
        source.close()

    with (
        stdout_path.open("w", encoding="utf-8", newline="\n") as stdout,
        stderr_path.open("w", encoding="utf-8", newline="\n") as stderr,
    ):
        threads = [
            threading.Thread(
                target=forward,
                args=(process.stdout, stdout, sys.stdout),
                daemon=True,
            ),
            threading.Thread(
                target=forward,
                args=(process.stderr, stderr, sys.stderr),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        return_code = process.wait()
        for thread in threads:
            thread.join()
    return return_code


def agent_environment(agent_id: str, model_id: str) -> dict[str, str]:
    common = {
        "MODEL_ID": model_id,
        "OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}",
    }
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
                "ANTHROPIC_DEFAULT_FABLE_MODEL": model_id,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model_id,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model_id,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model_id,
                "CLAUDE_CODE_SUBAGENT_MODEL": model_id,
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
    model_id = config["model"]["id"]
    kwargs: dict = {}
    if agent["id"] in {"opencode", "opencode2"}:
        kwargs["opencode_config"] = resolve_model_placeholders(
            load_json(native_config), model_id
        )
        kwargs["variant"] = config["model"]["reasoning_effort"]
    elif agent["id"] == "codex":
        kwargs["config"] = resolve_model_placeholders(
            tomllib.loads(native_config.read_text(encoding="utf-8")), model_id
        )
        kwargs["reasoning_effort"] = config["model"]["reasoning_effort"]
    elif agent["id"] == "claude-code":
        kwargs["config"] = resolve_model_placeholders(
            load_json(native_config), model_id
        )
        kwargs["reasoning_effort"] = config["model"]["reasoning_effort"]
    elif agent["id"] == "pi":
        kwargs["model_api"] = agent.get("model_api", "openai-completions")
        kwargs["thinking"] = agent.get("native_reasoning_setting", "xhigh")

    kwargs["version"] = agent["version"]

    agent_config = {
        "name": agent["harbor_agent"],
        "model_name": agent["model_argument"],
        "kwargs": kwargs,
        "env": agent_environment(agent["id"], model_id),
    }
    dataset = {
        "name": config["dataset"]["name"],
        "ref": config["dataset"]["ref"],
    }
    if task_names:
        namespace = dataset["name"].rsplit("/", 1)[0]
        dataset["task_names"] = [
            task_name if "/" in task_name else f"{namespace}/{task_name}"
            for task_name in task_names
        ]

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
    cohort: str | None,
    dry_run: bool,
) -> int:
    run_directory = ROOT / "results" / "runs" / run_id(agent["id"])
    run_directory.mkdir(parents=True, exist_ok=False)
    job_config = build_job_config(
        config, agent, run_directory, concurrency, attempts, task_names
    )
    job_config_path = run_directory / "job-config.json"
    write_json(job_config_path, job_config)

    native_config_path = ROOT / agent["native_config"]
    source_files = [
        CONFIG_PATH,
        CATALOG_PATH,
        native_config_path,
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
    ]
    if agent["id"] == "opencode2":
        source_files.append(ROOT / "custom_agents" / "opencode2.py")
    if agent["id"] == "codex":
        source_files.append(ROOT / "custom_agents" / "codex.py")

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
            "cohort": cohort,
            "task_names": task_names,
        },
        "tool_versions": tool_versions(),
        "environment_presence": {
            key: bool(parse_dotenv(env_file).get(key))
            for key in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY")
        },
        "model_metadata": model_metadata(config["model"]["id"]),
        "endpoint_metadata": endpoint_metadata(config["model"]["id"]),
        "routing_policy": {
            "model_id": config["model"]["id"],
            "provider": config["model"]["openrouter_provider"],
            "provider_order": config["model"]["provider_order"],
            "allow_fallbacks": config["model"]["allow_fallbacks"],
            "require_parameters": config["model"].get("require_parameters", False),
        },
        "source_hashes": {
            relative_path(path): sha256_file(path)
            for path in source_files
            if path.exists()
        },
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

    print(f"[{agent['id']}] Starting Harbor job {run_directory.name}", flush=True)
    exit_code = run_streaming(
        command,
        run_directory / "stdout.log",
        run_directory / "stderr.log",
    )
    print(f"[{agent['id']}] Harbor process exited with code {exit_code}", flush=True)

    manifest["status"] = "process-complete" if exit_code == 0 else "process-error"
    manifest["harbor_process_ok"] = exit_code == 0
    manifest["exit_code"] = exit_code
    manifest["finished_at"] = utc_now()
    write_json(run_directory / "manifest.json", manifest)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["all", "opencode", "opencode2", "codex", "claude-code", "pi"], default="all")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--attempts", type=int)
    task_selection = parser.add_mutually_exclusive_group()
    task_selection.add_argument("--cohort", choices=COHORTS)
    task_selection.add_argument("--task", action="append", dest="task_names")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        model_id = resolve_model_id(args.env_file)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    config = resolve_model_placeholders(load_json(args.config), model_id)
    execution = config["execution"]
    concurrency = args.concurrency or execution["concurrency"]
    attempts = args.attempts or execution["attempts"]
    catalog = load_json(CATALOG_PATH)

    if args.cohort:
        try:
            task_names = task_names_for_cohort(catalog, args.cohort)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    elif args.task_names:
        unknown_tasks = sorted(
            {
                task_name.rsplit("/", 1)[-1]
                for task_name in args.task_names
                if task_name.rsplit("/", 1)[-1] not in catalog.get("tasks", {})
            }
        )
        if unknown_tasks:
            raise SystemExit(f"Unknown TB4 task(s): {', '.join(unknown_tasks)}")
        task_names = args.task_names
    else:
        task_names = args.task_names if args.task_names else config["dataset"].get("task_names")

    if not args.dry_run and not config["dataset"].get("ref"):
        raise SystemExit("Refusing to run with an unpinned dataset. Set dataset.ref first.")
    if not args.dry_run and not args.env_file.exists():
        raise SystemExit(f"Missing environment file: {args.env_file}")

    selected = config["agents"] if args.agent == "all" else [
        next(agent for agent in config["agents"] if agent["id"] == args.agent)
    ]
    exit_codes = [
        run_one(
            config,
            agent,
            args.env_file,
            concurrency,
            attempts,
            task_names,
            args.cohort,
            args.dry_run,
        )
        for agent in selected
    ]
    return 0 if all(code == 0 for code in exit_codes) else 1


if __name__ == "__main__":
    sys.exit(main())
