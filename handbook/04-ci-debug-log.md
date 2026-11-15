# 04 · 上传与 CI 排障全记录

本文完整记录把项目推上 GitHub（afterwards-1st/kube-p4-orchestrator）并让 CI
从零到全绿的全过程：上传前的安全审计、提交整理，以及 **6 轮 CI 排障**的
现象/诊断/根因/修复/教训。这是"真实工程落地"最好的面试素材。

## 0. 上传前的准备

- **敏感信息审计**：全仓 grep 凭据模式（password/token/api_key 赋值）——无真实
  Secret（脚本均走环境变量或 token 文件，`*token*` 已在 .gitignore）；把 216
  Worker manifest 里真实设备风格的 MAC 换成与 226 一致的合成 MAC；实验室私有 IP
  （RFC1918）与节点名按 README 安全声明保留。
- **提交整理**：在既有 5 个提交之上按模块拆 17 个语义化 commit（agent /
  dashboard+Prometheus / helm+e2e / ci / docs…）。
- **rebase 整合网页编辑**：远端多了网页上改的 "Update README.md"（删除两节），
  rebase 保留该意图。
- **隐私**：提交身份用 noreply 邮箱。
- **网络**：直连 GitHub 被重置，推送用 `git -c http.proxy=http://127.0.0.1:7890`。

## 1. 诊断方法论（比修复本身更值钱）

**问题**：Actions 失败日志有三重门——
1. 网页日志要求登录；
2. API 下载日志（/jobs/{id}/logs）要求仓库管理员权限；
3. Job Summary 与日志同权限。

**解法（本项目沉淀出的匿名诊断体系）**：
1. e2e 脚本内置 `annotate()` 辅助函数，用 **workflow 命令**
   `::error title=E2E::...` / `::notice::` 把诊断写进 **check-run 注解**——
   注解对匿名用户开放，可用公开 API 读取：
   `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` → 找失败 check-run id →
   `GET /repos/{owner}/{repo}/check-runs/{id}/annotations`。
2. 关键失败点（helm 安装、镜像构建）先 `tee` 到文件，失败时把 **tail 注解化**。
3. 脚本设置 **ERR 陷阱** + `LAST_STEP` 变量：任何未显式处理的命令失败都会以
   `::error::` 带上"失败于哪一步、第几行"退出。
4. 镜像构建失败时注解携带构建日志尾部（25~45 行）。

这套体系让每一轮失败都能在 **1~2 分钟内拿到确切根因**，无需登录。

## 2. 六轮排障时间线

| 轮 | 提交 | 现象 | 根因 | 修复 |
| --- | --- | --- | --- | --- |
| R1 | a2e08c6 首推 | tests/chart ✓；images job 在"构建并推送 node-agent"步失败；e2e job 1m9s 即失败 | 当时未取证（诊断体系还没有） | 加注解体系 |
| R2 | 4757d81 | e2e 注解首次生效：`docker build agent` 在 **cmake configure 阶段 0.127s 失败** | 误判为 GHCR 嵌套路径推送问题，改名（后证伪） | 镜像名改中划线单段 |
| R3 | 483372c | 注解携带完整构建日志 → **铁证：`CMake was unable to find a build program corresponding to "Unix Makefiles". CMAKE_MAKE_PROGRAM not found`** | agent Dockerfile `apt-get install --no-install-recommends g++ cmake` **没装 make**（make 是 cmake 的 Recommends） | Dockerfile 补 `make` |
| R4 | df7fec5 | make 修复后构建通过；e2e 失败于 **[3/7] helm --wait 超时**（ERR 陷阱定位），无更多输出 | 当时未知（疑 Pod 未调度） | 加 tolerations + helm 输出捕获 + Pod 状态注解 |
| R5 | 99675d3/f7abbf2 | Pod 状态注解：三组件全部 **ImagePullBackOff**；`docker-imgs` 注解显示 runner 上只有 `:ci` 镜像；pull-msg 注解给出 `pull access denied ... docker.io/library/kube-orchestrator:e2e` | **镜像标签错位**：e2e job 环境变量构建/加载 `:ci`，values-kind 部署 `:e2e` | ci.yml 环境变量对齐为 `:e2e` |
| R6 | 9dd91e1 | 前五步全过（含 reconcile Planned），最后一步 [7/7] 指标链路不 fresh；注解抓到 **`/node_agent: error while loading shared libraries: libstdc++.so.6`** CrashLoopBackOff | distroless **base** 变体不含 C++ 运行时；agent 是 g++ 动态链接 | 运行镜像换 **distroless/cc-debian12**（自带 libstdc++/libgcc） |
| ✓ | 3d63ee9 | 四个 job 全部 success；GHCR 发布两个镜像 | — | — |

