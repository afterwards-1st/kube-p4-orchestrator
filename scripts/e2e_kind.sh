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

# Workflow 命令注解：GitHub 把它们挂到 check-run 上，匿名 API 可读，
# 用于 CI 环境下无需登录即可诊断（本地运行时只是多几行 stderr）。
annotate() {
  local level="$1"
  shift
  printf '::%s title=E2E::%s\n' "$level" \
    "$(printf '%s' "$*" | sed 's/%/%25/g; s/\r/%0D/g; s/\n/%0A/g')" >&2
}

log_step() {
  echo "$1"
  annotate notice "$1"
}

fail() {
  echo "E2E-FAIL: $*" >&2
  annotate error "$(printf '%s' "$*" | tr '\n' ' ' | cut -c1-280)"
  annotate error "pods: $(kubectl get pods -A --no-headers 2>&1 | head -15 | tr '\n' '|' | cut -c1-700)"
  annotate error "pull-msg: $(kubectl get pods -A -o json 2>/dev/null | jq -r '[.items[] | select(.status.containerStatuses[]?.state.waiting.message != null) | {n: .metadata.name, m: .status.containerStatuses[].state.waiting.message}] | tostring' | cut -c1-600)"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "### E2E 失败点"
      echo ""
      echo "$*"
      echo ""
      echo "### 集群诊断"
      echo ""
      echo '```'
      kubectl get pods -A -o wide 2>&1 | head -30 || true
      echo ""
      kubectl -n "$NAMESPACE" logs deploy/"$RECEIVER_DEPLOY" --tail=40 2>&1 | tail -40 || true
      echo ""
      kubectl -n "$NAMESPACE" get computetask -o yaml 2>&1 | tail -80 || true
      echo '```'
    } >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true
  fi
  exit 1
}

build_and_load() {
  local tag="$1" context="$2" logfile="$3"
  if ! docker build -t "$tag" "$context" >"$logfile" 2>&1; then
    tail -40 "$logfile" >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
    annotate error "docker build $tag: $(tail -45 "$logfile" | tr '\n' ' ' | cut -c1-900)"
    fail "镜像构建失败: $tag"
  fi
  if ! kind load docker-image "$tag" --name "$CLUSTER_NAME" >>"$logfile" 2>&1; then
    tail -10 "$logfile" >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
    annotate error "kind load $tag: $(tail -8 "$logfile" | tr '\n' ' ' | cut -c1-240)"
    fail "镜像加载 kind 失败: $tag"
  fi
}
need() { command -v "$1" >/dev/null 2>&1 || fail "缺少工具: $1"; }
LAST_STEP="(初始化)"
# 任何未显式处理的命令失败都会带上当前步骤注解退出，保证 CI 匿名可诊断。
trap 'rc=$?; if [[ "$rc" -ne 0 ]]; then trap - ERR; fail "命令失败于步骤: $LAST_STEP (行 $LINENO, rc=$rc)"; fi' ERR
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

LAST_STEP="[1/7] kind创建"; log_step "[1/7] 创建 kind 集群 $CLUSTER_NAME ..."
if ! kind create cluster --name "$CLUSTER_NAME" --wait 120s >/tmp/e2e-kind.log 2>&1; then
  tail -20 /tmp/e2e-kind.log >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
  annotate error "kind create: $(tail -12 /tmp/e2e-kind.log | tr '\n' ' ' | cut -c1-240)"
  fail "kind 创建集群失败"
fi

LAST_STEP="[2/7] 构建加载镜像"; log_step "[2/7] 构建并加载镜像 ..."
build_and_load "$CONTROLLER_IMAGE" "$REPO_ROOT" /tmp/e2e-build-controller.log
build_and_load "$AGENT_IMAGE" "$REPO_ROOT/agent" /tmp/e2e-build-agent.log

