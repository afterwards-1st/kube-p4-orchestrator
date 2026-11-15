# 03 · 代码跟读指南

按下面的顺序读，每个模块给出**文件 → 重点函数 → 读完要能回答的问题**。
深层的机制论述与取舍见 `docs/SYSTEM_DESIGN.md`，C++ 细节与 24 道自测题见
`docs/AGENT_LEARNING_GUIDE.md`。本篇只做"怎么读、读什么"的路线图。

## 路线图：先闭环，再深入

~~~
第 1 圈（数据流主线，约 2 小时）
  app/integration_models.py        →  调度的输入输出模型（纯数据类）
  app/adapters.py                  →  K8s 对象 ↔ 模型的纯转换
  app/policies/completion_time_scheduler.py  →  调度算法核心
  app/multi_worker_planner.py      →  把 CRD/Worker/算法串成纯函数规划
第 2 圈（控制器，约 2 小时）
  app/controller.py                →  双时间尺度循环 + 幂等写回 + 双门禁
  app/runtime_metrics.py           →  双源指标合并与降级
  app/p4_control.py / p4_lifecycle.py → 数据面客户端与删除恢复
第 3 圈（指标链路，约 2 小时）
  app/metrics_receiver.py          →  UDP 白名单摄入 / HTTP / Prometheus / 面板伺服
  agent/src/resource_reader.cpp    →  cgroup v2 + /proc 采集
  agent/src/reporter.cpp + main.cpp→  UDP 上报 + 信号处理
