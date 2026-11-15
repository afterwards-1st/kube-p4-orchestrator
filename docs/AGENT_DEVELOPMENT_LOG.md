# C++ 节点指标 Agent 开发记录

本文档记录 node-agent（`agent/` 目录）从需求定位、接口调研、实现、测试到部署文件
的完整开发过程，供后续维护与面试讲解使用。开发日期：2026-10-27。

## 1. 目标与约束

- 目标：为容器运维/开发方向补齐 C++ 侧证据，把项目叙事从"纯 Python 控制器"扩展为
  "K8s 自定义控制器 + C++ 节点 agent + P4 数据面联动"。
- 约束（明确的最小改动原则）：**不对现有 Python 控制器、调度算法、P4 链路做任何重构**，
  全部改动为加法；agent 必须与现有 `metrics-receiver` 的 UDP 契约兼容，
  receiver 只允许向后兼容的小幅透传扩展。

## 2. 接口调研结论

开发前通读了链路两端代码，确定以下契约：

| 结论 | 出处 |
| --- | --- |
| UDP 报文为扁平 JSON，字段：`backend_id`(数字)、`queue_depth`、`reason`、`ts`、`completed_tasks`、`total_batches`、`last_flush_size`、`last_flush_reason`、`last_process_time_ms`、`last_avg_wait_ms`、`last_batch_efficiency` | `workers/batch_server_report_sim.py` 的 `BatchServer.report()` |
| receiver 只抽取已知字段，未知字段解析时不报错但被丢弃；`backend_id` 经 `int(str(...))` 归一后必须命中 `WORKER_SLOTS` 白名单 | `app/metrics_receiver.py` 的 `WorkerMetricStore.ingest()` |
| receiver 的下游 `metrics_from_worker_reports()` 只消费 `queue_depth`，因此给快照附加新字段不影响调度链路 | `app/runtime_metrics.py` |
| UDP 无 Token 鉴权，白名单即鉴权；Token 只保护 HTTP 查询接口 | `app/metrics_receiver.py` |
| 部署约定：namespace `kube-orchestrator`、非 root `runAsUser: 65532`、drop ALL capabilities、只读根文件系统、readiness/liveness 探针、资源 requests/limits | `manifests/06-metrics-receiver.yaml` |

## 3. 架构与文件清单

~~~
agent/
  CMakeLists.txt            构建定义：agent_logic(可移植核心) + agent_node(POSIX 运行时)
  Dockerfile                多阶段构建：bookworm 工具链（构建期跑全部单测）→ distroless 运行时
  README.md                 agent 使用说明与设计取舍
  deploy/daemonset.yaml     DaemonSet 部署（安全上下文对齐 receiver 约定）
  include/agent/
    json_writer.hpp         紧凑 JSON 对象序列化器（保序、转义）
    flat_json.hpp           扁平 JSON 解析器 + 严格数字解析
    file_util.hpp           文本文件读取、trim
    workload_stats.hpp      工作负载统计结构与状态文件读取
    resource_reader.hpp     cgroup v2 / proc 资源采样器与纯解析函数
    config.hpp              环境变量配置与校验
    payload.hpp             报文构造、epoch 时间、字符串列表序列化
    reporter.hpp            UDP 上报器（Pimpl 封装 POSIX socket）
    agent.hpp               NodeAgent 主循环与 selfcheck
  src/                      上述头文件对应实现 + main.cpp（信号处理与入口）
  tests/
    test_agent.cpp          单元测试（自带微型断言框架，零第三方依赖）
    smoke_local.sh          端到端冒烟：receiver + agent 真实链路 + 断言校验
~~~

分层动机：`agent_logic` 只依赖标准库，在任意平台可编译可测试；
POSIX socket 与信号循环收在 `agent_node` 和 `main.cpp`，
使核心逻辑的单元测试不依赖操作系统特性。

## 4. 关键设计决策

1. **报文口径与 Python 模拟器逐字段对齐**：receiver 与调度链路无需感知报文来自
   C++ 还是 Python，替换部署时风险最低。
2. **新增字段白名单透传**：agent 附加的 `container_cpu_percent`、
   `container_mem_usage_bytes`、`container_mem_limit_bytes`、`cgroup_version`、
   `resource_source`、`agent_source` 六个字段，在 receiver 的 `ingest()` 中按白名单
   合入快照。数值字段做 `max(0.0, float(...))` 钳制与类型校验；未上报这些字段的
   backend 快照保持原样，对存量测试与消费者完全向后兼容。
