"""ComputeTask Finalizer helpers for safely restoring shared P4 routes."""

from app.p4_control import normalize_routes


FINALIZER = "orchestrator.wzt.io/p4-route-cleanup"


def cleanup_p4_routing(task: dict, p4, configured_owner: str) -> tuple:
    """Return ``(completed, phase, reason)`` for deletion reconciliation.

    A shared route table is restored only when it still equals the last value
    applied by this task. Drift means another actor changed the table, so the
    safe action is to leave that value untouched and release the Finalizer.
    """
    routing = (task.get("status", {}) or {}).get("routing", {}) or {}
    name = task.get("metadata", {}).get("name", "")
    if not routing.get("applied"):
        return True, "NotRequired", "No live P4 route was applied"
    if routing.get("routeOwner") != name or configured_owner != name:
        return True, "Skipped", "Task is no longer the configured P4 route owner"
    if "previousRoutes" not in routing:
        return True, "Skipped", "No pre-apply route snapshot is available"
    if p4 is None:
        return False, "WaitingForP4", "P4 control client is not configured"

    remote = p4.get_routes(force=True)
    if not remote.get("ok"):
        return False, "WaitingForP4", remote.get(
            "error", "P4 control query failed"
        )
    if (
        str(remote.get("control_owner", "")) != "server-114"
        or str(remote.get("control_mode", "")) != "external"
    ):
        return False, "WaitingForP4", (
            "114 is not the external single-writer control plane"
        )
    try:
        observed = normalize_routes(remote.get("routes", []))
        expected = normalize_routes(routing.get("desiredRoutes", []))
        previous = normalize_routes(routing.get("previousRoutes", []))
    except (TypeError, ValueError) as error:
        return True, "Skipped", "Invalid recorded P4 route state: {}".format(
            error
        )
    if observed != expected:
        return True, "Skipped", (
            "P4 route table changed after this task applied it"
        )

    result = p4.replace_routes(
        previous,
        "computetask/{}/cleanup".format(name),
    )
    if not result.get("ok"):
        return False, "WaitingForP4", result.get(
            "error", "P4 route restore failed"
        )
    verified = p4.get_routes(force=True)
    if not verified.get("ok"):
        return False, "WaitingForP4", verified.get(
            "error", "P4 route verification failed"
        )
    try:
        restored = normalize_routes(verified.get("routes", []))
    except (TypeError, ValueError) as error:
        return False, "WaitingForP4", (
            "Invalid P4 verification result: {}".format(error)
        )
    if restored != previous:
        return False, "WaitingForP4", "P4 route restore verification failed"
    return True, "Restored", (
        "Restored the route snapshot recorded before apply"
    )