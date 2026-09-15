#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import select
import socket
import struct
import time
from typing import Dict, List, Optional, Tuple

TASK_MAGIC = 0xAB
HEALTHY_PATHS = {"/health", "/ready"}


def now_ts() -> float:
    return time.time()


def health_http_response(path: str) -> bytes:
    healthy = path in HEALTHY_PATHS
    status = "200 OK" if healthy else "404 Not Found"
    body = json.dumps(
        {"status": "ok" if healthy else "not_found", "path": path},
        separators=(",", ":"),
    ).encode("utf-8")
    headers = (
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + body


class BatchServer:
    def __init__(
        self,
        backend_id: int,
        listen_ip: str,
        listen_port: int,
        health_ip: str,
        health_port: int,
        batch_size: int,
        batch_timeout: float,
        report_ip: Optional[str],
        report_port: int,
        report_interval: float,
        base_time: float,
        per_task_time: float,
        immediate_time: float,
    ) -> None:
        self.backend_id = backend_id
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.health_ip = health_ip
        self.health_port = health_port
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.report_addr: Optional[Tuple[str, int]] = (report_ip, report_port) if report_ip else None
        self.report_interval = report_interval

        # 模拟处理耗时模型：
        # batch_process_time = base_time + per_task_time * batch_len
        # immediate_time 用于模拟非 batchable 任务单独处理耗时
        self.base_time = base_time
        self.per_task_time = per_task_time
        self.immediate_time = immediate_time

        self.last_report = 0.0
        self.flow_queues: Dict[int, Dict[str, object]] = {}

        # 统计信息，后续可用于控制器 / 强化学习 reward
        self.completed_tasks = 0
        self.total_batches = 0
        self.last_flush_size = 0
        self.last_flush_reason = "none"
        self.last_process_time_ms = 0.0
        self.last_avg_wait_ms = 0.0
        self.last_batch_efficiency = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.listen_ip, self.listen_port))

        self.health_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.health_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.health_sock.bind((self.health_ip, self.health_port))
        self.health_sock.listen(16)
        self.health_sock.setblocking(False)

        self.report_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def handle_health_connection(self) -> None:
        conn, _ = self.health_sock.accept()
        try:
            conn.settimeout(0.2)
            request = conn.recv(1024)
            first_line = request.split(b"\r\n", 1)[0].decode("ascii", errors="ignore")
            parts = first_line.split()
            path = parts[1] if len(parts) >= 2 else ""
            conn.sendall(health_http_response(path))
        except (OSError, socket.timeout):
            # 单个异常探针请求不应影响任务处理主循环。
            pass
        finally:
            conn.close()

    def total_queue_depth(self) -> int:
        total = 0
        for state in self.flow_queues.values():
            total += len(state.get("data", []))
        return total

    def report(self, reason: str, force: bool = False) -> None:
        if not self.report_addr:
            return
        t = now_ts()
        if not force and (t - self.last_report) < self.report_interval:
            return

        msg = {
            "backend_id": self.backend_id,
            "queue_depth": self.total_queue_depth(),
            "reason": reason,
            "ts": t,
            "completed_tasks": self.completed_tasks,
            "total_batches": self.total_batches,
            "last_flush_size": self.last_flush_size,
            "last_flush_reason": self.last_flush_reason,
            "last_process_time_ms": round(self.last_process_time_ms, 3),
            "last_avg_wait_ms": round(self.last_avg_wait_ms, 3),
            "last_batch_efficiency": round(self.last_batch_efficiency, 4),
        }
        self.report_sock.sendto(json.dumps(msg).encode("utf-8"), self.report_addr)
        self.last_report = t

    def simulate_batch_compute(self, batch_len: int) -> float:
        process_time = self.base_time + self.per_task_time * batch_len
        time.sleep(process_time)
        return process_time

    def flush_flow(self, fid: int, reason: str) -> None:
        state = self.flow_queues.get(fid)
        if not state:
            return

        q: List[dict] = state.get("data", [])  # type: ignore[assignment]
        if not q:
            self.flow_queues[fid] = {"data": [], "start_time": None}
            return

        flush_start = now_ts()
        batch_len = len(q)
        waits = [(flush_start - float(item["arrival_ts"])) * 1000.0 for item in q]
        avg_wait_ms = sum(waits) / len(waits) if waits else 0.0
        batch_efficiency = batch_len / float(self.batch_size)

        print(
            f"🔥 [BATCH-FLUSH-START] 后端:{self.backend_id} | 流ID:{fid} | "
            f"原因:{reason} | 批大小:{batch_len} | 平均等待:{avg_wait_ms:.2f}ms | "
            f"batch效率:{batch_efficiency:.2f}",
            flush=True,
        )

        # 先清空队列并上报，表示这些任务已经进入计算阶段，不再等待排队。
        batch_contents = [item["content"] for item in q]
        self.flow_queues[fid] = {"data": [], "start_time": None}
        self.last_flush_size = batch_len
        self.last_flush_reason = reason
        self.last_avg_wait_ms = avg_wait_ms
        self.last_batch_efficiency = batch_efficiency
        self.report("flush_start", force=True)

        process_time = self.simulate_batch_compute(batch_len)

        self.completed_tasks += batch_len
        self.total_batches += 1
        self.last_process_time_ms = process_time * 1000.0

        print(
            f"✅ [BATCH-DONE] 后端:{self.backend_id} | 流ID:{fid} | "
            f"批大小:{batch_len} | 模拟计算耗时:{process_time*1000:.2f}ms | "
            f"单位任务耗时:{process_time*1000/batch_len:.2f}ms | 内容:{batch_contents}",
            flush=True,
        )
        self.report("flush_done", force=True)

    def loop(self) -> None:
        print(
            f"✅ 模拟计算后端已启动 | backend={self.backend_id} | listen={self.listen_ip}:{self.listen_port} | "
            f"batch_size={self.batch_size} | timeout={self.batch_timeout}s | "
            f"process_time=base({self.base_time}s)+per_task({self.per_task_time}s)*N | "
            f"report={self.report_addr} | health={self.health_ip}:{self.health_port}",
            flush=True,
        )
        self.report("start", force=True)

        while True:
            t = now_ts()
            for fid, state in list(self.flow_queues.items()):
                start_time = state.get("start_time")
                if start_time and (t - float(start_time) > self.batch_timeout):
                    self.flush_flow(fid, "timeout")

            self.report("heartbeat")

            readable, _, _ = select.select([self.sock, self.health_sock], [], [], 0.05)
            if self.health_sock in readable:
                self.handle_health_connection()

            if self.sock not in readable:
                continue

            data, addr = self.sock.recvfrom(2048)
            if len(data) < 4 or data[0] != TASK_MAGIC:
                print(f"⚠️  丢弃非法包: len={len(data)} from={addr}", flush=True)
                continue

            _, tag, flags, flow_id = struct.unpack("BBBB", data[:4])
            is_batch = flags & 0x01
            content = data[4:].decode(errors="ignore")

            if not is_batch:
                print(f"⚡ [IMMEDIATE-START] 后端:{self.backend_id} | 流ID:{flow_id} | 内容:{content}", flush=True)
                time.sleep(self.immediate_time)
                self.completed_tasks += 1
                self.last_flush_size = 1
                self.last_flush_reason = "immediate"
                self.last_process_time_ms = self.immediate_time * 1000.0
                self.last_avg_wait_ms = 0.0
                self.last_batch_efficiency = 1.0 / float(self.batch_size)
                print(
                    f"✅ [IMMEDIATE-DONE] 后端:{self.backend_id} | 流ID:{flow_id} | "
                    f"模拟耗时:{self.immediate_time*1000:.2f}ms",
                    flush=True,
                )
                self.report("immediate_done", force=True)
                continue

            if flow_id not in self.flow_queues:
                self.flow_queues[flow_id] = {"data": [], "start_time": None}

            q: List[dict] = self.flow_queues[flow_id]["data"]  # type: ignore[index]
            if not q:
                self.flow_queues[flow_id]["start_time"] = now_ts()

            q.append({"content": content, "arrival_ts": now_ts()})
            print(
                f"📦 [ENQUEUE] 后端:{self.backend_id} | 流ID:{flow_id} | "
                f"当前流队列:{len(q)} | 后端总等待:{self.total_queue_depth()}",
                flush=True,
            )
            self.report("enqueue", force=True)

            if len(q) >= self.batch_size:
                self.flush_flow(flow_id, "size")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch server with simulated compute time and queue-depth reporting")
    parser.add_argument("--backend-id", type=int, required=True, help="Logical backend id, such as 116/117/118")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=9000)
    parser.add_argument("--health-ip", default="0.0.0.0")
    parser.add_argument("--health-port", type=int, default=8081)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--batch-timeout", type=float, default=5.0)
    parser.add_argument("--report-ip", default=None, help="Controller IP to receive queue reports, e.g. 192.168.199.208")
    parser.add_argument("--report-port", type=int, default=9999)
    parser.add_argument("--report-interval", type=float, default=0.2)

    parser.add_argument("--base-time", type=float, default=0.10, help="Fixed batch startup cost in seconds")
    parser.add_argument("--per-task-time", type=float, default=0.01, help="Additional processing time per task in seconds")
    parser.add_argument("--immediate-time", type=float, default=0.11, help="Processing time for one immediate task")

    args = parser.parse_args()

    server = BatchServer(
        backend_id=args.backend_id,
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        health_ip=args.health_ip,
        health_port=args.health_port,
        batch_size=args.batch_size,
        batch_timeout=args.batch_timeout,
        report_ip=args.report_ip,
        report_port=args.report_port,
        report_interval=args.report_interval,
        base_time=args.base_time,
        per_task_time=args.per_task_time,
        immediate_time=args.immediate_time,
    )
    server.loop()


if __name__ == "__main__":
    main()
