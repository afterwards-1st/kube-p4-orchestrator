# P4 路由 Finalizer 与删除回收

## 为什么需要 Finalizer

Kubernetes 删除 `ComputeTask` 时，可以通过 OwnerReference 自动回收它创建的 Job，但 P4 路由位于 Kubernetes 之外，垃圾回收器并不知道这项外部状态。

因此，带有 `spec.routing` 的任务会获得：

```text
orchestrator.wzt.io/p4-route-cleanup
```

只要该 Finalizer 仍存在，Kubernetes 就会保留带 `deletionTimestamp` 的 ComputeTask，给 Controller 留出清理外部路由的机会。

## 写入前快照

Controller 第一次真正修改 P4 路由时，将修改前的路由保存到：

```yaml
status:
  routing:
    desiredRoutes:    # 当前任务写入的路由
    previousRoutes:   # 写入前的路由快照
    applied: true     # 本任务确实执行过真实写入
```

如果路由原本就与期望值相同，Controller 只记录 `InSync`，不会把别人的路由错误认领为自己写入的路由，也不会设置 `applied: true`。

## 删除流程

```text
删除 ComputeTask
  ↓
进入 Terminating
  ↓
是否由本任务实际写入？
  ├─ 否：NotRequired，释放 Finalizer
  └─ 是：强制读取 114 的实时路由
           ↓
       当前路由是否仍等于 desiredRoutes？
         ├─ 否：说明发生外部修改
         │      Skipped，不覆盖当前路由，释放 Finalizer
         └─ 是：恢复 previousRoutes
                ↓
              再次读取并验证
                ├─ 成功：Restored，释放 Finalizer
                └─ 失败：WaitingForP4，保留 Finalizer，下轮重试
```

## 为什么发现漂移后不恢复

P4 路由表是共享状态。任务运行期间，管理员或其他控制程序可能已经修改路由。如果删除任务时无条件恢复旧快照，就可能覆盖更新后的正确配置。

所以本项目使用比较后恢复：只有当前值仍等于本任务最后写入值，才能确认任务仍拥有这段外部状态。

## 观察命令

```bash
kubectl get ctask <任务名> -n kube-orchestrator -o yaml
kubectl get ctask <任务名> -n kube-orchestrator \
  -o jsonpath='{.metadata.finalizers}{"\n"}{.status.cleanup}{"\n"}'
```

删除时可能看到：

```yaml
status:
  phase: Terminating
  cleanup:
    phase: WaitingForP4
    reason: P4 control unavailable
```

这不是删除失败，而是 Controller 为避免遗留路由而暂时阻止对象消失。

## 紧急人工解除

只有在已经人工确认 P4 路由安全，并且愿意接受可能遗留外部状态时，才可以手动移除 Finalizer。正常流程不应使用该操作。

```bash
kubectl patch ctask <任务名> -n kube-orchestrator \
  --type=merge -p '{"metadata":{"finalizers":[]}}'
```

## 安全边界

- 默认部署仍是 `P4_ROUTING_MODE=plan`，不会真实修改 P4。
- 只有全局模式、任务模式和路由 Owner 三个条件同时允许，才可能写路由。
- dry-run 返回不会设置 `applied: true`，删除时不执行恢复。
- P4 不可用或恢复验证失败时，Finalizer 保留并重试。
- 检测到路由漂移时，保护当前配置优先于恢复旧快照。