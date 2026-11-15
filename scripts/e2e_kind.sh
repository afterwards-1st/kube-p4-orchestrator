#!/usr/bin/env bash
# 端到端验证：kind 集群内完整跑通"Helm 交付 → CRD → controller reconcile → Status
# 调度结果 → agent/receiver 指标链路"。
#
# 用法（需要 docker/kind/kubectl/helm/jq）：
#   bash scripts/e2e_kind.sh
#   KEEP_CLUSTER=1 bash scripts/e2e_kind.sh      # 失败时保留集群便于排查
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-kube-p4-e2e}"
RELEASE="${RELEASE:-kube-orch}"
NAMESPACE="kube-orchestrator"
CONTROLLER_IMAGE="${CONTROLLER_IMAGE:-kube-orchestrator:e2e}"
AGENT_IMAGE="${AGENT_IMAGE:-kube-orchestrator/node-agent:e2e}"
FULLNAME="${RELEASE}-kube-orchestrator"
RECEIVER_DEPLOY="${FULLNAME}-metrics-receiver"
TASK_NAME="multi-worker-kind"

fail() { echo "E2E-FAIL: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "缺少工具: $1"; }
need docker
need kind
need kubectl
need helm
need jq

cleanup() {
  local rc=$?
  if [[ "$rc" -ne 0 || "${KEEP_CLUSTER:-0}" == "1" ]]; then
    echo "保留集群 $CLUSTER_NAME 便于排查（kubectl --context kind-$CLUSTER_NAME）"
  else
    kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT

echo "[1/7] 创建 kind 集群 $CLUSTER_NAME ..."
kind create cluster --name "$CLUSTER_NAME" --wait 120s >/dev/null

echo "[2/7] 构建并加载镜像 ..."
docker build -q -t "$CONTROLLER_IMAGE" "$REPO_ROOT" >/dev/null
docker build -q -t "$AGENT_IMAGE" "$REPO_ROOT/agent" >/dev/null
kind load docker-image "$CONTROLLER_IMAGE" --name "$CLUSTER_NAME"
kind load docker-image "$AGENT_IMAGE" --name "$CLUSTER_NAME"

echo "[3/7] Helm 安装三组件（plan 门禁）..."
helm upgrade --install "$RELEASE" "$REPO_ROOT/charts/kube-orchestrator" \
  --create-namespace -n "$NAMESPACE" \
  -f "$REPO_ROOT/scripts/e2e/values-kind.yaml" \
  --wait --timeout 240s >/dev/null

echo "[4/7] 等待组件就绪 ..."
kubectl -n "$NAMESPACE" wait --for=condition=available \
  deployment -l app.kubernetes.io/part-of=kube-orchestrator --timeout=240s

echo "[5/7] 部署 e2e Worker（slot=216/217）与 ComputeTask ..."
kubectl apply -f "$REPO_ROOT/scripts/e2e/workers-kind.yaml" >/dev/null
kubectl -n default wait --for=condition=available \
  deployment -l app=kube-p4-e2e-worker --timeout=240s
kubectl apply -f "$REPO_ROOT/scripts/e2e/computetask-kind.yaml" >/dev/null

echo "[6/7] 轮询 ComputeTask 调度结果 ..."
PHASE=""
PLANNED=""
for _ in $(seq 1 60); do
  PHASE="$(kubectl -n "$NAMESPACE" get computetask "$TASK_NAME" -o json 2>/dev/null \
    | jq -r '.status.phase // ""')"
  if [[ "$PHASE" == "Planned" ]]; then
    PLANNED=1
    break
  fi
  sleep 2
done
[[ -n "$PLANNED" ]] || fail "ComputeTask 120 秒内未进入 Planned（phase=$PHASE）"
TASK_JSON="$(kubectl -n "$NAMESPACE" get computetask "$TASK_NAME" -o json)"
SELECTED="$(jq '.status.selectedWorkers | length' <<<"$TASK_JSON")"
[[ "$SELECTED" -eq 2 ]] || fail "selectedWorkers 期望 2，实际 $SELECTED"
MAKESPAN="$(jq -r '.status.estimatedMakespanMs' <<<"$TASK_JSON")"
BACKENDS="$(jq -r '[.status.selectedWorkers[].backendId] | sort | join(",")' <<<"$TASK_JSON")"
echo "  phase=Planned selectedWorkers=2 backendIds=$BACKENDS makespan=${MAKESPAN}ms"

echo "[7/7] 验证 node-agent → metrics-receiver 指标链路 ..."
TOKEN="$(kubectl -n "$NAMESPACE" get secret metrics-receiver-auth -o jsonpath='{.data.token}' | base64 -d)"
FRESH=""
for _ in $(seq 1 30); do
  BODY="$(kubectl -n "$NAMESPACE" exec "deploy/$RECEIVER_DEPLOY" -- python3 -c \
    "import urllib.request as u
r = u.Request('http://127.0.0.1:8080/v1/backends', headers={'X-Metrics-Token': '$TOKEN'})
print(u.urlopen(r, timeout=2).read().decode())")"
  if jq -e '.backends[] | select(.backend_id == 216 and .fresh == true)' >/dev/null 2>&1 <<<"$BODY" \
    && jq -e '.backends[] | select(.backend_id == 217 and .fresh == true)' >/dev/null 2>&1 <<<"$BODY"; then
    FRESH=1
    break
  fi
  sleep 2
done
[[ -n "$FRESH" ]] || fail "agent 上报未在 60 秒内进入 fresh 状态"
METRICS="$(kubectl -n "$NAMESPACE" exec "deploy/$RECEIVER_DEPLOY" -- python3 -c \
  "import urllib.request as u
r = u.Request('http://127.0.0.1:8080/metrics', headers={'Authorization': 'Bearer $TOKEN'})
print(u.urlopen(r, timeout=2).read().decode())")"
grep -q '^kube_orchestrator_worker_queue_depth{backend_id="216"}' <<<"$METRICS" \
  || fail "/metrics 缺少 worker queue depth 序列"

echo ""
echo "E2E-PASS: reconcile=${SELECTED} Workers(${BACKENDS}) makespan=${MAKESPAN}ms | metrics 链路 fresh(216,217) | /metrics OK"
