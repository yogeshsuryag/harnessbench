"""Small standard-library tests for the pilot result normalizer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from common import (
    COHORTS,
    LOCKED_MODEL_ID,
    resolve_model_placeholders,
    task_names_for_cohort,
)
from pilot_results import build_summaries, load_json, load_rows
from run_benchmark import build_job_config


class PilotResultsTest(unittest.TestCase):
    def test_runner_qualifies_terminal_bench_task_names(self) -> None:
        config = resolve_model_placeholders(
            load_json(Path(__file__).resolve().parents[1] / "config" / "experiment.json"),
            LOCKED_MODEL_ID,
        )
        job_config = build_job_config(
            config,
            config["agents"][0],
            Path("run"),
            concurrency=1,
            attempts=1,
            task_names=["html-js-filter", "terminal-bench/bun-sourcemap-leak"],
        )

        self.assertEqual(
            job_config["datasets"][0]["task_names"],
            ["terminal-bench/html-js-filter", "terminal-bench/bun-sourcemap-leak"],
        )

    def test_runner_resolves_model_configuration_from_the_environment_value(self) -> None:
        config = resolve_model_placeholders(
            load_json(Path(__file__).resolve().parents[1] / "config" / "experiment.json"),
            LOCKED_MODEL_ID,
        )
        job_config = build_job_config(
            config,
            config["agents"][0],
            Path("run"),
            concurrency=1,
            attempts=1,
            task_names=None,
        )

        agent = job_config["agents"][0]
        self.assertEqual(agent["model_name"], f"openrouter/{LOCKED_MODEL_ID}")
        self.assertEqual(agent["env"]["MODEL_ID"], LOCKED_MODEL_ID)
        self.assertIn(
            LOCKED_MODEL_ID,
            agent["kwargs"]["opencode_config"]["provider"]["openrouter"]["models"],
        )

    def test_catalog_cohorts_partition_the_full_task_set(self) -> None:
        catalog = load_json(Path(__file__).resolve().parents[1] / "config" / "tb4_task_catalog.json")
        cohorts = [set(task_names_for_cohort(catalog, cohort)) for cohort in COHORTS]

        self.assertEqual([len(tasks) for tasks in cohorts], [21, 24, 21])
        self.assertEqual(set.union(*cohorts), set(catalog["tasks"]))
        self.assertEqual(sum(len(tasks) for tasks in cohorts), len(set.union(*cohorts)))

    def test_normalizes_a_harbor_trial_and_groups_it(self) -> None:
        catalog = load_json(Path(__file__).resolve().parents[1] / "config" / "tb4_task_catalog.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            jobs_dir = run_dir / "harbor-job" / "job"
            trial_dir = jobs_dir / "html-js-filter__abc1234"
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "agent").mkdir()

            (run_dir).mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "agent": {"id": "opencode", "version": "test"},
                        "experiment": {
                            "dataset": {
                                "name": "terminal-bench/terminal-bench",
                                "ref": "sha256:39d9f44b40420cde8fdcc087579c0d72a7e14fa3656d603c3f0d22fb35e27732",
                                "scope": "full",
                                "task_names": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "job-config.json").write_text(
                json.dumps({"jobs_dir": str(run_dir / "harbor-job")}),
                encoding="utf-8",
            )
            (trial_dir / "results.json").write_text(
                json.dumps(
                    {
                        "id": "trial-id",
                        "task_name": "terminal-bench/html-js-filter",
                        "trial_name": "html-js-filter__abc1234",
                        "task_checksum": "task-checksum",
                        "agent_info": {
                            "name": "opencode",
                            "version": "test",
                            "model_info": {"name": LOCKED_MODEL_ID},
                        },
                        "started_at": "2026-01-01T00:00:00Z",
                        "finished_at": "2026-01-01T00:00:02Z",
                        "agent_result": {
                            "n_input_tokens": 100,
                            "n_cache_tokens": 20,
                            "n_output_tokens": 40,
                            "cost_usd": 0.1,
                        },
                        "verifier_result": {"rewards": {"reward": 1}},
                    }
                ),
                encoding="utf-8",
            )

            rows = load_rows([run_dir], catalog)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["passed"])
            self.assertEqual(rows[0]["uncached_input_tokens"], 80)
            summary = build_summaries(rows, catalog, {"html-js-filter"})
            self.assertEqual(summary["harness_summary"][0]["passes"], 1)
            self.assertEqual(summary["harness_summary"][0]["expected_observations"], 1)
            self.assertEqual(summary["harness_summary"][0]["unobserved_observations"], 0)
            self.assertEqual(len(summary["task_matrix"]), 1)
            self.assertEqual(
                next(item for item in summary["task_matrix"] if item["task_id"] == "html-js-filter")[
                    "harnesses"
                ]["opencode"]["passed"],
                True,
            )

    def test_rejects_a_legacy_dataset_manifest(self) -> None:
        catalog = load_json(Path(__file__).resolve().parents[1] / "config" / "tb4_task_catalog.json")
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "legacy-run"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "experiment": {
                            "dataset": {
                                "name": "terminal-bench/terminal-bench",
                                "ref": "sha256:a32a61879ea94eb9dc16fa1fbeb398759f0c07ca633d9d1f6aec760207036da3",
                                "scope": "full",
                                "task_names": None,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "job-config.json").write_text(
                json.dumps({"jobs_dir": str(run_dir / "harbor-job")}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not a full TB4 run"):
                load_rows([run_dir], catalog)


if __name__ == "__main__":
    unittest.main()
