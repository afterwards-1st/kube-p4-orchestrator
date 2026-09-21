"""Kubernetes controller for ComputeTask planning.

The controller is intentionally conservative.  In its default ``plan`` mode it
only writes a decision to ComputeTask.status; it never creates a workload.
``apply`` mode additionally requires a per-task opt-in and is not enabled by
the shipped manifest.
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from kubernetes import client, config

from app.multi_worker_planner import plan_multi_worker_task
from app.p4_control import P4ControlClient, normalize_routes
from app.runtime_metrics import load_combined_runtime_metrics
from app.scoring import NodeSnapshot, ResourceRequest, choose_node, score_candidates


GROUP = "orchestrator.wzt.io"
VERSION = "v1alpha1"
PLURAL = "computetasks"
NAMESPACE = os.environ.get("WATCH_NAMESPACE", "kube-orchestrator")
RESOURCE_REFRESH_SECONDS = max(
    1,
    int(
        os.environ.get(
            "RESOURCE_REFRESH_SECONDS",
            os.environ.get("POLL_INTERVAL_SECONDS", "15"),
        )
    ),
)
METRICS_POLL_SECONDS = max(
    1,
    int(os.environ.get("METRICS_POLL_SECONDS", "2")),
)
EXECUTION_MODE = os.environ.get("EXECUTION_MODE", "plan")
EXCLUDED_NODES = frozenset(
    name.strip() for name in os.environ.get("EXCLUDED_NODES", "").split(",") if name.strip()
)
WORKER_NAMESPACE = os.environ.get("WORKER_NAMESPACE", "default")
WORKER_SLOTS = frozenset(
    slot.strip() for slot in os.environ.get("WORKER_SLOTS", "").split(",") if slot.strip()
)
RUNTIME_METRICS_PATH = os.environ.get("RUNTIME_METRICS_PATH", "")
RUNTIME_METRICS_URL = os.environ.get("RUNTIME_METRICS_URL", "")
RUNTIME_METRICS_TOKEN = os.environ.get("RUNTIME_METRICS_TOKEN", "")
RUNTIME_METRICS_TIMEOUT_SECONDS = float(
    os.environ.get("RUNTIME_METRICS_TIMEOUT_SECONDS", "2")
)
P4_ROUTING_MODE = os.environ.get("P4_ROUTING_MODE", "plan")
P4_ROUTE_OWNER = os.environ.get("P4_ROUTE_OWNER", "")
P4_CONTROL_URL = os.environ.get("P4_CONTROL_URL", "")
P4_CONTROL_TOKEN = os.environ.get("P4_CONTROL_TOKEN", "")
P4_CONTROL_TIMEOUT_SECONDS = float(
    os.environ.get("P4_CONTROL_TIMEOUT_SECONDS", "2")
)
P4_STATUS_CACHE_SECONDS = float(
    os.environ.get("P4_STATUS_CACHE_SECONDS", "5")
)


def parse_cpu(value) -> int:
    value = str(value or "0").strip()
    if value.endswith("m"):
        return int(float(value[:-1]))
    return int(float(value) * 1000)


def parse_memory(value) -> int:
    value = str(value or "0").strip()
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / (1024 * 1024), "M": 1000 / 1024, "G": 1000, "T": 1000 * 1024}
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return int(float(value[:-len(suffix)]) * multiplier)
    return int(float(value) / (1024 * 1024))


def condition_is_true(conditions, condition_type: str) -> bool:
    return any(item.type == condition_type and item.status == "True" for item in conditions or [])


class ComputeTaskController:
    def __init__(self):
        config.load_incluster_config()
        self.core = client.CoreV1Api()
        self.batch = client.BatchV1Api()
        self.custom = client.CustomObjectsApi()
        self._cached_nodes = []
        self._cached_worker_pods = []
        self._cached_tasks = []
        self._next_resource_refresh = 0.0
        self._last_metrics_state = None
        self.p4 = (
            P4ControlClient(
                P4_CONTROL_URL,
                P4_CONTROL_TOKEN,
                P4_CONTROL_TIMEOUT_SECONDS,
                P4_STATUS_CACHE_SECONDS,
            )
            if P4_CONTROL_URL
            else None
        )

    def _requested_resources(self, containers: Iterable) -> ResourceRequest:
        cpu = memory = gpu = 0
        for container in containers or []:
            requests = (container.resources.requests if container.resources else None) or {}
            cpu += parse_cpu(requests.get("cpu", "0"))
            memory += parse_memory(requests.get("memory", "0"))
            gpu += int(requests.get("nvidia.com/gpu", requests.get("amd.com/gpu", 0)) or 0)
        return ResourceRequest(cpu, memory, gpu)

    def snapshots(self) -> List[NodeSnapshot]:
        usage: Dict[str, ResourceRequest] = {}
        for pod in self.core.list_pod_for_all_namespaces().items:
            node_name = pod.spec.node_name
            if not node_name or pod.status.phase not in ("Running", "Pending"):
                continue
            old = usage.get(node_name, ResourceRequest(0, 0, 0))
            current = self._requested_resources(pod.spec.containers)
            usage[node_name] = ResourceRequest(
                old.cpu_millicores + current.cpu_millicores,
                old.memory_mib + current.memory_mib,
                old.gpu + current.gpu,
            )

        snapshots = []
        for node in self.core.list_node().items:
            if node.metadata.name in EXCLUDED_NODES:
                logging.info("excluding node %s from scheduling decisions", node.metadata.name)
                continue
            allocatable = node.status.allocatable or {}
            used = usage.get(node.metadata.name, ResourceRequest(0, 0, 0))
            snapshots.append(NodeSnapshot(
                name=node.metadata.name,
                free_cpu_millicores=max(parse_cpu(allocatable.get("cpu", "0")) - used.cpu_millicores, 0),
                free_memory_mib=max(parse_memory(allocatable.get("memory", "0")) - used.memory_mib, 0),
                free_gpu=max(int(allocatable.get("nvidia.com/gpu", 0) or 0) - used.gpu, 0),
                ready=condition_is_true(node.status.conditions, "Ready"),
                memory_pressure=condition_is_true(node.status.conditions, "MemoryPressure"),
                disk_pressure=condition_is_true(node.status.conditions, "DiskPressure"),
                labels=dict(node.metadata.labels or {}),
            ))
        return snapshots

    def worker_pods(self) -> List[dict]:
        """Return only explicitly allowed backend Pods as serializable data."""
        serializer = client.ApiClient()
        result = []
        for pod in self.core.list_namespaced_pod(
            namespace=WORKER_NAMESPACE
        ).items:
            labels = dict(pod.metadata.labels or {})
            if labels.get("slot", "") not in WORKER_SLOTS:
                continue
            if pod.spec.node_name in EXCLUDED_NODES:
                continue
            result.append(serializer.sanitize_for_serialization(pod))
        return result

    @staticmethod
    def _request(task: dict) -> ResourceRequest:
        resources = task.get("spec", {}).get("resources", {})
        return ResourceRequest(
            parse_cpu(resources.get("cpu", "0")),
            parse_memory(resources.get("memory", "0")),
            int(resources.get("gpu", 0) or 0),
        )

    @staticmethod
    def _evidence_payload(evidence) -> list:
        return [{
            "node": item.node,
            "accepted": item.accepted,
            "score": item.score,
            "reasons": list(item.reasons),
            "factors": item.factors,
        } for item in evidence]

    @staticmethod
    def _status_without_trace(status: dict) -> dict:
        return {
            key: value for key, value in (status or {}).items()
            if key != "decisionTrace"
        }

    @staticmethod
    def _lifecycle_conditions(status: dict) -> list:
        """Return deterministic Conditions without clock-driven status writes."""
        phase = str(status.get("phase", ""))
        scheduled = phase not in ("", "Unschedulable", "Terminating")
        execution_allowed = bool(status.get("executionAllowed", False))
        if phase == "Unschedulable":
            execution_reason = "NoEligibleWorker"
        elif phase == "Terminating":
            execution_reason = "Terminating"
        elif phase == "Failed":
            execution_reason = "JobFailed"
        elif phase in ("Running", "Succeeded"):
            execution_reason = "JobActive" if phase == "Running" else "JobSucceeded"
        elif not execution_allowed:
            execution_reason = "PlanOnly"
        else:
            execution_reason = "AwaitingJob"
        execution_ready = phase in ("Running", "Succeeded")
        return [
            {
                "type": "Scheduled",
                "status": "True" if scheduled else "False",
                "reason": "PlacementSelected" if scheduled else "NoEligibleWorker",
                "message": "A placement decision exists" if scheduled else "No eligible placement exists",
            },
            {
                "type": "ExecutionReady",
                "status": "True" if execution_ready else "False",
                "reason": execution_reason,
                "message": "Workload is active or completed" if execution_ready else "Workload is not executing",
            },
        ]

    @classmethod
    def _with_lifecycle_status(cls, task: dict, status: dict) -> dict:
        """Attach observed generation and Conditions to every status result."""
        result = dict(status)
        result["observedGeneration"] = int(
            task.get("metadata", {}).get("generation", 0) or 0
        )
        result["conditions"] = cls._lifecycle_conditions(result)
        return result
    @classmethod
    def _decision_change_reason(
        cls,
        current_status: dict,
        new_status: dict,
    ) -> str:
        current_plan = cls._status_without_trace(current_status)
        new_plan = cls._status_without_trace(new_status)
        if not current_plan:
            return "initial-plan"
        if current_plan == new_plan:
            return "observability-initialized"
        if current_plan.get("phase") != new_plan.get("phase"):
            return "phase-changed"
        current_workers = [
            item.get("pod")
            for item in current_plan.get("selectedWorkers", [])
        ]
        new_workers = [
            item.get("pod")
            for item in new_plan.get("selectedWorkers", [])
        ]
        if current_workers != new_workers:
            return "selected-workers-changed"
        if current_plan.get("selectedNode") != new_plan.get("selectedNode"):
            return "selected-node-changed"
        if (
            current_plan.get("estimatedMakespanMs")
            != new_plan.get("estimatedMakespanMs")
        ):
            return "estimated-completion-changed"
        return "plan-details-changed"

    @staticmethod
    def _queue_depths(worker_pods, runtime_metrics) -> dict:
        backends = (runtime_metrics or {}).get("backends", {})
        result = {}
        for pod in worker_pods or []:
            labels = pod.get("metadata", {}).get("labels", {}) or {}
            slot = str(labels.get("slot", "")).strip()
            values = backends.get(slot, {}) if slot else {}
            if not isinstance(values, dict) or "queue_depth" not in values:
                continue
            try:
                result[slot] = max(0.0, float(values["queue_depth"]))
            except (TypeError, ValueError):
                continue
        return result

    def _decision_trace(
        self,
        current_status: dict,
        new_status: dict,
        worker_pods=None,
        runtime_metrics=None,
        metrics_state=None,
    ) -> dict:
        state = metrics_state or {}
        source = state.get("source", "kubernetes-resource-snapshot")
        freshness = {
            "dynamic": "fresh",
            "static-no-fresh-data": "stale",
            "static-fallback": "unavailable",
            "static": "static",
        }.get(source, "not-applicable")
        reason = self._decision_change_reason(
            current_status,
            new_status,
        )
        current_trace = (current_status or {}).get("decisionTrace", {})
        new_trace_state = (
            source,
            freshness,
            int(state.get("dynamic_backend_count", 0) or 0),
        )
        current_trace_state = (
            current_trace.get("metricsSource"),
            current_trace.get("metricsFreshness"),
            int(current_trace.get("dynamicBackendCount", 0) or 0),
        )
        if (
            reason == "observability-initialized"
            and current_trace
            and current_trace_state != new_trace_state
        ):
            reason = "metrics-state-changed"
        return {
            "reason": reason,
            "recordedAt": datetime.now(timezone.utc).isoformat().replace(
                "+00:00",
                "Z",
            ),
            "metricsSource": source,
            "metricsFreshness": freshness,
            "dynamicBackendCount": int(
                state.get("dynamic_backend_count", 0) or 0
            ),
            "queueDepthByBackend": self._queue_depths(
                worker_pods,
                runtime_metrics,
            ),
        }

    def _patch_status_if_changed(
        self,
        task: dict,
        status: dict,
        decision_trace=None,
    ) -> bool:
        """只在规划变化或首次补充审计信息时写入 API Server。"""
        current_status = task.get("status", {})
        same_plan = (
            self._status_without_trace(current_status)
            == self._status_without_trace(status)
        )
        current_trace = current_status.get("decisionTrace")
        has_trace = isinstance(current_trace, dict)
        trace_state_changed = False
        if has_trace and decision_trace is not None:
            trace_state_changed = (
                current_trace.get("metricsSource"),
                current_trace.get("metricsFreshness"),
                current_trace.get("dynamicBackendCount"),
            ) != (
                decision_trace.get("metricsSource"),
                decision_trace.get("metricsFreshness"),
                decision_trace.get("dynamicBackendCount"),
            )
        if same_plan and (
            decision_trace is None
            or (has_trace and not trace_state_changed)
        ):
            logging.debug("status unchanged for %s", task["metadata"]["name"])
            return False

        stored_status = dict(status)
        if decision_trace is not None:
            stored_status["decisionTrace"] = decision_trace
        self.custom.patch_namespaced_custom_object_status(
            GROUP,
            VERSION,
            NAMESPACE,
            PLURAL,
            task["metadata"]["name"],
            {"status": stored_status},
        )
        task["status"] = stored_status
        return True

    @staticmethod
    def _event_key(status: dict) -> tuple:
        trace = (status or {}).get("decisionTrace", {})
        workers = tuple(
            item.get("pod")
            for item in (status or {}).get("selectedWorkers", [])
        )
        return (
            (status or {}).get("phase"),
            (status or {}).get("selectedNode"),
            workers,
            trace.get("metricsSource"),
            trace.get("metricsFreshness"),
            trace.get("dynamicBackendCount"),
        )

    def _emit_decision_event(
        self,
        task: dict,
        previous_status: dict,
        current_status: dict,
    ) -> bool:
        """只为选择、阶段或指标可用性变化创建 Kubernetes Event。"""
        if self._event_key(previous_status) == self._event_key(current_status):
            return False

        trace = current_status.get("decisionTrace", {})
        reason = trace.get("reason", "plan-details-changed")
        event_reason = {
            "initial-plan": "SchedulingDecisionInitialized",
            "observability-initialized": "SchedulingDecisionInitialized",
            "phase-changed": "SchedulingPhaseChanged",
            "selected-workers-changed": "WorkerSelectionChanged",
            "selected-node-changed": "NodeSelectionChanged",
            "metrics-state-changed": "MetricsStateChanged",
        }.get(reason, "SchedulingDecisionChanged")
        workers = [
            item.get("pod")
            for item in current_status.get("selectedWorkers", [])
        ]
        destination = ",".join(workers) or current_status.get(
            "selectedNode",
            "none",
        )
        message = (
            "reason={} phase={} destination={} metrics={}/{} queues={}"
        ).format(
            reason,
            current_status.get("phase", "unknown"),
            destination,
            trace.get("metricsSource", "unknown"),
            trace.get("metricsFreshness", "unknown"),
            trace.get("queueDepthByBackend", {}),
        )
        event = client.V1Event(
            metadata=client.V1ObjectMeta(
                generate_name="computetask-{}-".format(
                    task["metadata"]["name"]
                ),
                namespace=NAMESPACE,
            ),
            involved_object=client.V1ObjectReference(
                api_version="{}/{}".format(GROUP, VERSION),
                kind="ComputeTask",
                name=task["metadata"]["name"],
                namespace=NAMESPACE,
                uid=task["metadata"].get("uid"),
            ),
            reason=event_reason,
            message=message,
            source=client.V1EventSource(component="kube-orchestrator"),
            type=(
                "Warning"
                if current_status.get("phase") == "Unschedulable"
                else "Normal"
            ),
            first_timestamp=datetime.now(timezone.utc),
            last_timestamp=datetime.now(timezone.utc),
            count=1,
        )
        self.core.create_namespaced_event(NAMESPACE, event)
        return True

    def reconcile(
        self,
        task: dict,
        nodes: List[NodeSnapshot],
        worker_pods=None,
        runtime_metrics=None,
        metrics_state=None,
    ) -> bool:
        metadata = task["metadata"]
        spec = task.get("spec", {})

        if spec.get("strategy", "nodeScore") == "minExpectedCompletionTime":
            status = plan_multi_worker_task(
                task,
                worker_pods or [],
                runtime_metrics or {},
            )
            status["executionAllowed"] = self._execution_allowed(spec)
            routing_status = self._p4_routing_status(
                task,
                status.get("selectedWorkers", []),
            )
            if routing_status is not None:
                status["routing"] = routing_status
            status = self._with_lifecycle_status(task, status)
            previous_status = task.get("status", {})
            decision_trace = self._decision_trace(
                previous_status,
                status,
                worker_pods,
                runtime_metrics,
                metrics_state,
            )
            changed = self._patch_status_if_changed(
                task,
                status,
                decision_trace,
            )
            if changed:
                self._emit_decision_event(
                    task,
                    previous_status,
                    task["status"],
                )
                logging.info(
                    "reconciled multi-worker task %s: %s",
                    metadata["name"],
                    status["phase"],
                )
            return changed

        evidence = score_candidates(nodes, self._request(task), spec.get("preferredNodeLabels", {}))
        chosen = choose_node(evidence)
        status = {
            "observedGeneration": metadata.get("generation", 0),
            "executionAllowed": self._execution_allowed(spec),
            "phase": "Planned" if chosen else "Unschedulable",
            "jobName": "",
            "candidateEvidence": self._evidence_payload(evidence),
            "decision": "No eligible node was found" if not chosen else
                        "Selected by weighted headroom, fragmentation, health, and topology score.",
        }
        if chosen:
            status["selectedNode"] = chosen.node
            status["score"] = chosen.score
            job_name = self._job_name(task)
            if status["executionAllowed"]:
                self.ensure_job(task, chosen.node)
            job = self._read_job_if_exists(job_name)
            if job is not None:
                status["jobName"] = job_name
                status["phase"] = self._job_phase(job)

        status = self._with_lifecycle_status(task, status)
        previous_status = task.get("status", {})
        decision_trace = self._decision_trace(
            previous_status,
            status,
        )
        changed = self._patch_status_if_changed(
            task,
            status,
            decision_trace,
        )
        if changed:
            self._emit_decision_event(
                task,
                previous_status,
                task["status"],
            )
            logging.info("reconciled %s: %s", metadata["name"], status["phase"])
        return changed

    @staticmethod
    def _desired_p4_routes(selected_workers: list) -> list:
        routes = []
        for worker in selected_workers or []:
            if "flowId" not in worker or "backendId" not in worker:
                raise ValueError(
                    "selected worker is missing flowId/backendId"
                )
            routes.append({
                "flowId": int(worker["flowId"]),
                "backendId": int(worker["backendId"]),
            })
        return normalize_routes(routes)

    @staticmethod
    def _routing_write_allowed(task: dict) -> bool:
        routing = task.get("spec", {}).get("routing", {}) or {}
        return (
            P4_ROUTING_MODE == "apply"
            and routing.get("mode", "plan") == "apply"
            and bool(P4_ROUTE_OWNER)
            and task.get("metadata", {}).get("name") == P4_ROUTE_OWNER
        )

    def _p4_routing_status(self, task: dict, selected_workers: list):
        routing = task.get("spec", {}).get("routing")
        if not isinstance(routing, dict):
            return None

        result = {
            "phase": "Planned",
            "writeAllowed": self._routing_write_allowed(task),
            "routeOwner": P4_ROUTE_OWNER,
            "desiredRoutes": [],
            "observedRoutes": [],
            "inSync": False,
            "applied": False,
            # Status patches use JSON merge semantics. Keep the field present
            # so a successful reconcile clears an error from an earlier one.
            "error": "",
        }
        try:
            desired = self._desired_p4_routes(selected_workers)
        except (TypeError, ValueError) as error:
            result["phase"] = "Invalid"
            result["error"] = str(error)
            return result
        result["desiredRoutes"] = desired
        if not desired:
            result["phase"] = "Invalid"
            result["error"] = "routing requires at least one selected worker"
            return result

        p4 = getattr(self, "p4", None)
        if p4 is None:
            result["phase"] = "Unavailable"
            result["error"] = "P4 control client is not configured"
            return result
        remote = p4.get_routes()
        if not remote.get("ok"):
            result["phase"] = "Unavailable"
            result["error"] = remote.get("error", "P4 control query failed")
            return result

        result["controlOwner"] = str(remote.get("control_owner", ""))
        result["controlMode"] = str(remote.get("control_mode", ""))
        result["dryRun"] = bool(remote.get("dry_run", False))
        if (
            result["controlOwner"] != "server-114"
            or result["controlMode"] != "external"
        ):
            result["phase"] = "Unavailable"
            result["error"] = "114 is not the external single-writer control plane"
            return result

        try:
            observed = normalize_routes(remote.get("routes", []))
        except (TypeError, ValueError) as error:
            result["phase"] = "Unavailable"
            result["error"] = "invalid routes from 114: {}".format(error)
            return result
        result["observedRoutes"] = observed
        result["inSync"] = observed == desired

        if result["writeAllowed"] and not result["inSync"]:
            remote = p4.replace_routes(
                desired,
                "computetask/{}/generation/{}".format(
                    task["metadata"]["name"],
                    task["metadata"].get("generation", 0),
                ),
            )
            if not remote.get("ok"):
                result["phase"] = "Failed"
                result["error"] = remote.get("error", "P4 route replace failed")
                return result
            result["controlOwner"] = str(remote.get("control_owner", ""))
            result["dryRun"] = bool(remote.get("dry_run", False))
            result["observedRoutes"] = normalize_routes(
                remote.get("routes", [])
            )
            result["inSync"] = result["observedRoutes"] == desired

        if result["writeAllowed"] and result["inSync"]:
            result["applied"] = not result.get("dryRun", False)
            result["phase"] = (
                "DryRunValidated" if result.get("dryRun", False)
                else "Applied"
            )
        return result

    @staticmethod
    def _job_phase(job) -> str:
        job_status = getattr(job, "status", None)
        if int(getattr(job_status, "failed", 0) or 0) > 0:
            return "Failed"
        if int(getattr(job_status, "succeeded", 0) or 0) > 0:
            return "Succeeded"
        if int(getattr(job_status, "active", 0) or 0) > 0:
            return "Running"
        return "Submitted"

    @staticmethod
    def _execution_allowed(spec: dict) -> bool:
        return (
            EXECUTION_MODE == "apply"
            and spec.get("execution") == "apply"
        )

    def _read_job_if_exists(self, name: str):
        try:
            return self.batch.read_namespaced_job(name, NAMESPACE)
        except client.ApiException as error:
            if error.status == 404:
                return None
            raise

    @staticmethod
    def _job_name(task: dict) -> str:
        raw = "task-{}-g{}".format(
            task["metadata"]["name"],
            int(task["metadata"].get("generation", 1) or 1),
        )
        if len(raw) <= 63:
            return raw
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return "{}-{}".format(raw[:54].rstrip("-"), digest)

    @staticmethod
    def _job_owner_references(task: dict) -> list:
        metadata = task["metadata"]
        uid = metadata.get("uid")
        if not uid:
            return []
        return [
            client.V1OwnerReference(
                api_version="{}/{}".format(GROUP, VERSION),
                kind="ComputeTask",
                name=metadata["name"],
                uid=uid,
                controller=True,
                block_owner_deletion=False,
            )
        ]

    def ensure_job(self, task: dict, node_name: str) -> str:
        """Create one owned, affinity-pinned Job per task generation."""
        name = self._job_name(task)
        if self._read_job_if_exists(name) is not None:
            return name

        spec = task["spec"]
        resources = spec.get("resources", {})
        container = client.V1Container(
            name="workload", image=spec["image"], command=spec.get("command"),
            resources=client.V1ResourceRequirements(
                requests={"cpu": str(resources["cpu"]), "memory": str(resources["memory"]), **(
                    {"nvidia.com/gpu": str(resources["gpu"])} if resources.get("gpu") else {})},
                limits={"cpu": str(resources["cpu"]), "memory": str(resources["memory"]), **(
                    {"nvidia.com/gpu": str(resources["gpu"])} if resources.get("gpu") else {})},
            ),
        )
        affinity = client.V1Affinity(node_affinity=client.V1NodeAffinity(
            required_during_scheduling_ignored_during_execution=client.V1NodeSelector(
                node_selector_terms=[client.V1NodeSelectorTerm(match_expressions=[
                    client.V1NodeSelectorRequirement(key="kubernetes.io/hostname", operator="In", values=[node_name])
                ])]
            )
        ))
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=name,
                labels={
                    "app.kubernetes.io/managed-by": "kube-orchestrator",
                    "orchestrator.wzt.io/generation": str(
                        task["metadata"].get("generation", 1)
                    ),
                },
                owner_references=self._job_owner_references(task),
            ),
            spec=client.V1JobSpec(
                backoff_limit=0,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app.kubernetes.io/managed-by": "kube-orchestrator"}),
                    spec=client.V1PodSpec(restart_policy="Never", affinity=affinity, containers=[container]),
                ),
            ),
        )
        self.batch.create_namespaced_job(NAMESPACE, job)
        return name

    def _log_metrics_state(self, metrics_state: dict) -> None:
        summary = (
            metrics_state["source"],
            metrics_state["dynamic_backend_count"],
            metrics_state["error"],
        )
        if summary == self._last_metrics_state:
            return
        self._last_metrics_state = summary
        if metrics_state["source"] == "static-fallback":
            logging.warning(
                "dynamic metrics unavailable; using static baseline: %s",
                metrics_state["error"],
            )
        else:
            logging.info(
                "runtime metrics source=%s dynamic_backends=%d",
                metrics_state["source"],
                metrics_state["dynamic_backend_count"],
            )

    def run_once(self, now=None) -> bool:
        """执行一次快循环，并在到期时原子刷新 Kubernetes 资源缓存。"""
        current_time = time.monotonic() if now is None else float(now)
        resources_refreshed = current_time >= self._next_resource_refresh

        if resources_refreshed:
            nodes = self.snapshots()
            worker_pods = self.worker_pods()
            response = self.custom.list_namespaced_custom_object(
                GROUP,
                VERSION,
                NAMESPACE,
                PLURAL,
            )
            tasks = response.get("items", [])
            self._cached_nodes = nodes
            self._cached_worker_pods = worker_pods
            self._cached_tasks = tasks
            self._next_resource_refresh = (
                current_time + RESOURCE_REFRESH_SECONDS
            )
            logging.info(
                "resource cache refreshed nodes=%d workers=%d tasks=%d",
                len(nodes),
                len(worker_pods),
                len(tasks),
            )

        dynamic_tasks = [
            task for task in self._cached_tasks
            if task.get("spec", {}).get("strategy", "nodeScore")
            == "minExpectedCompletionTime"
        ]
        runtime_metrics = {}
        metrics_state = {}
        if dynamic_tasks:
            runtime_metrics, metrics_state = load_combined_runtime_metrics(
                RUNTIME_METRICS_PATH,
                RUNTIME_METRICS_URL,
                RUNTIME_METRICS_TOKEN,
                WORKER_SLOTS,
                RUNTIME_METRICS_TIMEOUT_SECONDS,
            )
            self._log_metrics_state(metrics_state)

        tasks_to_reconcile = (
            self._cached_tasks if resources_refreshed else dynamic_tasks
        )
        for task in tasks_to_reconcile:
            self.reconcile(
                task,
                self._cached_nodes,
                self._cached_worker_pods,
                runtime_metrics,
                metrics_state,
            )
        return resources_refreshed

    def run(self) -> None:
        while True:
            loop_started = time.monotonic()
            try:
                self.run_once(loop_started)
            except Exception:
                logging.exception("reconcile loop failed")
            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.1, METRICS_POLL_SECONDS - elapsed))


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    ComputeTaskController().run()
