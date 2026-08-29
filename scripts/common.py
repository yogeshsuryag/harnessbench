"""Shared standard-library helpers for the benchmark runner."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Sequence
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "experiment.json"
CATALOG_PATH = ROOT / "config" / "tb4_task_catalog.json"
ENV_PATH = ROOT / ".env"
UV_COMMAND = os.environ.get("UV", "uv")
COHORTS = ("core_swe", "adjacent_engineering", "domain")
DATASET_NAME = "terminal-bench/terminal-bench"
DATASET_REF = "sha256:39d9f44b40420cde8fdcc087579c0d72a7e14fa3656d603c3f0d22fb35e27732"
MODEL_ID_ENV = "MODEL_ID"
MODEL_ID_PLACEHOLDER = "${MODEL_ID}"
LOCKED_MODEL_ID = "stealth/ox-alpha"


def task_names_for_cohort(catalog: dict[str, Any], cohort: str | None = None) -> list[str]:
    if cohort is not None and cohort not in COHORTS:
        raise ValueError(f"Unknown cohort {cohort!r}; expected one of {', '.join(COHORTS)}")

    tasks = catalog.get("tasks")
    if not isinstance(tasks, dict):
        raise ValueError("Task catalog is missing its tasks object")

    task_names = sorted(
        task_name
        for task_name, metadata in tasks.items()
        if isinstance(task_name, str)
        and (
            cohort is None
            or (isinstance(metadata, dict) and metadata.get("coding_relevance") == cohort)
        )
    )
    if cohort is not None and not task_names:
        raise ValueError(f"Cohort {cohort!r} does not contain any tasks")
    return task_names


def uv_run_command(*args: str) -> list[str]:
    return [UV_COMMAND, "run", "--locked", *args]


def harbor_command(*args: str) -> list[str]:
    return uv_run_command("harbor", *args)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_model_id(path: Path = ENV_PATH) -> str:
    model_id = os.environ.get(MODEL_ID_ENV) or parse_dotenv(path).get(MODEL_ID_ENV)
    if not model_id or not model_id.strip():
        raise ValueError(
            f"{MODEL_ID_ENV} is missing or empty in the process environment or {path}"
        )
    return model_id.strip()


def resolve_model_placeholders(value: Any, model_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key.replace(MODEL_ID_PLACEHOLDER, model_id)
            if isinstance(key, str)
            else key: resolve_model_placeholders(item, model_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [resolve_model_placeholders(item, model_id) for item in value]
    if isinstance(value, str):
        return value.replace(MODEL_ID_PLACEHOLDER, model_id)
    return value


def command_result(args: Sequence[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    command = list(args)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def command_text(args: Sequence[str], *, cwd: Path = ROOT) -> str | None:
    result = command_result(args, cwd=cwd)
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip()


def tool_versions() -> dict[str, str | None]:
    commands = {
        "harbor": harbor_command("--version"),
        "uv": [UV_COMMAND, "--version"],
        "python": uv_run_command("python", "--version"),
        "node": ["node", "--version"],
        "npm": ["npm.cmd" if os.name == "nt" else "npm", "--version"],
    }
    return {name: command_text(args) for name, args in commands.items()}


def fetch_json(url: str) -> dict[str, Any] | None:
    try:
        request = Request(url, headers={"User-Agent": "harnessbench-preflight/1.0"})
        with urlopen(request, timeout=30) as response:
            value = json.load(response)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def model_metadata(model_id: str) -> dict[str, Any] | None:
    payload = fetch_json("https://openrouter.ai/api/v1/models")
    if payload is None:
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == model_id:
            return {"data": row}
    return None


def endpoint_metadata(model_id: str) -> dict[str, Any] | None:
    return fetch_json(f"https://openrouter.ai/api/v1/models/{model_id}/endpoints")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
