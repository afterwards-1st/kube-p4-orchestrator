import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "workers"
    / "batch_server_report_sim.py"
)
SPEC = importlib.util.spec_from_file_location("batch_server_report_sim", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class WorkerHealthTests(unittest.TestCase):
    def test_liveness_and_readiness_paths_are_healthy(self):
        for path in ("/health", "/ready"):
            response = MODULE.health_http_response(path)
            self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
            self.assertIn(b'"status":"ok"', response)

    def test_unknown_path_is_not_healthy(self):
        response = MODULE.health_http_response("/unknown")
        self.assertTrue(response.startswith(b"HTTP/1.1 404 Not Found\r\n"))
        self.assertIn(b'"status":"not_found"', response)


if __name__ == "__main__":
    unittest.main()
