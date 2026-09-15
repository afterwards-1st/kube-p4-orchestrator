# 调度决策快照与可观测性

## 目标

Controller 不只给出“选了谁”，还要回答：

- 为什么这次 status 发生变化；
- 决策使用了动态指标还是静态回退；
- 指标当时是否新鲜；
- 做出决策时各真实 Worker 的队列深度是多少；
- 决策发生在什么时间。

这些信息写入 ComputeTask.status.decisionTrace。

## 为什么只在决策变化时记录

Worker 每秒上报一次，Controller 每 2 秒读取一次。如果把采样年龄或每次心跳都写入 status，会产生持续的 API Server、etcd 和 Watch 写入压力。

decisionTrace 采用“最近一次决策快照”语义：

- 规划结果变化：写入新 status 和新快照；
- 规划结果不变：保留原快照，不更新 recordedAt；
- 老任务首次升级：补写一次 observability-initialized。

因此它适合解释当前决策，但不是永久历史数据库。需要完整历史时，应把变化写入 Kubernetes Events、日志或外部时序存储。

## 字段说明

~~~yaml
decisionTrace:
  reason: selected-workers-changed
  recordedAt: 2026-09-13T03:28:33.491468Z
  metricsSource: dynamic
  metricsFreshness: fresh
  dynamicBackendCount: 2
  queueDepthByBackend:
    "216": 9
    "226": 0
~~~

### reason

可能值：

- initial-plan：任务首次生成规划；
- observability-initialized：已有规划首次补充审计信息；
- phase-changed：Planned、Unschedulable 等阶段改变；
- selected-workers-changed：多 Worker 选择发生变化；
- selected-node-changed：普通节点策略选择发生变化；
- estimated-completion-changed：Worker 不变，但预计完成时间变化；
- plan-details-changed：其他规划细节变化。

### metricsSource 与 metricsFreshness

对应关系：

~~~text
dynamic                   -> fresh
static-no-fresh-data       -> stale
static-fallback            -> unavailable
static                     -> static
kubernetes-resource-snapshot -> not-applicable
~~~

普通 nodeScore 任务依赖 Kubernetes 资源快照，不依赖 Worker 指标，因此新鲜度为 not-applicable。

### queueDepthByBackend

只记录当前实际发现并在白名单内的 Worker Pod，不把未启用的 217、218、227、228 静态槽位混入审计快照。

队列值来自这次规划实际使用的合并指标。

## status 去重如何保留

比较规划是否变化时，Controller 暂时忽略 decisionTrace：

~~~text
当前规划主体 == 新规划主体
  且已经存在 decisionTrace
  -> 不 PATCH

当前规划主体 == 新规划主体
  但没有 decisionTrace
  -> 仅初始化一次审计快照
~~~

这样 recordedAt 不会造成每 2 秒一次的自我更新循环。

## CRD Schema

manifests/01-computetask-crd.yaml 明确定义 decisionTrace 的字段、类型、reason 枚举和 freshness 枚举。

好处是 API Server 会拒绝不符合结构的数据，而不是任由 Controller 写入任意格式。

## 线上验证

Controller 0.4.1 部署后，三个已有任务首先得到 observability-initialized。等待多个快循环后，resourceVersion 没有变化。

随后向 slot216 发送 15 个任务：

~~~text
11:28:33
node200 / 214.333 ms
reason=selected-workers-changed
queues={216: 9, 226: 0}

11:28:40
node194 / 161 ms
reason=selected-workers-changed
queues={216: 0, 226: 0}
~~~

第一次快循环发生在任务突发尚未全部发送完成时，因此快照捕获到队列 9，而不是最终峰值 15。这说明记录的是“决策时使用的输入”，不是事后最大值。

## 当前状态

- Controller：kube-orchestrator:0.4.1；
- Controller Ready，重启 0；
- CRD 已接受 decisionTrace 结构；
- 动态指标来源为 dynamic/fresh；
- 52 项测试通过；
- 最短预计完成时间算法未修改；
- P4 数据面尚未接入。

## 下一步

Kubernetes Event 决策历史已经完成，详见 docs/KUBERNETES_DECISION_EVENTS.md。下一阶段设计从 plan 到受控 Job 执行的双重许可与生命周期验证。
