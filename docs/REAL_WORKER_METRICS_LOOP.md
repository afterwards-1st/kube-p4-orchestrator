# 真实 Worker 动态队列闭环实验

## 实验目标

把 node194 和 node200 上实际运行的 Worker 接入 Kubernetes `metrics-receiver`，并验证真实任务进入 Worker 后，队列变化能够自动影响 `ComputeTask` 的规划结果。

本实验仍使用 `execution: plan`，Controller 只更新任务状态，不创建 Job；任务包直接发送到 Worker Pod IP，没有经过 114 或 P4。

## 上报链路

```text
Worker heartbeat
  -> metrics-receiver.kube-orchestrator.svc.cluster.local:9999/UDP
  -> metrics-receiver 内存快照
  -> Controller /v1/backends:8080/TCP
  -> 动态 queue_depth 覆盖 ConfigMap 静态值
  -> ComputeTask.status
```

使用 Service DNS 而不是写死 ClusterIP。Service 被重新创建时 ClusterIP 可能改变，而 DNS 名称可以保持稳定。

## Worker 清单修改

项目只接管当前启用的两个 Worker：

- `manifests/workers/ubuntu-194-216.yaml`
- `manifests/workers/ubuntu-200-226.yaml`

保留了原有镜像、CPU/内存、Multus 网络、`net1` 地址和 MAC，只把上报参数改为环境变量：

```text
METRICS_REPORT_HOST=metrics-receiver.kube-orchestrator.svc.cluster.local
METRICS_REPORT_PORT=9999
METRICS_REPORT_INTERVAL=1
```

两个 Deployment 分开应用，因此更新 slot216 时 slot226 继续工作，更新 slot226 时 slot216 已经恢复工作。

## 连通性与持续上报验证

两个 Worker 都能把 Service DNS 解析到当前 ClusterIP，并成功发送 UDP 报文。正式更新后，接收器观测到：

```text
slot216 fresh=true, reason=heartbeat, queue_depth=0
slot226 fresh=true, reason=heartbeat, queue_depth=0
rejected_reports=0
```

Controller 连续输出：

```text
runtime metrics source=dynamic dynamic_backends=2
```

## 真实队列实验

负载脚本：`scripts/send_batchable_burst.py`。

它按照 Worker 的真实协议构造 UDP 包：

```text
0xAB | tag | flags | flow_id | payload
```

其中 `flags=0x01` 表示可批处理任务。本次向 slot216 发送 5 轮任务，每轮 15 个、间隔 6 秒。单轮没有达到 16 的满批阈值，因此任务会在队列中等待，随后由 5 秒超时触发处理。

Worker 日志确认每轮队列达到 15，平均等待约 5 秒，处理后队列归零。按秒采样 `ComputeTask.status` 得到：

```text
00:55:07  node194 / slot216 / 161 ms
00:55:13  node200 / slot226 / 214.333 ms
00:55:47  node194 / slot216 / 161 ms
```

这证明闭环已经成立：实际任务造成队列压力后，Controller 避开繁忙 Worker；任务处理结束后，调度结果自动恢复。

## 当前状态

- slot216：`1/1 Running`，`net1=10.10.1.216/24`。
- slot226：`1/1 Running`，`net1=10.10.1.226/24`。
- 两个 Worker 都每秒向 Service 上报一次。
- Controller 使用两个真实 Worker 的新鲜指标。
- P4 数据面尚未接入，本阶段只验证后端闭环。

## 本阶段知识点

### 跨 Namespace Service DNS

默认 Namespace 中的 Worker 访问另一个 Namespace 的 Service，需要使用：

```text
<service>.<namespace>.svc.cluster.local
```

### Deployment 的 Recreate 策略

`Recreate` 会先结束旧 Pod，再启动新 Pod，因此单个 Worker 更新期间会短暂停止服务。本实验把两个 Worker 分开更新，避免它们同时中断。

### Controller 轮询与短时状态

Worker 队列等待窗口约 5 秒，而 Controller 每 15 秒读取一次。只查看实验结束后的最终状态可能漏掉中间切换，因此实验使用每秒采样记录状态变化。

## Worker 健康探针

两个真实 Worker 已增加独立的 8081/TCP 健康端口：

- Readiness Probe 请求 /ready，决定 Pod 是否可以接收任务；
- Liveness Probe 请求 /health，持续失败时由 kubelet 重启容器；
- 健康请求由任务 select 主循环直接响应，可以发现主循环失去响应，而不仅是检查 Python 进程是否存在。

详细设计、参数和验证过程见 docs/WORKER_HEALTH_PROBES.md。

## 下一步

Worker 探针、Controller 双周期刷新和最近一次决策快照均已完成，详见 docs/DECISION_TRACE_OBSERVABILITY.md。下一阶段增加带去重边界的 Kubernetes Event 决策历史；之后再设计 P4 路由写入与 Controller 决策之间的单写者边界。
