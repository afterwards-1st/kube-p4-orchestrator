#!/usr/bin/env python3
"""向一个 Worker 发送可控的批处理任务突发，用于队列闭环实验。"""

import argparse
import socket
import struct
import time


TASK_MAGIC = 0xAB


def build_packet(flow_id: int, content: str, tag: int = 0) -> bytes:
    """构造 Worker 能识别的 batchable UDP 任务包。"""
    if not 0 <= flow_id <= 255:
        raise ValueError("flow_id 必须位于 0..255")
    if not 0 <= tag <= 255:
        raise ValueError("tag 必须位于 0..255")
    return struct.pack("BBBB", TASK_MAGIC, tag, 0x01, flow_id) + content.encode(
        "utf-8"
    )


def send_bursts(
    target: str,
    port: int,
    flow_id: int,
    count: int,
    bursts: int,
    pause_seconds: float,
) -> None:
    """按指定次数发送任务突发；每个突发不超过 Worker 的批大小。"""
    if not 1 <= count <= 15:
        raise ValueError("每次突发数量必须位于 1..15，避免立即触发满批")
    if not 1 <= bursts <= 20:
        raise ValueError("突发轮数必须位于 1..20")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for burst_index in range(bursts):
            for task_index in range(count):
                content = "burst-{}-task-{}".format(
                    burst_index + 1,
                    task_index + 1,
                )
                sock.sendto(build_packet(flow_id, content), (target, port))
            print(
                "已发送第 {}/{} 轮，每轮 {} 个任务，目标 {}:{}".format(
                    burst_index + 1,
                    bursts,
                    count,
                    target,
                    port,
                ),
                flush=True,
            )
            if burst_index + 1 < bursts:
                time.sleep(max(0.0, pause_seconds))
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="发送可批处理 UDP 任务突发")
    parser.add_argument("--target", required=True, help="Worker Pod IP")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--flow-id", type=int, default=1)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--bursts", type=int, default=4)
    parser.add_argument("--pause-seconds", type=float, default=6.0)
    args = parser.parse_args()
    send_bursts(
        args.target,
        args.port,
        args.flow_id,
        args.count,
        args.bursts,
        args.pause_seconds,
    )


if __name__ == "__main__":
    main()