LAST_STEP="[3/7] helm 安装"; log_step "[3/7] Helm 安装三组件（plan 门禁）..."
if ! helm upgrade --install "$RELEASE" "$REPO_ROOT/charts/kube-orchestrator" \
    --create-namespace -n "$NAMESPACE" \
    -f "$REPO_ROOT/scripts/e2e/values-kind.yaml" \
    --wait --timeout 240s > /tmp/e2e-helm.log 2>&1; then
  annotate error "pods: $(kubectl get pods -A --no-headers 2>&1 | head -15 | tr '\n' '|' | cut -c1-700)"
  annotate error "pull-msg: $(kubectl get pods -A -o json 2>/dev/null | jq -r '[.items[] | select(.status.containerStatuses[]?.state.waiting.message != null) | {n: .metadata.name, m: .status.containerStatuses[].state.waiting.message}] | tostring' | cut -c1-800)"
  annotate error "events: $(kubectl get events -A --sort-by=.metadata.creationTimestamp 2>/dev/null | tail -10 | tr '\n' '|' | cut -c1-800)"
  annotate error "docker-imgs: $(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E 'e2e|orchestrator' | tr '\n' '|' | cut -c1-300)"
  annotate error "helm install: $(tail -20 /tmp/e2e-helm.log | tr '\n' ' ' | cut -c1-800)"
  tail -30 /tmp/e2e-helm.log >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>/dev/null || true
  fail "helm 安装 --wait 超时或失败"
fi

LAST_STEP="[4/7] 等待组件"; log_step "[4/7] 等待组件就绪 ..."
kubectl -n "$NAMESPACE" wait --for=condition=available \
  deployment -l app.kubernetes.io/part-of=kube-orchestrator --timeout=240s

LAST_STEP="[5/7] 部署Worker与任务"; log_step "[5/7] 部署 e2e Worker（slot=216/217）与 ComputeTask ..."
kubectl apply -f "$REPO_ROOT/scripts/e2e/workers-kind.yaml" >/dev/null
kubectl -n default wait --for=condition=available \
  deployment -l app=kube-p4-e2e-worker --timeout=240s
kubectl apply -f "$REPO_ROOT/scripts/e2e/computetask-kind.yaml" >/dev/null

LAST_STEP="[6/7] 轮询调度结果"; log_step "[6/7] 轮询 ComputeTask 调度结果 ..."
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
if [[ -z "$PLANNED" ]]; then
  CONTROLLER_LOGS="$(kubectl -n "$NAMESPACE" logs deploy/"$RELEASE-kube-orchestrator-controller" --tail=60 2>&1 | tail -60 || true)"
  fail "ComputeTask 120 秒内未进入 Planned（phase=$PHASE）
=== controller 日志尾部 ===
$CONTROLLER_LOGS"
fi
TASK_JSON="$(kubectl -n "$NAMESPACE" get computetask "$TASK_NAME" -o json)"
SELECTED="$(jq '.status.selectedWorkers | length' <<<"$TASK_JSON")"
[[ "$SELECTED" -eq 2 ]] || fail "selectedWorkers 期望 2，实际 $SELECTED"
MAKESPAN="$(jq -r '.status.estimatedMakespanMs' <<<"$TASK_JSON")"
BACKENDS="$(jq -r '[.status.selectedWorkers[].backendId] | sort | join(",")' <<<"$TASK_JSON")"
echo "  phase=Planned selectedWorkers=2 backendIds=$BACKENDS makespan=${MAKESPAN}ms"

LAST_STEP="[7/7] 指标链路"; log_step "[7/7] 验证 node-agent → metrics-receiver 指标链路 ..."
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
[[ -n "$FRESH" ]] || {
  AGENT_LOGS="$(kubectl -n "$NAMESPACE" logs ds/"$FULLNAME-node-agent" --tail=25 2>&1 | tail -25 || true)"
  fail "agent 上报未在 60 秒内进入 fresh 状态
=== agent 日志尾部 ===
$AGENT_LOGS
=== /v1/backends 最后响应 ===
${BODY:-(exec 无输出)}"
}
METRICS="$(kubectl -n "$NAMESPACE" exec "deploy/$RECEIVER_DEPLOY" -- python3 -c \
  "import urllib.request as u
r = u.Request('http://127.0.0.1:8080/metrics', headers={'Authorization': 'Bearer $TOKEN'})
print(u.urlopen(r, timeout=2).read().decode())")"
grep -q '^kube_orchestrator_worker_queue_depth{backend_id="216"}' <<<"$METRICS" \
  || fail "/metrics 缺少 worker queue depth 序列"

echo ""
echo "E2E-PASS: reconcile=${SELECTED} Workers(${BACKENDS}) makespan=${MAKESPAN}ms | metrics 链路 fresh(216,217) | /metrics OK"
