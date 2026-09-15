# KubeOrchestrator：Kubernetes 与 P4 协同计算调度系统项目说明书

## 1. 文档目的与当前版本

本文档完整介绍 KubeOrchestrator 的问题背景、总体架构、核心实现、真实验证、安全边界和后续方向。

本公开版本将原先分散的 Kubernetes 控制器与 P4 控制器整理为一个仓库：

- Kubernetes 控制器、清单、脚本和文档位于仓库根目录。
- 114 候选 P4 控制器、路由快照和 systemd 文件位于 p4-controller 目录。

Kubernetes Controller 当前镜像版本为 0.5.1。项目默认处于安全演示状态：任务执行为 plan、P4 写入为 plan，114 上运行 dry-run P4 API；真实执行和真实 P4 写入都必须经过显式门禁。

本文档不记录服务器密码、令牌或其他敏感信息。

## 2. 项目一句话说明

这是一个面向批处理或 AI 计算任务的云网协同调度系统。用户提交 ComputeTask 后，Kubernetes Controller 会根据 Worker 的可用资源、实时队列压力、处理速率、网络时延和批处理行为，生成多 Worker 任务分配方案；在受控模式下，该方案还可以下发到 P4/BMv2 数据面，使不同任务流被转发到被选中的计算后端。

核心闭环如下：

    ComputeTask
      -> Kubernetes Controller
      -> Worker 资源与动态指标
      -> 最短预计完成时间规划
      -> ComputeTask Status
      -> 114 HTTP 控制 API
      -> BMv2 udp_tag_table
      -> 计算 Worker Pod

## 3. 要解决的问题

普通 Kubernetes 调度器主要负责把 Pod 放到合适的 Node，但本项目关注的是更上层的“计算任务如何拆分并分发到多个计算后端”：

1. 不同后端的空闲 CPU、内存和处理速度不同。
2. 后端当前队列长度不同，空闲节点不一定最快完成任务。
3. Worker 有批处理逻辑，最后一个非满批可能需要等待超时。
4. 网络路径会产生额外时延。
5. 选出计算后端后，网络必须把不同任务流准确送达该后端。
6. P4 设备不能被多个控制器同时修改，否则会出现配置竞争。

因此，本项目把计算调度和网络转发连接起来，但仍保持 Kubernetes、P4 控制面和数据面之间清晰的职责边界。

## 4. 实验环境与角色

| 角色 | 主要职责 |
| --- | --- |
| 102 | Kubernetes 控制面、KubeOrchestrator Controller、ComputeTask 和指标接收器 |
| 194 | 计算 Worker 所在 Kubernetes 节点，实验主要后端之一为逻辑编号 216 |
| 200 | 计算 Worker 所在 Kubernetes 节点，实验主要后端之一为逻辑编号 226 |
| 204 | 任务发送端，向 P4 虚拟地址发送 UDP 任务流 |
| 114 | BMv2/P4 交换机、P4 控制 HTTP API、旧控制器和候选控制器 |

这些角色不是简单的“多台服务器”，而是一个小型云网协同实验环境：102 做决策，114 做网络表项控制，194/200 做计算，204 发起实际流量。

## 5. 总体架构

### 5.1 控制路径

    用户或 YAML
        -> ComputeTask CRD
        -> Controller 读取 Kubernetes 资源缓存
        -> Controller 读取 metrics-receiver 动态指标
        -> 多 Worker 调度算法
        -> ComputeTask.status
        -> 可选：创建 Kubernetes Job
        -> 可选：调用 114 P4 HTTP API

### 5.2 数据路径

    ubuntu-204
        -> net1
        -> P4 虚拟地址 10.10.1.200:9000
        -> BMv2 根据 flow_id 查询 udp_tag_table
        -> 对应计算 Worker Pod
        -> Worker 排队、批处理和完成任务

### 5.3 为什么控制路径与数据路径必须分离

Controller 不直接转发数据包，也不直接操作 BMv2 Thrift。它只计算“应该把哪一个 flow 送到哪一个逻辑后端”；114 上的唯一 P4 控制器才负责把逻辑后端编号转换为 P4 端口并写入表项；BMv2 则在数据面高速转发数据包。

这种分层使调度逻辑、P4 设备控制和真实转发能够独立验证和故障隔离。

## 6. Kubernetes 资源设计

### 6.1 Namespace 与 RBAC

项目使用 kube-orchestrator Namespace，与已有业务隔离。Controller 通过专用 ServiceAccount 工作，RBAC 只授予其读取 Node、Pod、ComputeTask 和创建受控 Job、写入任务 Status/Event 所需要的权限。

