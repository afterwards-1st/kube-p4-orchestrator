import sys
import types
import unittest
from unittest.mock import Mock

try:
    import kubernetes  # noqa: F401
except ModuleNotFoundError:
    kubernetes_stub = types.ModuleType("kubernetes")
    kubernetes_stub.client = Mock()
    kubernetes_stub.config = Mock()
    sys.modules["kubernetes"] = kubernetes_stub

from app import controller as controller_module


def worker_pod(name, slot):
    return {
        "metadata": {
            "name": name,
            "labels": {"slot": slot},
        }
    }


class ControllerObservabilityTests(unittest.TestCase):
    def make_controller(self):
        controller = controller_module.ComputeTaskController.__new__(
            controller_module.ComputeTaskController
        )
        controller.custom = Mock()
        controller.core = Mock()
        return controller

    def test_worker_switch_trace_contains_dynamic_metrics_context(self):
        controller = self.make_controller()
        current = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-216"}],
            "estimatedMakespanMs": 161,
        }
        new = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-226"}],
            "estimatedMakespanMs": 214.333,
        }
        trace = controller._decision_trace(
            current,
            new,
            [
                worker_pod("worker-216", "216"),
                worker_pod("worker-226", "226"),
            ],
            {
                "backends": {
                    "216": {"queue_depth": 15},
                    "226": {"queue_depth": 0},
                }
            },
            {
                "source": "dynamic",
                "dynamic_backend_count": 2,
                "error": "",
            },
        )

        self.assertEqual("selected-workers-changed", trace["reason"])
        self.assertEqual("dynamic", trace["metricsSource"])
        self.assertEqual("fresh", trace["metricsFreshness"])
        self.assertEqual(2, trace["dynamicBackendCount"])
        self.assertEqual(
            {"216": 15.0, "226": 0.0},
            trace["queueDepthByBackend"],
        )
        self.assertTrue(trace["recordedAt"].endswith("Z"))

    def test_existing_plan_is_written_once_to_initialize_trace(self):
        controller = self.make_controller()
        status = {"phase": "Planned", "selectedWorkers": []}
        task = {
            "metadata": {"name": "plan"},
            "status": dict(status),
        }
        trace = controller._decision_trace(
            task["status"],
            status,
        )

        self.assertEqual("observability-initialized", trace["reason"])
        self.assertTrue(
            controller._patch_status_if_changed(task, status, trace)
        )
        self.assertIn("decisionTrace", task["status"])
        self.assertFalse(
            controller._patch_status_if_changed(task, status, trace)
        )
        controller.custom.patch_namespaced_custom_object_status.assert_called_once()

    def test_metric_availability_change_updates_trace_once(self):
        controller = self.make_controller()
        current = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-216"}],
            "decisionTrace": {
                "metricsSource": "dynamic",
                "metricsFreshness": "fresh",
                "dynamicBackendCount": 2,
            },
        }
        new = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-216"}],
        }
        trace = controller._decision_trace(
            current,
            new,
            metrics_state={
                "source": "static-fallback",
                "dynamic_backend_count": 0,
                "error": "connection refused",
            },
        )

        self.assertEqual("metrics-state-changed", trace["reason"])
        task = {"metadata": {"name": "plan"}, "status": current}
        self.assertTrue(
            controller._patch_status_if_changed(task, new, trace)
        )
        self.assertFalse(
            controller._patch_status_if_changed(task, new, trace)
        )

    def test_event_dedup_ignores_only_makespan_changes(self):
        controller = self.make_controller()
        base_trace = {
            "reason": "estimated-completion-changed",
            "metricsSource": "dynamic",
            "metricsFreshness": "fresh",
            "dynamicBackendCount": 2,
            "queueDepthByBackend": {"216": 0},
        }
        previous = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-216"}],
            "estimatedMakespanMs": 161,
            "decisionTrace": dict(base_trace),
        }
        current = dict(previous)
        current["estimatedMakespanMs"] = 170

        self.assertFalse(
            controller._emit_decision_event(
                {"metadata": {"name": "plan", "uid": "uid-1"}},
                previous,
                current,
            )
        )
        controller.core.create_namespaced_event.assert_not_called()

    def test_worker_change_creates_one_event(self):
        controller = self.make_controller()
        previous = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-216"}],
            "decisionTrace": {
                "metricsSource": "dynamic",
                "metricsFreshness": "fresh",
                "dynamicBackendCount": 2,
            },
        }
        current = {
            "phase": "Planned",
            "selectedWorkers": [{"pod": "worker-226"}],
            "decisionTrace": {
                "reason": "selected-workers-changed",
                "metricsSource": "dynamic",
                "metricsFreshness": "fresh",
                "dynamicBackendCount": 2,
                "queueDepthByBackend": {"216": 15, "226": 0},
            },
        }

        self.assertTrue(
            controller._emit_decision_event(
                {"metadata": {"name": "plan", "uid": "uid-1"}},
                previous,
                current,
            )
        )
        controller.core.create_namespaced_event.assert_called_once()

    def test_fallback_trace_marks_metrics_unavailable(self):
        controller = self.make_controller()
        trace = controller._decision_trace(
            {},
            {"phase": "Planned"},
            metrics_state={
                "source": "static-fallback",
                "dynamic_backend_count": 0,
                "error": "connection refused",
            },
        )

        self.assertEqual("initial-plan", trace["reason"])
        self.assertEqual("static-fallback", trace["metricsSource"])
        self.assertEqual("unavailable", trace["metricsFreshness"])


if __name__ == "__main__":
    unittest.main()
