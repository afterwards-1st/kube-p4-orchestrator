#!/usr/bin/env python3
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
try:
    from dataclasses import dataclass, field
except ImportError:  # Python 3.6 on older lab hosts
    from compat_dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Dict, List, Optional
from urllib.parse import urlparse

CLI = "simple_switch_CLI"
THRIFT_PORT = "9090"

# 统一任务流：send_task_plain.py 默认就是 tag=1,batchable=1,flow_id=1
TASK_TAG = 1
BATCHABLE = 1
FLOW_ID = 1

# 逻辑后端编号 -> P4 backend_id
BACKEND_TO_P4 = {
    116: 1,
    117: 2,
    118: 3,
    216: 4,
    217: 5,
    218: 6,
    226: 7,
    227: 8,
    228: 9,
}

# P4 backend_id -> 逻辑后端编号
P4_TO_BACKEND = {v: k for k, v in BACKEND_TO_P4.items()}

BACKEND_ORDER = [116, 117, 118, 216, 217, 218, 226, 227, 228]


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Python 3.6兼容的线程HTTP服务器。"""

    daemon_threads = True


@dataclass
class BackendState:
    queue_depth: int = 0
    reason: str = "init"
    ts: float = 0.0
    last_seen: float = 0.0


@dataclass
class SchedulerState:
    active_backend: int = 116
    # 运行期路由的唯一权威副本：flow_id -> 逻辑 backend_id。
    routes: Dict[int, int] = field(default_factory=dict)
    backend_state: Dict[int, BackendState] = field(
        default_factory=lambda: {b: BackendState() for b in BACKEND_ORDER}
    )
    lock: threading.Lock = field(default_factory=threading.Lock)


class BatchAwareController:
    def __init__(
        self,
        listen_ip: str,
        listen_port: int,
        high: int,
        batch_size: int,
        decision_interval: float,
        thrift_port: str,
        api_ip: str,
        api_port: int,
        control_mode: str,
        control_token: str = "",
        dry_run: bool = False,
    ) -> None:
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.high = high
        self.batch_size = batch_size
        self.decision_interval = decision_interval
        self.thrift_port = thrift_port
        self.api_ip = api_ip
        self.api_port = api_port
        self.control_mode = control_mode
        self.control_token = control_token
        self.dry_run = dry_run
        self.state = SchedulerState()
        self.stop_event = threading.Event()
        self.cli_lock = threading.Lock()
        self.last_applied_p4_backend: Optional[int] = None
        self.httpd: Optional[ThreadingHTTPServer] = None

    def run_cli(self, commands: str) -> str:
        if self.dry_run:
            print("[DRY-RUN CLI]\n" + commands.strip(), flush=True)
            return ""
        proc = subprocess.run(
            [CLI, "--thrift-port", self.thrift_port],
            input=commands,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        if proc.returncode != 0:
            raise RuntimeError(f"simple_switch_CLI failed\nSTDOUT:\n{out}\nSTDERR:\n{err}")
        return out

    def replace_routes(self, routes: List[dict], reason: str = "scheduler-api") -> dict:
        """验证并提交整组运行期路由；这是唯一写 udp_tag_table 的入口。"""
        if self.control_mode != "external":
            return {
                "ok": False,
                "error": "route API is disabled while control_mode is batch-aware",
            }
        if not isinstance(routes, list):
            return {"ok": False, "error": "routes must be a list"}

        normalized = []
        seen_flows = set()
        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                return {"ok": False, "error": f"routes[{index}] must be an object"}
            try:
                flow_id = int(route["flow_id"])
                backend_id = int(route["backend_id"])
            except (KeyError, TypeError, ValueError):
                return {
                    "ok": False,
                    "error": f"routes[{index}] requires integer flow_id and backend_id",
                }
            if not 1 <= flow_id <= 255:
                return {"ok": False, "error": f"invalid flow_id: {flow_id}"}
            if flow_id in seen_flows:
                return {"ok": False, "error": f"duplicate flow_id: {flow_id}"}
            if backend_id not in BACKEND_TO_P4:
                return {"ok": False, "error": f"unknown backend_id: {backend_id}"}
            seen_flows.add(flow_id)
            normalized.append((flow_id, backend_id))

        # CLI 没有事务，因此先验证全部参数，再在一个会话中 clear + add。
        commands = ["table_clear udp_tag_table"]
        for flow_id, backend_id in normalized:
            commands.append(
                "table_add udp_tag_table set_backend "
                f"{TASK_TAG} {BATCHABLE} {flow_id} => {BACKEND_TO_P4[backend_id]}"
            )
        try:
            with self.cli_lock:
                self.run_cli("\n".join(commands) + "\n")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        with self.state.lock:
            self.state.routes = {flow_id: backend_id for flow_id, backend_id in normalized}
            if len(normalized) == 1 and normalized[0][0] == FLOW_ID:
                self.state.active_backend = normalized[0][1]

        flow_mapping = {backend_id: flow_id for flow_id, backend_id in normalized}
        print(f"[P4-REPLACE] routes={self.state.routes} reason={reason}", flush=True)
        return {
            "ok": True,
            "applied": not self.dry_run,
            "dry_run": self.dry_run,
            "entries": len(normalized),
            "routes": [
                {
                    "flow_id": flow_id,
                    "backend_id": backend_id,
                    "p4_backend_id": BACKEND_TO_P4[backend_id],
                }
                for flow_id, backend_id in normalized
            ],
            "flow_mapping": flow_mapping,
            "control_owner": "server-114",
        }

    def route_status(self) -> dict:
        with self.state.lock:
            routes = dict(self.state.routes)
        return {
            "ok": True,
            "control_owner": "server-114",
            "control_mode": self.control_mode,
            "dry_run": self.dry_run,
            "entries": len(routes),
            "routes": [
                {
                    "flow_id": flow_id,
                    "backend_id": backend_id,
                    "p4_backend_id": BACKEND_TO_P4[backend_id],
                }
                for flow_id, backend_id in sorted(routes.items())
            ],
        }

    def make_http_handler(self):
        controller = self

        class ControlHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                print(f"[P4-API] {args[0]}", flush=True)

            def send_json(self, data, status=200):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                if not controller.control_token:
                    return True
                return self.headers.get("X-P4-Control-Token", "") == controller.control_token

            def read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("request body must be between 1 byte and 1 MiB")
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/health":
                    self.send_json(
                        {
                            "status": "ok",
                            "control_owner": "server-114",
                            "control_mode": controller.control_mode,
                        }
                    )
                    return
                if path == "/v1/routes":
                    if not self.authorized():
                        self.send_json({"ok": False, "error": "unauthorized"}, 403)
                        return
                    self.send_json(controller.route_status())
                    return
                self.send_json({"ok": False, "error": "not found"}, 404)

            def do_POST(self):
                path = urlparse(self.path).path
                if path not in ("/v1/routes/replace", "/v1/routes/clear"):
                    self.send_json({"ok": False, "error": "not found"}, 404)
                    return
                if not self.authorized():
                    self.send_json({"ok": False, "error": "unauthorized"}, 403)
                    return
                try:
                    if path == "/v1/routes/clear":
                        routes = []
                        reason = "scheduler-release"
                    else:
                        body = self.read_json()
                        routes = body.get("routes")
                        reason = str(body.get("reason", "scheduler-api"))
                    result = controller.replace_routes(routes, reason=reason)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                    return
                self.send_json(result, 200 if result.get("ok") else 409)

        return ControlHandler

    def api_listener(self) -> None:
        self.httpd = ThreadingHTTPServer(
            (self.api_ip, self.api_port), self.make_http_handler()
        )
        print(
            f"[P4-API] http://{self.api_ip}:{self.api_port} "
            f"owner=server-114 mode={self.control_mode}",
            flush=True,
        )
        self.httpd.serve_forever(poll_interval=0.5)

    def set_active_backend(self, backend: int, reason: str) -> None:
        p4_backend = BACKEND_TO_P4[backend]
        if self.last_applied_p4_backend == p4_backend:
            return

        commands = (
            "table_clear udp_tag_table\n"
            f"table_add udp_tag_table set_backend {TASK_TAG} {BATCHABLE} {FLOW_ID} => {p4_backend}\n"
        )
        with self.cli_lock:
            self.run_cli(commands)
        self.last_applied_p4_backend = p4_backend
        with self.state.lock:
            self.state.routes = {FLOW_ID: backend}
        print(
            f"🧭 [P4-UPDATE] unified flow(tag={TASK_TAG},batch={BATCHABLE},flow={FLOW_ID}) "
            f"-> backend={backend}(p4_id={p4_backend}) | reason={reason}",
            flush=True,
        )

    def next_backend(self, current: int) -> int:
        idx = BACKEND_ORDER.index(current)
        return BACKEND_ORDER[(idx + 1) % len(BACKEND_ORDER)]

    def choose_backend(self):
        """
        Batch-aware watermark policy:
        1. 默认保持当前 active_backend，让同类任务聚合成 batch。
        2. 当前后端达到 batch_size 或 high 后，切到下一个后端。
        3. 如果下一个后端也很高，选择 queue_depth 最低的后端。
        """
        with self.state.lock:
            active = self.state.active_backend
            active_q = self.state.backend_state[active].queue_depth

            if active_q >= self.batch_size:
                candidate = self.next_backend(active)
                reason = f"active backend {active} reached batch_size: q={active_q}"
            elif active_q > self.high:
                candidate = self.next_backend(active)
                reason = f"active backend {active} exceeded high watermark: q={active_q}"
            else:
                return active, f"keep backend {active} for batch aggregation: q={active_q}"

            # 如果候选后端也高，选择当前队列最短的后端
            candidate_q = self.state.backend_state[candidate].queue_depth
            if candidate_q > self.high:
                candidate = min(
                    BACKEND_ORDER,
                    key=lambda b: self.state.backend_state[b].queue_depth,
                )
                reason += f"; next backend high, choose min-queue backend {candidate}"

            self.state.active_backend = candidate
            return candidate, reason

    def report_listener(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.listen_ip, self.listen_port))
        sock.settimeout(0.5)
        print(f"📡 [REPORT-LISTEN] {self.listen_ip}:{self.listen_port}", flush=True)

        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                msg = json.loads(data.decode("utf-8"))
                backend_id = int(msg["backend_id"])
                queue_depth = int(msg.get("queue_depth", 0))
                reason = str(msg.get("reason", "unknown"))
                ts = float(msg.get("ts", time.time()))
            except Exception as e:
                print(f"⚠️ [BAD-REPORT] from={addr} err={e} raw={data!r}", flush=True)
                continue

            if backend_id not in BACKEND_TO_P4:
                print(f"⚠️ [UNKNOWN-BACKEND] {backend_id} from={addr}", flush=True)
                continue

            with self.state.lock:
                self.state.backend_state[backend_id] = BackendState(
                    queue_depth=queue_depth,
                    reason=reason,
                    ts=ts,
                    last_seen=time.time(),
                )

            # print(
            #     f"📥 [REPORT] backend={backend_id} queue={queue_depth} reason={reason} from={addr[0]}",
            #     flush=True,
            # )

        sock.close()

    def decision_loop(self) -> None:
        # 初始先让统一任务流去 116
        self.set_active_backend(self.state.active_backend, "initial")

        while not self.stop_event.is_set():
            time.sleep(self.decision_interval)

            backend, reason = self.choose_backend()
            self.set_active_backend(backend, reason)

            with self.state.lock:
                qstr = ", ".join(
                    f"{b}:{self.state.backend_state[b].queue_depth}"
                    for b in BACKEND_ORDER
                )
                active = self.state.active_backend
            if "keep backend" not in reason:
                print(f"🧠 [DECISION] active={active} queues=[{qstr}] reason={reason}", flush=True)

    def run(self) -> None:
        listener = threading.Thread(target=self.report_listener, daemon=True)
        listener.start()
        api = threading.Thread(target=self.api_listener, daemon=True)
        api.start()

        try:
            if self.control_mode == "batch-aware":
                self.decision_loop()
            else:
                while not self.stop_event.wait(0.5):
                    pass
        except KeyboardInterrupt:
            print("\n停止控制器。", flush=True)
        finally:
            self.stop_event.set()
            if self.httpd:
                self.httpd.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-aware controller for unified flow scheduling")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=9999)
    parser.add_argument("--high", type=int, default=12, help="high watermark")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--decision-interval", type=float, default=0.2)
    parser.add_argument("--thrift-port", default=THRIFT_PORT)
    parser.add_argument("--api-ip", default="0.0.0.0")
    parser.add_argument("--api-port", type=int, default=8100)
    parser.add_argument(
        "--control-mode",
        choices=("external", "batch-aware"),
        default="external",
        help="external: 仅接受102的调度结果；batch-aware: 114自主切换",
    )
    parser.add_argument(
        "--control-token",
        default=os.environ.get("P4_CONTROL_TOKEN", ""),
        help="可选的 X-P4-Control-Token 共享密钥",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    controller = BatchAwareController(
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        high=args.high,
        batch_size=args.batch_size,
        decision_interval=args.decision_interval,
        thrift_port=args.thrift_port,
        api_ip=args.api_ip,
        api_port=args.api_port,
        control_mode=args.control_mode,
        control_token=args.control_token,
        dry_run=args.dry_run,
    )
    controller.run()


if __name__ == "__main__":
    main()
