#!/usr/bin/env bash
# 单元文件保存在 wzt 下，仅在 /etc/systemd/system 中建立链接。
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo '请使用 root 安装 systemd 单元。' >&2
  exit 2
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod 700 \
  "$HERE/prepare_systemd_handover.sh" \
  "$HERE/restore_systemd_handover.sh" \
  "$HERE/check_live_control_api.sh" \
  "$HERE/run_live_candidate_controller.sh" \
  "$HERE/run_guarded_candidate_controller.sh"
chmod 600 "$HERE/p4-control-token"
systemctl link "$HERE/systemd/p4-k8s-control.service"
systemctl link "$HERE/systemd/p4-k8s-dryrun.service"
systemctl daemon-reload
systemctl enable p4-k8s-dryrun.service
echo 'systemd 单元已链接；实时服务未加入开机启动目标。'
