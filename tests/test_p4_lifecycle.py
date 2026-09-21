import sys
import types
import unittest
from unittest.mock import Mock, patch

try:
    import kubernetes  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("kubernetes")
    stub.client = Mock()
    stub.config = Mock()
    sys.modules["kubernetes"] = stub

from app import controller as controller_module
from app.controller import ComputeTaskController
from app.p4_lifecycle import FINALIZER, cleanup_p4_routing


class P4CleanupTests(unittest.TestCase):
    @staticmethod
    def task():
        return {
            "metadata": {"name": "route-owner", "generation": 3},
            "status": {
                "routing": {
                    "applied": True,
                    "routeOwner": "route-owner",
                    "desiredRoutes": [{"flowId": 1, "backendId": 216}],
                    "previousRoutes": [{"flowId": 1, "backendId": 226}],
                }
            },
        }

    @staticmethod
    def remote(routes):
        return {
            "ok": True,
            "control_owner": "server-114",
            "control_mode": "external",
            "routes": routes,
        }

    def test_owned_unchanged_routes_are_restored_and_verified(self):
        p4 = Mock()
        p4.get_routes.side_effect = [
            self.remote([{"flow_id": 1, "backend_id": 216}]),
            self.remote([{"flow_id": 1, "backend_id": 226}]),
        ]
        p4.replace_routes.return_value = {"ok": True}

        completed, phase, _ = cleanup_p4_routing(
            self.task(), p4, "route-owner"
        )

        self.assertTrue(completed)
        self.assertEqual("Restored", phase)
        p4.replace_routes.assert_called_once_with(
            [{"flowId": 1, "backendId": 226}],
            "computetask/route-owner/cleanup",
        )

    def test_drift_is_not_overwritten(self):
        p4 = Mock()
        p4.get_routes.return_value = self.remote([
            {"flow_id": 1, "backend_id": 218}
        ])

        completed, phase, reason = cleanup_p4_routing(
            self.task(), p4, "route-owner"
        )

        self.assertTrue(completed)
        self.assertEqual("Skipped", phase)
        self.assertIn("changed", reason)
        p4.replace_routes.assert_not_called()

    def test_unavailable_control_plane_keeps_cleanup_pending(self):
        p4 = Mock()
        p4.get_routes.return_value = {"ok": False, "error": "timeout"}

        completed, phase, reason = cleanup_p4_routing(
            self.task(), p4, "route-owner"
        )

        self.assertFalse(completed)
        self.assertEqual("WaitingForP4", phase)
        self.assertEqual("timeout", reason)
        p4.replace_routes.assert_not_called()


class FinalizerReconcileTests(unittest.TestCase):
    def make_controller(self):
        controller = ComputeTaskController.__new__(ComputeTaskController)
        controller.custom = Mock()
        controller.p4 = None
        return controller

    def test_finalizer_install_is_idempotent(self):
        controller = self.make_controller()
        task = {"metadata": {"name": "demo"}}

        self.assertTrue(controller._ensure_p4_finalizer(task))
        self.assertFalse(controller._ensure_p4_finalizer(task))
        self.assertEqual([FINALIZER], task["metadata"]["finalizers"])
        controller.custom.patch_namespaced_custom_object.assert_called_once()

    def test_deleting_plan_only_task_releases_finalizer(self):
        controller = self.make_controller()
        task = {
            "metadata": {
                "name": "demo",
                "generation": 2,
                "deletionTimestamp": "2026-09-21T00:00:00Z",
                "finalizers": [FINALIZER],
            },
            "status": {
                "phase": "Planned",
                "routing": {"applied": False},
            },
        }

        with patch.object(controller_module, "P4_ROUTE_OWNER", "demo"):
            self.assertTrue(controller._reconcile_deletion(task))

        self.assertEqual("Terminating", task["status"]["phase"])
        self.assertEqual("NotRequired", task["status"]["cleanup"]["phase"])
        self.assertNotIn(FINALIZER, task["metadata"]["finalizers"])
        controller.custom.patch_namespaced_custom_object_status.assert_called_once()
        controller.custom.patch_namespaced_custom_object.assert_called_once()


if __name__ == "__main__":
    unittest.main()