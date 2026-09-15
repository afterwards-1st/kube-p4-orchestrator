#!/usr/bin/env python3
"""快照和恢复 BMv2 udp_tag_table，供 systemd 交接脚本调用。"""

import argparse
import os
import re
import subprocess
import tempfile


FLOW_RE = re.compile(r"task_hdr[.]flow_id[ ]*:[ ]*EXACT[ ]+([0-9a-fA-F]+)")
BACKEND_RE = re.compile(
    r"Action entry:[ ]+(?:MyIngress[.])?set_backend[ ]*-[ ]*([0-9a-fA-F]+)"
)


def parse_routes(output):
    """从 simple_switch_CLI 的 table_dump 输出解析 flow -> P4 后端。"""
    routes = []
    pending_flow = None
    for line in output.splitlines():
        flow_match = FLOW_RE.search(line)
        if flow_match:
            if pending_flow is not None:
                raise ValueError("发现没有对应 set_backend 的 flow 条目")
            pending_flow = int(flow_match.group(1), 16)
            continue
        backend_match = BACKEND_RE.search(line)
        if backend_match and pending_flow is not None:
            routes.append((pending_flow, int(backend_match.group(1), 16)))
            pending_flow = None
    if pending_flow is not None:
        raise ValueError("最后一个 flow 条目没有对应 set_backend")
    if not routes:
        raise ValueError("udp_tag_table 中没有可恢复的 set_backend 条目")
    flow_ids = [flow_id for flow_id, _ in routes]
    if len(flow_ids) != len(set(flow_ids)):
        raise ValueError("udp_tag_table 中存在重复 flow_id")
    return sorted(routes)


def render_restore_cli(routes):
    commands = ["table_clear udp_tag_table"]
    for flow_id, backend_id in sorted(routes):
        commands.append(
            "table_add udp_tag_table set_backend 1 1 {} => {}".format(
                flow_id, backend_id
            )
        )
    return "\n".join(commands) + "\n"


def run_cli(commands, thrift_port):
    process = subprocess.run(
        ["simple_switch_CLI", "--thrift-port", str(thrift_port)],
        input=commands,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "simple_switch_CLI 执行失败\nSTDOUT:\n{}\nSTDERR:\n{}".format(
                process.stdout, process.stderr
            )
        )
    return process.stdout


def atomic_write(path, content):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".p4-routes-", dir=directory)
    try:
        with os.fdopen(fd, "w") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def snapshot(path, thrift_port):
    output = run_cli("table_dump MyIngress.udp_tag_table\n", thrift_port)
    routes = parse_routes(output)
    atomic_write(path, render_restore_cli(routes))
    return routes


def restore(path, thrift_port):
    with open(path, "r") as file_handle:
        commands = file_handle.read()
    if not commands.startswith("table_clear udp_tag_table\n"):
        raise ValueError("拒绝执行格式不正确的 P4 路由快照")
    run_cli(commands, thrift_port)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "restore"))
    parser.add_argument("--file", required=True)
    parser.add_argument("--thrift-port", default="9090")
    args = parser.parse_args()
    if args.action == "snapshot":
        routes = snapshot(args.file, args.thrift_port)
        print("已快照 {} 条 P4 路由到 {}".format(len(routes), args.file))
    else:
        restore(args.file, args.thrift_port)
        print("已从 {} 恢复 P4 路由".format(args.file))


if __name__ == "__main__":
    main()