对应清单：

    manifests/00-namespace-rbac.yaml

项目还使用 ResourceQuota 限制项目资源，避免实验任务无限制消耗共享集群。

### 6.2 ComputeTask CRD

ComputeTask 是项目提供给用户的任务接口。其 spec 描述期望，status 描述 Controller 的实际观测和决策。

典型 spec 信息包括：

- taskCount：任务总数。
- resources：CPU、内存等资源需求。
- workerSelector：允许参与任务的 Worker 标签条件。
- maxWorkers：最多使用多少个 Worker。
- strategy：例如 minExpectedCompletionTime。
- execution：仅规划或允许创建 Job。
- routing：仅规划 P4 路由或允许写入 P4。

典型 status 信息包括：

- selectedWorkers：被选中的 Worker、分配任务数、flowId 和 backendId。
- estimatedMakespanMs：整体预计完成时间。
- decision 和 decisionTrace：选择原因与指标上下文。
- executionPhase：受控 Job 的实际生命周期。
- routing：期望路由、观测路由、是否同步、是否实际写入。

对应清单：

    manifests/01-computetask-crd.yaml

### 6.3 Controller Deployment

Controller 以 Deployment 方式运行。其默认设置为：

    EXECUTION_MODE=plan
    P4_ROUTING_MODE=plan

因此默认只计算和写入 Status，不创建新的 Job，也不会修改 P4 表。真实操作必须显式临时开启对应门禁。

对应清单：

    manifests/02-controller.yaml

## 7. Controller 的工作机制

### 7.1 双时间尺度刷新

Controller 不会每两秒都从 Kubernetes 读取全部对象，而是分成两类循环：

    每 2 秒：
      - 从 metrics-receiver 获取动态 Worker 指标
      - 重算 minExpectedCompletionTime 类型任务

    每 15 秒：
      - 读取 Node、Pod、白名单 Worker Pod、ComputeTask
      - 成功后整体替换本地资源缓存

这样做的原因是：Node、Pod 和任务定义变化相对慢；队列长度和处理速率变化相对快。

稳定状态下，Kubernetes 读取约为每轮 4 次、每分钟 4 轮，即约 16 次读取。动态指标 HTTP 请求直接访问 metrics-receiver，不经过 Kubernetes API Server。

### 7.2 原子资源缓存

慢周期会先把 Node、Worker 和 ComputeTask 读到局部变量。只有所有读取都成功，才整体替换旧缓存。

如果中途失败，旧缓存保持不变。这样 Controller 不会使用“新 Node、旧 Worker、新 Task”组成的混合状态。

这里的原子性是 Controller 进程内部的一致性边界，不是数据库事务。

### 7.3 Status 写入去重

Controller 每次计算完新状态后，会比较新旧 status。完全相同则不调用 Kubernetes Status Patch。

这样可以避免持续产生：

- Kubernetes API 写请求。
- etcd 对象新版本。
- Watch 事件。
- 无意义日志。

当 Worker 选择、任务分配或预计完成时间真正变化时，Status 才会更新。

## 8. 最短预计完成时间调度算法

### 8.1 目标

策略名称为 minExpectedCompletionTime。它不是简单选择当前队列最短的 Worker，而是选择一个 Worker 子集，使全部任务中最晚完成的一条任务链预计结束时间最小。

这通常称为最小化 makespan。

### 8.2 单个 Worker 的预计完成时间

对于每个候选 Worker，模型估计：

    queue_ms
    + network_latency_ms
    + compute_ms
    + batch_wait_ms

其中：

- queue_ms：现有队列长度除以处理速率。
- network_latency_ms：配置或动态采集的网络时延。
- compute_ms：分配任务数除以根据 CPU 份额调整后的有效处理速率。
- batch_wait_ms：当最后一批不是满批时，等待 batch timeout 的时间。

### 8.3 多 Worker 任务分配

算法流程为：

1. 过滤 NotReady、标签不匹配或资源不足的 Worker。
2. 枚举不超过 maxWorkers 的可行 Worker 组合。
3. 为每个组合分配资源份额。
4. 根据各 Worker 有效处理速率按比例分配 taskCount。
5. 计算每个 Worker 的预计完成时间。
6. 以最小 makespan 为主排序；总时间、Worker 数量和编号作为稳定的次级排序条件。

例如，两个 Worker 的候选方案可能是：

    只选 216
    只选 226
    同时选 216 和 226

算法选择整体最晚完成时间最短的方案。

### 8.4 复杂度与适用边界

若有 N 个候选 Worker，最多选择 M 个，组合数为：

    C(N,1) + C(N,2) + ... + C(N,M)

