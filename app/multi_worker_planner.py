"""把 ComputeTask、Worker Pod 和冻结算法串成纯函数规划流程。"""

from typing import Iterable, Optional

from app.adapters import (
    completion_plan_to_status,
    task_request_from_custom_resource,
    worker_snapshot_from_pod,
)
from app.policies import choose_by_completion_time


def _matches_selector(labels: dict, selector: dict) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _empty_status(task: dict, phase: str, decision: str) -> dict:
    return {
        "observedGeneration": int(
            task.get("metadata", {}).get("generation", 0) or 0
        ),
        "phase": phase,
        "selectedWorkers": [],
        "decision": decision,
    }


def plan_multi_worker_task(
    task: dict,
    pods: Iterable[dict],
    runtime_metrics: Optional[dict] = None,
) -> dict:
    """为一个多 Worker ComputeTask 生成计划，不产生任何集群副作用。"""
    request = task_request_from_custom_resource(task)

    if request.strategy != "minExpectedCompletionTime":
        return _empty_status(
            task,
            "Unsupported",
            "多 Worker 规划器仅处理 minExpectedCompletionTime 策略",
        )
    if request.task_count <= 0:
        return _empty_status(
            task,
            "Invalid",
            "minExpectedCompletionTime 策略要求 taskCount 大于 0",
        )
    if request.min_bandwidth_mbps > 0 or request.max_latency_ms > 0:
        return _empty_status(
            task,
            "Unsupported",
            "网络约束尚未接入拓扑验证，本阶段不会静默忽略该约束",
        )

    workers = []
    for pod in pods:
        worker = worker_snapshot_from_pod(pod)
        if not worker.ready:
            continue
        if not _matches_selector(worker.labels, request.worker_selector):
            continue
        workers.append(worker)

    if not workers:
        return _empty_status(
            task,
            "Unschedulable",
            "没有找到 Ready 且满足 workerSelector 的 Worker Pod",
        )

    plan = choose_by_completion_time(
        workers,
        request.resources,
        request.task_count,
        request.max_workers,
        runtime_metrics or {},
    )
    return completion_plan_to_status(
        plan,
        int(task.get("metadata", {}).get("generation", 0) or 0),
    )
