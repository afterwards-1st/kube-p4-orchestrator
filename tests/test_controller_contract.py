import ast
import unittest
from pathlib import Path


class ControllerStrategyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.source = (root / "app" / "controller.py").read_text(
            encoding="utf-8"
        )
        cls.deployment = (
            root / "manifests" / "02-controller.yaml"
        ).read_text(encoding="utf-8")
        cls.rbac = (
            root / "manifests" / "00-namespace-rbac.yaml"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_controller_source_remains_valid_python(self):
        self.assertIsInstance(self.tree, ast.Module)

    def test_multi_worker_strategy_has_a_separate_branch(self):
        self.assertIn(
            'spec.get("strategy", "nodeScore") '
            '== "minExpectedCompletionTime"',
            self.source,
        )
        self.assertIn("plan_multi_worker_task(", self.source)
        self.assertIn("list_namespaced_pod(", self.source)

    def test_worker_discovery_is_explicitly_scoped(self):
        self.assertIn("WORKER_NAMESPACE", self.source)
        self.assertIn("WORKER_SLOTS", self.source)
        self.assertIn("- name: WORKER_NAMESPACE", self.deployment)
        self.assertIn("- name: WORKER_SLOTS", self.deployment)
        self.assertIn("- name: RUNTIME_METRICS_PATH", self.deployment)
        self.assertIn("name: runtime-metrics", self.deployment)
        self.assertIn("load_combined_runtime_metrics(", self.source)
        self.assertIn(
            'value: "216,217,218,226,227,228"',
            self.deployment,
        )

    def test_dynamic_metrics_are_authenticated_and_have_static_fallback(self):
        self.assertIn(
            'metrics_state["source"] == "static-fallback"',
            self.source,
        )
        self.assertIn("- name: RUNTIME_METRICS_URL", self.deployment)
        self.assertIn(
            "http://metrics-receiver:8080/v1/backends",
            self.deployment,
        )
        self.assertIn("- name: RUNTIME_METRICS_TOKEN", self.deployment)
        self.assertIn("name: metrics-receiver-auth", self.deployment)
        self.assertIn(
            "- name: RUNTIME_METRICS_TIMEOUT_SECONDS",
            self.deployment,
        )
        self.assertIn("image: kube-orchestrator:0.5.1", self.deployment)
        self.assertIn("- name: P4_ROUTING_MODE", self.deployment)
        self.assertIn("- name: P4_ROUTE_OWNER", self.deployment)
        self.assertIn("- name: P4_CONTROL_URL", self.deployment)
        self.assertIn("http://192.168.199.114:18100", self.deployment)
        self.assertIn("- name: P4_CONTROL_TOKEN", self.deployment)
        self.assertIn("name: p4-control-auth", self.deployment)
        self.assertIn("from app.p4_control import", self.source)

    def test_resource_and_metrics_refresh_periods_are_separate(self):
        self.assertIn("RESOURCE_REFRESH_SECONDS", self.source)
        self.assertIn("METRICS_POLL_SECONDS", self.source)
        self.assertIn("- name: RESOURCE_REFRESH_SECONDS", self.deployment)
        self.assertIn("- name: METRICS_POLL_SECONDS", self.deployment)
        self.assertNotIn("- name: POLL_INTERVAL_SECONDS", self.deployment)
        self.assertIn("def run_once(", self.source)
        self.assertIn("def _patch_status_if_changed(", self.source)

    def test_controller_uses_single_writer_recreate_strategy(self):
        self.assertIn("type: Recreate", self.deployment)

    def test_controller_can_create_deduplicated_decision_events(self):
        self.assertIn("create_namespaced_event(", self.source)
        self.assertIn('resources: ["events"]', self.rbac)
        self.assertIn('verbs: ["create"]', self.rbac)

    def test_decision_trace_records_change_and_metrics_context(self):
        for field in (
            "decisionTrace",
            "reason",
            "recordedAt",
            "metricsSource",
            "metricsFreshness",
            "dynamicBackendCount",
            "queueDepthByBackend",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.source)


if __name__ == "__main__":
    unittest.main()
