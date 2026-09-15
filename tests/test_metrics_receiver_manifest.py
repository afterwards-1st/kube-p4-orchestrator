import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "06-metrics-receiver.yaml"


class MetricsReceiverManifestTests(unittest.TestCase):
    def test_manifest_keeps_receiver_isolated_and_probed(self):
        text = MANIFEST.read_text(encoding="utf-8")

        self.assertIn("image: kube-orchestrator:0.3.0", text)
        self.assertIn("automountServiceAccountToken: false", text)
        self.assertIn("type: Recreate", text)
        self.assertIn("readinessProbe:", text)
        self.assertIn("livenessProbe:", text)
        self.assertIn("readOnlyRootFilesystem: true", text)
        self.assertIn("runAsNonRoot: true", text)

    def test_service_exposes_udp_reports_and_tcp_http(self):
        text = MANIFEST.read_text(encoding="utf-8")

        self.assertIn("name: reports", text)
        self.assertIn("protocol: UDP", text)
        self.assertIn("port: 9999", text)
        self.assertIn("name: http", text)
        self.assertIn("protocol: TCP", text)
        self.assertIn("port: 8080", text)

    def test_token_is_referenced_from_secret_not_embedded(self):
        text = MANIFEST.read_text(encoding="utf-8")

        self.assertIn("secretKeyRef:", text)
        self.assertIn("name: metrics-receiver-auth", text)
        self.assertIn("key: token", text)
        self.assertNotIn("value: change-me", text)


if __name__ == "__main__":
    unittest.main()
