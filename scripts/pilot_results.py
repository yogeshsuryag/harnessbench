"""Normalize Harbor pilot results into SWE-focused derived summaries."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Iterable

from common import COHORTS, ROOT, relative_path, sha256_file, task_names_for_cohort, utc_now


CATALOG_PATH = ROOT / "config" / "tb3_task_catalog.json"
RUNS_PATH = ROOT / "results" / "runs"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def task_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rsplit("/", 1)[-1]


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    started = parse_timestamp(value.get("started_at"))
    finished = parse_timestamp(value.get("finished_at"))
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


def sum_contexts(result: dict[str, Any]) -> tuple[int | None, int | None, int | None, float | None]:
    contexts: list[dict[str, Any]] = []
    if isinstance(result.get("agent_result"), dict):
        contexts.append(result["agent_result"])
    for step in result.get("step_results") or []:
        if isinstance(step, dict) and isinstance(step.get("agent_result"), dict):
            contexts.append(step["agent_result"])

    if not contexts:
        return None, None, None, None

    totals: list[int | None] = [None, None, None]
    cost: float | None = None
    for context in contexts:
        for index, key in enumerate(("n_input_tokens", "n_cache_tokens", "n_output_tokens")):
            value = context.get(key)
            if isinstance(value, int):
                totals[index] = (totals[index] or 0) + value
        value = context.get("cost_usd")
        if isinstance(value, (int, float)):
            cost = (cost or 0.0) + float(value)
    return totals[0], totals[1], totals[2], cost


def reward_data(trial_dir: Path, result: dict[str, Any]) -> dict[str, Any] | None:
    verifier_result = result.get("verifier_result")
    rewards = verifier_result.get("rewards") if isinstance(verifier_result, dict) else None
    if not isinstance(rewards, dict):
        for filename in ("reward.json", "reward.txt"):
            path = trial_dir / "verifier" / filename
            if not path.is_file():
                continue
            try:
                if filename.endswith(".json"):
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                else:
                    candidate = {"reward": float(path.read_text(encoding="utf-8").strip())}
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict):
                rewards = candidate
                break

    if not isinstance(rewards, dict):
        return None

    numeric = {
        key: value
        for key, value in rewards.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    scalar: float | None = None
    if isinstance(numeric.get("reward"), (int, float)):
        scalar = float(numeric["reward"])
    elif len(numeric) == 1:
        scalar = float(next(iter(numeric.values())))

    passed: bool | None = None
    if scalar is not None and scalar in {0.0, 1.0}:
        passed = scalar == 1.0

    return {
        "rewards": rewards,
        "numeric_rewards": numeric,
        "primary_reward": scalar,
        "passed": passed,
    }


def classify_exception(result: dict[str, Any]) -> tuple[str, str]:
    exception = result.get("exception_info")
    if not isinstance(exception, dict):
        return "unknown", "unknown"

    exception_type = str(exception.get("exception_type") or "").lower()
    message = str(exception.get("exception_message") or "").lower()
    combined = f"{exception_type} {message}"

    if any(token in exception_type for token in ("api", "authentication", "modelnotfound")):
        return "model_api", "agent"
    if any(token in combined for token in ("rate limit", "openrouter", "http 4", "http 5", "provider", "api key")):
        return "model_api", "agent"
    if any(token in exception_type for token in ("networkconnection", "environment", "daytona", "docker")):
        return "infrastructure", "environment"
    if any(token in combined for token in ("timeout", "timed out", "gpu", "container", "sandbox")):
        return "infrastructure", "environment"
    if "verifier" in combined or "reward" in combined:
        return "verifier", "verifier"
    return "agent", "agent"


def trajectory_stats(trial_dir: Path) -> dict[str, Any]:
    paths = sorted(trial_dir.rglob("trajectory.json"))
    if not paths:
        return {
            "trajectory_path": None,
            "trajectory_sha256": None,
            "trajectory_steps": None,
            "agent_turns": None,
            "tool_calls": None,
        }

    path = paths[0]
    try:
        trajectory = load_json(path)
    except (OSError, ValueError):
        trajectory = {}
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        steps = []
    agent_steps = [step for step in steps if isinstance(step, dict) and step.get("source") == "agent"]
    tool_calls = 0
    for step in agent_steps:
        calls = step.get("tool_calls")
        if isinstance(calls, list):
            tool_calls += len(calls)
    return {
        "trajectory_path": relative_path(path),
        "trajectory_sha256": sha256_file(path),
        "trajectory_steps": len(steps),
        "agent_turns": len(agent_steps),
        "tool_calls": tool_calls,
    }


def trial_result_paths(jobs_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for filename in ("result.json", "results.json"):
        paths.extend(jobs_dir.rglob(filename))
    return sorted(set(paths))


def normalize_trial(run_dir: Path, result_path: Path, catalog: dict[str, Any]) -> dict[str, Any] | None:
    try:
        result = load_json(result_path)
    except (OSError, ValueError):
        return None
    if "task_name" not in result or "trial_name" not in result:
        return None

    manifest_path = run_dir / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    agent = manifest.get("agent") if isinstance(manifest.get("agent"), dict) else {}
    agent_id = agent.get("id") or (result.get("agent_info") or {}).get("name")
    normalized_task_id = task_id(result.get("task_name"))
    task = catalog.get("tasks", {}).get(normalized_task_id or "", {})
    trial_dir = result_path.parent
    rewards = reward_data(trial_dir, result)
    input_tokens, cache_tokens, output_tokens, cost = sum_contexts(result)
    exception_category, exception_stage = classify_exception(result)

    if rewards is not None:
        passed = rewards["passed"]
        outcome = "pass" if passed is True else "task_fail" if passed is False else "graded"
        failure_category = "none"
        failure_stage = "verifier"
    elif result.get("exception_info"):
        passed = False
        outcome = "error"
        failure_category = exception_category
        failure_stage = exception_stage
    else:
        passed = False
        outcome = "incomplete"
        failure_category = "unknown"
        failure_stage = "unknown"

    started = parse_timestamp(result.get("started_at"))
    finished = parse_timestamp(result.get("finished_at"))
    wall_time = None
    if started is not None and finished is not None:
        wall_time = max(0.0, (finished - started).total_seconds())

    trial = {
        "run_id": manifest.get("run_id") or run_dir.name,
        "agent_id": agent_id,
        "agent_version": agent.get("version"),
        "task_id": normalized_task_id,
        "task_name": result.get("task_name"),
        "task_digest": task.get("digest"),
        "domain": task.get("domain"),
        "subcategory": task.get("subcategory"),
        "coding_relevance": task.get("coding_relevance"),
        "capabilities": task.get("capabilities", []),
        "harbor_trial_id": result.get("id"),
        "trial_name": result.get("trial_name"),
        "task_checksum": result.get("task_checksum"),
        "requested_model": (result.get("agent_info") or {}).get("model_info", {}).get("name"),
        "served_model": None,
        "outcome": outcome,
        "passed": passed,
        "verifier_valid": rewards is not None,
        "failure_category": failure_category,
        "failure_stage": failure_stage,
        "primary_reward": rewards.get("primary_reward") if rewards else None,
        "rewards": rewards.get("rewards") if rewards else None,
        "input_tokens": input_tokens,
        "cached_tokens": cache_tokens,
        "uncached_input_tokens": (
            max(input_tokens - cache_tokens, 0)
            if isinstance(input_tokens, int) and isinstance(cache_tokens, int)
            else None
        ),
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "wall_time_sec": wall_time,
        "environment_setup_sec": duration(result.get("environment_setup")),
        "agent_setup_sec": duration(result.get("agent_setup")),
        "agent_time_sec": duration(result.get("agent_execution")),
        "verifier_time_sec": duration(result.get("verifier")),
        "exception_type": ((result.get("exception_info") or {}).get("exception_type")),
        "exception_message": ((result.get("exception_info") or {}).get("exception_message")),
        "native_result_path": relative_path(result_path),
        "trial_directory": relative_path(trial_dir),
    }
    trial.update(trajectory_stats(trial_dir))
    return trial


def discover_run_dirs(inputs: list[Path]) -> list[Path]:
    if inputs:
        return [path.resolve() for path in inputs]
    if not RUNS_PATH.is_dir():
        return []
    return sorted(path for path in RUNS_PATH.iterdir() if path.is_dir())


def load_rows(run_dirs: list[Path], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        manifest_path = run_dir / "manifest.json"
        job_config_path = run_dir / "job-config.json"
        if not manifest_path.is_file() or not job_config_path.is_file():
            continue
        try:
            job_config = load_json(job_config_path)
        except (OSError, ValueError):
            continue
        jobs_dir = Path(job_config.get("jobs_dir", run_dir / "harbor-job"))
        if not jobs_dir.is_absolute():
            jobs_dir = run_dir / jobs_dir
        if not jobs_dir.is_dir():
            continue
        for result_path in trial_result_paths(jobs_dir):
            row = normalize_trial(run_dir, result_path, catalog)
            if row is not None:
                rows.append(row)
    return rows


def category_key(row: dict[str, Any]) -> str:
    return str(row.get("subcategory") or row.get("domain") or "unknown")


def build_summaries(
    rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    expected_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    if expected_task_ids is None:
        expected_task_ids = set(task_names_for_cohort(catalog))
    else:
        expected_task_ids = set(expected_task_ids)
    expected_task_count = len(expected_task_ids)

    agents = sorted({str(row.get("agent_id")) for row in rows if row.get("agent_id")})
    if not agents:
        agents = ["opencode", "opencode2", "codex", "claude-code", "pi"]

    harness_summary: list[dict[str, Any]] = []
    for agent in agents:
        selected = [row for row in rows if row.get("agent_id") == agent]
        counts = Counter(row.get("outcome") for row in selected)
        failures = Counter(row.get("failure_category") for row in selected)
        valid = [row for row in selected if row.get("verifier_valid")]
        observed_tasks = {
            row.get("task_id") for row in selected if row.get("task_id") in expected_task_ids
        }
        passed = sum(row.get("passed") is True for row in selected)
        valid_passed = sum(row.get("passed") is True for row in valid)
        harness_summary.append(
            {
                "agent_id": agent,
                "expected_observations": expected_task_count,
                "scheduled_observations": len(selected),
                "unobserved_observations": max(expected_task_count - len(observed_tasks), 0),
                "passes": passed,
                "end_to_end_pass_rate": passed / len(selected) if selected else None,
                "valid_verifier_observations": len(valid),
                "valid_task_passes": valid_passed,
                "valid_task_pass_rate": valid_passed / len(valid) if valid else None,
                "outcomes": dict(sorted(counts.items())),
                "failure_categories": dict(sorted(failures.items())),
                "median_wall_time_sec": median([row["wall_time_sec"] for row in selected]),
                "median_output_tokens": median([row["output_tokens"] for row in selected]),
                "median_cost_usd": median([row["cost_usd"] for row in selected]),
            }
        )

    category_summary: list[dict[str, Any]] = []
    category_names = sorted({category_key(row) for row in rows})
    for category in category_names:
        for agent in agents:
            selected = [
                row
                for row in rows
                if row.get("agent_id") == agent and category_key(row) == category
            ]
            valid = [row for row in selected if row.get("verifier_valid")]
            passes = sum(row.get("passed") is True for row in selected)
            valid_passes = sum(row.get("passed") is True for row in valid)
            if not selected:
                continue
            category_summary.append(
                {
                    "agent_id": agent,
                    "category": category,
                    "domain": selected[0].get("domain"),
                    "coding_relevance": selected[0].get("coding_relevance"),
                    "tasks_observed": len({row.get("task_id") for row in selected}),
                    "scheduled_observations": len(selected),
                    "passes": passes,
                    "end_to_end_pass_rate": passes / len(selected),
                    "valid_verifier_observations": len(valid),
                    "valid_task_passes": valid_passes,
                    "valid_task_pass_rate": valid_passes / len(valid) if valid else None,
                    "failure_categories": dict(
                        sorted(Counter(row.get("failure_category") for row in selected).items())
                    ),
                }
            )

    catalog_tasks = catalog.get("tasks", {})
    by_task: dict[str, dict[str, Any]] = {
        task_name: {
            "task_id": task_name,
            "task_digest": metadata.get("digest"),
            "domain": metadata.get("domain"),
            "subcategory": metadata.get("subcategory"),
            "coding_relevance": metadata.get("coding_relevance"),
            "capabilities": metadata.get("capabilities", []),
            "harnesses": {},
        }
        for task_name, metadata in catalog_tasks.items()
        if task_name in expected_task_ids
    }
    for row in rows:
        task = row.get("task_id") or "unknown"
        item = by_task.setdefault(
            task,
            {
                "task_id": task,
                "task_digest": row.get("task_digest"),
                "domain": row.get("domain"),
                "subcategory": row.get("subcategory"),
                "coding_relevance": row.get("coding_relevance"),
                "capabilities": row.get("capabilities", []),
                "harnesses": {},
            },
        )
        item["harnesses"][row.get("agent_id")] = {
            "outcome": row.get("outcome"),
            "passed": row.get("passed"),
            "failure_category": row.get("failure_category"),
            "primary_reward": row.get("primary_reward"),
        }

    unique_wins = []
    difficulty_ladder = Counter()
    complete_task_count = 0
    for item in by_task.values():
        if len(item["harnesses"]) != len(agents):
            continue
        complete_task_count += 1
        passed_agents = sorted(
            agent for agent, result in item["harnesses"].items() if result.get("passed") is True
        )
        difficulty_ladder[len(passed_agents)] += 1
        if len(passed_agents) == 1:
            unique_wins.append(
                {
                    "task_id": item["task_id"],
                    "domain": item["domain"],
                    "subcategory": item["subcategory"],
                    "winner": passed_agents[0],
                    "harnesses": item["harnesses"],
                }
            )

    capability_summary: list[dict[str, Any]] = []
    capability_names = sorted(
        {
            capability
            for row in rows
            for capability in row.get("capabilities", [])
        }
    )
    for capability in capability_names:
        for agent in agents:
            selected = [
                row
                for row in rows
                if row.get("agent_id") == agent
                and capability in row.get("capabilities", [])
            ]
            if not selected:
                continue
            valid = [row for row in selected if row.get("verifier_valid")]
            passes = sum(row.get("passed") is True for row in selected)
            valid_passes = sum(row.get("passed") is True for row in valid)
            capability_summary.append(
                {
                    "agent_id": agent,
                    "capability": capability,
                    "tasks_observed": len({row.get("task_id") for row in selected}),
                    "scheduled_observations": len(selected),
                    "passes": passes,
                    "end_to_end_pass_rate": passes / len(selected),
                    "valid_verifier_observations": len(valid),
                    "valid_task_passes": valid_passes,
                    "valid_task_pass_rate": valid_passes / len(valid) if valid else None,
                }
            )

    return {
        "task_catalog_count": len(catalog.get("tasks", {})),
        "expected_task_count": expected_task_count,
        "observed_trial_count": len(rows),
        "observed_task_count": len({row.get("task_id") for row in rows if row.get("task_id")}),
        "complete_task_count": complete_task_count,
        "harness_summary": harness_summary,
        "category_summary": category_summary,
        "capability_summary": capability_summary,
        "task_matrix": sorted(by_task.values(), key=lambda item: item["task_id"]),
        "unique_wins": sorted(unique_wins, key=lambda item: item["task_id"]),
        "difficulty_ladder": {
            f"{count}_of_5_harnesses_passed": difficulty_ladder.get(count, 0)
            for count in range(6)
        },
    }


def median(values: list[Any]) -> float | None:
    numeric = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not numeric:
        return None
    middle = len(numeric) // 2
    if len(numeric) % 2:
        return numeric[middle]
    return (numeric[middle - 1] + numeric[middle]) / 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directories", nargs="*", type=Path)
    parser.add_argument("--cohort", choices=COHORTS)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "derived" / "pilot",
    )
    args = parser.parse_args()

    catalog = load_json(CATALOG_PATH)
    tasks = catalog.get("tasks", {})
    if len(tasks) != catalog.get("task_count"):
        raise SystemExit(
            f"Task catalog count mismatch: expected {catalog.get('task_count')}, found {len(tasks)}"
        )

    run_dirs = discover_run_dirs(args.run_directories)
    rows = load_rows(run_dirs, catalog)
    expected_task_ids = set(task_names_for_cohort(catalog, args.cohort))
    if args.cohort is not None:
        rows = [row for row in rows if row.get("coding_relevance") == args.cohort]
    summaries = build_summaries(rows, catalog, expected_task_ids)
    write_jsonl(args.output / "trial_records.jsonl", rows)
    write_json(args.output / "harness_summary.json", summaries["harness_summary"])
    write_json(args.output / "category_summary.json", summaries["category_summary"])
    write_json(args.output / "capability_summary.json", summaries["capability_summary"])
    write_json(args.output / "task_matrix.json", summaries["task_matrix"])
    write_json(
        args.output / "pilot_summary.json",
        {
            "schema_version": "1.0",
            "created_at": utc_now(),
            "cohort": args.cohort,
            "input_run_directories": [relative_path(path) for path in run_dirs],
            **summaries,
        },
    )
    print(
        f"Normalized {len(rows)} Harbor trial results from {len(run_dirs)} run directories "
        f"into {relative_path(args.output)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
