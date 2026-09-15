# ComputeTask 受控 Job 执行

## 双重许可

真实 Job 只有同时满足两项条件才会创建：

~~~text
Controller: EXECUTION_MODE=apply
ComputeTask: spec.execution=apply
~~~

任一项为 plan 时只生成规划，不创建工作负载。正式清单中的全局模式始终保存为 plan。

## 最小 RBAC

Controller 仅获得 batch/jobs 的 get 和 create 权限，没有 delete、update 或任意 Pod 写权限。

## 生命周期

Controller 读取 Job.status 并映射：

- 尚未 active：Submitted；
- active 大于 0：Running；
- succeeded 大于 0：Succeeded；
- failed 大于 0：Failed。

测试任务 manifests/07-controlled-job.yaml 使用 10m CPU、16Mi 内存，在 node194 上输出 controlled execution succeeded 后结束。

## 单写者问题

首次切换全局模式时发现 Deployment 默认 RollingUpdate 会让旧 plan Controller 与新 apply Controller 短暂共存。项目没有 Leader Election，两个实例会竞争回写同一 ComputeTask.status。

因此 Controller Deployment 改为 Recreate。更新期间会短暂停止调度，但可以保证任一时刻只有一个 Controller 写者。对于当前单副本学习项目，这比引入完整 Leader Election 更清晰安全。

## 实测

~~~text
任务 apply + 全局 plan:
phase=Planned, jobName 为空，Job 数量 0

任务 apply + 临时全局 apply:
Job 创建并完成，phase=Succeeded
日志：controlled execution succeeded

实验结束:
全局恢复 plan
Controller strategy=Recreate
Controller Ready，重启 0
~~~

完成的 Job 与 ComputeTask 暂时保留，作为生命周期证据。

## 下一步

为 Job 增加 ComputeTask OwnerReference、按 generation 生成唯一 Job 名，并验证任务更新及清理语义。
