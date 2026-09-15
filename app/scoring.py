"""Pure, testable scheduling policy with explainable decisions."""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ResourceRequest:
    cpu_millicores: int
    memory_mib: int
    gpu: int = 0


@dataclass(frozen=True)
class NodeSnapshot:
    name: str
    free_cpu_millicores: int
    free_memory_mib: int
    free_gpu: int = 0
    ready: bool = True
    memory_pressure: bool = False
    disk_pressure: bool = False
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateEvidence:
    node: str
    accepted: bool
    score: Optional[float]
    reasons: Tuple[str, ...]
    factors: Dict[str, float]


def _fit_ratio(free: int, requested: int) -> float:
    if requested == 0:
        return 1.0
    return min(1.0, free / requested)


def score_candidates(
    nodes: Iterable[NodeSnapshot],
    request: ResourceRequest,
    preferred_labels: Optional[Dict[str, str]] = None,
) -> List[CandidateEvidence]:
    """Score nodes and retain a human-readable audit trail.

    Score composition deliberately favours safe placement before packing:
    resource headroom 45%, fragmentation cost 25%, health 20%, topology 10%.
    Nodes that cannot satisfy a hard resource or health requirement are rejected.
    """
    preferred_labels = preferred_labels or {}
    evidence: List[CandidateEvidence] = []

    for node in nodes:
        reasons: List[str] = []
        if not node.ready:
            reasons.append("node is not Ready")
        if node.memory_pressure:
            reasons.append("node reports MemoryPressure")
        if node.disk_pressure:
            reasons.append("node reports DiskPressure")
        if node.free_cpu_millicores < request.cpu_millicores:
            reasons.append("insufficient free CPU")
        if node.free_memory_mib < request.memory_mib:
            reasons.append("insufficient free memory")
        if node.free_gpu < request.gpu:
            reasons.append("insufficient free GPU")

        if reasons:
            evidence.append(CandidateEvidence(
                node=node.name, accepted=False, score=None,
                reasons=tuple(reasons), factors={},
            ))
            continue

        cpu_fit = _fit_ratio(node.free_cpu_millicores, max(request.cpu_millicores * 2, 1))
        memory_fit = _fit_ratio(node.free_memory_mib, max(request.memory_mib * 2, 1))
        gpu_fit = _fit_ratio(node.free_gpu, max(request.gpu * 2, 1)) if request.gpu else 1.0
        headroom = (cpu_fit + memory_fit + gpu_fit) / 3

        # A balanced remaining CPU/memory ratio is less likely to strand resources.
        remaining_cpu = max(node.free_cpu_millicores - request.cpu_millicores, 0)
        remaining_memory = max(node.free_memory_mib - request.memory_mib, 0)
        cpu_after = remaining_cpu / max(node.free_cpu_millicores, 1)
        memory_after = remaining_memory / max(node.free_memory_mib, 1)
        fragmentation = 1.0 - abs(cpu_after - memory_after)

        topology_matches = sum(
            1 for key, value in preferred_labels.items() if node.labels.get(key) == value
        )
        topology = topology_matches / len(preferred_labels) if preferred_labels else 1.0
        health = 1.0
        factors = {
            "headroom": round(headroom, 4),
            "fragmentation": round(fragmentation, 4),
            "health": health,
            "topology": round(topology, 4),
        }
        score = round(
            0.45 * headroom + 0.25 * fragmentation + 0.20 * health + 0.10 * topology,
            4,
        )
        evidence.append(CandidateEvidence(
            node=node.name, accepted=True, score=score,
            reasons=("eligible",), factors=factors,
        ))

    return sorted(evidence, key=lambda item: (not item.accepted, -(item.score or -1), item.node))


def choose_node(evidence: Iterable[CandidateEvidence]) -> Optional[CandidateEvidence]:
    """Return the highest-scoring eligible candidate, if one exists."""
    return next((item for item in evidence if item.accepted), None)
