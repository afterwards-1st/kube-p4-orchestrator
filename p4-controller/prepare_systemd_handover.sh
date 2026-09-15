#!/usr/bin/env bash
# systemd 实时服务启动前：确认唯一旧写入者、暂停它并快照当前 P4 路由。
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HERE/runtime"
PID_FILE="$RUNTIME_DIR/legacy-controller.pid"
SNAPSHOT_FILE="$RUNTIME_DIR/pre-live-routes.cli"
ACTIVE_FILE="$RUNTIME_DIR/handover.active"
mkdir -p "$RUNTIME_DIR"

if [[ -e "$ACTIVE_FILE" ]]; then
  echo "拒绝重复交接：$ACTIVE_FILE 已存在" >&2
  exit 2
fi
if ! systemctl is-active --quiet global-gateway.service; then
  echo 'global-gateway.service 未运行，不能接管 P4。' >&2
  exit 2
fi

for _ in 1 2 3 4 5; do
  if ! ss -lntp | grep -q ':18100'; then
    break
  fi
  sleep 1
done
if ss -lntp | grep -q ':18100'; then
  echo '18100 端口仍被占用，拒绝启动实时控制器。' >&2
  exit 2
fi

gateway_pid="$(systemctl show -p MainPID --value global-gateway.service)"
mapfile -t legacy_pids < <(
  pgrep -P "$gateway_pid" -f 'controller_global_9backend.py' || true
)
if [[ "${#legacy_pids[@]}" -ne 1 ]]; then
  echo "需要且只能找到一个旧控制器，实际数量=${#legacy_pids[@]}" >&2
  exit 2
fi
legacy_pid="${legacy_pids[0]}"
legacy_cmd="$(ps -o cmd= -p "$legacy_pid")"
if [[ "$legacy_cmd" != *'--listen-port 9999'* ]]; then
  echo "PID $legacy_pid 不是预期的旧控制器。" >&2
  exit 2
fi
if ps -o stat= -p "$legacy_pid" | grep -q 'T'; then
  echo "旧控制器 PID $legacy_pid 已经处于暂停状态。" >&2
  exit 2
fi

printf '%s\n' "$legacy_pid" > "$PID_FILE"
paused=false
resume_on_error() {
  if [[ "$paused" == true ]]; then
    kill -CONT "$legacy_pid" 2>/dev/null || true
  fi
}
trap resume_on_error ERR

kill -STOP "$legacy_pid"
paused=true
for _ in 1 2 3 4 5; do
  if ps -o stat= -p "$legacy_pid" | grep -q 'T'; then
    break
  fi
  sleep 1
done
if ! ps -o stat= -p "$legacy_pid" | grep -q 'T'; then
  echo "无法暂停旧控制器 PID $legacy_pid" >&2
  exit 2
fi

python3 "$HERE/p4_route_snapshot.py" snapshot \
  --file "$SNAPSHOT_FILE" --thrift-port 9090
date -u +'%Y-%m-%dT%H:%M:%SZ' > "$ACTIVE_FILE"
trap - ERR
echo "P4 控制权已准备：旧控制器 PID $legacy_pid 已暂停。"
