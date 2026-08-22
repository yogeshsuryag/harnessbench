"""Small standard-library tests for the pilot result normalizer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pilot_results import build_summaries, load_json, load_rows


class PilotResultsTest(unittest.TestCase):
    def test_normalizes_a_harbor_trial_and_groups_it(self) -> None:
        catalog = load_json(Path(__file__).resolve().parents[1] / "config" / "tb3_task_catalog.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            jobs_dir = run_dir / "harbor-job" / "job"
            trial_dir = jobs_dir / "ico-path-patch__abc1234"
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "agent").mkdir()

            (run_dir).mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "run", "agent": {"id": "opencode", "version": "test"}}),
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
                        "task_name": "terminal-bench/ico-path-patch",
                        "trial_name": "ico-path-patch__abc1234",
                        "task_checksum": "task-checksum",
                        "agent_info": {
                            "name": "opencode",
                            "version": "test",
                            "model_info": {"name": "stealth/ox-alpha"},
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
            summary = build_summaries(rows, catalog)
            self.assertEqual(summary["harness_summary"][0]["passes"], 1)
            self.assertEqual(
                next(item for item in summary["task_matrix"] if item["task_id"] == "ico-path-patch")[
                    "harnesses"
                ]["opencode"]["passed"],
                True,
            )


if __name__ == "__main__":
    unittest.main()
