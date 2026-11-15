#!/usr/bin/env bash
# 端到端冒烟测试：启动 metrics_receiver 与 C++ node_agent，
# 验证 UDP 上报链路、容器资源字段透传、工作负载状态文件与优雅停机。
# 用法（Linux/WSL）: bash agent/tests/smoke_local.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENT_BIN="${REPO_ROOT}/agent/build/node_agent"

if [[ ! -x "${AGENT_BIN}" ]]; then
  echo "缺少 agent 可执行文件，请先构建: cmake -S agent -B agent/build && cmake --build agent/build -j" >&2
  exit 1
fi

cleanup() {
  [[ -n "${AGENT_PID:-}" ]] && kill -TERM "${AGENT_PID}" 2>/dev/null || true
  [[ -n "${RECV_PID:-}" ]] && kill -TERM "${RECV_PID}" 2>/dev/null || true
  [[ -n "${AGENT_PID:-}" ]] && wait "${AGENT_PID}" 2>/dev/null || true
  [[ -n "${RECV_PID:-}" ]] && wait "${RECV_PID}" 2>/dev/null || true
}
trap cleanup EXIT

STATUS_FILE="$(mktemp /tmp/agent_status.XXXXXX.json)"
cat > "${STATUS_FILE}" <<'EOF'
{"queue_depth": 3, "completed_tasks": 42, "total_batches": 5, "last_flush_reason": "timeout"}
EOF

cd "${REPO_ROOT}"
WORKER_SLOTS=216,217 METRICS_UDP_PORT=19999 METRICS_HTTP_PORT=18080 \
  python3 -m app.metrics_receiver >/tmp/agent_smoke_receiver.log 2>&1 &
RECV_PID=$!
sleep 1

AGENT_BACKEND_IDS=216,217 AGENT_REPORT_HOST=127.0.0.1 AGENT_REPORT_PORT=19999 \
  AGENT_REPORT_INTERVAL_MS=100 AGENT_STATUS_FILE="${STATUS_FILE}" \
  "${AGENT_BIN}" >/tmp/agent_smoke_agent.log 2>&1 &
AGENT_PID=$!
sleep 1.5

echo "=== /v1/backends 快照 ==="
curl -sf http://127.0.0.1:18080/v1/backends > /tmp/agent_smoke_snapshot.json
cat /tmp/agent_smoke_snapshot.json
echo

kill -TERM "${AGENT_PID}"
wait "${AGENT_PID}" || true
AGENT_PID=""

echo "=== agent 日志（含优雅停机）==="
cat /tmp/agent_smoke_agent.log

echo "=== 断言校验 ==="
python3 - "${STATUS_FILE}" <<'PYEOF'
import json
import sys

with open("/tmp/agent_smoke_snapshot.json", encoding="utf-8") as fh:
    snapshot = json.load(fh)
with open(sys.argv[1], encoding="utf-8") as fh:
    status = json.load(fh)

backends = {item["backend_id"]: item for item in snapshot["backends"]}
assert snapshot["accepted_reports"] > 0, "receiver 未收到任何上报"
assert snapshot["rejected_reports"] == 0, "receiver 拒绝了上报"
for backend_id in (216, 217):
    item = backends[backend_id]
    assert item["fresh"] is True, f"{backend_id} 报文过期"
    assert item["queue_depth"] == status["queue_depth"], f"{backend_id} queue_depth 不一致"
    assert item["completed_tasks"] == status["completed_tasks"], f"{backend_id} completed_tasks 不一致"
    assert item["agent_source"] == "cpp-node-agent", f"{backend_id} 缺少 agent 来源标记"
    assert item["cgroup_version"] == "v2", f"{backend_id} 缺少 cgroup 版本"
    assert "container_mem_usage_bytes" in item, f"{backend_id} 缺少内存用量"
print("断言全部通过：UDP 链路、状态文件、容器资源透传字段均正常")
PYEOF

echo "=== 可视化面板 ==="
curl -sf http://127.0.0.1:18080/dashboard -o /tmp/agent_smoke_dashboard.html
python3 - <<'PYEOF'
with open("/tmp/agent_smoke_dashboard.html", encoding="utf-8") as fh:
    html = fh.read()
assert "kube-p4-orchestrator" in html, "面板标题缺失"
assert "/v1/backends" in html, "面板数据源缺失"
print("dashboard 路由正常，返回面板页面")
PYEOF

echo "冒烟测试完成"
