import unittest

from app.multi_worker_planner import plan_multi_worker_task


def worker_pod(
    name,
    node,
    slot,
    cpu,
    memory,
    *,
    ready=True,
    worker_label="true",
):
    return {
        "metadata": {
            "name": name,
            "namespace": "default",
            "labels": {
                "slot": slot,
                "orchestrator.wzt.io/worker": worker_label,
            },
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
            "conditions": [{
                "type": "Ready",
                "status": "True" if ready else "False",
            }],
        },
    }


def compute_task(**spec_overrides):
    spec = {
        "image": "busybox:1.36",
        "execution": "plan",
        "strategy": "minExpectedCompletionTime",
        "taskCount": 20,
        "maxWorkers": 2,
        "resources": {"cpu": "6", "memory": "12Gi"},
        "workerSelector": {"orchestrator.wzt.io/worker": "true"},
    }
    spec.update(spec_overrides)
    return {
        "metadata": {"name": "multi-plan", "generation": 3},
        "spec": spec,
    }


class MultiWorkerPlannerTests(unittest.TestCase):
    def test_only_ready_matching_workers_enter_the_plan(self):
        pods = [
            worker_pod("worker-216", "node194", "216", "4", "8Gi"),
            worker_pod("worker-226", "node200", "226", "2", "4Gi"),
            worker_pod(
                "wrong-label", "node194", "217", "100", "100Gi",
                worker_label="false",
            ),
            worker_pod(
                "not-ready", "node200", "227", "100", "100Gi",
                ready=False,
            ),
        ]

        status = plan_multi_worker_task(compute_task(), pods)

        self.assertEqual("Planned", status["phase"])
        self.assertEqual(
            {"worker-216", "worker-226"},
            {item["pod"] for item in status["selectedWorkers"]},
        )
        self.assertEqual(
            20,
            sum(
                item["assignedTaskCount"]
                for item in status["selectedWorkers"]
            ),
        )

    def test_busy_worker_is_avoided_by_frozen_policy(self):
        task = compute_task(
            taskCount=16,
            maxWorkers=1,
            resources={"cpu": "2", "memory": "2Gi"},
        )
        pods = [
            worker_pod("worker-216", "node194", "216", "4", "4Gi"),
            worker_pod("worker-226", "node200", "226", "4", "4Gi"),
        ]
        metrics = {
            "default": {
                "service_rate_tasks_per_sec": 100,
                "queue_depth": 0,
                "network_latency_ms": 1,
                "batch_size": 16,
                "batch_timeout_ms": 5000,
            },
            "backends": {
                "216": {"queue_depth": 1000},
                "226": {"queue_depth": 0},
            },
        }

        status = plan_multi_worker_task(task, pods, metrics)

        self.assertEqual("worker-226", status["selectedWorkers"][0]["pod"])

    def test_network_constraints_are_not_silently_ignored(self):
        task = compute_task(
            network={"minBandwidthMbps": 1000, "maxLatencyMs": 5}
        )
        status = plan_multi_worker_task(
            task,
            [worker_pod("worker-216", "node194", "216", "8", "16Gi")],
        )

        self.assertEqual("Unsupported", status["phase"])
        self.assertEqual([], status["selectedWorkers"])
        self.assertIn("不会静默忽略", status["decision"])


if __name__ == "__main__":
    unittest.main()
