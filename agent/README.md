# C++ 节点指标 Agent（node-agent）

面向容器平台形态的节点 agent：以 **DaemonSet** 方式在每个 Worker 节点运行一个实例，
采集本机容器资源用量与工作负载统计，按 Worker 槽位通过 UDP 上报给
`metrics-receiver`。使用 C++17 标准库实现，无第三方依赖。

## 定位

- 与 `app/metrics_receiver.py` 的 UDP 契约完全兼容：报文字段口径与
  `workers/batch_server_report_sim.py` 一致，receiver 与调度链路无需区分报文来源。
- 在 Python 模拟器上报的基础上，补充了**真实容器资源观测**：cgroup v2 的内存用量/限额、
  CPU 累计用量与限额，由相邻采样差分得到 CPU 百分比。
- 对应容器平台里 node agent / DaemonSet 的标准形态：非 root、drop ALL capabilities、
  只读根文件系统、livenessProbe 用自带 `--selfcheck`。

## 能力清单

- UDP JSON 上报：字段与 Python 模拟器一致（`backend_id`、`queue_depth`、`reason`、
  `ts`、`completed_tasks`、`total_batches`、`last_flush_size`、`last_flush_reason`、
  `last_process_time_ms`、`last_avg_wait_ms`、`last_batch_efficiency`）。
- 容器资源采集（额外字段）：`container_cpu_percent`、`container_mem_usage_bytes`、
  `container_mem_limit_bytes`、`cgroup_version`、`resource_source`、`agent_source`。
- 采集路径两级回退：cgroup v2（`cpu.stat`/`cpu.max`/`memory.current`/`memory.max`）
  → `/proc/self/stat` + `VmRSS`；两者都不可用时仅上报工作负载字段。
- 工作负载统计从本地状态文件读取（由批处理服务写出）；未配置或文件缺失时上报空闲心跳。
- 优雅停机：SIGTERM/SIGINT 后补发一条 `reason=agent_shutdown` 的最终上报再退出。
- 自检模式：`node_agent --selfcheck` 输出 JSON 诊断（配置、状态文件、资源采样），
  被 Docker HEALTHCHECK 与 K8s livenessProbe 复用；只校验本机状态，不依赖 receiver 可达。
- 上报尽力而为：失败只计数不重试，时效性由 receiver 的 `stale_after_seconds` 过期过滤兜底；
  DNS 解析失败会在后续发送时自动重试（覆盖 receiver 尚未就绪的场景）。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| AGENT_BACKEND_IDS | 必填 | 逗号分隔的 Worker 槽位，必须是 receiver `WORKER_SLOTS` 白名单的子集；一个节点可承载多个槽位，agent 为每个编号各发一份报文 |
| AGENT_BACKEND_ID | 无 | 单槽位的兼容写法，与 AGENT_BACKEND_IDS 二选一 |
| AGENT_REPORT_HOST | metrics-receiver.kube-orchestrator.svc | receiver 地址 |
| AGENT_REPORT_PORT | 9999 | receiver UDP 端口 |
| AGENT_REPORT_INTERVAL_MS | 200 | 上报周期，与 Python 模拟器默认一致 |
| AGENT_STATUS_FILE | 空 | 可选，本地工作负载状态 JSON（扁平对象） |
| AGENT_CGROUP_ROOT | /sys/fs/cgroup | cgroup v2 挂载根，测试时可重定向 |

## 本地构建与测试（Linux/WSL2）

~~~
cmake -S agent -B agent/build -DCMAKE_BUILD_TYPE=Release
cmake --build agent/build -j
ctest --test-dir agent/build --output-on-failure
~~~

端到端冒烟（同时拉起 receiver 与 agent，验证 UDP 链路与透传字段）：

~~~
bash agent/tests/smoke_local.sh
~~~

自检与运行示例：

~~~
AGENT_BACKEND_IDS=216,217 ./agent/build/node_agent --selfcheck
AGENT_BACKEND_IDS=216,217 AGENT_REPORT_HOST=192.168.x.x ./agent/build/node_agent
~~~

## 镜像与部署

~~~
docker build -t kube-orchestrator/node-agent:0.1.0 agent/
kubectl apply -f agent/deploy/daemonset.yaml
~~~

镜像为多阶段构建：`debian:bookworm-slim` 工具链（构建期跑全部单元测试）→
`distroless/base-debian12` 运行时。部署前把 DaemonSet 里的 `AGENT_BACKEND_IDS`
改成该节点实际承载的槽位。

## 设计取舍

- **为什么 UDP**：指标高频、单条很小、可容忍丢失，丢失一条只影响一次采样的时效，
  由 receiver 的过期过滤自然兜底；不值得为它建立 TCP 连接和重传队列。
- **为什么 UDP 不带 Token**：receiver 对 UDP 报文的鉴权依赖 `WORKER_SLOTS` 白名单
  （不在名单内的 backend_id 直接拒收）；Token 保护的是 HTTP 查询接口。
- **为什么单线程循环**：采集和上报都是微秒级轻操作，一个定时循环足够，
  把并发复杂度留给真正需要的组件；CPU 百分比依赖相邻两次采样的墙钟差分，
  单线程天然保证采样间隔的确定性。
- **文件读取与解析分离**：所有解析函数接收字符串内容而非路径，纯函数可单测，
  文件读取只留一层薄封装。
