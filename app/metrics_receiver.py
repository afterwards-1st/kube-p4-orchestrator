"""接收 Worker UDP 上报并通过只读 HTTP API 暴露最新快照。"""

import json
import os
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable, Dict, Iterable, Optional
from urllib.parse import urlparse


REPORT_FIELDS = (
    "completed_tasks",
    "total_batches",
    "last_flush_size",
    "last_process_time_ms",
    "last_avg_wait_ms",
    "last_batch_efficiency",
)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _non_negative_number(value, *, integer=False):
    number = int(value) if integer else float(value)
    return max(0, number) if integer else max(0.0, number)


class WorkerMetricStore:
    """线程安全地保存白名单 Worker 的最后一份合法上报。"""

    def __init__(
        self,
        allowed_backend_ids: Iterable[str],
        stale_after_seconds: float = 3.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.allowed = frozenset(str(value) for value in allowed_backend_ids)
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self.clock = clock
        self._states: Dict[str, dict] = {}
        self._accepted_reports = 0
        self._rejected_reports = 0
        self._udp_ready = False
        self._lock = threading.Lock()

    def set_udp_ready(self, ready: bool) -> None:
        with self._lock:
            self._udp_ready = bool(ready)

    def health(self) -> dict:
        with self._lock:
            udp_ready = self._udp_ready
        return {
            "status": "ok" if udp_ready else "starting",
            "role": "metrics-receiver",
            "udp_ready": udp_ready,
        }

    def ingest(self, raw, received_at: Optional[float] = None) -> bool:
        """校验并保存一份上报；坏报文返回 False，不影响已有快照。"""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(message, dict):
                raise ValueError("report must be an object")
            backend_id = str(message["backend_id"]).strip()
            if backend_id not in self.allowed:
                raise ValueError("backend is not allowed")
            state = {
                "backend_id": int(backend_id),
                "queue_depth": _non_negative_number(
                    message.get("queue_depth", 0), integer=True
                ),
                "reason": str(message.get("reason", "unknown")),
                "report_ts": float(message.get("ts", 0.0) or 0.0),
                "received_at": float(
                    self.clock() if received_at is None else received_at
                ),
                "completed_tasks": _non_negative_number(
                    message.get("completed_tasks", 0), integer=True
                ),
                "total_batches": _non_negative_number(
                    message.get("total_batches", 0), integer=True
                ),
                "last_flush_size": _non_negative_number(
                    message.get("last_flush_size", 0), integer=True
                ),
                "last_process_time_ms": _non_negative_number(
                    message.get("last_process_time_ms", 0.0)
                ),
                "last_avg_wait_ms": _non_negative_number(
                    message.get("last_avg_wait_ms", 0.0)
                ),
                "last_batch_efficiency": _non_negative_number(
                    message.get("last_batch_efficiency", 0.0)
                ),
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            with self._lock:
                self._rejected_reports += 1
            return False

        with self._lock:
            self._states[backend_id] = state
            self._accepted_reports += 1
        return True

    def snapshot(self, now: Optional[float] = None) -> dict:
        """生成与 runtime_metrics.metrics_from_worker_reports 兼容的响应。"""
        current_time = float(self.clock() if now is None else now)
        with self._lock:
            states = {key: dict(value) for key, value in self._states.items()}
            accepted = self._accepted_reports
            rejected = self._rejected_reports

        backends = []
        for backend_id in sorted(self.allowed, key=lambda value: int(value)):
            state = states.get(backend_id)
            if state is None:
                backends.append({
                    "backend_id": int(backend_id),
                    "queue_depth": 0,
                    "reason": "missing",
                    "age_seconds": None,
                    "fresh": False,
                })
                continue
            age_seconds = max(0.0, current_time - state["received_at"])
            item = dict(state)
            item["age_seconds"] = round(age_seconds, 3)
            item["fresh"] = age_seconds <= self.stale_after_seconds
            backends.append(item)

        return {
            "ok": True,
            "source": "kubernetes-worker-metrics-receiver",
            "stale_after_seconds": self.stale_after_seconds,
            "accepted_reports": accepted,
            "rejected_reports": rejected,
            "backends": backends,
        }


def make_http_handler(store: WorkerMetricStore, token: str = ""):
    """创建只读 HTTP Handler；有 Token 时保护指标接口。"""

    class MetricsHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            print("[METRICS-API] " + format % args, flush=True)

        def send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            return not token or self.headers.get("X-Metrics-Token", "") == token

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                health = store.health()
                self.send_json(health, 200 if health["udp_ready"] else 503)
                return
            if path == "/v1/backends":
                if not self.authorized():
                    self.send_json({"ok": False, "error": "unauthorized"}, 403)
                    return
                self.send_json(store.snapshot())
                return
            self.send_json({"ok": False, "error": "not found"}, 404)

    return MetricsHandler


def serve_single_threaded(
    store: WorkerMetricStore,
    udp_host: str,
    udp_port: int,
    http_host: str,
    http_port: int,
    token: str,
) -> None:
    """在单线程事件循环中同时处理 UDP 上报和 HTTP 查询。"""
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind((udp_host, udp_port))
    udp_sock.setblocking(False)
    httpd = HTTPServer(
        (http_host, http_port),
        make_http_handler(store, token),
    )
    store.set_udp_ready(True)
    print(
        "[METRICS-UDP] listening on {}:{}".format(udp_host, udp_port),
        flush=True,
    )
    print(
        "[METRICS-HTTP] listening on {}:{}".format(http_host, http_port),
        flush=True,
    )
    try:
        while True:
            readable, _, _ = select.select(
                [udp_sock, httpd.socket], [], [], 0.5
            )
            for ready_socket in readable:
                if ready_socket is udp_sock:
                    while True:
                        try:
                            data, _ = udp_sock.recvfrom(4096)
                        except BlockingIOError:
                            break
                        store.ingest(data)
                else:
                    httpd._handle_request_noblock()
    except KeyboardInterrupt:
        pass
    finally:
        store.set_udp_ready(False)
        httpd.server_close()
        udp_sock.close()


def main() -> None:
    slots = [
        value.strip()
        for value in os.environ.get(
            "WORKER_SLOTS", "216,217,218,226,227,228"
        ).split(",")
        if value.strip()
    ]
    stale_after = float(os.environ.get("METRICS_STALE_AFTER_SECONDS", "3"))
    udp_host = os.environ.get("METRICS_UDP_HOST", "0.0.0.0")
    udp_port = int(os.environ.get("METRICS_UDP_PORT", "9999"))
    http_host = os.environ.get("METRICS_HTTP_HOST", "0.0.0.0")
    http_port = int(os.environ.get("METRICS_HTTP_PORT", "8080"))
    token = os.environ.get("METRICS_TOKEN", "")

    store = WorkerMetricStore(slots, stale_after_seconds=stale_after)
    serve_single_threaded(
        store,
        udp_host,
        udp_port,
        http_host,
        http_port,
        token,
    )


if __name__ == "__main__":
    main()