当前实验主要使用两个 Worker、maxWorkers 为 2，只需计算 3 种方案；即使有 9 个候选 Worker且允许全部组合，也只有 511 种非空组合。因此在当前小集群中，计算时间通常是毫秒级。

taskCount 只进入数学公式，不会逐条模拟任务，所以任务数增加不会直接线性增加调度计算量。

当候选 Worker 达到十几个以上、且允许任意数量组合时，精确枚举会呈指数增长。届时应采用预筛选、贪心或束搜索等近似策略。

## 9. 动态指标与 Worker 健康

### 9.1 指标数据链路

    Worker Pod
      -> UDP 指标报告
      -> metrics-receiver
      -> HTTP 指标查询
      -> Controller runtime_metrics
      -> 调度算法

动态指标包含队列深度、服务速率、批大小和超时等。Controller 只接受白名单 Worker 的新鲜指标；没有新鲜动态指标时，自动使用静态默认值，而不是让调度器不可用。

相关代码：

    app/metrics_receiver.py
    app/runtime_metrics.py

相关清单：

    manifests/06-metrics-receiver.yaml
    manifests/workers/ubuntu-194-216.yaml
    manifests/workers/ubuntu-200-226.yaml

### 9.2 Readiness 与 Liveness

Worker 的真实计算数据端口使用 UDP 9000，而 Kubernetes HTTP/TCP 探针不能直接确认 UDP 业务是否正常。因此 Worker 额外暴露 TCP 健康端口：

- Readiness：决定 Worker 是否可以被纳入新调度方案。
- Liveness：持续失败时由 Kubernetes 重启该容器。

调度算法只使用 Ready Worker，避免把新任务分给还没有准备好的后端。

## 10. Job 执行与生命周期

### 10.1 计划和执行分离

项目把“是否允许创建新 Job”和“已有 Job 的实际结果”分开记录：

- executionAllowed：当前是否允许创建新的 Job。
- executionPhase：已有 Job 的真实运行状态，例如 Running、Succeeded、Failed。
- jobName：当前 ComputeTask Generation 对应的 Job。

因此，当系统重新切回 plan 后，已经完成的 Job 仍然保留 Succeeded 状态，而不会被误判为“没有执行”。

### 10.2 双重执行门禁

创建 Job 必须同时满足：

    Controller 全局 EXECUTION_MODE=apply
    且 ComputeTask 自身允许 execution.mode=apply

该设计防止仅修改一项配置就意外在共享集群中创建任务。

### 10.3 Generation 与 OwnerReference

ComputeTask 的每次 spec 修改都会形成新的 Generation。Controller 为每一代使用独立 Job 名称，并设置 OwnerReference：

- Job 清楚地归属于哪个 ComputeTask。
- 删除 ComputeTask 时 Kubernetes 可以级联清理其 Job。
- 新一代任务不会误用旧一代结果。

## 11. P4 控制面接入

### 11.1 职责边界

102 上的 Kubernetes Controller 不能直接操作 BMv2 Thrift。它只能调用 114 上的 HTTP API。

    Kubernetes Controller
      -> 114 HTTP API
      -> 114 唯一 P4 控制器
      -> BMv2 Thrift 9090
      -> udp_tag_table

这样 114 保持唯一的 P4 写入者，避免多个控制器对同一张表产生竞争。

### 11.2 两套编号

调度器使用逻辑后端编号，P4 表使用 P4 后端/端口编号：

| 逻辑后端 | P4 后端/端口 |
| --- | --- |
| 116 / 117 / 118 | 1 / 2 / 3 |
| 216 / 217 / 218 | 4 / 5 / 6 |
| 226 / 227 / 228 | 7 / 8 / 9 |

因此，ComputeTask 中 flow 1 -> backend 216，在 BMv2 中表现为 flow 1 -> P4 端口 4。

### 11.3 P4 三重写入门禁

只有同时满足以下条件，Controller 才能请求 114 修改路由：

    P4_ROUTING_MODE=apply
    ComputeTask spec.routing.mode=apply
    ComputeTask 名称等于 P4_ROUTE_OWNER

默认均不满足，因此 Controller 即使能读取 114 API，也只会比较 desiredRoutes 和 observedRoutes，不会写 P4 表。

### 11.4 认证

102 使用 Kubernetes Secret 保存 P4 控制令牌，通过 X-P4-Control-Token 请求头调用 114。令牌不写进 Deployment YAML，也不应通过 kubectl 查询 Secret data 输出。

### 11.5 真实端到端验证

已完成的实验过程：

