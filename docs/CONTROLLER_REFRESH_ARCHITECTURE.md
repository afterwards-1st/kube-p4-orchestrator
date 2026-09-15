# Controller 双周期刷新与写入去重

## 要解决的问题

旧 Controller 每 15 秒执行一次完整循环：

1. 列出所有 Namespace 的 Pod；
2. 列出所有 Node；
3. 列出 default Namespace 中的 Worker Pod；
4. 请求 metrics-receiver；
5. 列出全部 ComputeTask；
6. 无条件 PATCH 每个 ComputeTask.status。

Worker 的排队窗口约为 5 秒，所以 15 秒循环可能完全错过一次排队和恢复。如果直接把完整循环缩短到 2 秒，又会把 API Server 压力显著放大。

按当前 3 个 ComputeTask 估算，旧循环每轮包含 4 次 Kubernetes 读取和 3 次 status 写入：

~~~text
15 秒完整轮询：7 × 4 = 约 28 次 Kubernetes API 调用/分钟
2 秒完整轮询： 7 × 30 = 约 210 次 Kubernetes API 调用/分钟
~~~

## 新架构

Controller 0.4.0 把一个循环拆成两个时间尺度：

~~~text
每 2 秒
  ├─ 从 metrics-receiver 读取动态 Worker 指标
  └─ 只重算 minExpectedCompletionTime 任务

每 15 秒
  ├─ 列出全局 Pod 和 Node
  ├─ 列出白名单 Worker Pod
  ├─ 列出 ComputeTask
  └─ 原子替换资源缓存
~~~

因此动态指标 HTTP 请求可以更频繁，但昂贵的 Kubernetes 全量读取仍保持原周期。

稳定状态下，Kubernetes 读取约为：

~~~text
4 次读取/轮 × 4 轮/分钟 = 约 16 次读取/分钟
status 写入 = 仅在规划结果变化时发生
~~~

metrics-receiver 的 HTTP 请求不经过 Kubernetes API Server。

## 为什么称为原子刷新缓存

run_once 会先把 Node、Worker 和 ComputeTask 读取到局部变量。三次读取全部成功后，才一起替换旧缓存。

如果中途某次 Kubernetes API 调用失败：

- 不会留下“新 Node + 旧 Worker”这样的混合快照；
- 原缓存仍保留；
- 下一次 2 秒循环会再次尝试刷新。

这里的“原子”是 Controller 进程内部的一致性边界，不是数据库事务。

## status 写入去重

每次规划后，Controller 比较当前 status 和新 status：

~~~python
if task.get("status", {}) == status:
    return False
~~~

完全相同时不调用 patch_namespaced_custom_object_status。这样心跳每秒变化、但调度结论不变时，不会持续制造：

- API Server 写请求；
- etcd 新版本；
- Watch 事件；
- 无意义日志。

当队列导致 Worker 选择或预计完成时间真正变化时，status 仍会立即更新。

## 配置参数

~~~yaml
RESOURCE_REFRESH_SECONDS: "15"
METRICS_POLL_SECONDS: "2"
~~~

RESOURCE_REFRESH_SECONDS 控制 Kubernetes 资源缓存周期；METRICS_POLL_SECONDS 控制动态指标快循环。

代码仍兼容旧的 POLL_INTERVAL_SECONDS：如果没有设置新资源周期，会把旧值作为回退。但部署清单已经只使用两个含义明确的新参数。

## 自动化测试

新增 tests/test_controller_loop.py，验证：

- 2 秒快循环复用资源缓存；
- 快循环只重算动态策略任务；
- 15 秒到期后重新读取资源；
- 相同 status 不会再次 PATCH。

项目全量测试为 47 项。

## 线上实验

向 slot216 一次发送 15 个可批处理任务，Worker 在 5 秒超时前保持队列：

~~~text
11:13:18  node194 / slot216 / 161 ms
11:13:20  node200 / slot226 / 214.333 ms
11:13:23  node194 / slot216 / 161 ms
~~~

新 Controller 在约 2 秒内避开繁忙的 node194，并在队列清空后快速恢复。ComputeTask.resourceVersion 只在两次真实决策变化时更新，之后保持不变。

## 当前运行状态

- Controller 镜像：kube-orchestrator:0.4.0；
- Controller Pod：Ready，重启 0；
- 动态指标来源：2 个新鲜 Worker；
- kube-orchestrator Namespace：没有 Warning；
- 最短预计完成时间算法：未修改；
- P4 数据面：尚未接入。

## 下一步

调度决策快照已经完成，详见 docs/DECISION_TRACE_OBSERVABILITY.md。下一阶段把关键决策变化写成带去重边界的 Kubernetes Event；之后再推进受控执行与 P4 单写者边界。
