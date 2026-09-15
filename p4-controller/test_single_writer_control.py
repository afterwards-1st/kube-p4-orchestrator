"""114唯一P4写表控制器的本地回归测试，不访问真实BMv2。"""

import unittest

from controller_global_9backend import BatchAwareController


def make_controller(control_mode="external"):
    return BatchAwareController(
        listen_ip="127.0.0.1",
        listen_port=0,
        high=8,
        batch_size=16,
        decision_interval=0.1,
        thrift_port="9090",
        api_ip="127.0.0.1",
        api_port=0,
        control_mode=control_mode,
        dry_run=True,
    )


class SingleWriterControlTests(unittest.TestCase):
    def test_replace_routes_validates_then_updates_authoritative_state(self):
        controller = make_controller()

        result = controller.replace_routes(
            [
                {"flow_id": 1, "backend_id": 216},
                {"flow_id": 2, "backend_id": 226},
            ],
            reason="test",
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["control_owner"], "server-114")
        self.assertEqual(controller.state.routes, {1: 216, 2: 226})
        self.assertEqual(result["flow_mapping"], {216: 1, 226: 2})

    def test_invalid_backend_is_rejected_before_table_write(self):
        controller = make_controller()

        result = controller.replace_routes([{"flow_id": 1, "backend_id": 219}])

        self.assertFalse(result["ok"])
        self.assertIn("unknown backend_id", result["error"])
        self.assertEqual(controller.state.routes, {})

    def test_duplicate_flow_id_is_rejected(self):
        controller = make_controller()

        result = controller.replace_routes(
            [
                {"flow_id": 1, "backend_id": 216},
                {"flow_id": 1, "backend_id": 226},
            ]
        )

        self.assertFalse(result["ok"])
        self.assertIn("duplicate flow_id", result["error"])

    def test_external_route_api_is_disabled_in_batch_aware_mode(self):
        controller = make_controller(control_mode="batch-aware")

        result = controller.replace_routes([{"flow_id": 1, "backend_id": 216}])

        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"])


if __name__ == "__main__":
    unittest.main()
