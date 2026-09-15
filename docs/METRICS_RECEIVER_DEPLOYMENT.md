# Worker 动态指标接收器：实现与部署记录

## 本阶段目标

把 Worker 的运行时队列指标从静态 ConfigMap 扩展为动态数据链路：Worker 通过 UDP 上报，`metrics-receiver` 保存每个白名单 Worker 的最新快照，并通过受令牌保护的 HTTP 接口提供查询。

本阶段只完成“接收与查询”，Controller 尚未改为自动读取该接口，Worker 的正式上报地址也尚未切换。

## 数据链路

```text
Worker --UDP/9999--> metrics-receiver Service
                           |
                           +-- 内存中的最新指标快照
                           |
Controller --HTTP/8080--> /v1/backends
```

- `/health`：供 Kubernetes 存活探针和就绪探针使用，不需要令牌。
- `/v1/backends`：返回 Worker 指标，需要 `X-Metrics-Token`。
- 超过 3 秒没有新上报的指标会标记为 `fresh: false`，避免调度器长期使用旧数据。
- 只接受配置在 `WORKER_SLOTS` 白名单中的 backend ID。

## Kubernetes 资源

清单：`manifests/06-metrics-receiver.yaml`。

- Deployment：1 个副本，当前固定在 node194。
- Service：同时暴露 UDP 9999 和 TCP 8080。
- Secret：`metrics-receiver-auth`，令牌不写入清单和代码仓库。
- requests：20m CPU、64Mi 内存。
- limits：100m CPU、128Mi 内存。
- 安全配置：非 root 用户、只读根文件系统、禁止提权、删除全部 Linux capabilities、不挂载 ServiceAccount Token。

## 构建时发现的问题

第一次容器测试出现：

```text
RuntimeError: can't start new thread
```

原因是服务器使用较旧的 Docker 20.10.1，其默认 seccomp 策略与当前 Python 3.11 容器创建线程的方式存在兼容问题。临时关闭 seccomp 后程序可以运行，但降低容器安全级别不适合作为正式方案。

最终把生产服务改成基于 `select` 的单线程事件循环，在同一进程中同时监听 UDP socket 和 HTTP socket。这样不需要修改 Kubernetes 安全策略，也减少了这个轻量服务的并发复杂度。

## 验证结果

- 本机单元测试：30/30 通过。
- 102 单元测试：30/30 通过。
- 镜像：`kube-orchestrator:0.3.0`。
- 102、node194、node200 的镜像 ID 一致。
- Pod：`1/1 Running`，重启次数为 0。
- `/health` 返回 200。
- 未带令牌访问 `/v1/backends` 返回 403。
- 通过 Kubernetes UDP Service 发送合成指标后，授权查询成功读取：slot226、`queue_depth=5`、`fresh=true`。

## 本阶段涉及的 Kubernetes 知识

### Service 可以同时承载 TCP 和 UDP

同一个 Service 可以声明多个端口，每个端口分别指定协议。本项目的 HTTP 查询使用 TCP，Worker 指标上报使用 UDP。

### Readiness 与 Liveness 的作用不同

- Readiness 失败：Pod 暂时不会成为 Service 的可用后端。
- Liveness 失败：kubelet 会重启容器。

本项目两个探针都访问 `/health`；只有 UDP socket 完成绑定后，该接口才返回 200。

### Secret 与 ConfigMap 的边界

运行参数和非敏感基线适合 ConfigMap；访问令牌属于敏感信息，应放在 Secret 中。本项目通过 `secretKeyRef` 把令牌注入环境变量，清单中没有明文值。

### 镜像必须存在于可能运行 Pod 的节点

当前集群没有统一镜像仓库，且清单使用 `imagePullPolicy: IfNotPresent`，因此要先把同一个镜像导入候选节点。分发时使用 SHA-256 校验归档，再核对最终镜像 ID。

## Controller 动态接入（0.3.1）

Controller 现在每 15 秒读取一次 `/v1/backends`，请求超时为 2 秒，并使用与接收器相同的 Secret 进行鉴权。动态指标只覆盖 `queue_depth`，服务速率、批大小和网络时延仍来自 ConfigMap 静态基线。

运行时共有三种明确状态：

- `dynamic`：至少有一个白名单 Worker 的新鲜指标，合并后参与规划。
- `static-no-fresh-data`：接口正常，但没有新鲜指标，使用静态基线。
- `static-fallback`：连接失败、超时、鉴权失败或响应无效，记录 Warning 日志并使用静态基线。

### 动态切换实验

连续向 slot216 上报 `queue_depth=1000`，覆盖一个 Controller 轮询周期：

```text
source=dynamic, dynamic_backends=1
选择结果：node200 / slot226
estimatedMakespanMs：214.333
```

停止上报后，指标超过 3 秒变为 stale。下一轮规划结果自动恢复为：

```text
source=static-no-fresh-data, dynamic_backends=0
选择结果：node194 / slot216
estimatedMakespanMs：161
```

### 服务故障回退实验

把本项目的 `metrics-receiver` 临时缩容到 0。Controller 收到 `Connection refused` 后进入 `static-fallback`，但没有中断调度循环：任务仍为 `Planned`，继续选择 node194，预计完成时间保持 161 ms。接收器恢复为 1 个副本后，Controller 自动恢复访问。

缩容期间旧 Pod 在终止窗口产生过一次 Liveness `connection refused` Event；恢复后的新 Pod 为 `1/1 Running`、重启 0。这是终止过程的历史事件，不是当前故障。

## 下一步

把 node194 和 node200 上现有 Worker 的正式上报地址从旧控制端切换到 Kubernetes UDP Service，并验证真实任务负载变化能够驱动调度决策。P4 转发仍保持在后续阶段，不在当前后端接入中修改。
