#!/usr/bin/env bash
set -euo pipefail

# 不把真实 Token 写入 Git。运行前由操作者通过环境变量提供。
: "${METRICS_TOKEN:?请先设置 METRICS_TOKEN 环境变量}"

kubectl create secret generic metrics-receiver-auth \
  --namespace kube-orchestrator \
  --from-literal="token=${METRICS_TOKEN}" \
  --dry-run=client \
  -o yaml | kubectl apply -f -
