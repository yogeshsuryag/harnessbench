"""Validate the local benchmark contract without launching a job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from common import (
    CONFIG_PATH,
    ENV_PATH,
    ROOT,
    command_result,
    endpoint_metadata,
    load_json,
    model_metadata,
    parse_dotenv,
    tool_versions,
    uv_run_command,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument(
        "--allow-latest",
        action="store_true",
        help="Allow an unpinned dataset for discovery only; never use for final runs.",
    )
    args = parser.parse_args()

    config = load_json(args.config)
    errors: list[str] = []
    warnings: list[str] = []
    endpoint_provider_tags: list[str] = []

    versions = tool_versions()
    expected_harbor = config["toolchain"]["harbor_version"]
    actual_harbor = versions.get("harbor") or ""
    if expected_harbor not in actual_harbor:
        errors.append(f"Harbor {expected_harbor} is required; found {actual_harbor or 'unavailable'}")

    env_values = parse_dotenv(args.env_file)
    for required in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY"):
        if not env_values.get(required):
            errors.append(f"{required} is missing or empty in {args.env_file}")

    model = config["model"]
    if model["id"] != "stealth/ox-alpha":
        errors.append("The experiment model must remain stealth/ox-alpha")
    metadata = model_metadata(model["id"])
    endpoints = endpoint_metadata(model["id"])
    if metadata is None:
        errors.append("OpenRouter model metadata could not be fetched")
    else:
        data = metadata.get("data", {})
        if data.get("id") != model["id"]:
            errors.append("OpenRouter returned metadata for a different model ID")
        supported = set(data.get("supported_parameters", []))
        for parameter in ("tools", "tool_choice", "reasoning"):
            if parameter not in supported:
                errors.append(f"Ox Alpha metadata does not advertise {parameter}")
        if data.get("reasoning", {}).get("mandatory") is not True:
            warnings.append("Ox Alpha reasoning is no longer advertised as mandatory")

        if "reasoning_effort" not in supported:
            errors.append("Ox Alpha metadata does not advertise reasoning_effort")

    if endpoints is None:
        warnings.append("OpenRouter endpoint metadata could not be fetched")
    else:
        endpoint_data = endpoints.get("data", {})
        endpoint_rows = endpoint_data.get("endpoints", [])
        provider_tags = {
            str(row.get("tag") or row.get("provider_name") or "").lower()
            for row in endpoint_rows
            if isinstance(row, dict)
        }
        provider_tags.discard("")
        endpoint_provider_tags = sorted(provider_tags)
        expected_provider = model["openrouter_provider"].lower()
        if provider_tags != {expected_provider}:
            errors.append(
                "OpenRouter model endpoints are not exclusively routed through "
                f"{expected_provider}: {sorted(provider_tags)}"
            )

    dataset = config["dataset"]
    if not dataset.get("ref"):
        if args.allow_latest:
            warnings.append("Dataset is unpinned; this invocation is discovery-only")
        else:
            errors.append("config/experiment.json dataset.ref must be pinned before a benchmark run")

    catalog_path = ROOT / "config" / "tb3_task_catalog.json"
    try:
        catalog = load_json(catalog_path)
        catalog_tasks = catalog.get("tasks", {})
        if catalog.get("task_count") != 74 or len(catalog_tasks) != 74:
            errors.append("TB3 task catalog must contain exactly 74 tasks")
        if catalog.get("release") != "v3.0.0":
            errors.append("TB3 task catalog must be pinned to v3.0.0")
    except (OSError, ValueError) as error:
        errors.append(f"TB3 task catalog could not be loaded: {error}")

    if dataset.get("scope") != "full" or dataset.get("task_names") is not None:
        errors.append("The pilot must run the full 74-task dataset")

    for agent in config["agents"]:
        if not agent.get("version"):
            errors.append(f"Agent {agent['id']} has no pinned version")
        native_config = ROOT / agent["native_config"]
        if not native_config.exists():
            errors.append(f"Missing native config for {agent['id']}: {native_config}")

        if agent["id"] == "pi" and agent.get("model_api") != "openai-completions":
            errors.append("Pi must use model_api=openai-completions for OpenRouter")
        if agent["id"] == "opencode2" and agent.get("model_argument") != "openrouter/stealth/ox-alpha":
            errors.append("OpenCode 2 must use the OpenRouter-qualified model argument")
        if agent["id"] == "codex" and agent.get("harbor_agent") != "custom_agents.codex:OpenRouterCodex":
            errors.append("Codex must use the full-model OpenRouter adapter")

    study = config.get("study", {})
    expected_trials = len(config["agents"]) * len(config["dataset"].get("task_names") or range(74))
    if study.get("scheduled_trials") != expected_trials:
        errors.append(
            f"Pilot scheduled_trials must equal {expected_trials}; "
            f"found {study.get('scheduled_trials')}"
        )

    import_check = command_result(
        uv_run_command(
            "python",
            "-c",
            "from custom_agents.opencode2 import OpenCode2; print(OpenCode2.import_path())",
        ),
        cwd=ROOT,
    )
    if import_check.returncode != 0:
        errors.append(
            "OpenCode 2 adapter import failed: "
            + (import_check.stderr or import_check.stdout).strip()
        )

    for module_path, class_name in (
        ("custom_agents.codex", "OpenRouterCodex"),
        ("custom_agents.opencode2", "OpenCode2"),
    ):
        check = command_result(
            uv_run_command(
                "python",
                "-c",
                f"from {module_path} import {class_name}; print({class_name}.name())",
            ),
            cwd=ROOT,
        )
        if check.returncode != 0:
            errors.append(
                f"{class_name} import failed: "
                + (check.stderr or check.stdout).strip()
            )

    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "versions": versions,
        "environment_file": str(args.env_file),
        "environment_presence": {
            key: bool(env_values.get(key))
            for key in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY")
        },
        "model_id": model["id"],
        "model_metadata_available": metadata is not None,
        "endpoint_metadata_available": endpoints is not None,
        "endpoint_provider_tags": endpoint_provider_tags,
        "single_provider_route_verified": endpoint_provider_tags == [model["openrouter_provider"]],
        "dataset": dataset,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
