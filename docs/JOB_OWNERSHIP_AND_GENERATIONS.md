# Job 所有权与任务版本

## 本阶段目标

让控制器创建的 Job 与 ComputeTask 建立明确的父子关系，并让同一个 ComputeTask 每次修改 `spec` 后都生成新的 Job，避免复用旧 Job。

当前控制器版本：`kube-orchestrator:0.4.4`。

## 为什么需要 generation

Kubernetes 对象的 `metadata.generation` 表示期望配置的版本。创建 ComputeTask 时通常为 1；每次修改 `spec`，API Server 会自动递增该值。仅修改 `status` 不会增加 generation。

控制器现在按下面的格式生成 Job 名称：

```text
task-<ComputeTask 名称>-g<generation>
```

例如：

```text
owned-job-demo generation=1 -> task-owned-job-demo-g1
owned-job-demo generation=2 -> task-owned-job-demo-g2
```

这样做有三个作用：

1. 同一代任务重复 reconcile 时命中同一个 Job，保证幂等。
2. 修改任务配置后生成新 Job，不会把上一代成功结果误认为本次结果。
3. g1、g2 等 Job 可作为执行历史，便于排障和审计。

Job 名称最长 63 个字符。控制器对超长名称执行截断并追加 8 位 SHA-256 摘要，兼顾合法性与低冲突概率。

## 为什么需要 OwnerReference

控制器在 Job 的 `metadata.ownerReferences` 中记录父对象 ComputeTask 的信息：

```yaml
apiVersion: orchestrator.wzt.io/v1alpha1
kind: ComputeTask
name: owned-job-demo
uid: <ComputeTask UID>
controller: true
blockOwnerDeletion: false
```

关键点：

- `name` 便于人识别父对象。
- `uid` 才是 Kubernetes 判断父对象身份的依据；删除后重建同名对象会得到新 UID。
- `controller: true` 表示该 Job 由此 ComputeTask 的控制器负责。
- 父 ComputeTask 删除后，Kubernetes 垃圾回收器会自动删除这些从属 Job 和 Job 的 Pod。

控制器不会在缺少 UID 时伪造 OwnerReference，而是返回空列表，避免产生错误归属。

## 本次实测过程

### 1. 全局 plan 阻止执行

```bash
kubectl apply -f manifests/08-owned-job-demo.yaml
kubectl get computetask owned-job-demo -n kube-orchestrator
kubectl get job task-owned-job-demo-g1 -n kube-orchestrator
```

结果：ComputeTask 的 `spec.execution=apply`，但控制器全局 `EXECUTION_MODE=plan`，所以任务状态为 Planned，g1 Job 不存在。说明任务级与全局级双开关生效。

### 2. 临时开启 apply，执行第 1 代

```bash
kubectl set env deployment/controller -n kube-orchestrator EXECUTION_MODE=apply
kubectl rollout status deployment/controller -n kube-orchestrator --timeout=120s
kubectl get job task-owned-job-demo-g1 -n kube-orchestrator
kubectl logs -n kube-orchestrator job/task-owned-job-demo-g1
```

结果：g1 成功完成，日志为 `owned generation one`，运行节点为 node194。Job 的 OwnerReference 指向 `owned-job-demo` 的真实 UID。

### 3. 修改 spec，执行第 2 代

```bash
kubectl patch computetask owned-job-demo -n kube-orchestrator --type=merge \
  -p '{"spec":{"command":["python3","-c","print(\"owned generation two\")"]}}'
kubectl get computetask owned-job-demo -n kube-orchestrator
kubectl get jobs -n kube-orchestrator
kubectl logs -n kube-orchestrator job/task-owned-job-demo-g2
```

结果：ComputeTask 的 generation 从 1 增到 2，控制器创建 g2；g1、g2 同时保留且均成功，g2 日志为 `owned generation two`。

### 4. 恢复 plan 并验证垃圾回收

```bash
kubectl apply -f manifests/02-controller.yaml
kubectl rollout status deployment/controller -n kube-orchestrator --timeout=120s
kubectl delete computetask owned-job-demo -n kube-orchestrator
kubectl get jobs -n kube-orchestrator
```

结果：控制器恢复 `v0.4.4 + plan + Recreate + Ready=1`；删除父 ComputeTask 后，g1、g2 Job 均被自动回收。`manifests/08-owned-job-demo.yaml` 仍在，可重新创建实验对象。

## 验证结果

- 本机单元测试：61/61 通过。
- 102 单元测试：61/61 通过。
- Deployment 与示例清单均通过 `kubectl apply --dry-run=server`。
- node194 完成 g1、g2 两次真实执行。
- OwnerReference 字段与 UID 已核验。
- 删除 ComputeTask 后，从属 Job 自动删除。
- `kube-orchestrator` 命名空间无 Warning 事件。

## 状态模型改进（已在 v0.4.5 完成）

在 v0.4.4 验证时发现：全局模式从 apply 恢复到 plan 后，已执行任务的 `status.phase` 会显示 Planned，同时 `status.jobName` 仍保存最近一次 Job。这说明旧状态模型把“当前控制模式”和“最近执行结果”混在一起。

v0.4.5 已拆分为下面的状态：

```yaml
status:
  observedGeneration: 1
  phase: Succeeded
  executionAllowed: false
  jobName: task-controlled-job-demo-g1
```

控制器在 plan 下不创建新 Job，但仍会读取当前 generation 已存在的 Job。线上验证已确认全局暂停不会再抹掉最近一代的 Succeeded 结果。详细过程见 `docs/EXECUTION_STATUS_MODEL.md`。

## 本阶段掌握的 Kubernetes 知识

- `metadata.generation`：期望配置 `spec` 的版本号。
- OwnerReference：声明 Kubernetes 对象之间的所有权关系。
- UID：对象实例的唯一身份，不等同于可复用的名称。
- Garbage Collector：根据所有权关系回收从属资源。
- Reconcile 幂等：同一 generation 多次调谐不会重复创建不同 Job。
- 全局 plan 与任务 apply：两级执行安全门。
