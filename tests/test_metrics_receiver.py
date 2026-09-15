import json
import threading
import unittest
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app.metrics_receiver as metrics_receiver

from app.metrics_receiver import (
    ThreadingHTTPServer,
    WorkerMetricStore,
    make_http_handler,
)
from app.runtime_metrics import metrics_from_worker_reports


class MetricsReceiverTests(unittest.TestCase):
    def test_production_module_imports_select(self):
        self.assertIsNotNone(metrics_receiver.select)
    def test_health_requires_udp_receiver_to_be_ready(self):
        store = WorkerMetricStore({"216"})

        self.assertEqual(
            {
                "status": "starting",
                "role": "metrics-receiver",
                "udp_ready": False,
            },
            store.health(),
        )
        store.set_udp_ready(True)
        self.assertEqual(
            {
                "status": "ok",
                "role": "metrics-receiver",
                "udp_ready": True,
            },
            store.health(),
        )

    def test_valid_allowed_report_is_stored_and_becomes_stale(self):
        store = WorkerMetricStore({"216", "226"}, stale_after_seconds=3)

        accepted = store.ingest({
            "backend_id": 216,
            "queue_depth": 7,
            "reason": "enqueue",
            "ts": 100,
            "completed_tasks": 32,
            "total_batches": 2,
        }, received_at=101)

        self.assertTrue(accepted)
        fresh = store.snapshot(now=102)
        stale = store.snapshot(now=105)
        fresh_by_id = {item["backend_id"]: item for item in fresh["backends"]}
        stale_by_id = {item["backend_id"]: item for item in stale["backends"]}
        self.assertTrue(fresh_by_id[216]["fresh"])
        self.assertEqual(7, fresh_by_id[216]["queue_depth"])
        self.assertEqual(32, fresh_by_id[216]["completed_tasks"])
        self.assertFalse(stale_by_id[216]["fresh"])
        self.assertFalse(fresh_by_id[226]["fresh"])
        self.assertIsNone(fresh_by_id[226]["age_seconds"])

    def test_bad_and_unknown_reports_are_rejected_without_losing_state(self):
        store = WorkerMetricStore({"216"})
        self.assertTrue(store.ingest({"backend_id": 216, "queue_depth": 4}))
        self.assertFalse(store.ingest(b"not-json"))
        self.assertFalse(store.ingest({"backend_id": 999, "queue_depth": 1}))
        self.assertFalse(store.ingest({"backend_id": 216, "queue_depth": "bad"}))

        snapshot = store.snapshot()

        self.assertEqual(1, snapshot["accepted_reports"])
        self.assertEqual(3, snapshot["rejected_reports"])
        self.assertEqual(4, snapshot["backends"][0]["queue_depth"])

    def test_snapshot_contract_is_accepted_by_runtime_metrics_adapter(self):
        store = WorkerMetricStore({"216", "226"}, stale_after_seconds=3)
        store.ingest(
            {"backend_id": 216, "queue_depth": 9},
            received_at=100,
        )

        dynamic = metrics_from_worker_reports(
            store.snapshot(now=101),
            {"216", "226"},
        )

        self.assertEqual(
            {"backends": {"216": {"queue_depth": 9.0}}},
            dynamic,
        )

    def test_http_metrics_endpoint_requires_token(self):
        store = WorkerMetricStore({"216"})
        httpd = HTTPServer(
            ("127.0.0.1", 0),
            make_http_handler(store, token="test-token"),
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:{}/v1/backends".format(
            httpd.server_address[1]
        )
        health_url = "http://127.0.0.1:{}/health".format(
            httpd.server_address[1]
        )

        try:
            with self.assertRaises(HTTPError) as starting:
                urlopen(health_url, timeout=2)
            self.assertEqual(503, starting.exception.code)
            store.set_udp_ready(True)
            with urlopen(health_url, timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))

            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(url, timeout=2)
            self.assertEqual(403, unauthorized.exception.code)

            request = Request(
                url,
                headers={"X-Metrics-Token": "test-token"},
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertTrue(payload["ok"])
        self.assertTrue(health["udp_ready"])
        self.assertEqual(
            "kubernetes-worker-metrics-receiver",
            payload["source"],
        )


if __name__ == "__main__":
    unittest.main()