1. ComputeTask 得到 flow 1 -> 216、flow 2 -> 226 的调度结果。
2. 临时开启三重 apply 门禁。
3. 114 实时控制器将路由写入 BMv2。
4. BMv2 表项变为 flow 1 -> P4 端口 4、flow 2 -> P4 端口 7。
5. ubuntu-204 向虚拟地址发送两个带唯一标记的 UDP 流。
6. 216 只收到 flow 1 的 3 个任务。
7. 226 只收到 flow 2 的 3 个任务。
8. 两个后端均未收到对方 flow 的标记。

这证明链路不是“HTTP 返回成功”而已，而是完成了调度决策、路由写表和真实数据转发。

## 12. P4 systemd 单写者交接

### 12.1 两个互斥服务

114 上存在两个 systemd 服务：

| 服务 | 作用 | 开机状态 |
| --- | --- | --- |
| p4-k8s-dryrun.service | 提供 18100 只观察 API，不写 BMv2 | enabled |
| p4-k8s-control.service | 提供真实 P4 写入 API | linked，未加入开机启动 |

二者通过 Conflicts 互斥，不能同时占用 18100。

### 12.2 实时服务启动

启动真实服务时，systemd 自动执行：

1. 停止 dry-run 服务。
2. 检查 global-gateway.service 和 BMv2 是否存在。
3. 动态寻找旧 P4 控制器进程，不使用写死 PID。
4. 暂停旧控制器。
5. 从 BMv2 读取 udp_tag_table，生成动态恢复快照。
6. 启动带认证令牌的候选实时控制器。
7. 检查 HTTP API 的 owner、mode、dry_run 和认证状态。

### 12.3 停止、崩溃与恢复

真实服务正常停止或异常退出时，ExecStopPost 会：

1. 使用动态快照恢复 P4 路由。
2. 唤醒旧控制器。

实时服务设置 Restart=on-failure。已通过 SIGKILL 实验验证：

    NRestarts：0 -> 1
    先恢复路由并唤醒旧控制器
    等待 5 秒
    重新快照并暂停旧控制器
    新实例通过 API 就绪检查

这说明自动重启不会直接留下两个写入者或丢失恢复路径。

## 13. 安全与可靠性设计

项目采用多层安全边界：

| 风险 | 处理方式 |
| --- | --- |
| 误创建 Kubernetes Job | 全局和任务级双重 apply 门禁 |
| 误改 P4 路由 | 全局、任务级、路由所有者三重 apply 门禁 |
| 多控制器写 P4 | 114 唯一 Thrift 写入者与 systemd 单写者交接 |
| Controller API 令牌泄露 | Kubernetes Secret、文件权限、禁止输出 Secret data |
| 资源读取部分失败 | 原子替换旧缓存，不产生混合快照 |
| 指标不可用 | 使用静态指标回退 |
| Worker 未就绪 | Readiness 过滤，不进入调度候选集 |
| 实时 P4 服务崩溃 | 动态快照恢复、旧控制器唤醒、systemd 重启 |
| 重复 Status 写入 | 新旧 Status 比较后去重 |

## 14. 已完成验证

### 14.1 自动化测试

- Kubernetes 控制器项目：69 项单元与契约测试通过。
- 114 P4 控制器与动态路由快照：7 项测试通过。

测试覆盖资源适配、调度策略、Controller 循环、CRD Schema、Job 生命周期、动态指标、Worker 健康、P4 HTTP 合约和路由快照解析。

### 14.2 集群验证

已验证：

- Controller 0.5.1 正常 Ready。
- 194 上的 216 与 200 上的 226 均为 Running/Ready。
- 204 到 P4 虚拟地址的路由经 net1 发送。
- P4 真正修改并回滚过 udp_tag_table。
- 流 1 与流 2 真实到达不同 Worker，且未串流。
- P4 实时 systemd 服务可正常启动、停止和异常恢复。
- 验证结束后恢复到安全状态：旧控制器运行、P4 表恢复、dry-run API 运行、Kubernetes P4 模式为 plan。

## 15. 代码与文档地图

### 15.1 Kubernetes 主项目

    app/controller.py
      Controller 协调逻辑、状态、Job 与 P4 门禁。

    app/policies/completion_time_scheduler.py
      精确小集群最短预计完成时间算法。

    app/multi_worker_planner.py
      ComputeTask、Pod 与算法之间的纯函数规划层。

    app/adapters.py
      Kubernetes 对象和调度模型之间的数据转换。

    app/metrics_receiver.py
      Worker UDP 指标接收服务。

    app/runtime_metrics.py
      指标查询、认证、刷新与回退。

    app/p4_control.py
      102 调用 114 HTTP P4 控制 API 的客户端。

    manifests/
      Kubernetes 部署清单和实验任务。

    tests/
      69 项自动化测试。

