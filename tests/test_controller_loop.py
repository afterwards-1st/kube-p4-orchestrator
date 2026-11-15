import sys
import types
import unittest
from unittest.mock import Mock, patch

try:
    import kubernetes  # noqa: F401
except ModuleNotFoundError:
    kubernetes_stub = types.ModuleType("kubernetes")
    kubernetes_stub.client = Mock()
    kubernetes_stub.config = Mock()
    sys.modules["kubernetes"] = kubernetes_stub

from app import controller as controller_module


def task(name, strategy):
    return {
        "metadata": {"name": name, "generation": 1},
        "spec": {"strategy": strategy},
    }


class ControllerLoopTests(unittest.TestCase):
    def make_controller(self):
        controller = controller_module.ComputeTaskController.__new__(
            controller_module.ComputeTaskController
        )
        controller._cached_nodes = []
        controller._cached_worker_pods = []
        controller._cached_tasks = []
        controller._next_resource_refresh = 0.0
        controller._last_metrics_state = None
        controller.snapshots = Mock(return_value=["node-snapshot"])
        controller.worker_pods = Mock(return_value=["worker-pod"])
        controller.custom = Mock()
        controller.custom.list_namespaced_custom_object.return_value = {
            "items": [
                task("static-task", "nodeScore"),
                task("dynamic-task", "minExpectedCompletionTime"),
            ]
        }
        controller.reconcile = Mock()
        controller._log_metrics_state = Mock()
        return controller

    def test_failed_task_does_not_block_following_tasks_and_is_retried(self):
        controller = self.make_controller()
        def reconcile(task, *_args):
            if task["metadata"]["name"] == "static-task":
                raise RuntimeError("intentional failure")

        controller.reconcile.side_effect = reconcile
        metrics_state = {"source": "dynamic", "dynamic_backend_count": 1, "error": ""}
        with patch.object(controller_module, "RESOURCE_REFRESH_SECONDS", 15), patch.object(
            controller_module, "load_combined_runtime_metrics", return_value=({}, metrics_state)
        ), self.assertLogs(level="ERROR") as logs:
            self.assertTrue(controller.run_once(now=0))
            self.assertTrue(controller.run_once(now=15))

        names = [call.args[0]["metadata"]["name"] for call in controller.reconcile.call_args_list]
        self.assertEqual(["static-task", "dynamic-task", "static-task", "dynamic-task"], names)
        self.assertEqual(2, sum("reconcile failed for task static-task" in line for line in logs.output))

    def test_fast_cycle_reuses_resource_cache_and_only_reconciles_dynamic_tasks(self):
        controller = self.make_controller()
        metrics = {"backends": {"216": {"queue_depth": 0}}}
        metrics_state = {
            "source": "dynamic",
            "dynamic_backend_count": 1,
            "error": "",
        }

        with patch.object(
            controller_module,
            "RESOURCE_REFRESH_SECONDS",
            15,
        ), patch.object(
            controller_module,
            "load_combined_runtime_metrics",
            return_value=(metrics, metrics_state),
        ) as load_metrics:
            self.assertTrue(controller.run_once(now=0))
            self.assertFalse(controller.run_once(now=2))

        controller.snapshots.assert_called_once_with()
        controller.worker_pods.assert_called_once_with()
        controller.custom.list_namespaced_custom_object.assert_called_once()
        self.assertEqual(2, load_metrics.call_count)
        self.assertEqual(3, controller.reconcile.call_count)
        names = [
            call.args[0]["metadata"]["name"]
            for call in controller.reconcile.call_args_list
        ]
        self.assertEqual(
            ["static-task", "dynamic-task", "dynamic-task"],
            names,
        )

    def test_resource_cache_refreshes_again_after_slow_period(self):
        controller = self.make_controller()
        metrics_state = {
            "source": "dynamic",
            "dynamic_backend_count": 1,
            "error": "",
        }

        with patch.object(
            controller_module,
            "RESOURCE_REFRESH_SECONDS",
            15,
        ), patch.object(
            controller_module,
            "load_combined_runtime_metrics",
            return_value=({}, metrics_state),
        ):
            controller.run_once(now=0)
            controller.run_once(now=15)

        self.assertEqual(2, controller.snapshots.call_count)
        self.assertEqual(2, controller.worker_pods.call_count)
        self.assertEqual(
            2,
            controller.custom.list_namespaced_custom_object.call_count,
        )
        self.assertEqual(4, controller.reconcile.call_count)

    def test_unchanged_status_is_not_written_again(self):
        controller = controller_module.ComputeTaskController.__new__(
            controller_module.ComputeTaskController
        )
        controller.custom = Mock()
        current = {"phase": "Planned", "selectedWorkers": []}
        item = {
            "metadata": {"name": "same-plan"},
            "status": dict(current),
        }

        self.assertFalse(
            controller._patch_status_if_changed(item, dict(current))
        )
        controller.custom.patch_namespaced_custom_object_status.assert_not_called()

        changed = {"phase": "Unschedulable", "selectedWorkers": []}
        self.assertTrue(
            controller._patch_status_if_changed(item, changed)
        )
        controller.custom.patch_namespaced_custom_object_status.assert_called_once()
        self.assertEqual(changed, item["status"])


if __name__ == "__main__":
    unittest.main()
