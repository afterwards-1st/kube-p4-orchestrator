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
from app.p4_control import P4ControlClient, normalize_routes


class P4ControlClientTests(unittest.TestCase):
    def test_routes_accept_api_and_status_field_names(self):
        self.assertEqual(
            [
                {"flowId": 1, "backendId": 216},
                {"flowId": 2, "backendId": 226},
            ],
            normalize_routes([
                {"flow_id": 2, "backend_id": 226},
                {"flowId": 1, "backendId": 216},
            ]),
        )

    def test_replace_uses_114_http_contract(self):
        client = P4ControlClient("http://192.168.199.114:18100")
        client._request = Mock(return_value={"ok": True, "routes": []})

        client.replace_routes(
            [{"flowId": 1, "backendId": 216}],
            "computetask/demo/generation/1",
        )

        client._request.assert_called_once_with(
            "POST",
            "/v1/routes/replace",
            {
                "routes": [{"flow_id": 1, "backend_id": 216}],
                "reason": "computetask/demo/generation/1",
            },
        )
        self.assertIsNone(client._cached_routes)


class P4RoutingStatusTests(unittest.TestCase):
    def make_controller(self, current_routes):
        controller = controller_module.ComputeTaskController.__new__(
            controller_module.ComputeTaskController
        )
        controller.p4 = Mock()
        controller.p4.get_routes.return_value = {
            "ok": True,
            "control_owner": "server-114",
            "control_mode": "external",
            "dry_run": True,
            "routes": current_routes,
        }
        return controller

    @staticmethod
    def task(name="p4-routing-demo"):
        return {
            "metadata": {"name": name, "generation": 1},
            "spec": {"routing": {"mode": "apply"}},
        }

    @staticmethod
    def workers():
        return [
            {"pod": "worker-216", "flowId": 1, "backendId": 216},
            {"pod": "worker-226", "flowId": 2, "backendId": 226},
        ]

    def test_plan_reads_114_but_never_replaces_routes(self):
        controller = self.make_controller([
            {"flow_id": 1, "backend_id": 226},
        ])
        with patch.object(controller_module, "P4_ROUTING_MODE", "plan"), patch.object(
            controller_module,
            "P4_ROUTE_OWNER",
            "p4-routing-demo",
        ):
            status = controller._p4_routing_status(
                self.task(),
                self.workers(),
            )

        self.assertEqual("Planned", status["phase"])
        self.assertFalse(status["writeAllowed"])
        self.assertFalse(status["inSync"])
        self.assertEqual("", status["error"])
        controller.p4.replace_routes.assert_not_called()

    def test_apply_against_dry_run_is_reported_not_applied(self):
        controller = self.make_controller([])
        controller.p4.replace_routes.return_value = {
            "ok": True,
            "applied": False,
            "dry_run": True,
            "control_owner": "server-114",
            "routes": [
                {"flow_id": 1, "backend_id": 216},
                {"flow_id": 2, "backend_id": 226},
            ],
        }
        with patch.object(controller_module, "P4_ROUTING_MODE", "apply"), patch.object(
            controller_module,
            "P4_ROUTE_OWNER",
            "p4-routing-demo",
        ):
            status = controller._p4_routing_status(
                self.task(),
                self.workers(),
            )

        self.assertEqual("DryRunValidated", status["phase"])
        self.assertTrue(status["writeAllowed"])
        self.assertTrue(status["inSync"])
        self.assertFalse(status["applied"])
        controller.p4.replace_routes.assert_called_once()

    def test_non_owner_cannot_write_global_route_table(self):
        controller = self.make_controller([])
        with patch.object(controller_module, "P4_ROUTING_MODE", "apply"), patch.object(
            controller_module,
            "P4_ROUTE_OWNER",
            "p4-routing-demo",
        ):
            status = controller._p4_routing_status(
                self.task("another-task"),
                self.workers(),
            )

        self.assertFalse(status["writeAllowed"])
        controller.p4.replace_routes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
