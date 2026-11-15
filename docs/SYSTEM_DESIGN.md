# 协同调度系统设计详解

本文以"调度平面决定去向、数据面真实送达"为主线，把系统的每个部分拆开讲：
**它是什么、为什么这么设计、关键机制是什么、放弃了什么**。所有细节与仓库代码
一一对应，可直接作为面试讲解的底稿。组件速览见 README，Agent 单独深讲见
`AGENT_LEARNING_GUIDE.md`。

## 1. 系统总览

~~~
                    ┌──────────────── 控制平面 ────────────────┐
ComputeTask CRD ──► │ Kubernetes Controller（双时间尺度循环）    │
  (orchestrator.    │   ├─ Worker 发现（slot 白名单 + requests） │
   wzt.io)          │   ├─ 调度算法（单节点评分 / 多Worker规划）  │
                    │   ├─ plan/apply 门禁 + Status 去重写回     │
                    │   └─ decisionTrace + Events 可观测        │
                    └───────────┬─────────────────────────────┘
        ┌───────────────────────┼──────────────────────────┐
        ▼                       ▼                          ▼
  NodeScore 单节点        minExpectedCompletionTime    Batch Job（仅 apply）
  （score 证据链）        多 Worker 分配 + flowId       （OwnerRef + affinity）
                                │
                                ▼  flowId→backendId 路由表
                    ┌──────── 数据平面（P4/BMv2）────────────┐
                    │ 114 单写者 HTTP Control API             │
                    │   └─ udp_tag_table：按 flow 转发         │
                    └───────────────┬────────────────────────┘
                                    ▼
                        Worker Pod 216 / 217 / ...（batch server）
                                    │  UDP 遥测（queue_depth 等）
                                    ▼
              metrics-receiver（白名单/过期过滤/HTTP/Prometheus/面板）
                                    └──► 回到控制器，形成闭环
~~~

**一次任务的完整生命周期**：创建 ComputeTask → 控制器快循环内 reconcile →
按策略产出计划写入 Status（plan 门禁下到此为止）→ 若配置了 routing 且门禁全开，
对账并把路由写入 P4 → 任务流进入交换机后按 flowId 被转发到对应 Worker →
Worker 的 batch server 上报遥测 → 控制器下一轮用新队列深度重新规划 →
删除任务时 Finalizer 校验并恢复共享路由表。

## 2. API 层：ComputeTask CRD 设计

**spec 的字段语义与设计动机**：

| 字段 | 作用 | 设计点 |
| --- | --- | --- |
| `image` / `command` | apply 模式的真实负载 | CRD 层面 required；plan 模式不启动，避免"计划对象没有负载语义"的歧义 |
| `execution: plan/apply` | 任务级执行门禁 | 与控制器全局 EXECUTION_MODE 组成**双门禁**（见 §3） |
| `strategy: nodeScore / minExpectedCompletionTime` | 选择调度路径 | 一个 CRD 承载两类问题：放哪个节点 vs 拆给哪组 Worker |
| `taskCount` / `maxWorkers` | 可拆分任务的规模上限 | maxWorkers 同时是枚举上界，控制算法开销 |
| `resources.cpu/memory/gpu` | 总资源需求 | 注意语义是**总和**：单 Worker 不够时自动跨 Worker 组合 |
| `workerSelector` | 可选标签过滤 | 为空时仍受 WORKER_SLOTS 白名单二次约束，两层过滤兜底 |
| `network.minBandwidthMbps / maxLatencyMs` | 网络约束 | **已声明未实现**：规划器遇到会显式返回 Unsupported，绝不静默忽略 |
| `routing.mode` | 任务级 P4 写表门禁 | 与全局 P4_ROUTING_MODE 组成数据面的双门禁（见 §7） |

**status 的设计**：除了 phase/decision，重点是三块——
`selectedWorkers`（pod/node/flowId/backendId/assignedTaskCount/estimatedFinishMs/
allocatedResources，**它就是数据面路由表的来源**）、`routing`（desired/observed/
inSync/applied 对账状态）、`decisionTrace`（每次决策的触发原因与指标指纹）。
Status 以 **Subresource** 暴露：`/status` 单独更新，spec 与 status 的写权限分离，
RBAC 里控制器只有 `computetasks/status` 的 patch/update。

## 3. 控制器：控制循环与安全写回

**双时间尺度循环**（`run_once`，`controller.py`）：

- 快循环：每 `METRICS_POLL_SECONDS`（默认 2s）执行一次，使用缓存；
  只 reconcile `minExpectedCompletionTime` 任务——因为它们的队列深度变化快。
