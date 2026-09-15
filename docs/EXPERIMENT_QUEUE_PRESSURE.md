# 队列压力驱动的 Worker 切换实验

## 实验目标

验证 `minExpectedCompletionTime` 策略不仅比较静态服务速率，还会把运行时队列深度计入预计完成时间，并在 Worker 变忙时自动改变选择。

本实验全程使用 `execution: plan`，Controller 只更新 `ComputeTask.status`，不会创建 Job，也不会向 Worker 发送真实任务。

## 实验对象

- ComputeTask：`single-worker-metrics-plan`
- 候选 Worker：slot216（node194）与 slot226（node200）
- 子任务数：16
- 最大 Worker 数：1
- 资源需求：0 CPU、2 GiB 内存

这里使用 0 CPU 是为了排除 CPU 容量比例的影响，直接观察服务速率、队列深度与网络延迟对选择的作用。这只是规划算法实验参数，不用于真实工作负载。

## 当前预计完成时间公式

按当前冻结算法实现：

```text
预计完成时间
= 队列等待时间
+ 计算时间
+ 最后一批等待时间
+ 网络延迟
```

其中：

```text
队列等待时间 = queue_depth / service_rate × 1000 ms
计算时间 = assigned_task_count / effective_rate × 1000 ms
```

`1000` 用于把秒换算为毫秒。当前调度算法继续保持冻结，不在本实验中修改。

## 第一阶段：基线

应用任务：

```bash
kubectl apply -f manifests/05-single-worker-plan.yaml
```

基线指标为：

- slot216：100 tasks/s，queue_depth=0
- slot226：75 tasks/s，queue_depth=0

结果：

```text
selectedWorker: slot216 / node194
estimatedMakespanMs: 161
evaluatedCombinations: 2
```

16 个任务刚好构成完整批次，因此没有 5000 ms 的残缺批次等待：

```text
16 / 100 × 1000 + 1 = 161 ms
```

## 第二阶段：模拟 slot216 拥塞

临时应用实验指标：

```bash
kubectl apply -f experiments/queue-pressure/slot216-busy-runtime-metrics.yaml
```

该配置只把 slot216 的 `queue_depth` 改为 1000。Controller Pod 确认读到新挂载文件后，结果变为：

```text
selectedWorker: slot226 / node200
estimatedMakespanMs: 214.333
evaluatedCombinations: 2
```

slot216 的队列惩罚约为：

```text
1000 / 100 × 1000 = 10000 ms
```

虽然 slot216 的静态服务速率更高，但预计完成时间已经远大于 slot226，因此算法自动切换到 node200。

## 第三阶段：恢复基线

实验结束后必须恢复正式配置：

```bash
kubectl apply -f manifests/03-runtime-metrics.yaml
```

本次恢复后已确认：

- Controller Pod 内的挂载文件不再包含 slot216 的 `queue_depth: 1000`。
- 单 Worker 任务重新选择 slot216，预计完成时间恢复为 161 ms。
- 双 Worker 任务恢复为 5521 ms。
- 两个 Worker 都没有重启。
- `kube-orchestrator` Namespace 没有 Warning Event。

## Kubernetes 观察点

ConfigMap 在 API Server 中更新后，挂载到 Pod 内的文件不会保证瞬时变化。kubelet 会按同步周期刷新 ConfigMap Volume。本次实验中可以清楚观察到：API 对象已经更新时，Pod 内仍短暂保留旧内容。

因此验证动态配置时，应当分别检查：

```bash
kubectl get configmap runtime-metrics -n kube-orchestrator -o yaml
kubectl exec -n kube-orchestrator deployment/controller -- \
  cat /etc/kube-orchestrator/runtime_metrics.json
```

不能只看到 `kubectl apply` 成功，就认为运行中的程序已经读到了新配置。