最终全绿运行：<https://github.com/afterwards-1st/kube-p4-orchestrator/actions/runs/43733073955>

## 3. 根因明细与"为什么本地没发现"

### 3.1 `--no-install-recommends` 缺 make（R2→R3）
- **为什么本地没暴露**：WSL 里早就装过 make；本地构建跑的是 g++15+cmake4.2 环境。
- **为什么 0.127s 就失败**：configure 在编译器检查之前就找不到 Make 程序，
  直接报 `CMAKE_MAKE_PROGRAM not found`。
- **教训**：`--no-install-recommends` 是镜像瘦身标配，但 cmake 的默认生成器
  依赖 make（Recommends 级），**最小化安装后必须显式补齐工具链闭环**。

### 3.2 镜像标签错位（R5）
- e2e job 的 env 用 `:ci`，values-kind.yaml 部署 `:e2e`——构建加载的镜像与
  Deployment 引用的镜像名字不一致，Pod 去 docker.io 拉公开仓库，404 进 BackOff。
- **教训**：CI 里"构建的镜像"和"部署引用的镜像"必须从**同一个变量**派生；
  注解里的 `docker images` 清单一眼就暴露了清单上根本没有 `:e2e`。
- **顺带的真话**：同时加的 tolerations 属于防御性修复——注解显示 Pod 已经被
  调度并开始拉镜像（ImagePullBackOff 而非 Pending），说明标签错位才是主因；
  tolerations 保留（kind 单节点是控制面，带污点，留着无害且更稳）。

### 3.3 distroless base vs cc（R6）
- base-debian12 只有 libc/glibc 系；g++ 编译的动态链接二进制需要 libstdc++.so.6。
- **教训**：C++ 二进制的 distroless 运行时选 `cc` 变体；或编译期 `-static`。
  本地 WSL 有完整工具链所以从未暴露——**运行时镜像的依赖只在真实容器里验证**。

## 4. 过程中沉淀的基础设施（已留在仓库里）

- `scripts/e2e_kind.sh`：annotate/log_step/fail/ERR 陷阱/build_and_load
  （构建失败自动注解日志尾部）——任何 CI 失败 2 分钟内定位。
- Helm/manifests 的修复：metricsReceiver.tolerations 可配置化；agent Dockerfile
  工具链闭环 + cc 运行时。
- 修复全部以独立小 commit 提交，历史即排障日志。

## 5. 复盘教训（面试可直接引用）

1. **先建诊断，再修问题**：前两轮在无日志状态下的猜测（GHCR 嵌套路径）浪费了一轮；
   注解体系建立后每轮都拿到铁证。
2. **本地绿 ≠ CI 绿**：容器内工具链（make）、容器运行时（libstdc++）、集群拓扑
   （污点）这三类问题只会在 CI 的真实环境暴露——e2e 的价值正在于此。
3. **一次只改一个变量**：R4 同时改了 tolerations 和 helm 捕获，导致 R5 才能归因
   到真正的根因；复盘时明确承认这一点比假装顺利更加分。
4. **错误信息要带上下文进注解**：单句 "Process completed with exit code 1"
   毫无信息量；tail 日志 + Pod 状态 + 镜像清单三件套是排障铁三角。