- 慢刷新：每 `RESOURCE_REFRESH_SECONDS`（默认 15s）原子刷新三类缓存
  （节点快照、Worker Pod、任务列表），此时 reconcile **全部**任务。
- 两层 try/except（单任务失败不拖垮整轮、整轮失败不退出进程）。

**为什么不用 Informer/WorkQueue**：实验规模下 List 调用成本可接受，
轮询模型让"资源视图"和"指标视图"的时间尺度可以独立调节——这是这个设计的
核心取舍：**用简单性换实时性控制权**，代价是没有事件驱动的低延迟。
迁移到 controller-runtime 的映射关系在面试里要能讲：List→Informer 缓存、
轮询→WorkQueue 重试、双时间尺度→Resync 周期。

**幂等写回**（`_patch_status_if_changed`）：Status 比较时剔除 decisionTrace，
只有计划真正变化或 trace 指纹（指标源/新鲜度/动态 backend 数）变化才 PATCH。
没有这条去重，2 秒一轮的循环会在 etcd 里制造无意义的写放大。

**Conditions 与 observedGeneration**：每次写回都附加确定性的
Scheduled/ExecutionReady Conditions（reason 如 PlanOnly/AwaitingJob/JobActive），
不引入时钟驱动的状态抖动；observedGeneration 对齐 metadata.generation，
spec 改一次、status 跟进一次。

**plan/apply 双门禁**：`executionAllowed = 全局 EXECUTION_MODE==apply 且
spec.execution==apply`。任何一层是 plan 就绝不创建负载。这是"演示状态默认安全"
的根基：部署即安全，真实动作需要显式双开。

**Job 设计**（apply 路径）：名字 `task-<name>-g<generation>`（超 63 字符时截断+
sha256 前缀），**每个 generation 一个 Job**——spec 修改自动产生新 Job，旧 Job 不复用；
OwnerReference 挂到 ComputeTask（blockOwnerDeletion=False，级联清理但不阻塞删除）；
node affinity 硬钉到选中节点；backoffLimit=0 + restartPolicy=Never，
失败不重试，让状态机清晰可见。

## 4. Worker 发现与数据模型隔离

**发现规则**：列出 `WORKER_NAMESPACE` 的 Pod → `slot` 标签必须在
`WORKER_SLOTS` 白名单 → 剔除 EXCLUDED_NODES → Ready 且匹配 workerSelector 的
才进候选。白名单是第一道闸：控制器绝不会把陌生 Pod 当 Worker。

**容量语义**：Worker 的容量 = 其容器 **requests 之和**（换算为
cpu.core / memory.gb(GiB) / gpu.count）。选 requests 而非 status.capacity：
requests 是用户承诺的配额，语义稳定可测。

**数据模型隔离**（`integration_models.py`）：算法只读
`MultiWorkerRequest`/`WorkerSnapshot` 两个 frozen dataclass，不接触任何
Kubernetes 对象。K8s→模型的转换集中在 `adapters.py` 的纯函数里，
因此**算法可以在没有集群的情况下完整测试**——这也是 91 项测试里大部分
不需要 mock API 的原因。

## 5. 指标链路：双源合并与协议选择

**双源设计**：静态基线（ConfigMap 的 runtime_metrics.json，给服务速率/批次参数）
+ 动态上报（receiver 的 `/v1/backends`）。合并语义是**按 backend 覆盖**：
动态数据只覆盖 queue_depth，其余参数保留静态值。

**可用性降级链**（`load_combined_runtime_metrics`）：
dynamic（有新数据）→ static-no-fresh-data（服务可达但都过期）→
static-fallback（服务不可用，带 error 记录）→ static（未配置）。
控制器把这条链记进 decisionTrace.metricsFreshness，调度质量的"水分"永远可见。

**为什么用 UDP**：遥测高频、单条极小、可容忍丢失（丢一条只影响一次采样的
时效，下个周期就有新的），不值得建 TCP 连接；时效由 receiver 的
`stale_after_seconds`（3s）过期过滤兜底。**鉴权靠白名单**：backend_id 不在
WORKER_SLOTS 内直接拒收（计数 rejected_reports）；Token 只保护 HTTP 读接口
（X-Metrics-Token 或 Prometheus 风格的 Bearer 双兼容）。

**过期过滤**：snapshot 给每条记录算 age_seconds 和 fresh 标志，
控制器只消费 fresh=true 的队列深度——慢 Worker 的陈旧队列不会污染调度。

