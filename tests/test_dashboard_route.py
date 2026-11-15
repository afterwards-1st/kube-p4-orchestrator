"""可视化面板路由测试：/dashboard 提供面板页面，指标接口行为不变。"""

import json
import threading
import unittest
from http.server import HTTPServer
from urllib.request import Request, urlopen

from app.metrics_receiver import WorkerMetricStore, load_dashboard_html, make_http_handler


class DashboardRouteTest(unittest.TestCase):
    def _serve(self, **handler_kwargs):
        store = WorkerMetricStore({"216"})
        store.set_udp_ready(True)
        httpd = HTTPServer(("127.0.0.1", 0), make_http_handler(store, **handler_kwargs))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def test_dashboard_route_serves_panel(self):
        httpd, thread = self._serve()
        base = "http://127.0.0.1:{}".format(httpd.server_address[1])
        try:
            for path in ("/dashboard", "/"):
                with urlopen(base + path, timeout=2) as response:
                    self.assertEqual(200, response.status)
                    self.assertTrue(
                        response.headers["Content-Type"].startswith("text/html")
                    )
                    html = response.read().decode("utf-8")
                self.assertIn("kube-p4-orchestrator", html)
                self.assertIn("/v1/backends", html)

            request = Request(base + "/v1/backends")
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_dashboard_html_injection(self):
        """注入自定义 HTML 时应原样返回，便于上层替换面板实现。"""
        httpd, thread = self._serve(dashboard_html="<h1>自定义面板</h1>")
        base = "http://127.0.0.1:{}".format(httpd.server_address[1])
        try:
            with urlopen(base + "/dashboard", timeout=2) as response:
                body = response.read().decode("utf-8")
            self.assertEqual("<h1>自定义面板</h1>", body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_load_dashboard_html_reads_repo_file(self):
        html = load_dashboard_html()
        self.assertIn("kube-p4-orchestrator", html)
        self.assertIn("/v1/backends", html)


if __name__ == "__main__":
    unittest.main()
