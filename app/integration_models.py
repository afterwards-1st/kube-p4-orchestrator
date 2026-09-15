"""多 Worker 调度的稳定输入输出模型。

这些模型用于隔离 Kubernetes API 对象与调度算法。算法只读取这里定义的
规范化数据，因此后续更换快照来源时不需要修改算法。
"""

from dataclasses import dataclass, field
from typing import Dict


CPU_KEY = "cpu.core"
MEMORY_KEY = "memory.gb"
GPU_KEY = "gpu.count"


@dataclass(frozen=True)
class MultiWorkerRequest:
    """一个可以拆分到多个 Worker 的任务需求。"""

    resources: Dict[str, float]
    task_count: int = 0
    max_workers: int = 0
    strategy: str = "minExpectedCompletionTime"
    worker_selector: Dict[str, str] = field(default_factory=dict)
    min_bandwidth_mbps: float = 0.0
    max_latency_ms: float = 0.0


@dataclass(frozen=True)
class WorkerSnapshot:
    """一个已运行 Worker Pod 可提供的计算容量快照。"""

    id: str
    ip: str
    node_name: str
    namespace: str
    total_resources: Dict[str, float]
    allocatable_resources: Dict[str, float]
    labels: Dict[str, str] = field(default_factory=dict)
    ready: bool = False
