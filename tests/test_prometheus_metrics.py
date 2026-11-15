"""Prometheus /metrics 端点测试：文本格式、Counter/Gauge 类型与鉴权。"""

import json
import threading
import unittest
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.metrics_receiver import WorkerMetricStore, make_http_handler


def _serve(store, **kwargs):
    httpd = HTTPServer(("127.0.0.1", 0), make_http_handler(store, **kwargs))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


class PrometheusMetricsEndpointTest(unittest.TestCase):
    def test_metrics_exposition_content(self):
        store = WorkerMetricStore({"216", "226"}, stale_after_seconds=3)
        store.ingest(
            {
                "backend_id": 216,
                "queue_depth": 5,
                "completed_tasks": 42,
            },
            received_at=100,
        )
        store.ingest({"backend_id": 999}, received_at=100)  # 非白名单，应被拒

        text = store.metrics_exposition(now=101)

        self.assertIn(
            "# TYPE kube_orchestrator_metrics_reports_accepted_total counter", text
        )
        self.assertIn(
            "kube_orchestrator_metrics_reports_accepted_total 1", text
        )
        self.assertIn(
            "kube_orchestrator_metrics_reports_rejected_total 1", text
        )
        self.assertIn("kube_orchestrator_receiver_backends_total 2", text)
        self.assertIn("kube_orchestrator_receiver_backends_fresh 1", text)
        self.assertIn('kube_orchestrator_worker_queue_depth{backend_id="216"} 5', text)
        self.assertIn('kube_orchestrator_worker_fresh{backend_id="216"} 1', text)
        # 无状态的白名单 backend 也输出序列，且 fresh=0
        self.assertIn('kube_orchestrator_worker_fresh{backend_id="226"} 0', text)
        self.assertTrue(text.endswith("\n"))

    def test_metrics_endpoint_over_http(self):
        store = WorkerMetricStore({"216"})
        store.set_udp_ready(True)
        store.ingest({"backend_id": 216, "queue_depth": 3}, received_at=100)
        httpd, thread = _serve(store, token="test-token")
        base = "http://127.0.0.1:{}".format(httpd.server_address[1])
        try:
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(base + "/metrics", timeout=2)
            self.assertEqual(403, unauthorized.exception.code)

            request = Request(base + "/metrics", headers={"X-Metrics-Token": "test-token"})
            with urlopen(request, timeout=2) as response:
                self.assertEqual(200, response.status)
                self.assertIn("version=0.0.4", response.headers["Content-Type"])
                text = response.read().decode("utf-8")
            self.assertIn('kube_orchestrator_worker_queue_depth{backend_id="216"} 3', text)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
