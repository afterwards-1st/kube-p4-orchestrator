# P4 控制器的 systemd 单写者交接

## 1. 为什么需要这一层

上一阶段已经证明 Kubernetes 可以真实修改 P4 并完成数据转发，但候选控制器是通过 nohup 手工启动的。它存在三个问题：

- 服务器重启后不会自动恢复到明确状态。
- 进程崩溃时不能保证恢复旧路由。
- 手工填写旧控制器 PID 容易操作错误。

本阶段把启动、互斥、快照、失败恢复全部交给 systemd。

## 2. 两个互斥服务

| 服务 | 作用 | 开机状态 |
| --- | --- | --- |
| p4-k8s-dryrun.service | 提供只观察、不写表的 18100 API | enabled |
| p4-k8s-control.service | 提供真实 P4 写入 API | linked，不自动启动 |

Conflicts 保证两者不能同时运行。实时服务还通过 Requires 和 After 依赖原 global-gateway.service，确保 BMv2 已经存在。

## 3. systemd 生命周期钩子

- ExecStartPre：确认现场、寻找旧控制器、暂停旧控制器、动态快照路由。
- ExecStart：运行候选控制器。
- ExecStartPost：等待 HTTP API 就绪，校验所有者、控制模式和 dry-run 标志。
- ExecStopPost：恢复动态快照，并唤醒旧控制器。
- Restart=on-failure：候选控制器异常退出后自动执行恢复，再尝试重新接管。

动态快照保存在：

    /root/wzt/k8s_learning/p4-controller/runtime/pre-live-routes.cli

PID 和活动标记也只保存在该 runtime 目录中。代码及配置本体均位于 wzt，系统目录只有指向它们的符号链接。

## 4. 动态快照为什么比固定回滚更安全

旧脚本把回滚写死为 1→7、2→8、3→9。如果旧项目在接管前已经修改了路由，固定回滚会覆盖它的新状态。

现在每次启动实时服务都会：

1. 暂停旧控制器，冻结写入。
2. 从 BMv2 的 udp_tag_table 解析当时的真实 flow 和 P4 后端。
3. 生成可执行的恢复命令。
4. 只有快照成功后才启动新控制器。

快照失败会立即唤醒旧控制器，不会继续接管。

## 5. 2026-09-14 验证结果

正常启动验证：

- dry-run 服务自动变为 inactive。
- 旧控制器状态变为 T，即已暂停。
- 实时服务 API 通过令牌认证。
- API 明确返回 control_owner=server-114、control_mode=external、dry_run=false。
- Kubernetes 保持 P4_ROUTING_MODE=plan，没有写入路由。

异常退出验证：

- 向实时服务主进程发送 SIGKILL。
- systemd 的 NRestarts 从 0 增加为 1。
- ExecStopPost 先恢复 3 条路由并唤醒旧控制器。
- 5 秒后重新快照、暂停旧控制器并启动新实例。
- 新实例再次通过 API 就绪检查。

最终停止验证：

- 实时服务为 inactive。
- 旧控制器恢复为运行状态。
- 活动交接标记已清除。
- BMv2 路由恢复为 1→7、2→8、3→9。
- dry-run 服务重新运行并监听 18100。
- Kubernetes Controller 0.5.1 为 Ready，状态无错误。

## 6. 当前安全状态

当前没有启用长期真实写入：

    p4-k8s-control.service = inactive
    p4-k8s-dryrun.service = active
    global-gateway.service = active
    P4_ROUTING_MODE = plan

因此系统具备真实接管能力，但默认仍处于不会改表的安全状态。

## 7. 学习重点

- systemd 的 active 只代表进程已启动；ExecStartPost 可以进一步表达“应用真正就绪”。
- After 决定启动顺序，Requires 表达依赖，Conflicts 表达互斥。
- ExecStopPost 不只处理正常停止，也处理启动失败和异常退出后的清理。
- Restart 必须和回滚逻辑一起设计，否则自动重启可能把错误持续放大。
- 动态快照、单写者和默认 plan 共同构成 P4 外部控制面的安全边界。

## 8. 下一步

下一阶段可以实现一个 Kubernetes 侧的受控切换流程：先检查 systemd 实时服务和 P4 API，再临时打开全局 apply，完成路由同步后自动恢复 plan。这样可以把目前分散的人工命令收敛为一次可审计的发布操作。
