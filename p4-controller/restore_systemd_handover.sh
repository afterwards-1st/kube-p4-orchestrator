#!/usr/bin/env bash
# systemd 实时服务停止后：恢复启动前的动态快照，并唤醒旧控制器。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HERE/runtime"
PID_FILE="$RUNTIME_DIR/legacy-controller.pid"
SNAPSHOT_FILE="$RUNTIME_DIR/pre-live-routes.cli"
ACTIVE_FILE="$RUNTIME_DIR/handover.active"

if [[ ! -e "$ACTIVE_FILE" ]]; then
  echo '没有活动的 P4 交接，无需恢复。'
  exit 0
fi

legacy_pid="$(cat "$PID_FILE")"
resume_legacy() {
  if kill -0 "$legacy_pid" 2>/dev/null; then
    legacy_cmd="$(ps -o cmd= -p "$legacy_pid" || true)"
    if [[ "$legacy_cmd" == *'controller_global_9backend.py'* ]] \
      && [[ "$legacy_cmd" == *'--listen-port 9999'* ]]; then
      kill -CONT "$legacy_pid" 2>/dev/null || true
    fi
  fi
}
trap resume_legacy EXIT

python3 "$HERE/p4_route_snapshot.py" restore \
  --file "$SNAPSHOT_FILE" --thrift-port 9090
rm -f "$ACTIVE_FILE"
echo "P4 路由已恢复；将唤醒旧控制器 PID $legacy_pid。"
