# kube-p4-orchestrator 上手手册

目标：**照着本手册走一遍，三天内熟悉到"面试能讲、代码能答"，期间随时可以投简历**。
本文件夹自包含：从项目介绍到部署、代码跟读、上传排障记录、面试速查，按编号顺序读。

## 学习路线（建议节奏）

| 阶段 | 内容 | 用时 |
| --- | --- | --- |
| 第 0 步 | 读 `01-overview.md`，建立全局图景 | 30 分钟 |
| 第 1 步 | 按 `02-deployment.md` 把项目在本地/集群跑起来 | 1~2 小时 |
| 第 2 步 | 跟 `03-code-walkthrough.md` 通读核心代码（对照仓库） | 1 天 |
| 第 3 步 | 读 `04-ci-debug-log.md`，了解这个项目是怎么上线的（**面试最爱问的工程故事**） | 40 分钟 |
| 第 4 步 | 用 `05-interview-cheatsheet.md` 定稿简历，开始投递 | 1 小时 |
| 冲刺期 | 深读 `docs/SYSTEM_DESIGN.md` + `docs/AGENT_LEARNING_GUIDE.md` 的 24 道自测题 | 按面试节奏 |

## 各文档定位

- `01-overview.md`：项目是什么、架构全景、一次任务的完整生命周期、所有关键数字。
- `02-deployment.md`：五种部署/运行方式（Helm 集群部署、kind e2e、本地开发、面板演示、拉取 GHCR 镜像），全部可直接复制的命令。
- `03-code-walkthrough.md`：代码跟读顺序图——每个模块读哪个文件、重点看哪些函数、读完应该能回答什么。
- `04-ci-debug-log.md`：上传到 GitHub 后 CI 六轮排障的完整记录（现象 → 诊断手段 → 根因 → 修复 → 教训），以及"匿名诊断 Actions 失败"的方法论。
- `05-interview-cheatsheet.md`：简历文案、讲述脚本、数字速查表、高频必答题索引。

## 一分钟速览本项目

基于 Kubernetes CRD 的协同调度系统：**ComputeTask CRD** 描述任务需求 → **自定义控制器**
融合实时指标生成多 Worker 分配方案（plan/apply 双门禁）→ 方案中的 flowId→backendId
映射写入 **P4 可编程交换机**，让任务流真实转发到被调度的 Worker → Worker 的
**C++17 节点 agent**（DaemonSet）经 cgroup v2 采集真实容器用量并 UDP 回传 →
闭环。交付侧：Helm Chart 统一三组件、GitHub Actions CI 含 kind 端到端验证、
Prometheus 指标、单文件可视化面板。

- 仓库：<https://github.com/afterwards-1st/kube-p4-orchestrator>
- CI 徽章（建议加到根 README 顶部）：
  `![ci](https://github.com/afterwards-1st/kube-p4-orchestrator/actions/workflows/ci.yml/badge.svg)`
