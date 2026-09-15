import unittest

from app.adapters import (
    completion_plan_to_status,
    task_request_from_custom_resource,
    worker_snapshot_from_pod,
)
from app.integration_models import CPU_KEY, GPU_KEY, MEMORY_KEY


class AdapterTests(unittest.TestCase):
    def test_compute_task_is_normalized_for_multi_worker_scheduler(self):
        request = task_request_from_custom_resource({
            "spec": {
                "strategy": "minExpectedCompletionTime",
                "taskCount": 32,
                "maxWorkers": 3,
                "resources": {"cpu": "1500m", "memory": "1536Mi", "gpu": 1},
                "workerSelector": {"orchestrator.wzt.io/worker": "true"},
                "network": {"minBandwidthMbps": 1000, "maxLatencyMs": 5},
            }
        })

        self.assertEqual(1.5, request.resources[CPU_KEY])
        self.assertEqual(1.5, request.resources[MEMORY_KEY])
        self.assertEqual(1.0, request.resources[GPU_KEY])
        self.assertEqual(32, request.task_count)
        self.assertEqual(3, request.max_workers)
        self.assertEqual(1000.0, request.min_bandwidth_mbps)

    def test_worker_capacity_sums_container_requests(self):
        worker = worker_snapshot_from_pod({
            "metadata": {
                "name": "worker-216",
                "namespace": "default",
                "labels": {"slot": "216"},
            },
            "spec": {
                "nodeName": "node194",
                "containers": [
                    {"resources": {"requests": {"cpu": "500m", "memory": "1Gi"}}},
                    {"resources": {"requests": {"cpu": "250m", "memory": "512Mi"}}},
                ],
            },
            "status": {
                "phase": "Running",
                "podIP": "10.244.3.20",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        })

        self.assertEqual(0.75, worker.total_resources[CPU_KEY])
        self.assertEqual(1.5, worker.total_resources[MEMORY_KEY])
        self.assertEqual("node194", worker.node_name)
        self.assertTrue(worker.ready)

    def test_completion_plan_becomes_compute_task_status(self):
        first = worker_snapshot_from_pod({
            "metadata": {
                "name": "worker-216",
                "namespace": "default",
                "labels": {"slot": "216"},
            },
            "spec": {
                "nodeName": "node194",
                "containers": [
                    {"resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}
                ],
            },
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        })
        plan = {
            "selected": [first],
            "resource_allocations": {
                "worker-216": {"cpu.core": 1.0, "memory.gb": 1.0}
            },
            "task_allocations": {"worker-216": 10},
            "estimated_finish_ms": {"worker-216": 125.0},
            "estimated_makespan_ms": 125.0,
            "estimated_total_ms": 125.0,
            "evaluated_combinations": 3,
        }

        status = completion_plan_to_status(plan, observed_generation=4)

        self.assertEqual("Planned", status["phase"])
        self.assertEqual(4, status["observedGeneration"])
        self.assertEqual(10, status["selectedWorkers"][0]["assignedTaskCount"])
        self.assertEqual(1, status["selectedWorkers"][0]["flowId"])
        self.assertEqual(216, status["selectedWorkers"][0]["backendId"])
        self.assertEqual(125.0, status["estimatedMakespanMs"])


if __name__ == "__main__":
    unittest.main()
