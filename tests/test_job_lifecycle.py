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

from app.controller import ComputeTaskController


class JobLifecycleTests(unittest.TestCase):
    def job(self, **status):
        return types.SimpleNamespace(
            status=types.SimpleNamespace(**status)
        )

    def test_job_name_is_generation_scoped_and_bounded(self):
        first = {"metadata": {"name": "demo", "generation": 1}}
        second = {"metadata": {"name": "demo", "generation": 2}}
        self.assertEqual("task-demo-g1", ComputeTaskController._job_name(first))
        self.assertNotEqual(
            ComputeTaskController._job_name(first),
            ComputeTaskController._job_name(second),
        )
        long_task = {
            "metadata": {"name": "a" * 100, "generation": 123}
        }
        self.assertLessEqual(
            len(ComputeTaskController._job_name(long_task)),
            63,
        )

    def test_owner_reference_points_to_compute_task_uid(self):
        task = {
            "metadata": {
                "name": "demo",
                "generation": 2,
                "uid": "task-uid-123",
            }
        }
        with patch(
            "app.controller.client.V1OwnerReference",
            side_effect=lambda **values: types.SimpleNamespace(**values),
        ):
            owner = ComputeTaskController._job_owner_references(task)[0]
        self.assertEqual("orchestrator.wzt.io/v1alpha1", owner.api_version)
        self.assertEqual("ComputeTask", owner.kind)
        self.assertEqual("demo", owner.name)
        self.assertEqual("task-uid-123", owner.uid)
        self.assertTrue(owner.controller)
        self.assertFalse(owner.block_owner_deletion)

    def test_missing_uid_does_not_create_fake_owner(self):
        task = {"metadata": {"name": "demo", "generation": 1}}
        self.assertEqual(
            [],
            ComputeTaskController._job_owner_references(task),
        )

    def test_job_phase_progression(self):
        cases = (
            ({}, "Submitted"),
            ({"active": 1}, "Running"),
            ({"succeeded": 1}, "Succeeded"),
            ({"failed": 1}, "Failed"),
        )
        for values, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    expected,
                    ComputeTaskController._job_phase(self.job(**values)),
                )

    def test_global_plan_preserves_current_generation_job_result(self):
        controller = ComputeTaskController.__new__(ComputeTaskController)
        controller.batch = Mock()
        controller.custom = Mock()
        controller.core = Mock()
        controller.batch.read_namespaced_job.return_value = self.job(
            succeeded=1
        )
        task = {
            "metadata": {
                "name": "demo",
                "generation": 2,
                "uid": "task-uid-123",
            },
            "spec": {
                "execution": "apply",
                "resources": {"cpu": "10m", "memory": "16Mi"},
            },
            "status": {
                "observedGeneration": 2,
                "phase": "Planned",
                "jobName": "task-demo-g2",
            },
        }
        chosen = types.SimpleNamespace(node="node194", score=1.0)

        with patch("app.controller.EXECUTION_MODE", "plan"), patch(
            "app.controller.score_candidates",
            return_value=[],
        ), patch("app.controller.choose_node", return_value=chosen):
            controller.reconcile(task, [])

        self.assertFalse(task["status"]["executionAllowed"])
        self.assertEqual("Succeeded", task["status"]["phase"])
        self.assertEqual("task-demo-g2", task["status"]["jobName"])
        controller.batch.create_namespaced_job.assert_not_called()

    def test_execution_requires_both_safety_gates(self):
        cases = (
            ("plan", "plan", False),
            ("plan", "apply", False),
            ("apply", "plan", False),
            ("apply", "apply", True),
        )
        for global_mode, task_mode, expected in cases:
            with self.subTest(global_mode=global_mode, task_mode=task_mode):
                with patch("app.controller.EXECUTION_MODE", global_mode):
                    self.assertEqual(
                        expected,
                        ComputeTaskController._execution_allowed(
                            {"execution": task_mode}
                        ),
                    )


if __name__ == "__main__":
    unittest.main()