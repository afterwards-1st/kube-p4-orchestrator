#!/usr/bin/env bash
# 验证实时控制器已经监听，且必须通过令牌认证。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${P4_CONTROL_TOKEN_FILE:-$HERE/p4-control-token}"
EXPECT_DRY_RUN="${P4_EXPECT_DRY_RUN:-false}"
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "令牌文件不可读：$TOKEN_FILE" >&2
  exit 2
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if response="$(curl -fsS \
    -H "X-P4-Control-Token: $(tr -d '\r\n' < "$TOKEN_FILE")" \
    http://127.0.0.1:18100/v1/routes)"; then
    if grep -q '"control_owner": "server-114"' <<<"$response" \
      && grep -q '"control_mode": "external"' <<<"$response" \
      && grep -q "\"dry_run\": $EXPECT_DRY_RUN" <<<"$response"; then
      echo "P4 控制 API 已就绪：dry_run=$EXPECT_DRY_RUN。"
      exit 0
    fi
  fi
  sleep 1
done
echo "P4 控制 API 在超时前未达到预期状态：dry_run=$EXPECT_DRY_RUN。" >&2
exit 1
