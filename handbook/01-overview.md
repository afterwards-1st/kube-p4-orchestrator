# 01 · 项目详细介绍

## 1. 一句话定位

**"调度平面决定去向、数据面真实送达"的 Kubernetes 协同调度系统**：
ComputeTask CRD 声明任务需求，自定义控制器基于 Worker 实时指标算出多后端分配方案，
方案映射为 P4 可编程交换机的路由表项，任务流被真实转发到被调度的 Worker；
Worker 上的 C++ 节点 agent 回传真实容器用量，形成调度-反馈闭环。

## 2. 组件全景（四个进程 + 两个外围）

| 组件 | 语言/形态 | 职责 | 代码位置 |
| --- | --- | --- | --- |
| 自定义控制器 | Python（官方 kubernetes client），Deployment | 监听 ComputeTask，双时间尺度 reconcile，写 Status/Event，apply 模式建 Job | `app/controller.py`（约 950 行） |
| metrics-receiver | Python，Deployment | 收 UDP 遥测（白名单+过期过滤）、HTTP API、Prometheus /metrics、伺服可视化面板 | `app/metrics_receiver.py` |
| node-agent | C++17，DaemonSet（distroless/cc） | cgroup v2 / proc 两级回退采集容器资源，UDP 上报，优雅停机，selfcheck 探针 | `agent/`（约 15 个源文件） |
| batch server（Worker 负载模拟） | Python | 按 batch_size/timeout 模拟批处理，产出 queue_depth 等遥测 | `workers/batch_server_report_sim.py` |
| 可视化面板 | 单文件 HTML（零依赖） | 实时指标/调度模拟/P4 转发动画，支持无集群演示模式 | `dashboard/index.html` |
| P4 控制器（外围） | Python + systemd | 114 主机上 BMv2 的单写者控制面 | `p4-controller/` |

## 3. 架构图与一次任务的完整生命周期

~~~
ComputeTask CRD
      |
      v
Kubernetes Controller（2s 快循环 + 15s 资源慢刷新）
  |                 |
  |                 +--> Job（仅 apply 双门禁全开时）
  |
  +--> Worker 资源快照 + 实时队列指标
             |
             v
      最短预计完成时间规划（全枚举 + 最大余数法）
             |
             v  flowId -> backendId
      114 P4 HTTP Control API（仅 apply + owner 绑定）
             |
             v
      BMv2 udp_tag_table（按 flow 转发）
             |
             v
      Worker Pod 216 / 217 / ...（batch server）
             |
             |  UDP 遥测（queue_depth / completed_tasks / cgroup 资源）
             v
metrics-receiver ──HTTP──► 回到控制器，形成闭环
~~~

删除任务时：Finalizer 校验"当前表项仍是自己写的值"才恢复共享路由表（漂移检测），
然后释放 Finalizer。

## 4. 关键机制一览（细节见 docs/SYSTEM_DESIGN.md）

- **双门禁**：执行 = 全局 EXECUTION_MODE==apply **且** 任务 spec.execution==apply；
  P4 写表 = 全局模式 + 任务 routing.mode + **owner 绑定**（三层）。默认部署即安全。
- **双时间尺度**：2s 快循环只重规划 minExpectedCompletionTime 任务（队列变化快）；
  15s 慢刷新全量缓存（节点/Worker/任务）。
- **幂等写回**：Status 剔除 decisionTrace 比较，只有计划或指标指纹变化才 PATCH，
  消除 2s 循环的写放大。
- **可解释调度**：单节点路径输出 candidateEvidence（每节点的接受原因/评分因子）；
  多 Worker 路径输出 evaluatedCombinations 与每个 Worker 的预计完成时间。
- **指标三级降级**：dynamic → static-no-fresh-data → static-fallback → static，
  新鲜度永远写进 decisionTrace。

## 5. 交付与质量资产

| 资产 | 说明 |
| --- | --- |
| Helm Chart | `charts/kube-orchestrator`：controller + receiver + agent 统一交付，CRD 放 crds/ |
| CI | `.github/workflows/ci.yml`：双语言单测 → Helm lint → kind e2e → main 发 GHCR |
| kind e2e | 断言"CRD → reconcile → Status Planned(selectedWorkers=2) → 指标链路 fresh → /metrics 序列存在" |
| 测试 | 91 项 Python 单测 + 109 项 C++ 逻辑检查 + 本地冒烟脚本 + 集群级 e2e |
| 安全基线 | 非 root 65532、drop ALL capabilities、只读根文件系统、不挂 SA token（receiver/agent） |
| 可观测 | decisionTrace（8 种 reason）、K8s Events（去重键）、Prometheus Counter/Gauge、面板 |

## 6. 数字速查表（面试随手引用，全部可验证）

| 数字 | 含义 |
| --- | --- |
| 91 | Python 单元测试（`tests/`，CI 中运行） |
| 109 | C++ agent 逻辑检查（`agent_tests`，ctest 运行） |
| 4 | CI job 数：测试 / Helm lint / kind e2e / 镜像发布 |
| 13 | Helm chart 渲染的资源数（含 CRD 之外的全部对象） |
| 3 | P4 写表门禁层数（全局模式/任务模式/owner 绑定） |
| 2s / 15s | 快循环周期 / 资源缓存刷新周期 |
| 63 | 6 个 Worker 时的可行子集总数（e2e 用 42） |
| 3s | 指标过期阈值 stale_after_seconds |
| 2 | kind e2e 强制跨 Worker 的最小节点数（资源恰好需要两台合力） |
