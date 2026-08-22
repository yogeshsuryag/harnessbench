"""Validate a Terminal-Bench 3.0 Oracle job before interpreting the pilot."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

from common import ROOT, relative_path, utc_now


CATALOG_PATH = ROOT / "config" / "tb3_task_catalog.json"
REQUIRED_ORACLE_ATTEMPTS = 5


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def task_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rsplit("/", 1)[-1]


def reward_passed(trial_dir: Path, result: dict[str, Any]) -> bool | None:
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    if not isinstance(rewards, dict):
        reward_json = trial_dir / "verifier" / "reward.json"
        reward_text = trial_dir / "verifier" / "reward.txt"
        if reward_json.is_file():
            try:
                rewards = json.loads(reward_json.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                rewards = None
        elif reward_text.is_file():
            try:
                rewards = {"reward": float(reward_text.read_text(encoding="utf-8").strip())}
            except (OSError, ValueError):
                rewards = None
    if not isinstance(rewards, dict):
        return None
    numeric = [
        value
        for key, value in rewards.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and (key == "reward" or len(rewards) == 1)
    ]
    if len(numeric) != 1 or numeric[0] not in {0, 1, 0.0, 1.0}:
        return None
    return float(numeric[0]) == 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("oracle_job", type=Path, help="Harbor Oracle job directory")
    parser.add_argument(
        "--attempts",
        type=int,
        default=REQUIRED_ORACLE_ATTEMPTS,
        help="Required successful Oracle observations per task",
    )
    args = parser.parse_args()

    catalog = load_json(CATALOG_PATH)
    expected_tasks = set(catalog.get("tasks", {}))
    job_dir = args.oracle_job.resolve()
    if not job_dir.is_dir():
        print(f"Oracle job directory does not exist: {job_dir}", file=sys.stderr)
        return 1

    observations: list[dict[str, Any]] = []
    for filename in ("result.json", "results.json"):
        for result_path in job_dir.rglob(filename):
            try:
                result = load_json(result_path)
            except (OSError, ValueError):
                continue
            normalized_task = task_id(result.get("task_name"))
            if normalized_task is None or "trial_name" not in result:
                continue
            observations.append(
                {
                    "task_id": normalized_task,
                    "passed": reward_passed(result_path.parent, result),
                    "result_path": relative_path(result_path),
                }
            )

    counts = Counter(observation["task_id"] for observation in observations)
    failures = Counter(
        observation["task_id"]
        for observation in observations
        if observation["passed"] is not True
    )
    observed_tasks = set(counts)
    missing_tasks = sorted(expected_tasks - observed_tasks)
    unexpected_tasks = sorted(observed_tasks - expected_tasks)
    insufficient_attempts = sorted(
        task for task in expected_tasks if counts[task] < args.attempts
    )

    report = {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "oracle_job": relative_path(job_dir),
        "required_attempts_per_task": args.attempts,
        "expected_task_count": len(expected_tasks),
        "observed_trial_count": len(observations),
        "observed_task_count": len(observed_tasks & expected_tasks),
        "missing_tasks": missing_tasks,
        "unexpected_tasks": unexpected_tasks,
        "insufficient_attempts": insufficient_attempts,
        "nonpassing_observations": dict(sorted(failures.items())),
        "ok": not missing_tasks
        and not unexpected_tasks
        and not insufficient_attempts
        and not failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
