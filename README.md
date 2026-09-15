# Kube P4 Orchestrator

面向批处理与 AI 计算任务的 Kubernetes 和 P4 协同调度原型。

项目使用 ComputeTask 自定义资源描述任务需求；Controller 基于 Worker 资源、实时队列、处理速率、网络时延和批处理行为生成多后端分配方案。在经过明确门禁后，方案可由 114 上的唯一 P4 控制器写入 BMv2，从而让不同任务流到达被调度选中的 Worker。

## 已完成能力

- Kubernetes 原生 CRD、Status Subresource、RBAC、ResourceQuota 与 ServiceAccount。
- 多 Worker 最短预计完成时间调度，输出任务分配和预计 makespan。
- Controller 双时间尺度刷新：资源快照与动态指标分离。
- Worker UDP 指标上报、HTTP 拉取、过期指标过滤和静态回退。
- Readiness/Liveness 健康探针与 Worker 候选过滤。
- plan/apply 双重 Job 执行门禁、Generation 和 OwnerReference 生命周期管理。
- ComputeTask 调度结果到 P4 路由的受认证 HTTP 控制链路。
- P4 单写者、动态路由快照、systemd 正常停止与崩溃恢复。
- 已完成真实数据面验证：不同 flow 被 P4 转发到不同 Kubernetes Worker，且未串流。

## 架构

~~~
ComputeTask CRD
      |
      v
Kubernetes Controller
  |                 |
  |                 +--> Job（仅 apply）
  |
  +--> Worker 资源快照 + 实时队列指标
             |
             v
      最短预计完成时间规划
             |
             v
      114 P4 HTTP Control API（仅 P4 apply）
             |
             v
      BMv2 udp_tag_table
             |
             v
      Worker Pod 216 / 226 / ...
~~~

## 当前安全状态

仓库默认对应安全演示状态：

~~~
Kubernetes Job 执行：plan
Kubernetes P4 写入：plan
P4 dry-run API：active
P4 live API：按需启动，不自动启动
~~~

因此，部署 Controller 默认只生成规划结果和 Status，不会创建 Job 或修改 P4 表。真实操作需要显式开启对应门禁，并应在隔离实验环境中进行。

## 仓库结构

| 目录 | 内容 |
| --- | --- |
| app | Kubernetes Controller、调度算法、指标与 P4 HTTP 客户端 |
| manifests | Namespace、RBAC、CRD、Controller、Worker 和实验 ComputeTask |
| tests | Kubernetes 控制器相关测试 |
| p4-controller | 114 候选 P4 控制器、快照恢复脚本、systemd 单元与测试 |
| docs | 中文设计、验证、运维和学习文档 |
| scripts | 创建 Secret、部署 Worker、发送批处理任务的辅助脚本 |

## 本地验证

安装 Python 依赖后执行：

~~~
python -m unittest discover -s tests -v
~~~

P4 控制器测试：

~~~
cd p4-controller
python -m unittest -v test_p4_route_snapshot.py test_single_writer_control.py
~~~

当前发布版本包含 69 项 Kubernetes Controller 测试和 7 项 P4 控制与快照测试。

## 文档入口

- docs/PROJECT_FULL_DETAILS.md：完整项目说明书，推荐首先阅读。
- docs/P4_CONTROL_PLANE_INTEGRATION.md：Kubernetes 到 P4 的真实转发闭环。
- docs/P4_SYSTEMD_HANDOVER.md：P4 单写者、动态快照与故障恢复。
- docs/CONTROLLER_REFRESH_ARCHITECTURE.md：双时间尺度缓存与 Status 去重。
- docs/ALGORITHM_BASELINE.md：最短预计完成时间算法基线。

## 安全说明

请使用自己的 Secret、节点标签、Worker 配置和实验网络。

P4 live 控制器仅适用于已明确授权的实验设备。不要将其直接用于未知或共享的生产网络。

