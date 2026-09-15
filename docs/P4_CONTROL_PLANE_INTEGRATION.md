# P4 控制面接入与真实转发验证

## 1. 本阶段目标

让 Kubernetes 中的 ComputeTask 调度结果能够转化为 P4 路由，并由 114 上的 BMv2 交换机把任务流量转发到对应计算后端。

本阶段没有修改“最短预计完成时间”调度算法，只打通下面的闭环：

    ComputeTask
      -> Kubernetes Controller（102 上管理）
      -> 114 HTTP 控制 API
      -> BMv2 Thrift 9090
      -> udp_tag_table
      -> 后端 Pod

## 2. 为什么必须由 114 做唯一写入者

BMv2 的 Thrift 接口直接修改交换机表项。如果 Kubernetes Controller 和原有 114 控制器同时写表，最后生效的配置取决于写入时序，会产生难以复现的竞争。

因此采用明确的职责边界：

- 102 上的 Kubernetes Controller 只调用 114 的 HTTP API。
- 只有 114 上的控制器可以访问 BMv2 Thrift 9090。
- 真实切换时，先暂停旧控制器，再启动候选控制器。
- BMv2 进程不重启，所以交接期间数据面继续运行。

## 3. 两套编号的映射

调度器使用逻辑后端编号，P4 表使用端口编号：

| 逻辑后端 | P4 后端/端口 |
| --- | --- |
| 116 / 117 / 118 | 1 / 2 / 3 |
| 216 / 217 / 218 | 4 / 5 / 6 |
| 226 / 227 / 228 | 7 / 8 / 9 |

本次调度结果为：

- flowId=1 -> backendId=216，写入 P4 后变为 flow 1 -> 端口 4。
- flowId=2 -> backendId=226，写入 P4 后变为 flow 2 -> 端口 7。

这解释了为什么 ComputeTask 状态中的 216/226 与 P4 CLI 中的 04/07 不相同。

## 4. 三重写入门禁

控制器只有同时满足以下条件才允许修改 P4：

1. Deployment 环境变量 P4_ROUTING_MODE=apply。
2. ComputeTask 中 spec.routing.mode=apply。
3. ComputeTask 名称等于 P4_ROUTE_OWNER。

任意一项不满足时，控制器仍会读取并比较路由，但不会写入。默认部署清单保持 P4_ROUTING_MODE=plan。

## 5. 身份认证

Kubernetes Controller 通过 Secret p4-control-auth 获得控制令牌，并放入 X-P4-Control-Token 请求头。

令牌不能直接写进 Deployment YAML，也不能在检查时输出 Secret 的 data。按文件创建 Secret 时还要注意末尾换行：请求头值中不能包含换行。本次最终使用去除 CR/LF 后的 64 字节值创建 Secret。

## 6. ComputeTask 路由状态

status.routing 中的重要字段：

- desiredRoutes：调度器希望得到的路由。
- observedRoutes：从 114 HTTP API 读取到的路由。
- inSync：期望值与观测值是否一致。
- writeAllowed：三重门禁是否全部打开。
- dryRun：114 当前是否只模拟写入。
- applied：是否已由真实控制器完成写入。
- phase：常见值为 Planned、DryRunValidated、Applied、Unavailable、Failed。
- error：最近一次协调错误；成功后必须清空，避免保留过期错误。

## 7. 2026-09-13 实测记录

### 7.1 前置条件

- 发送端 ubuntu-204：Running/Ready。
- 后端 ubuntu-194-216：Running/Ready。
- 后端 ubuntu-200-226：Running/Ready。
- 发送端 net1=10.10.1.4/24。
- 10.10.1.200 的路由从 net1 发出。

### 7.2 控制面结果

Kubernetes Controller 0.5.1 开启全局 apply 后，ComputeTask 状态为：

    phase=Applied
    writeAllowed=true
    applied=true
    inSync=true
    desiredRoutes=[1->216, 2->226]
    observedRoutes=[1->216, 2->226]
    controlOwner=server-114
    controlMode=external
    dryRun=false

BMv2 实际 udp_tag_table 同步变为：

    flow 1 -> P4 后端 4
    flow 2 -> P4 后端 7
    default -> drop

### 7.3 数据面结果

从 ubuntu-204 向 10.10.1.200:9000 发送两个流，每个流 3 个任务：

- flow 1 的三个任务全部由逻辑后端 216 完成。
- flow 2 的三个任务全部由逻辑后端 226 完成。
- 216 没有收到 flow 2 的测试标记。
- 226 没有收到 flow 1 的测试标记。

这证明 HTTP 返回成功不是“假成功”：P4 表项确实生效，数据包也按调度结果到达了正确 Pod。

## 8. 验证后的安全回滚

真实验证结束后执行：

1. 把 Kubernetes 全局写入模式恢复为 plan。
2. 停止候选实时控制器。
3. 恢复原 P4 路由 1->7、2->8、3->9。
4. 恢复旧控制器进程。
5. 重新启动 18100 上的 guarded dry-run 控制器。

回滚后的核验结果：

- 旧控制器状态为运行。
- 实时候选控制器已退出。
- BMv2 未重启。
- P4 表恢复为原来的 7/8/9。
- Kubernetes Controller 仍可在 plan 模式读取 114 dry-run API。

## 9. 本阶段学习重点

- 控制面负责“决定并下发表项”，数据面负责“按表项高速转发”。
- P4 后端端口编号与业务逻辑后端编号是两个不同的标识空间。
- plan、dry-run 和 apply 分别对应只规划、模拟写入、真实写入。
- 外部系统接入 Kubernetes 时，要同时处理权限、认证、幂等、状态观测和回滚。
- 单写者不是代码风格问题，而是防止两个控制器互相覆盖配置的一致性约束。

## 10. 下一步

把 114 候选实时控制器从手工 nohup 进程改造成受 systemd 管理的服务，并设计原控制器与新控制器的互斥启动、失败恢复和开机行为。完成这一层后，才适合让 Kubernetes 长期保持真实 P4 控制能力。
