"""Kubernetes 对象与多 Worker 调度模型之间的纯转换函数。"""

from decimal import Decimal
from typing import Dict, Iterable, Optional

from app.integration_models import (
    CPU_KEY,
    GPU_KEY,
    MEMORY_KEY,
    MultiWorkerRequest,
    WorkerSnapshot,
)


_GIB = Decimal(1024) ** 3
_MEMORY_MULTIPLIERS = {
    "Ki": Decimal(1024),
    "Mi": Decimal(1024) ** 2,
    "Gi": _GIB,
    "Ti": Decimal(1024) ** 4,
    "K": Decimal(1000),
    "M": Decimal(1000) ** 2,
    "G": Decimal(1000) ** 3,
    "T": Decimal(1000) ** 4,
}


def parse_cpu_cores(value) -> float:
    """把 Kubernetes CPU quantity 转换为 CPU 核数。"""
    text = str(value or "0").strip()
    multipliers = {
        "n": Decimal("0.000000001"),
        "u": Decimal("0.000001"),
        "m": Decimal("0.001"),
    }
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            return float(Decimal(text[:-1]) * multiplier)
    return float(Decimal(text))


def parse_memory_gib(value) -> float:
    """把 Kubernetes memory quantity 转换为 GiB。"""
    text = str(value or "0").strip()
    for suffix in sorted(_MEMORY_MULTIPLIERS, key=len, reverse=True):
        if text.endswith(suffix):
            number = Decimal(text[:-len(suffix)])
            return float(number * _MEMORY_MULTIPLIERS[suffix] / _GIB)
    return float(Decimal(text) / _GIB)


def task_request_from_custom_resource(task: dict) -> MultiWorkerRequest:
    """从 ComputeTask 字典生成算法无关的多 Worker 需求。"""
    spec = task.get("spec", {})
    resources = spec.get("resources", {})
    network = spec.get("network", {})
    normalized = {
        CPU_KEY: parse_cpu_cores(resources.get("cpu", "0")),
        MEMORY_KEY: parse_memory_gib(resources.get("memory", "0")),
        GPU_KEY: float(resources.get("gpu", 0) or 0),
    }
    return MultiWorkerRequest(
        resources={key: value for key, value in normalized.items() if value > 0},
        task_count=max(0, int(spec.get("taskCount", 0) or 0)),
        max_workers=max(0, int(spec.get("maxWorkers", 0) or 0)),
        strategy=str(spec.get("strategy", "minExpectedCompletionTime")),
        worker_selector=dict(spec.get("workerSelector", {}) or {}),
        min_bandwidth_mbps=max(
            0.0, float(network.get("minBandwidthMbps", 0) or 0)
        ),
        max_latency_ms=max(0.0, float(network.get("maxLatencyMs", 0) or 0)),
    )


def _container_requests(containers: Iterable[dict]) -> Dict[str, float]:
    result = {CPU_KEY: 0.0, MEMORY_KEY: 0.0, GPU_KEY: 0.0}
    for container in containers or []:
        requests = container.get("resources", {}).get("requests", {}) or {}
        result[CPU_KEY] += parse_cpu_cores(requests.get("cpu", "0"))
        result[MEMORY_KEY] += parse_memory_gib(requests.get("memory", "0"))
        result[GPU_KEY] += float(
            requests.get("nvidia.com/gpu", requests.get("amd.com/gpu", 0)) or 0
        )
    return result


def _pod_is_ready(pod: dict) -> bool:
    conditions = pod.get("status", {}).get("conditions", []) or []
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in conditions
    )


def worker_snapshot_from_pod(pod: dict) -> WorkerSnapshot:
    """把一个 Worker Pod 转换为算法可以直接读取的容量快照。"""
    metadata = pod.get("metadata", {})
    spec = pod.get("spec", {})
    status = pod.get("status", {})
    capacity = _container_requests(spec.get("containers", []))
    namespace = metadata.get("namespace", "default")
    node_name = spec.get("nodeName", "")
    labels = dict(metadata.get("labels", {}) or {})
    labels.update({"_namespace": namespace, "_node": node_name})
    return WorkerSnapshot(
        id=metadata.get("name", ""),
        ip=status.get("podIP", ""),
        node_name=node_name,
        namespace=namespace,
        total_resources=dict(capacity),
        allocatable_resources=dict(capacity),
        labels=labels,
        ready=status.get("phase") == "Running" and _pod_is_ready(pod),
    )


def completion_plan_to_status(
    plan: Optional[dict], observed_generation: int
) -> dict:
    """把原预计完成时间算法的返回值转换为 ComputeTask.status。"""
    if not plan:
        return {
            "observedGeneration": observed_generation,
            "phase": "Unschedulable",
            "selectedWorkers": [],
            "decision": "没有找到满足总资源需求的 Worker 组合",
        }

    allocations = plan.get("resource_allocations", {})
    task_allocations = plan.get("task_allocations", {})
    finish_times = plan.get("estimated_finish_ms", {})
    workers = []
    for flow_id, worker in enumerate(plan.get("selected", []), start=1):
        item = {
            "pod": worker.id,
            "namespace": worker.namespace,
            "node": worker.node_name,
            "flowId": flow_id,
            "assignedTaskCount": task_allocations.get(worker.id, 0),
            "estimatedFinishMs": finish_times.get(worker.id),
            "allocatedResources": allocations.get(worker.id, {}),
        }
        backend_id = str(worker.labels.get("slot", "")).strip()
        if backend_id.isdigit():
            item["backendId"] = int(backend_id)
        workers.append(item)

    return {
        "observedGeneration": observed_generation,
        "phase": "Planned",
        "selectedWorkers": workers,
        "estimatedMakespanMs": plan.get("estimated_makespan_ms"),
        "estimatedTotalMs": plan.get("estimated_total_ms"),
        "evaluatedCombinations": plan.get("evaluated_combinations", 0),
        "decision": "按预计最晚完成时间选择 Worker 组合",
    }
