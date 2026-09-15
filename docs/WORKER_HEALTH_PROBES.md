# Worker 健康探针设计与部署

## 本阶段目标

为 node194 的 slot216 和 node200 的 slot226 增加 Kubernetes 健康探针，让集群能够区分：进程尚未就绪、可以接收任务，以及进程存在但任务事件循环已经失去响应。

本阶段没有修改最短预计完成时间算法，也没有接入 114/P4 数据面。

## 为什么不能直接探测 UDP 9000

Worker 使用“9000/UDP”接收任务，而 Kubernetes 原生的 HTTP、TCP Socket 和 Exec 探针中没有 UDP Socket 探针。

只检查 Python 进程是否存在也不够：进程可能仍在运行，但主循环已经阻塞。Worker 因此新增“8081/TCP”健康端口，并让现有 select 主循环亲自处理请求。主循环不能运行时，健康请求也不会得到响应。

~~~text
9000/UDP  任务数据
9999/UDP  Worker -> metrics-receiver 指标上报
8081/TCP  Kubernetes -> Worker 健康检查
~~~

## 两类探针

### Readiness Probe

“GET /ready”成功表示 Pod 可以接收任务。失败时 Pod 仍运行，但被标记为 NotReady；如果它位于 Service 后面，还会被移出 Service Endpoints。

~~~yaml
initialDelaySeconds: 2
periodSeconds: 5
timeoutSeconds: 1
failureThreshold: 3
~~~

即启动 2 秒后开始，每 5 秒检查一次，单次最多等待 1 秒，连续失败 3 次才判定未就绪。

### Liveness Probe

“GET /health”成功表示事件循环仍能响应。连续失败达到阈值时，kubelet 会重启容器。

~~~yaml
initialDelaySeconds: 5
periodSeconds: 10
timeoutSeconds: 1
failureThreshold: 3
~~~

短暂计算不会立刻触发重启；只有约 30 秒持续无法响应才会被判定为存活失败。

## Worker 实现

“workers/batch_server_report_sim.py”同时监听任务 UDP Socket 和健康 TCP Socket：

~~~python
readable, _, _ = select.select(
    [self.sock, self.health_sock], [], [], 0.05
)
~~~

健康请求由任务主循环处理，没有创建后台健康线程。因此探针验证的是核心事件循环，而不只是另一个健康线程或 Python 进程还活着。

“/ready”和“/health”返回 HTTP 200，其他路径返回 HTTP 404，避免把任意 URL 误判为健康。

## 顺序部署

~~~bash
bash scripts/deploy-workers.sh
~~~

脚本依次更新 Worker 脚本 ConfigMap、slot216、slot226，并在更新下一个 Worker 前等待当前 Deployment 可用。因为两个 Deployment 使用 Recreate，这可避免两个 Worker 同时不可用。

## 验证命令

~~~bash
kubectl get pods -n default -l 'slot in (216,226)' -o wide

kubectl exec -n default <pod-name> -- python3 -c   "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8081/ready').read().decode())"

kubectl get events -n default   --field-selector involvedObject.name=<pod-name>   --sort-by=.lastTimestamp
~~~

## 本次实测结果

slot216 与 slot226 均为 1/1 Running、重启 0；两个健康路径都返回 HTTP 200，新 Pod Events 中没有 Unhealthy。Controller 持续输出“runtime metrics source=dynamic dynamic_backends=2”。

这说明新增探针没有破坏任务端口、Multus 网络或动态指标闭环。

## 下一步

Controller 双周期刷新已经完成，详细设计和实测结果见 docs/CONTROLLER_REFRESH_ARCHITECTURE.md。下一阶段为调度决策补充可解释、可审计的观测信息。
