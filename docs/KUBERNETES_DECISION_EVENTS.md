# Kubernetes 调度决策 Event

## 作用

ComputeTask.status.decisionTrace 保存最近一次决策快照；Kubernetes Event 保存一段时间内发生过的关键变化，可以通过 kubectl get events 查询。

Controller 0.4.2 仅为以下变化创建 Event：

- Worker 或 Node 选择变化；
- 调度阶段变化；
- 指标在 fresh、stale、unavailable 之间变化。

只有预计完成时间变化、但选择和指标可用性不变时，不创建 Event。

## RBAC

Controller 的 Namespace Role 只新增：

~~~yaml
- apiGroups: [""]
  resources: ["events"]
  verbs: ["create"]
~~~

它不能修改或删除其他 Event。实测 ServiceAccount 的 create events 权限为 yes。

## Event 内容

~~~text
REASON: WorkerSelectionChanged
MESSAGE:
reason=selected-workers-changed
phase=Planned
destination=ubuntu-200-226-...
metrics=dynamic/fresh
queues={'216': 15.0, '226': 0.0}
~~~

Event 通过 involvedObject 指向对应 ComputeTask，因此可以按任务过滤：

~~~bash
kubectl get events -n kube-orchestrator   --field-selector involvedObject.name=single-worker-metrics-plan   --sort-by=.lastTimestamp
~~~

## 真实验证

向 slot216 发送 15 个任务后只产生两条 Event：

~~~text
03:46:34 WorkerSelectionChanged
node194 -> node200，queues={216:15, 226:0}

03:46:38 WorkerSelectionChanged
node200 -> node194，queues={216:0, 226:0}
~~~

中间没有因为队列或预计完成时间数值波动产生额外 Event，证明事件去重有效。

## Event 与日志、status 的区别

- status：当前最终状态，适合程序读取；
- decisionTrace：当前状态所对应的最近一次决策证据；
- Event：有限时间内的关键变化历史，适合 kubectl 排查；
- Controller 日志：进程级运行细节，Pod 被删除后不一定保留。

Kubernetes Event 本身不是永久审计数据库，集群会按保留策略清理旧 Event。

## 当前状态

Controller 使用 kube-orchestrator:0.4.2，Pod Ready、重启 0；56 项测试通过；调度算法和 P4 数据面均未修改。

## 下一步

开始设计从 plan 到受控执行的安全边界：继续保持全局 EXECUTION_MODE 与单个 ComputeTask.execution 双重许可，并先用最小测试任务验证 Job 生命周期。
