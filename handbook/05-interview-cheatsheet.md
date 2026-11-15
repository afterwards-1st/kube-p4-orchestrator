# 05 · 面试速查（投递与面试冲刺用）

## 1. 简历文案（直接粘贴）

> **基于 Kubernetes CRD 的任务编排与节点观测系统**（个人项目，附 GitHub 链接）
> 技术栈：Python（kubernetes client）、C++17、Kubernetes（CRD/控制器/DaemonSet/RBAC）、Helm、Prometheus、GitHub Actions、kind、Docker（多阶段构建/distroless/cc）、P4/BMv2

> 面向批处理/AI 计算任务的 Kubernetes 协同调度系统：ComputeTask CRD 描述任务需求，
> 自定义控制器基于 Worker 实时指标生成多后端分配方案，C++ 节点 agent 采集容器真实
> 资源用量，并通过 P4 可编程交换机把任务流真实转发到被调度的 Worker——完成
> "调度平面决定去向、数据面真实送达"的闭环。

**完整版五条**：

- 设计 ComputeTask CRD 与自定义控制器，实现 plan/apply 双执行门禁、
  Generation/OwnerReference 生命周期管理与决策 trace 记录，91 项单元测试覆盖契约与控制循环。
- 实现最短预计完成时间调度算法：可行子集全枚举 + 最大余数法任务分配 + 批次等待修正，
  融合静态基线与 UDP 实时上报的动态指标（过期过滤、静态回退），输出分配方案与 makespan 估算。
- 使用 C++17（零第三方依赖）实现节点指标 agent（DaemonSet 部署）：cgroup v2 / proc
  两级回退采集容器 CPU/内存用量，UDP 上报 + 优雅停机 + selfcheck 探针（复用为 Docker
  HEALTHCHECK 与 K8s livenessProbe），109 项逻辑检查。
- 指标服务提供白名单校验、过期过滤与规范 Prometheus 指标（Counter/Gauge 建模、
  backend_id 标签、Bearer 鉴权对接 ServiceMonitor），并伺服单文件可视化面板
  （实时指标/调度模拟/P4 转发动画，支持无集群演示）。
- Helm Chart 统一交付三组件（CRD 与安全基线模板化），GitHub Actions CI 自动执行双语言
  测试、Helm lint、镜像发布与 kind 端到端验证——真实集群内断言"CRD → controller
  reconcile → Status 调度结果 → 指标链路 fresh"全链路；另在实验室环境完成 P4 数据面
  真实转发验证（不同 flow 到不同 Worker 且不串流）。

**精简版三条**：

- 基于 Kubernetes CRD 实现自定义控制器与最短预计完成时间调度算法（plan/apply 门禁、
  Generation 生命周期、动态指标融合），91 项单元测试 + kind 端到端验证。
- C++17 零依赖实现 DaemonSet 节点 agent：cgroup v2/proc 容器资源采集、UDP 上报、
  优雅停机与 selfcheck 探针，109 项逻辑检查；指标服务暴露规范 Prometheus 指标。
- Helm Chart 统一交付三组件并接入 GitHub Actions CI；打通"调度决策 → P4 交换机写表 →
  真实流量转发"闭环并在实验室环境验证。

**岗位口味**：投平台开发 → 第 1 条放最前；投运维开发/SRE → 第 5 条（Helm/CI/e2e）放最前。

## 2. 讲述脚本（3 分钟版骨架）

1. **问题**（30s）：批处理任务要挑多个 Worker，调度完还要"真的把流量送过去"——
   市面上没有现成方案把 K8s 调度面和 P4 数据面连起来，所以我做了一个闭环。
2. **架构**（60s）：CRD → 控制器（双时间尺度、双门禁）→ 完成时间算法 →
   flowId→backendId 映射写入 P4 → Worker 的 C++ agent 回传 cgroup 真实用量 →
   指标回流影响下一轮调度。
3. **工程**（45s）：Helm 统一交付、CI 四段流水线、kind e2e 断言全链路、
   Prometheus 指标、200+ 项测试。
4. **真实验证**（30s）：实验室环境不同 flow 转发到不同 Worker 且不串流；
   CI 在真实 kind 集群里跑通 reconcile。

## 3. 数字速查（背下来）

| 数字 | 出处 |
| --- | --- |
| 91 / 109 / 200+ | Python 单测 / C++ 检查 / 总测试量 |
| 2s / 15s | 快循环 / 资源慢刷新 |
| 3 层 | P4 写表门禁；UDP 白名单/HTTP Token/P4 Token 三层鉴权 |
| 63（e2e 42） | 6 Worker 全枚举子集数 |
| 4 job | CI：测试/Helm lint/kind e2e/镜像发布 |
| 6 轮 | CI 从零到全绿的排障轮次（见 04 文档） |
| 5 | Chart 渲染的组件级对象组（RBAC/ConfigMap/Secret×2/Deployment×2/DS/Service） |

## 4. 高频必答题索引

| 话题 | 去哪准备 |
| --- | --- |
| cgroup v2 / namespace / /proc 解析 | `docs/AGENT_LEARNING_GUIDE.md` §2.5 + 第 5 章 24 题 |
| UDP vs TCP、信号安全、Pimpl、时钟语义 | 同上 §2.1/2.6 |
| 控制循环/双门禁/写回去重/Job 设计 | `docs/SYSTEM_DESIGN.md` §3 |
| 调度算法（枚举/最大余数/批次修正/单节点最优） | 同上 §6 |
| P4 单写者/三层门禁/漂移检测删除 | 同上 §7 |
| Prometheus Counter/Gauge、序列断开防护 | 同上 §8 + handbook 03 |
| Helm crds/ 目录、Release 升级 | 同上 §9 |
| kind e2e 断什么、为什么比 mock 可信 | `handbook/02-deployment.md` 方式四 |
| CI 排障故事（make/标签错位/libstdc++/kind 污点） | `handbook/04-ci-debug-log.md` 全文 |

## 5. 投递清单

- [ ] 简历 1 页定稿（上方文案），GitHub 链接放项目名旁
- [ ] 根 README 加 CI 徽章：`![ci](https://github.com/afterwards-1st/kube-p4-orchestrator/actions/workflows/ci.yml/badge.svg)`
- [ ] 录 30 秒面板演示 GIF 存手机（演示模式即可）
- [ ] 自我介绍 1 分钟 / 3 分钟各一版（用上面脚本压缩）
- [ ] 渠道：实习僧 / Boss 直聘 / 牛客内推 / 各云厂商官网，先投 30~50 家
- [ ] 面试后立刻记录被问倒的题，当晚补齐

## 6. 诚实底线（每场面试通用）

- 说"实验室环境真实验证"，不说生产；被问环境答 BMv2 + 实验集群 + kind。
- 没做的主动讲：leader election 未做、网络约束显式拒绝、精确枚举适合 ≤10 节点——
  **知道自己没做什么和知道自己做了什么同样重要**。
- 简历上每个数字都能在仓库/CI 里现场验证。