### 15.2 原调度与 P4 项目

    p4-controller/controller_global_9backend.py
      114 上候选 P4 控制器和 HTTP API。

    p4-controller/p4_route_snapshot.py
      动态读取和恢复 BMv2 udp_tag_table。

    p4-controller/prepare_systemd_handover.sh
      实时服务启动前的暂停与快照。

    p4-controller/restore_systemd_handover.sh
      服务停止或失败后的恢复。

    p4-controller/systemd/
      dry-run 与 live systemd 单元文件。

## 16. 当前运行状态

当前系统刻意停留在安全状态：

    Kubernetes Job 执行：plan
    Kubernetes P4 写入：plan
    114 dry-run API：active
    114 实时 P4 服务：inactive
    旧 P4 控制器：active
    BMv2：active

这意味着系统具备真实执行和真实 P4 写入能力，但不会因为 Controller 的日常循环意外修改共享环境。

## 17. 当前限制

本项目是完成端到端验证的工程原型，不应描述为生产级高可用平台。主要限制包括：

1. 调度算法适合小规模候选 Worker；大规模集群需近似算法。
2. P4 写表底层使用 clear 加 add，不具备硬件事务；当前依靠快照和补偿恢复。
3. 114 API 返回的路由状态主要反映候选控制器状态；下一步应在每次真实写入后从 BMv2 再次读取并验证实际表项。
4. 当前主要验证一个 BMv2 和两个核心 Worker，未验证多交换机路径选择和高可用。
5. 控制 API 使用令牌认证，尚未引入 TLS、证书轮换和细粒度授权。
6. 还缺少标准化的一键实验报告、性能图表和多算法对比。

## 18. 推荐后续路线

优先级最高的完善方向：

1. 增加 BMv2 写后真实回读、比对和自动补偿回滚。
2. 编写一次可审计的端到端发布脚本：检查服务、临时 apply、等待同步、发送流量、收集证据、恢复 plan。
3. 对比 Round Robin、最短队列、资源余量优先和最短预计完成时间四类策略。
4. 保存平均完成时间、P95/P99、makespan、Worker 利用率、P4 路由更新次数和故障恢复时间。

进一步的创新方向是“计算与网络联合调度”：

    预计完成时间
      = 排队时间
      + 计算时间
      + 网络传输时间
      + P4 路径拥塞代价

届时可以将 P4 端口队列、带宽、RTT、丢包率和路径利用率引入调度评分。

## 19. 项目价值与简历表述

该项目的价值不在于单独部署 Kubernetes、写一个调度算法或配置一张 P4 表，而在于将这些部分组合为一个可验证的云网协同闭环，并为共享实验环境实现了明确的安全门禁和故障恢复。

可使用的简历表述：

    设计并实现 Kubernetes 与 P4 协同的计算任务调度系统：基于 CRD 和 Controller
    构建多 Worker 任务编排，结合资源余量、实时队列指标和预计完成时间完成
    多后端分配；通过受认证 HTTP 控制面将调度结果下发至 BMv2，完成真实
    数据流转发验证；实现 RBAC、plan/apply 门禁、Job Generation、动态路由
    快照和 systemd 故障回滚，累计完成 76 项自动化测试。

## 20. 建议阅读顺序

1. README.md：先建立项目全貌。
2. manifests/00-namespace-rbac.yaml 和 manifests/01-computetask-crd.yaml：理解 Kubernetes 入口。
3. docs/ALGORITHM_BASELINE.md：理解算法从何而来。
4. docs/CONTROLLER_REFRESH_ARCHITECTURE.md：理解双时间尺度 Controller。
5. docs/METRICS_RECEIVER_DEPLOYMENT.md、docs/REAL_WORKER_METRICS_LOOP.md、docs/WORKER_HEALTH_PROBES.md：理解动态闭环。
6. docs/CONTROLLED_JOB_EXECUTION.md、docs/JOB_OWNERSHIP_AND_GENERATIONS.md、docs/EXECUTION_STATUS_MODEL.md：理解真实执行。
7. docs/P4_CONTROL_PLANE_INTEGRATION.md：理解 Kubernetes 到 P4 的真实闭环。
8. docs/P4_SYSTEMD_HANDOVER.md：理解单写者、快照与故障恢复。

阅读每个阶段时都遵循同一方法：先读文档，再读相关 YAML，然后读 Python 实现，最后读 tests 中的验证方式。
