"""Exact small-cluster scheduler minimizing estimated task completion time.

For 6-9 Pods, enumerating every feasible subset (64-512 cases) is cheap and
more dependable than a heuristic-only choice.
"""
from itertools import combinations


def _covers(capacity, requirement):
    return all(capacity.get(key, 0.0) + 1e-9 >= value for key, value in requirement.items())


def _capacity(devices, keys):
    return {key: sum(max(0.0, device.allocatable_resources.get(key, 0.0)) for device in devices) for key in keys}


def _resource_allocations(selected, requirement):
    allocations = {device.id: {} for device in selected}
    primary = next((key for key in ("cpu.core", "gpu.count", "memory.gb") if requirement.get(key, 0) > 0), None)
    if primary:
        remaining = requirement[primary]
        for device in selected:
            value = min(max(0.0, device.allocatable_resources.get(primary, 0.0)), remaining)
            allocations[device.id][primary] = value
            remaining -= value
    primary_total = sum(item.get(primary, 0.0) for item in allocations.values()) if primary else 0.0
    for key, need in requirement.items():
        if key == primary:
            continue
        remaining = need
        for device in selected:
            weight = allocations[device.id].get(primary, 0.0) / primary_total if primary_total else 1.0 / len(selected)
            value = min(max(0.0, device.allocatable_resources.get(key, 0.0)), need * weight)
            allocations[device.id][key] = value
            remaining -= value
        for device in selected:
            if remaining <= 1e-9:
                break
            headroom = max(0.0, device.allocatable_resources.get(key, 0.0) - allocations[device.id].get(key, 0.0))
            extra = min(headroom, remaining)
            allocations[device.id][key] = allocations[device.id].get(key, 0.0) + extra
            remaining -= extra
    return allocations


def _metrics_for(device, metrics):
    defaults = dict(metrics.get("default", {}))
    backend = str(device.labels.get("slot", ""))
    defaults.update(metrics.get("backends", {}).get(backend, {}))
    return {
        "service_rate_tasks_per_sec": float(defaults.get("service_rate_tasks_per_sec", 100.0)),
        "queue_depth": max(0.0, float(defaults.get("queue_depth", 0.0))),
        "network_latency_ms": max(0.0, float(defaults.get("network_latency_ms", 0.0))),
        "batch_size": max(1, int(defaults.get("batch_size", 16))),
        "batch_timeout_ms": max(0.0, float(defaults.get("batch_timeout_ms", 0.0))),
    }


def _largest_remainder(total, weights):
    denominator = sum(weights)
    exact = [total * weight / denominator for weight in weights]
    counts = [int(value) for value in exact]
    for _, index in sorted(((value - int(value), index) for index, value in enumerate(exact)), reverse=True)[:total - sum(counts)]:
        counts[index] += 1
    return counts


def _evaluate(selected, requirement, task_count, metrics):
    allocations = _resource_allocations(selected, requirement)
    effective_rates = []
    device_metrics = []
    for device in selected:
        item = _metrics_for(device, metrics)
        total_cpu = max(device.total_resources.get("cpu.core", 0.0), 1e-9)
        assigned_cpu = allocations[device.id].get("cpu.core", total_cpu)
        rate = item["service_rate_tasks_per_sec"] * min(1.0, assigned_cpu / total_cpu)
        effective_rates.append(max(rate, 1e-9))
        device_metrics.append(item)
    task_counts = _largest_remainder(task_count, effective_rates)
    task_allocations, estimates = {}, {}
    for device, item, rate, count in zip(selected, device_metrics, effective_rates, task_counts):
        queue_ms = item["queue_depth"] / item["service_rate_tasks_per_sec"] * 1000.0
        compute_ms = count / rate * 1000.0
        # A non-full final batch waits for the configured timeout in this batch server.
        batch_wait_ms = item["batch_timeout_ms"] if count and count % item["batch_size"] else 0.0
        estimated = queue_ms + item["network_latency_ms"] + compute_ms + batch_wait_ms
        task_allocations[device.id] = count
        estimates[device.id] = round(estimated, 3)
    return {
        "selected": list(selected),
        "resource_allocations": allocations,
        "task_allocations": task_allocations,
        "estimated_finish_ms": estimates,
        "estimated_makespan_ms": round(max(estimates.values()), 3),
        "estimated_total_ms": round(sum(estimates.values()), 3),
    }


def choose_by_completion_time(candidates, requirement, task_count, max_devices, metrics):
    """Return the feasible Pod subset with minimum estimated completion time."""
    if task_count <= 0 or not candidates:
        return None
    upper = min(max_devices or len(candidates), len(candidates))
    best = None
    evaluated = 0
    for size in range(1, upper + 1):
        for subset in combinations(candidates, size):
            if not _covers(_capacity(subset, requirement.keys()), requirement):
                continue
            evaluated += 1
            plan = _evaluate(subset, requirement, task_count, metrics)
            score = (plan["estimated_makespan_ms"], plan["estimated_total_ms"], len(subset), tuple(device.id for device in subset))
            if best is None or score < best[0]:
                best = (score, plan)
    if best is None:
        return None
    best[1]["evaluated_combinations"] = evaluated
    return best[1]