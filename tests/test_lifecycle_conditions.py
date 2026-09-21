import sys
import types
import unittest
from unittest.mock import Mock

try:
    import kubernetes  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("kubernetes")
    stub.client = Mock()
    stub.config = Mock()
    sys.modules["kubernetes"] = stub

from app.controller import ComputeTaskController


class LifecycleConditionTests(unittest.TestCase):
    def test_plan_only_task_is_scheduled_but_not_executing(self):
        conditions = ComputeTaskController._lifecycle_conditions({
            "phase": "Planned",
            "executionAllowed": False,
        })
        self.assertEqual("True", conditions[0]["status"])
        self.assertEqual("PlacementSelected", conditions[0]["reason"])
        self.assertEqual("False", conditions[1]["status"])
        self.assertEqual("PlanOnly", conditions[1]["reason"])

    def test_running_task_has_execution_ready_condition(self):
        conditions = ComputeTaskController._lifecycle_conditions({
            "phase": "Running",
            "executionAllowed": True,
        })
        self.assertEqual("True", conditions[0]["status"])
        self.assertEqual("True", conditions[1]["status"])
        self.assertEqual("JobActive", conditions[1]["reason"])

    def test_unschedulable_task_has_clear_negative_conditions(self):
        conditions = ComputeTaskController._lifecycle_conditions({
            "phase": "Unschedulable",
            "executionAllowed": False,
        })
        self.assertEqual("False", conditions[0]["status"])
        self.assertEqual("NoEligibleWorker", conditions[0]["reason"])
        self.assertEqual("False", conditions[1]["status"])
        self.assertEqual("NoEligibleWorker", conditions[1]["reason"])

    def test_status_observes_generation_without_a_timestamp(self):
        task = {"metadata": {"generation": 7}}
        status = ComputeTaskController._with_lifecycle_status(task, {
            "phase": "Planned",
            "executionAllowed": False,
        })
        self.assertEqual(7, status["observedGeneration"])
        self.assertEqual(["Scheduled", "ExecutionReady"], [
            item["type"] for item in status["conditions"]
        ])
        self.assertNotIn("lastTransitionTime", status["conditions"][0])


if __name__ == "__main__":
    unittest.main()