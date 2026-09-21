import unittest
from pathlib import Path


class CRDContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        cls.manifest = (
            project_root / "manifests" / "01-computetask-crd.yaml"
        ).read_text(encoding="utf-8")

    def test_multi_worker_spec_fields_are_declared(self):
        for field in (
            "strategy:",
            "taskCount:",
            "maxWorkers:",
            "workerSelector:",
            "minBandwidthMbps:",
            "maxLatencyMs:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.manifest)

    def test_multi_worker_status_fields_are_declared(self):
        for field in (
            "observedGeneration:",
            "executionAllowed:",
            "conditions:",
            "reason:",
            "selectedWorkers:",
            "flowId:",
            "backendId:",
            "assignedTaskCount:",
            "estimatedFinishMs:",
            "estimatedMakespanMs:",
            "evaluatedCombinations:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.manifest)

    def test_p4_routing_contract_is_declared(self):
        for field in (
            "routing:",
            "writeAllowed:",
            "routeOwner:",
            "desiredRoutes:",
            "observedRoutes:",
            "inSync:",
            "controlOwner:",
            "controlMode:",
            "dryRun:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.manifest)

    def test_decision_trace_status_fields_are_declared(self):
        for field in (
            "decisionTrace:",
            "reason:",
            "recordedAt:",
            "metricsSource:",
            "metricsFreshness:",
            "dynamicBackendCount:",
            "queueDepthByBackend:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.manifest)

    def test_existing_node_strategy_remains_default(self):
        self.assertIn(
            "enum: [nodeScore, minExpectedCompletionTime]",
            self.manifest,
        )
        self.assertIn("default: nodeScore", self.manifest)


if __name__ == "__main__":
    unittest.main()