**C++ agent 的关键设计**（详见学习指南）：cgroup v2 优先（cpu.stat 的
usage_usec 差分 / cpu.max 限额 / memory.current/max），cgroup 不可用回退
`/proc/self/stat`（comm 含空格需定位最后一个右括号）+ VmRSS；CPU 百分比用
steady_clock 差分、报文 ts 用 system_clock；SIGTERM 只做原子置位，主循环补发
`agent_shutdown` 最终上报；selfcheck 不探测外部依赖，避免 receiver 故障被
livenessProbe 放大成 agent 重启风暴。

## 6. 调度算法：两条路径

### 6.1 nodeScore：单节点加权评分

硬过滤（Not Ready / MemoryPressure / DiskPressure / 资源不足）先拒绝并保留
完整 reasons；可调度节点打分：

~~~
score = 0.45×headroom + 0.25×fragmentation + 0.20×health + 0.10×topology
~~~

- headroom：CPU/内存/GPU 的 (free / max(2×需求,1)) 均值——放大概率余量；
- fragmentation：`1 - |CPU剩余比 - 内存剩余比|`——放置后 CPU/内存剩余比例越
  接近，越不容易把异构余量切碎；
- 健康分恒为 1（不健康已被硬过滤拒绝）；
- topology：preferredNodeLabels 命中比例。

**每个候选的 accepted/score/reasons/factors 全部写入
status.candidateEvidence**——评分是可解释的，不是黑盒。

### 6.2 minExpectedCompletionTime：多 Worker 规划

输入校验先行：taskCount>0、网络约束显式 Unsupported（诚实拒绝优于静默忽略）。
核心（`choose_by_completion_time`）：

1. **可行子集全枚举**：size 1..maxWorkers 的所有组合，`_covers` 用 1e-9 容差
   判断"总容量 ≥ 总需求"。6 个 Worker 全量枚举只有 63 个子集，**精确解比
   启发式更可信**；evaluated_combinations 写入 Status 供审计。
2. **资源分配**：主资源（cpu.core/gpu.count/memory.gb 中第一个 >0 的）按
   贪心分配（第一个 Worker 吃满、不够再给下一个），次资源按主资源占比摊派 +
   余量回填。
3. **速率折减**：服务速率 × 分到的 CPU 比例（min(1, assigned/total)）。
4. **任务分配**：最大余数法按有效速率比例分 taskCount，保证整数分配且无
   舍入偏差。
5. **耗时估计**：`排队时间(queue/rate) + 网络时延 + 计算(count/rate) +
   批次等待`。批次等待是关键细节：`count % batch_size != 0` 时补一个
   batch_timeout——不满的批次要等超时才能起批。
6. **择优**：按 (makespan, 总耗时, 节点数, id 序列) 元组序取最优。
   注意已知语义：需求小于单节点容量时主资源全给第一个 Worker，其余 Worker
   速率趋零分不到任务，**单节点就是最优解**；需求超过单节点容量才会跨节点。

产出映射进 Status：selectedWorkers 按 enumerate 顺序获得 **flowId（1..N）**，
**backendId 来自 slot 标签**——这两个字段就是数据面路由表的 (flow_id, backend_id)。

## 7. 数据平面：P4/BMv2 的安全写表

**角色模型**：控制器永远不直接连 BMv2 Thrift，只走 114 的 HTTP Control API；
写表前必须确认 `control_owner=="server-114"` 且 `control_mode=="external"`——
**单写者校验**：如果 114 不处于外部单写者模式，说明有别的控制面在管表，拒绝写。
查询结果带 TTL 缓存（P4_STATUS_CACHE_SECONDS），replace 成功后主动失效缓存
（写响应不含权威 control_mode，必须重读）。

**三层写表门禁**：全局 `P4_ROUTING_MODE==apply` + 任务 `spec.routing.mode==apply`
+ **owner 绑定**（任务名必须等于配置的 P4_ROUTE_OWNER）。三层全开才允许写，
任何一层是 plan 都只做对账。

**对账循环**（`_p4_routing_status`）：desiredRoutes（来自调度结果）与
observedRoutes（114 实际表项，按 flowId 规范化排序）比较得 inSync；
允许写且不同步时才 replace；**首次写之前把 observedRoutes 存为
previousRoutes**——这是删除恢复的锚点；写后**回读验证**并重算 inSync。
状态机：Planned → Invalid（路由非法）/ Unavailable（114 不可用或非单写者）/
Failed（写失败）→ DryRunValidated（dry-run 下对账成功）→ InSync / Applied。