3. **cgroup v2 优先、/proc 回退**：容器内挂载 private cgroup namespace 时，
   `/sys/fs/cgroup` 即本容器根，读 `cpu.stat`（usage_usec）、`cpu.max`（限额核数）、
   `memory.current`、`memory.max`；cgroup 不可用时回退 `/proc/self/stat`
   （utime+stime，注意 comm 字段可含空格，需定位最后一个右括号后按序取第 12、13 个
   token）与 `/proc/self/status` 的 `VmRSS`。CPU 百分比由相邻采样差分计算：
   `100 × Δusage_usec / Δwall_time`，用 `steady_clock` 计墙钟、报文 `ts` 用
   `system_clock`。
4. **上报尽力而为、失败计数不重试**：指标链路可容忍丢失，时效由 receiver 的
   `stale_after_seconds` 过期过滤兜底；DNS 首次解析失败（receiver 未就绪）在后续
   发送时自动重试解析，覆盖 Pod 启动顺序问题。
5. **优雅停机**：SIGINT/SIGTERM 在信号处理函数里只做原子写，主循环以 20ms 步进
   检查退出标志，置位后补发 `reason=agent_shutdown` 最终上报再退出；运行日志按
   30 秒节流输出统计。
6. **selfcheck 复用为探针**：`--selfcheck` 校验配置、读一次资源采样并输出 JSON 诊断，
   不发任何报文、不依赖 receiver 可达——避免 receiver 故障导致 livenessProbe 误杀 agent。
7. **自写微型 JSON 序列化/解析而非引入第三方库**：报文是一层扁平对象，
   自写约 300 行可控、可测（含转义回环、非法输入用例），并保持零依赖构建。
   已知边界：解析器不支持嵌套与数组，仅用于本仓库组件间的状态文件。

## 5. 测试与验证结果

| 验证项 | 结果 |
| --- | --- |
| C++ 单元测试（JSON 转义/序列化、扁平解析及 9 类非法输入、状态文件映射、cgroup 纯解析、/proc/stat 解析、配置校验、报文构造回环） | 109 项检查，0 失败 |
| WSL2 实机 selfcheck | `resource_source: cgroup-v2`，读出真实内存用量 |
| 全部 Python 测试（receiver 改动后回归） | 85 项，全部通过 |
| 端到端冒烟（`agent/tests/smoke_local.sh`）：receiver + agent 真实 UDP 链路 | 30+ 份上报、0 拒绝；216/217 两槽位快照含全部容器资源字段且 `fresh: true`；断言脚本全部通过 |
| 优雅停机验证 | SIGTERM 后补发 `agent_shutdown`，日志 `已发送=34 失败=0` |
| DaemonSet YAML 合法性 | PyYAML 解析通过，kind=DaemonSet |

## 6. 部署

~~~
docker build -t kube-orchestrator/node-agent:0.1.0 agent/
kubectl apply -f agent/deploy/daemonset.yaml
~~~

- 运行镜像为 `distroless/base-debian12`，非 root 65532、无 shell；
  `HEALTHCHECK` 与 K8s livenessProbe 均使用 exec 形式的 `--selfcheck`。
- 部署前必须把 DaemonSet 的 `AGENT_BACKEND_IDS` 改为该节点实际承载的槽位，
  且必须是 receiver `WORKER_SLOTS` 的子集，否则报文被白名单拒收。
- agent 资源占用极小（requests 5m/16Mi），且不写任何本地文件，
  与 `readOnlyRootFilesystem` 兼容。

## 7. 已知边界与后续方向

- 状态文件未配置时 `queue_depth` 恒为 0（空闲心跳），仅用于链路联调；
  接真实批处理服务时由其写出状态 JSON 即可。
- CPU 百分比是相对单核的口径（可超过 100）；限额核数单独上报，消费方可自行换算。
- 尚未覆盖：TLS/鉴权加固（UDP 白名单已足够实验环境）、多节点灰度验证、
  与 controller 的 P4 路由联动验证（agent 只影响指标面，不影响数据面逻辑）。
- 可能的演进：把 cgroup 观测扩展到 network/IO 子系统；引入 eBPF 采集更底层的
  每容器指标；为 receiver 增加容器资源的展示页。

## 8. 改动清单汇总

- 新增 `agent/`（15 个源文件/头文件 + CMake + Dockerfile + DaemonSet + README +
  2 个测试脚本）。
- 修改 `app/metrics_receiver.py`：新增 6 个容器资源字段的白名单透传（约 15 行，
  向后兼容，85 项既有测试全部保持通过）。
- 修改 `README.md`：能力清单、架构图、仓库结构、本地验证补充 agent 相关内容。
- 修改 `.gitignore`：忽略 `agent/build/` 构建产物。
