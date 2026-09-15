# 执行安全门与任务状态分离

## 本阶段目标

修复下面这种状态歧义：一个 Job 已经成功完成，但控制器恢复全局 `plan` 后，ComputeTask 的 `status.phase` 又变成 Planned。

当前控制器版本：`kube-orchestrator:0.4.5`。

## 三个字段分别回答什么问题

```yaml
status:
  observedGeneration: 1
  executionAllowed: false
  phase: Succeeded
  jobName: task-controlled-job-demo-g1
```

- `observedGeneration`：控制器处理到了哪一版 `spec`。
- `executionAllowed`：当前全局开关和任务开关是否同时允许创建 Job。
- `phase`：当前 generation 对应 Job 的真实生命周期；没有 Job 时才表示规划结果。
- `jobName`：当前 generation 对应的 Job；空字符串表示尚未创建。

因此 `executionAllowed=false` 与 `phase=Succeeded` 并不矛盾。它表示任务过去已经成功完成，但现在安全门关闭，不允许创建新的执行。

## 双安全门真值表

| 全局 `EXECUTION_MODE` | `spec.execution` | `executionAllowed` | 是否允许创建 Job |
|---|---|---:|---|
| plan | plan | false | 否 |
| plan | apply | false | 否 |
| apply | plan | false | 否 |
| apply | apply | true | 是 |

这个规则由 `_execution_allowed()` 集中计算，并由单元测试覆盖全部四种组合。

## v0.4.5 的 reconcile 顺序

```text
读取 ComputeTask
  -> 计算节点规划
  -> 计算 executionAllowed
  -> 若允许，确保当前 generation 的 Job 存在
  -> 无论是否允许，都查询当前 generation 已存在的 Job
  -> Job 存在：phase 使用 Submitted/Running/Succeeded/Failed
  -> Job 不存在：phase 使用 Planned/Unschedulable
  -> 写入 status
```

这里最关键的变化是：关闭安全门只禁止“创建新 Job”，不禁止“观察已有 Job”。控制器仍然要读取已存在的 Job，才能继续履行观察和状态同步职责。

## 为什么 jobName 默认写空字符串

Custom Resource 的 status 使用 merge patch。若新状态完全省略 `jobName`，API Server 可能保留旧值，导致 generation 已变化但状态仍指向上一代 Job。

单节点状态现在默认写入：

```yaml
jobName: ""
```

找到当前 generation 的 Job 后再写入真实名称。这样可以显式清除过期引用，同时符合 CRD 中的 string 类型。

## 线上验证

升级前：

```text
phase=Planned
jobName=task-controlled-job-demo-g1
executionAllowed=<不存在>
generation=1
```

升级 CRD 和 v0.4.5 控制器后：

```text
phase=Succeeded
jobName=task-controlled-job-demo-g1
executionAllowed=false
observedGeneration=1
generation=1
```

验证说明：

- 全局模式始终保持 plan，没有为了测试重新打开执行。
- 已完成 Job 没有被重新创建，Job 总数没有增加。
- 控制器从已有 g1 Job 读取到 `succeeded=1`，纠正了 ComputeTask 状态。
- 产生一次正常的 `SchedulingPhaseChanged` Event，记录 Planned 到 Succeeded。
- 控制器和指标接收器均 Ready、0 重启。
- `kube-orchestrator` 命名空间无 Warning 事件。

## 本阶段掌握的 Kubernetes 控制器知识

- `spec` 表示用户期望，`status` 表示控制器观察到的现实。
- `observedGeneration == metadata.generation` 表示控制器已处理当前配置版本。
- 安全策略不能覆盖真实执行结果，应使用独立字段表达。
- plan 模式应禁止产生副作用，但仍应执行只读观察。
- status merge patch 中，省略字段不一定等于清空字段。
- reconcile 必须同时具备执行职责与持续观察职责。

## 下一步

下一阶段适合完善失败语义：创建一个确定失败的 Job，记录失败原因、退出码和重试策略，并区分“业务失败、镜像拉取失败、调度失败”。
