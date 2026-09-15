import unittest

from app.adapters import (
    completion_plan_to_status,
    task_request_from_custom_resource,
    worker_snapshot_from_pod,
)
from app.policies import choose_by_completion_time


def worker_pod(name, node, slot, cpu, memory):
    return {
        "metadata": {
            "name": name,
            "namespace": "default",
            "labels": {"slot": slot, "orchestrator.wzt.io/worker": "true"},
        },
        "spec": {
            "nodeName": node,
            "containers": [{
                "resources": {
                    "requests": {"cpu": cpu, "memory": memory}
                }
            }],
        },
        "status": {
            "phase": "Running",
            "podIP": "10.244.0.10",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


class CompletionPolicyCompatibilityTests(unittest.TestCase):
    def test_adapter_output_runs_through_frozen_policy(self):
        request = task_request_from_custom_resource({
            "spec": {
                "strategy": "minExpectedCompletionTime",
                "taskCount": 20,
                "maxWorkers": 2,
                "resources": {"cpu": "6", "memory": "12Gi"},
            }
        })
        workers = [
            worker_snapshot_from_pod(
                worker_pod("worker-216", "node194", "216", "4", "8Gi")
            ),
            worker_snapshot_from_pod(
                worker_pod("worker-226", "node200", "226", "2", "4Gi")
            ),
        ]
        metrics = {
            "default": {
                "service_rate_tasks_per_sec": 100,
                "queue_depth": 0,
                "network_latency_ms": 1,
                "batch_size": 16,
                "batch_timeout_ms": 5000,
            }
        }

        plan = choose_by_completion_time(
            workers,
            request.resources,
            request.task_count,
            request.max_workers,
            metrics,
        )
        status = completion_plan_to_status(plan, observed_generation=1)

        self.assertIsNotNone(plan)
        self.assertEqual({"worker-216", "worker-226"}, {
            worker.id for worker in plan["selected"]
        })
        self.assertEqual(20, sum(plan["task_allocations"].values()))
        self.assertEqual("Planned", status["phase"])
        self.assertEqual(2, len(status["selectedWorkers"]))
        self.assertEqual(20, sum(
            item["assignedTaskCount"] for item in status["selectedWorkers"]
        ))


if __name__ == "__main__":
    unittest.main()