**删除恢复与漂移检测**（`p4_lifecycle.py`）：配置了 routing 的任务自动挂
Finalizer。删除时只有同时满足"applied 过 + 仍是 route owner + 记录过
previousRoutes + **当前表项仍等于自己写的值**"才恢复——最后这条是关键：
如果表已经被别的 actor 改了，恢复反而会破坏别人的配置，此时只记录
Skipped 并直接放行 Finalizer。恢复后还要回读验证，确认才返回 Restored。
共享资源的"谁负责收拾"问题在这里被显式建模，而不是事后人工兜底。

## 8. 可观测性：让每次决策可审计

- **decisionTrace**：每次写 Status 附带 reason（8 个枚举：initial-plan /
  phase-changed / selected-workers-changed / estimated-completion-changed /
  metrics-state-changed …）+ metricsSource + metricsFreshness +
  dynamicBackendCount + queueDepthByBackend。**为什么方案变了，永远可以回答**。
- **K8s Events**：以 (phase, selectedNode, selectedWorkers, metricsSource,
  freshness, dynamicBackendCount) 为去重键，只有真正变化才建 Event，
  reason 映射为 SchedulingPhaseChanged / WorkerSelectionChanged 等语义事件，
  Unschedulable 标 Warning。
- **Prometheus 指标**：accepted/rejected 用 Counter（单调累计），
  queue_depth/fresh 用 Gauge（瞬时值）；掉线的 backend 也输出 0，防止
  Prometheus 序列断开；Bearer Token 对接 ServiceMonitor。
- **可视化面板**：实时模式消费 /v1/backends；演示模式本地仿真用于无集群展示；
  调度模拟器与 Python 算法同口径，选中方案联动 P4 动画。

## 9. 交付与 CI

**Helm 决策**：CRD 放 `crds/`（Helm 只安装不接管升级——CRD 是集群级共享资源，
升级语义应显式管理）；所有对象名带 release 前缀避免多实例冲突；安全基线
（非 root 65532 / drop ALL / 只读根文件系统）收敛为单个模板片段，三组件复用；
auth 支持 Chart 建密钥或引用 existingSecret。

**CI 四段**：双语言单测 → Helm lint/渲染 → kind e2e → main 分支发 GHCR。
e2e 的断言设计刻意选了**恰好需要两个 Worker 合力**的资源需求（单 Worker 不够），
一次验证"跨 Worker 组合调度"这条最难造假的路径，再顺带断言指标链路 fresh
和 /metrics 序列存在。假 Worker 用 busybox httpd 提供 /health /ready 探针，
容量靠 requests 声明——不需要真实 batch server 也能验证调度语义。

**安全基线**：UDP 白名单、HTTP Token、P4 Token 三层各管一段；
plan/apply 与路由 owner 三门禁；最小权限（receiver/agent 不挂 SA token，
controller 只给需要的 verbs）。

## 10. 关键取舍一览

| 决策 | 选了什么 | 放弃了什么 | 为什么 |
| --- | --- | --- | --- |
| 控制循环 | 轮询 + 双时间尺度缓存 | Informer 事件驱动 | 实验规模成本可控，两个节奏独立可调；迁移路径清晰 |
| 写回 | patch-if-changed + trace 指纹 | 每轮全量写 | 消除 2s 循环的写放大 |
| 调度 | 小规模精确枚举 | 启发式/DP | 63 个子集内精确解更可信；规模扩大时换 Top-K 预筛选（README 已声明） |
| 遥测 | UDP + 白名单 + 过期过滤 | TCP/TLS | 可容忍丢失、时效优先；白名单即鉴权够实验环境 |
| 指标 | 双源合并（静态参数+动态队列） | 纯动态 | 服务速率等慢变量没必要高频上报 |
| P4 | HTTP 单写者 + 三门禁 + 快照恢复 | 直连 Thrift 多写者 | 共享表的安全边界高于便利性 |
| 删除 | Finalizer + 漂移检测 | 无条件恢复 | 别人改过的表不能瞎恢复 |
| 交付 | Helm + kind e2e + 双语言 CI | 手工 apply | 面向工程化交付而非一次性实验 |

## 11. 已知边界（面试时主动讲）

- 控制器无 leader election，多副本会双写（Deployment 固定 1 副本 + Recreate 规避）；
- 精确枚举适合 ≤10 Worker，更大规模需要 Top-K 预筛选或分支定界；
- 网络约束（带宽/时延）已建模但未接拓扑验证，显式拒绝而非静默忽略；
- P4 写表是 clear+add 原子替换，粒度是整表；单表项增量写与回读补偿在路线图上；
- 高可用/TLS/多交换机未覆盖。

每一条边界都有对应的演进方向——**知道自己没做什么，和知道自己做了什么同样重要**。