第 4 圈（交付，约 1 小时）
  charts/kube-orchestrator/*       →  Helm 模板化与安全基线
  scripts/e2e_kind.sh + .github/workflows/ci.yml → 端到端断言
~~~

## 第 1 圈：调度算法（面试深挖概率最高的部分）

### `app/integration_models.py`（50 行，先读它定词汇）
- `CPU_KEY/MEMORY_KEY/GPU_KEY`：算法字典键（cpu.core/memory.gb/gpu.count）。
- `MultiWorkerRequest`：任务需求（资源是**总和**、taskCount、maxWorkers、策略）。
- `WorkerSnapshot`：Worker 容量快照（requests 换算值 + labels + ready）。
- **要能回答**：为什么算法层不直接吃 Kubernetes 对象？（换快照来源不用改算法；
  测试不需要 mock 集群。）

### `app/adapters.py`
- `parse_cpu_cores / parse_memory_gib`：K8s quantity → 核数 / GiB（Decimal 精度）。
- `worker_snapshot_from_pod`：容量=容器 requests 之和；ready=Running+Ready 条件。
- `completion_plan_to_status`：算法输出 → Status（**flowId 从 1 起 enumerate，
  backendId 来自 slot 标签——这就是数据面路由表的来源**）。
- **要能回答**：requests 为什么比 capacity 更适合当容量口径？

### `app/policies/completion_time_scheduler.py`（核心中的核心）
- `choose_by_completion_time`：size 1..maxWorkers 全枚举；`_covers` 容量校验
  （1e-9 容差）；评分元组 (makespan, 总耗时, 节点数, id 序列)。
- `_resource_allocations`：主资源贪心 → 次资源按主资源占比摊派 → 余量回填。
- `_largest_remainder`：按有效速率整数分任务，无舍入偏差。
- `_evaluate`：排队 + 网络时延 + 计算 + **批次等待修正**（count % batch_size != 0
  补一个 batch_timeout）。
- **要能回答**：为什么 6 个 Worker 用精确枚举而不是启发式？需求小于单节点容量时
  为什么单节点就是最优？makespan 和总耗时各代表什么？

### `app/multi_worker_planner.py`
- 输入校验三连：策略必须匹配、taskCount>0、**网络约束显式 Unsupported**。
- **要能回答**：为什么不静默忽略没实现的约束？（诚实语义优于静默错误。）

## 第 2 圈：控制器（`app/controller.py`）

- `run / run_once`：2s 快循环（只跑动态任务）+ 15s 全量缓存刷新；两层 try/except。
- `snapshots / worker_pods`：节点用量=全集群 Pod requests 累加；Worker 白名单过滤。
- `_patch_status_if_changed`：剔除 decisionTrace 比较 + trace 指纹变化检测。
- `_decision_trace / _decision_change_reason`：8 种 reason 枚举 + 指标指纹。
- `_lifecycle_conditions`：确定性 Conditions（PlanOnly/AwaitingJob/JobActive…）。
- `_execution_allowed`：**双门禁**——全局模式与任务模式同时为 apply。
- `_p4_routing_status`：三层门禁 → 单写者校验（control_owner/server-114）→
  desired/observed 对账 → previousRoutes 快照 → 写后回读验证 → 状态机
  （Planned/Invalid/Unavailable/Failed/DryRunValidated/InSync/Applied）。
- `ensure_job`：按 generation 命名 + OwnerReference + node affinity + backoffLimit 0。
- `_reconcile_deletion` + `app/p4_lifecycle.py`：Finalizer + **漂移检测**
  （表被别人改过就不恢复）+ 恢复后回读验证。
- **要能回答**：为什么不用 Informer/WorkQueue？patch-if-changed 防止了什么？
  共享路由表的删除语义为什么是"漂移则不恢复"？

## 第 3 圈：指标链路

### `app/metrics_receiver.py`
- `WorkerMetricStore.ingest`：JSON 校验 + 白名单（不认识直接拒，计数 rejected）。
- `snapshot`：age_seconds/fresh（stale_after 3s）。
- `metrics_exposition`：Prometheus 文本格式——accepted/rejected 是 Counter，
  queue_depth/fresh 是 Gauge；**掉线的 backend 也输出 0**（防序列断开）。
- `make_http_handler`：/health /v1/backends（X-Metrics-Token 或 Bearer）
  /metrics /dashboard；单线程 select 循环同时服务 UDP 与 HTTP。
- **要能回答**：UDP 为什么不带 Token？fresh=False 的报告去哪了？

### `agent/`（跟读顺序）
1. `src/main.cpp`：信号处理只做原子置位；--selfcheck 分发。
2. `src/config.cpp`：env 注入（依赖注入最小形式）+ validate fail-fast。
3. `src/json_writer.cpp / flat_json.cpp`：零依赖序列化/解析；from_chars 严格数字。
4. `src/workload_stats.cpp`：状态文件三分降级（缺文件/坏文件/坏字段）。
5. `src/resource_reader.cpp`：**本项目最有面试密度**——cgroup v2 文件族、
   CPU% 差分（steady_clock）、/proc/self/stat 定位最后一个右括号、两级回退。
6. `src/reporter.cpp`：UDP sendto 尽力而为 + DNS 失败重解析 + Pimpl + RAII close。
7. `src/agent.cpp`：单线程主循环、20ms 步进退出、30s 日志节流、selfcheck
   **故意不探测 receiver**（防故障放大）。
- **要能回答**：24 道自测题（见 `docs/AGENT_LEARNING_GUIDE.md` 第 5 章）。

## 第 4 圈：交付

- `charts/kube-orchestrator/templates/_helpers.tpl`：fullname 规则 + 安全基线片段。
- `templates/controller-deployment.yaml`：env 全景（每个环境变量对应一个设计点）。
- `templates/node-agent-daemonset.yaml`：DS + tolerations + exec 探针。
- `scripts/e2e_kind.sh`：build_and_load → helm --wait → 断言链 → **注解诊断体系**
  （annotate/log_step/fail/ERR 陷阱——CI 排障方法论见 `04-ci-debug-log.md`）。
- `.github/workflows/ci.yml`：四个 job 的依赖关系与权限（packages:write 仅 images）。

## 自测节奏

每读完一圈，合上仓库回答对应模块"要能回答"的问题；答不上回读，再答不上查
`docs/SYSTEM_DESIGN.md` 对应章节。全部通过后进入 `05-interview-cheatsheet.md`。
