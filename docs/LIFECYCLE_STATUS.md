# ComputeTask 生命周期状态

本项目将 `status.phase` 与 `status.conditions` 分开使用：前者给人快速阅读，后者让自动化系统分别判断“是否完成了选址”和“是否真的在执行”。

## 状态模型

```text
ComputeTask
  ├─ phase: Planned / Submitted / Running / Succeeded / Failed / Unschedulable
  ├─ conditions[type=Scheduled]
  └─ conditions[type=ExecutionReady]
```

`phase` 是当前最主要的汇总状态。`conditions` 不互相覆盖，因此一个任务可以同时是“已选址、未执行”：这正是默认 `plan` 模式的正常结果。

## Conditions 的含义

| Condition | True | False |
| --- | --- | --- |
| `Scheduled` | 已找到合格节点或 Worker 组合 | 没有符合资源、健康度或约束的候选对象 |
| `ExecutionReady` | Job 正在运行，或已成功完成 | 仅规划、等待 Job、任务失败或正在终止 |

常见原因码：

- `PlacementSelected`：已生成选址决策。
- `NoEligibleWorker`：没有可用节点或 Worker。
- `PlanOnly`：全局或任务级执行开关仍是 `plan`。
- `AwaitingJob`：已允许执行，但 Job 尚未进入运行态。
- `JobActive`、`JobSucceeded`、`JobFailed`：来自 Kubernetes Job 的实际状态。

## observedGeneration

每次 Controller 写入状态时都会写入 `observedGeneration`。它等于 Controller 已处理的 `metadata.generation`，因此可用于区分：

```text
spec 已修改，但 Controller 尚未处理  → generation > observedGeneration
spec 已被当前 Controller 处理        → generation = observedGeneration
```

Conditions 不写 `lastTransitionTime`。这是有意设计：Controller 使用轮询和状态写入去重，如果每轮只因时间变化就更新状态，会制造无意义的 API Server 写入、etcd 版本和 Watch 事件。

## 观察方式

```bash
kubectl get ctask <任务名> -n kube-orchestrator -o yaml
kubectl get ctask <任务名> -n kube-orchestrator \
  -o jsonpath='{.status.phase}{"\\n"}{.status.conditions}{"\\n"}'
```

## 当前边界

这一版先建立一致、可测试的状态契约；不改变默认的 `plan` 安全边界，也不创建额外 Job 或 P4 路由。下一步会在此基础上加入 Finalizer 和“仅恢复自己仍拥有的 P4 路由快照”的删除回收流程。