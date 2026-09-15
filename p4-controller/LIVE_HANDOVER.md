# P4 单写者与 systemd 交接

## 服务划分

- p4-k8s-dryrun.service：安全观察服务，占用 18100，只验证路由请求，不写 BMv2。
- p4-k8s-control.service：真实写入服务，占用 18100，是 Kubernetes 的唯一 P4 写入入口。
- global-gateway.service：保留原 BMv2 和旧控制器，不删除、不重启 BMv2。

两个 p4-k8s 服务通过 Conflicts 互斥，不能同时占用 18100。

## 实时服务启动过程

启动 p4-k8s-control.service 时，systemd 自动完成：

1. 停止 p4-k8s-dryrun.service。
2. 确认 global-gateway 正常，并动态寻找它唯一的旧控制器子进程。
3. 暂停旧控制器。
4. 从 BMv2 读取 udp_tag_table，生成 runtime/pre-live-routes.cli。
5. 启动带令牌认证的实时 HTTP 控制器。
6. 等待 API 就绪，并确认 control_owner、control_mode 和 dry_run=false。

不要再手工写死 PID，也不要同时启动两个真实写表控制器。

## 停止与失败恢复

停止或崩溃时，ExecStopPost 会先执行动态路由快照，然后唤醒旧控制器。

实时服务配置为 Restart=on-failure。异常退出时的顺序是：

1. 恢复接管前路由并唤醒旧控制器。
2. 等待 RestartSec。
3. 重新快照、暂停旧控制器。
4. 启动新的实时控制器实例。

若连续失败达到 systemd 启动限制，最终状态仍应是旧控制器运行、接管前路由已恢复。

## 常用命令

启动真实控制：

    systemctl start p4-k8s-control.service

恢复到安全观察状态：

    systemctl stop p4-k8s-control.service
    systemctl start p4-k8s-dryrun.service

查看状态和日志：

    systemctl status p4-k8s-control.service
    systemctl status p4-k8s-dryrun.service
    journalctl -u p4-k8s-control.service

实时服务只保持 linked，不设置开机启动；dry-run 服务设置为 enabled。
