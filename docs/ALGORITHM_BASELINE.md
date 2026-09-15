# 调度算法整合基线

本文记录将 `scheduler-sim` 接入 Kubernetes `ComputeTask` Controller 之前的
稳定基线。第一轮整合期间，预计完成时间算法及其原有数据模型保持不变。

## 已验证测试

记录日期：2026-09-12。

```powershell
# kube-orchestrator
python -m unittest discover -s tests -v
# 结果：2/2 通过

# scheduler-sim/server-102
python -m unittest -v test_scheduler_core.py test_dispatch_plan.py
# 结果：21/21 通过
```

## 冻结源码哈希

以下为 `scheduler-sim/project-full-package-multi-flow-v1.2-clean/server-102`
中三个核心文件的 SHA-256：

```text
b5d84ed09b0292dcb11a2d86814af08db48f4efa6f496d40ac8a15f259834ca6  completion_time_scheduler.py
c6319aa8879e72abc86a650bd466e8e3168991410a8d164555a3144bbd79b7e5  device_pool.py
374e2b1e16273fbfaf01b87f7a057fc7f26eacca8ca0af540ff7d68b7ab68271  controller/requirement.py
```

## 第一轮整合约束

第一轮可以增加适配器，把 Kubernetes 对象转换为原算法需要的
`Requirement` 和 `Device`，并把 `SelectionResult` 转换为
`ComputeTask.status`。不得修改已冻结源码中的评分公式、组合枚举、权重或任务分配行为。
