import json
import tempfile
import unittest
from pathlib import Path

from app.runtime_metrics import (
    WorkerMetricsFetchError,
    fetch_worker_metrics,
    load_combined_runtime_metrics,
    load_runtime_metrics,
    merge_runtime_metrics,
    metrics_from_worker_reports,
)


class RuntimeMetricsTests(unittest.TestCase):
    def test_fetch_worker_metrics_sends_token_and_converts_payload(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                captured["read_size"] = size
                return json.dumps({
                    "ok": True,
                    "backends": [
                        {"backend_id": 216, "queue_depth": 8, "fresh": True},
                    ],
                }).encode("utf-8")

        def opener(request, timeout):
            captured["token"] = request.get_header("X-metrics-token")
            captured["timeout"] = timeout
            return Response()

        metrics = fetch_worker_metrics(
            "http://metrics-receiver:8080/v1/backends",
            "test-token",
            {"216"},
            timeout_seconds=1.5,
            opener=opener,
        )

        self.assertEqual(
            {"backends": {"216": {"queue_depth": 8.0}}},
            metrics,
        )
        self.assertEqual("test-token", captured["token"])
        self.assertEqual(1.5, captured["timeout"])

    def test_combined_metrics_fall_back_to_static_on_fetch_error(self):
        def failing_fetcher(*args):
            raise WorkerMetricsFetchError("receiver unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            baseline = {"backends": {"216": {"queue_depth": 2}}}
            path.write_text(json.dumps(baseline), encoding="utf-8")
            metrics, state = load_combined_runtime_metrics(
                str(path),
                "http://metrics-receiver:8080/v1/backends",
                "test-token",
                {"216"},
                fetcher=failing_fetcher,
            )

        self.assertEqual(baseline, metrics)
        self.assertEqual("static-fallback", state["source"])
        self.assertIn("receiver unavailable", state["error"])

    def test_combined_metrics_report_no_fresh_dynamic_data(self):
        def empty_fetcher(*args):
            return {"backends": {}}

        metrics, state = load_combined_runtime_metrics(
            "",
            "http://metrics-receiver:8080/v1/backends",
            "test-token",
            {"216"},
            fetcher=empty_fetcher,
        )

        self.assertEqual({"backends": {}}, metrics)
        self.assertEqual("static-no-fresh-data", state["source"])

    def test_only_fresh_allowed_worker_reports_become_dynamic_metrics(self):
        payload = {
            "ok": True,
            "backends": [
                {"backend_id": 216, "queue_depth": 7, "fresh": True},
                {"backend_id": 226, "queue_depth": 99, "fresh": False},
                {"backend_id": 999, "queue_depth": 3, "fresh": True},
                {
                    "backend_id": 217,
                    "queue_depth": "invalid",
                    "fresh": True,
                },
            ],
        }

        metrics = metrics_from_worker_reports(payload, {"216", "226"})

        self.assertEqual(
            {"backends": {"216": {"queue_depth": 7.0}}},
            metrics,
        )

    def test_dynamic_queue_depth_overrides_baseline_without_mutation(self):
        baseline = {
            "default": {"batch_size": 16},
            "backends": {
                "216": {"service_rate_tasks_per_sec": 100},
                "226": {"service_rate_tasks_per_sec": 75},
            },
        }
        dynamic = {"backends": {"216": {"queue_depth": 7}}}

        merged = merge_runtime_metrics(baseline, dynamic)

        self.assertEqual(16, merged["default"]["batch_size"])
        self.assertEqual(
            {"service_rate_tasks_per_sec": 100, "queue_depth": 7},
            merged["backends"]["216"],
        )
        self.assertEqual(
            {"service_rate_tasks_per_sec": 75},
            merged["backends"]["226"],
        )
        self.assertNotIn("queue_depth", baseline["backends"]["216"])

    def test_invalid_worker_report_root_is_rejected(self):
        with self.assertRaises(ValueError):
            metrics_from_worker_reports([])
        with self.assertRaises(ValueError):
            metrics_from_worker_reports({"ok": False, "backends": []})

    def test_empty_or_missing_path_uses_algorithm_defaults(self):
        self.assertEqual({}, load_runtime_metrics(""))
        self.assertEqual({}, load_runtime_metrics("missing-metrics.json"))

    def test_metrics_are_loaded_from_json_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text(
                json.dumps({
                    "default": {"service_rate_tasks_per_sec": 80},
                    "backends": {"216": {"queue_depth": 2}},
                }),
                encoding="utf-8",
            )

            metrics = load_runtime_metrics(str(path))

        self.assertEqual(
            80,
            metrics["default"]["service_rate_tasks_per_sec"],
        )
        self.assertEqual(2, metrics["backends"]["216"]["queue_depth"])

    def test_non_object_metrics_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_runtime_metrics(str(path))


if __name__ == "__main__":
    unittest.main()