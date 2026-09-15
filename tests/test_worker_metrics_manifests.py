import unittest
from pathlib import Path


class WorkerMetricsManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.manifests = [
            (root / "manifests" / "workers" / "ubuntu-194-216.yaml").read_text(
                encoding="utf-8"
            ),
            (root / "manifests" / "workers" / "ubuntu-200-226.yaml").read_text(
                encoding="utf-8"
            ),
        ]

    def test_only_the_two_active_workers_are_managed(self):
        combined = "\n".join(self.manifests)
        self.assertIn("name: ubuntu-194-216", combined)
        self.assertIn("name: ubuntu-200-226", combined)
        self.assertEqual(2, combined.count("kind: Deployment"))
        self.assertEqual(2, combined.count("replicas: 1"))

    def test_reports_use_service_dns_and_configurable_arguments(self):
        for manifest in self.manifests:
            self.assertNotIn("192.168.199.114", manifest)
            self.assertIn(
                "metrics-receiver.kube-orchestrator.svc.cluster.local",
                manifest,
            )
            self.assertIn("name: METRICS_REPORT_HOST", manifest)
            self.assertIn("name: METRICS_REPORT_PORT", manifest)
            self.assertIn("name: METRICS_REPORT_INTERVAL", manifest)
            self.assertIn('--report-ip "${METRICS_REPORT_HOST}"', manifest)
            self.assertIn('--report-port "${METRICS_REPORT_PORT}"', manifest)
            self.assertIn(
                '--report-interval "${METRICS_REPORT_INTERVAL}"',
                manifest,
            )

    def test_existing_network_and_resource_contracts_are_preserved(self):
        combined = "\n".join(self.manifests)
        self.assertIn("10.10.1.216", combined)
        self.assertIn("10.10.1.226", combined)
        self.assertIn('cpu: "26"', combined)
        self.assertIn('cpu: "13"', combined)
        self.assertEqual(2, combined.count("name: batch-backend-script"))

    def test_worker_health_probes_use_dedicated_tcp_port(self):
        for manifest in self.manifests:
            self.assertIn("name: task-udp", manifest)
            self.assertIn("containerPort: 9000", manifest)
            self.assertIn("protocol: UDP", manifest)
            self.assertIn("name: health", manifest)
            self.assertIn("containerPort: 8081", manifest)
            self.assertIn("readinessProbe:", manifest)
            self.assertIn("path: /ready", manifest)
            self.assertIn("livenessProbe:", manifest)
            self.assertIn("path: /health", manifest)
            self.assertIn("port: health", manifest)


if __name__ == "__main__":
    unittest.main()
