# 02 · 部署方式大全

所有命令均可直接复制。环境要求分两类：
**集群类**（Helm / kind e2e / GHCR 镜像）需要 docker + kubectl + helm + jq；
**本地类**（构建 / 测试 / 面板演示）只需要 WSL2/Linux + Python 3.11+ + g++/CMake。

## 方式一：本地构建与测试（最常用，无集群）

~~~
# C++ agent：构建 + 单测（109 项检查）
cmake -S agent -B agent/build -DCMAKE_BUILD_TYPE=Release
cmake --build agent/build -j
ctest --test-dir agent/build --output-on-failure

# Python：全量单测（91 项）
pip install -r requirements.txt
python -m unittest discover -s tests -v

# 端到端冒烟（拉起真 receiver + 真 agent，验证 UDP 链路与透传字段）
bash agent/tests/smoke_local.sh

# 可视化面板自检（无需集群）
AGENT_BACKEND_IDS=216,217 ./agent/build/node_agent --selfcheck
~~~

## 方式二：无集群演示（面试投屏用）

双击打开 `dashboard/index.html` 即进入演示模式：本地仿真 6 个 Worker 槽位，
含模拟掉线、批次冲刷、P4 转发动画与调度模拟器——**不依赖任何集群**。

## 方式三：Helm 一键部署到集群

~~~
# 从仓库源码 Chart 安装（三组件：controller + metrics-receiver + node-agent）
helm install kube-orch charts/kube-orchestrator --create-namespace -n kube-orchestrator

# 面板本地访问
kubectl -n kube-orchestrator port-forward \
  svc/kube-orch-kube-orchestrator-metrics-receiver 8080:8080
# 浏览器打开 http://127.0.0.1:8080/dashboard
~~~

关键 values（详见 `charts/kube-orchestrator/values.yaml`）：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| controller.executionMode | plan | 只写 Status 不建 Job；apply 需与任务双开 |
| controller/metricsReceiver.workerSlots | 216,217,...228 | Worker 白名单，两边必须一致 |
| nodeAgent.backendIds | 216 | 每节点只上报自己承载的槽位（须为白名单子集） |
| metricsReceiver.auth | create + 占位 token | 生产用 existingSecret 引用已有 Secret |
| serviceMonitor.enabled | false | 需要 prometheus-operator CRD |

注意：多节点集群安装 agent 时，每个节点用独立 release 并设置各自的 backendIds。

## 方式四：kind 端到端验证（CI 同款）

~~~
# 需要 docker/kind/kubectl/helm/jq；脚本自带建群、构建、加载、安装、断言、清理
bash scripts/e2e_kind.sh
# 通过标志：
# E2E-PASS: reconcile=2 Workers(216,217) makespan=...ms | metrics 链路 fresh(216,217) | /metrics OK

# 失败时保留集群排查（脚本会把 Pod 状态/控制器日志写进 GitHub Job Summary）
KEEP_CLUSTER=1 bash scripts/e2e_kind.sh
~~~

e2e 断言链条（背下来，面试讲"端到端"就讲这个）：
1. CRD 安装（helm crds/）→ controller 可用；
2. 两个带 `slot` 标签的 busybox 假 Worker Ready（容量靠 requests 声明）；
3. apply ComputeTask（资源恰好需要两台合力）→ Status.phase == Planned；
4. selectedWorkers == 2 且 makespan 有值；
5. agent→receiver UDP 链路在 60s 内 fresh(216,217)；
6. /metrics 输出 worker queue depth 序列。

## 方式五：直接使用 CI 发布的 GHCR 镜像

main 分支每次推送会发布两个镜像（tag 为 commit 前 7 位）：

~~~
ghcr.io/afterwards-1st/kube-p4-orchestrator:<sha>          # controller + receiver 同镜像双命令
ghcr.io/afterwards-1st/kube-p4-orchestrator-node-agent:<sha> # C++ agent（distroless/cc）
~~~

Chart 的镜像仓库/标签通过 values 覆盖即可指向它们。

## 方式六：裸 manifests（实验集群原始方式）

~~~
kubectl apply -f manifests/00-namespace-rbac.yaml   # Namespace/Quota/RBAC
kubectl apply -f manifests/01-computetask-crd.yaml  # CRD
# 创建 Secret（Token 由环境变量提供，不进 Git）
METRICS_TOKEN=xxx bash scripts/create-metrics-secret.sh
kubectl apply -f manifests/02-controller.yaml       # 控制器
kubectl apply -f manifests/03-runtime-metrics.yaml  # 静态指标基线
kubectl apply -f manifests/06-metrics-receiver.yaml # 指标服务
~~~

## 本机网络备注

- 直连 GitHub 可能被重置：git 推送/拉取使用
  `git -c http.proxy=http://127.0.0.1:7890 ...`（按本机代理端口调整）。
- WSL2 与 Windows 互通：Windows 侧可直接访问 WSL 内监听的端口；
  WSL 内也用 `hostname -I` 取自身 IP 供 Windows 访问。
