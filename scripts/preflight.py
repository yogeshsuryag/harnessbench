"""Validate the local benchmark contract without launching a job."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from common import (
    CATALOG_PATH,
    CONFIG_PATH,
    DATASET_NAME,
    DATASET_REF,
    ENV_PATH,
    LOCKED_MODEL_ID,
    MODEL_ID_ENV,
    ROOT,
    command_result,
    endpoint_metadata,
    load_json,
    model_metadata,
    parse_dotenv,
    resolve_model_placeholders,
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

    source_config = load_json(args.config)
    errors: list[str] = []
    warnings: list[str] = []
    endpoint_provider_tags: list[str] = []

    versions = tool_versions()
    expected_harbor = source_config["toolchain"]["harbor_version"]
    actual_harbor = versions.get("harbor") or ""
    if expected_harbor not in actual_harbor:
        errors.append(f"Harbor {expected_harbor} is required; found {actual_harbor or 'unavailable'}")

    env_values = parse_dotenv(args.env_file)
    for required in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY"):
        if not (os.environ.get(required) or env_values.get(required)):
            errors.append(f"{required} is missing or empty in the process environment or {args.env_file}")

    raw_model_id = os.environ.get(MODEL_ID_ENV) or env_values.get(MODEL_ID_ENV)
    model_id = raw_model_id.strip() if raw_model_id else None
    if not model_id:
        errors.append(f"{MODEL_ID_ENV} is missing or empty in the process environment or {args.env_file}")
        config = source_config
    else:
        config = resolve_model_placeholders(source_config, model_id)

    model = config["model"]
    if model.get("id_env") != MODEL_ID_ENV:
        errors.append(f"The experiment model must use {MODEL_ID_ENV}")
    if model_id and model["id"] != LOCKED_MODEL_ID:
        errors.append(f"The experiment model must remain {LOCKED_MODEL_ID}")
    if model.get("openrouter_provider") != "stealth":
        errors.append("The experiment must use the stealth OpenRouter provider")
    if model.get("openrouter_base_url") != "https://openrouter.ai/api/v1":
        errors.append("The experiment must use OpenRouter's /api/v1 endpoint")
    if model.get("provider_order") != ["stealth"]:
        errors.append("OpenRouter provider_order must be exactly ['stealth']")
    if model.get("allow_fallbacks") is not False:
        errors.append("OpenRouter fallbacks must be disabled")
    if model.get("require_parameters") is not True:
        errors.append("OpenRouter require_parameters must be enabled")
    metadata = model_metadata(model_id) if model_id else None
    endpoints = endpoint_metadata(model_id) if model_id else None
    if model_id and metadata is None:
        errors.append("OpenRouter model metadata could not be fetched")
    elif metadata is not None:
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

    if model_id and endpoints is None:
        warnings.append("OpenRouter endpoint metadata could not be fetched")
    elif endpoints is not None:
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

    catalog: dict = {}
    try:
        catalog = load_json(CATALOG_PATH)
        catalog_tasks = catalog.get("tasks", {})
        if catalog.get("dataset") != DATASET_NAME:
            errors.append(f"TB4 task catalog must target {DATASET_NAME}")
        if catalog.get("dataset_ref") != DATASET_REF:
            errors.append(f"TB4 task catalog must be pinned to {DATASET_REF}")
        if catalog.get("task_count") != 66 or len(catalog_tasks) != 66:
            errors.append("TB4 task catalog must contain exactly 66 tasks")
        if catalog.get("release") != "v4.0.0":
            errors.append("TB4 task catalog must be pinned to v4.0.0")
        digests = [metadata.get("digest") for metadata in catalog_tasks.values()]
        if len(set(digests)) != len(digests):
            errors.append("TB4 task catalog task digests must be unique")
        for field in ("gpu_tasks", "compose_tasks", "sidecar_artifact_tasks"):
            listed_tasks = catalog.get(field, [])
            unknown_tasks = sorted(set(listed_tasks) - set(catalog_tasks))
            if unknown_tasks:
                errors.append(f"TB4 catalog {field} contains unknown tasks: {unknown_tasks}")
    except (OSError, ValueError) as error:
        errors.append(f"TB4 task catalog could not be loaded: {error}")

    if dataset.get("name") != DATASET_NAME:
        errors.append(f"The pilot dataset must be {DATASET_NAME}")
    if dataset.get("ref") != DATASET_REF:
        if not dataset.get("ref") and args.allow_latest:
            warnings.append("Dataset is unpinned; this invocation is discovery-only")
        else:
            errors.append(f"The pilot dataset ref must be {DATASET_REF}")
    if dataset.get("scope") != "full" or dataset.get("task_names") is not None:
        errors.append("The pilot must run the full 66-task dataset")

    resources = config.get("execution", {}).get("resources", {})
    if any(value is not None for value in resources.values()):
        errors.append("Global resource overrides must remain null so TB4 task resources are used")

    for agent in config["agents"]:
        if not agent.get("version"):
            errors.append(f"Agent {agent['id']} has no pinned version")
        native_config = ROOT / agent["native_config"]
        if not native_config.exists():
            errors.append(f"Missing native config for {agent['id']}: {native_config}")

        if agent["id"] == "pi" and agent.get("model_api") != "openai-completions":
            errors.append("Pi must use model_api=openai-completions for OpenRouter")
        expected_opencode2_model = f"openrouter/{model_id or '${MODEL_ID}'}"
        if agent["id"] == "opencode2" and agent.get("model_argument") != expected_opencode2_model:
            errors.append("OpenCode 2 must use the OpenRouter-qualified model argument")
        if agent["id"] == "codex" and agent.get("harbor_agent") != "custom_agents.codex:OpenRouterCodex":
            errors.append("Codex must use the full-model OpenRouter adapter")

    study = config.get("study", {})
    catalog_task_count = catalog.get("task_count", 0)
    selected_task_count = len(config["dataset"].get("task_names") or range(catalog_task_count))
    expected_trials = len(config["agents"]) * selected_task_count
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
            key: bool(os.environ.get(key) or env_values.get(key))
            for key in ("OPENROUTER_API_KEY", "DAYTONA_API_KEY", MODEL_ID_ENV)
        },
        "model_id": model_id,
        "model_id_env": MODEL_ID_ENV,
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
