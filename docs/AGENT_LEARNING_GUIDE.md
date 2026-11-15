# C++ 节点 Agent 学习指南

配套代码：`agent/` 目录（设计背景与开发过程见 `docs/AGENT_DEVELOPMENT_LOG.md`）。
本文档不是说明书，而是**学习材料**：逐模块讲清"代码做了什么、为什么这么写、
背后的知识点是什么、去哪里补课、面试会怎么问"。开发日期：2026-10-28。

## 0. 建议的学习路径

~~~
第 1 步（半天）：把代码跑起来
    cmake -S agent -B agent/build && cmake --build agent/build -j
    ctest --test-dir agent/build
    AGENT_BACKEND_IDS=216 ./agent/build/node_agent --selfcheck
    bash agent/tests/smoke_local.sh
第 2 步（1~2 天）：按本文档第 2 章顺序精读源码，每个模块读完做一遍自测题
第 3 步（按需）：第 3 章容器部署知识点、第 4 章外部资源补课
第 4 步（面试前）：第 5 章自测清单全部口述作答
~~~

目标不是背答案，而是做到：**任何一个文件里任何一行，你都能说出"为什么这么写、
换一种写法会怎样"**。

## 1. 全景：一次上报的生命周期

~~~
main() (src/main.cpp)
  |-- std::signal(SIGINT/SIGTERM, handler)      信号 → 原子标志
  |-- AgentConfig::from_environment()           读环境变量 (src/config.cpp)
  |-- validate(config)                          配置校验，错误直接退出码 2
  v
NodeAgent::run(shutdown) (src/agent.cpp)
  |-- 每 report_interval_ms 一次 tick(reason)
  |     |-- read_workload_stats()               读工作负载状态文件 (src/workload_stats.cpp)
  |     |-- ResourceReader::sample()            cgroup v2 / proc 采样 (src/resource_reader.cpp)
  |     |-- build_report_payload()              构造 JSON 报文 (src/payload.cpp)
  |     `-- Reporter::send()                    UDP sendto (src/reporter.cpp)
  |-- shutdown 置位后退出循环
  `-- tick("agent_shutdown")                    补发最终上报，打印统计，退出
~~~

记住这张图，后面每一章都是图上的一个节点。

## 2. 模块精讲

### 2.1 main.cpp — 入口、信号与优雅停机的地基

**代码要点**：信号处理函数只有一行 `g_shutdown.store(true, std::memory_order_release)`，
真正的清理（补发上报、打日志、关 socket）全部在主循环里完成。

**为什么必须这样**：信号可能在程序执行到**任意一条指令**时打断它，包括 `malloc`
执行到一半、持着堆锁的时候。信号处理函数里能安全调用的函数集合叫
**async-signal-safe**（`man 7 signal-safety`），`printf`、`malloc`、锁操作都不在其中。
所以我们只做一件事：原子写一个标志位，让正常流程自己发现并退出。

**关键知识点**：
- `std::atomic<bool>` 的 `store(release)` / `load(acquire)` 配对：release 保证
  "写标志之前的所有写操作"对 acquire 读到标志之后可见。本项目标志位之后没有
  更多写操作，所以这里 memory_order 只是规范用法；但要能讲清 acquire/release
  的语义，以及它和 `memory_order_seq_cst`（默认、最严格）的区别。
- 传统 C 的等价物是 `volatile sig_atomic_t`；`std::atomic` 是 C++11 后的正确选择。
- 为什么不在线程里 `sleep(interval)` 一刀切：那样 SIGTERM 后最多要等满一个上报周期
  才退出，可能超过 K8s 给的 `terminationGracePeriodSeconds`。所以主循环以 20ms
  步进小睡，随时响应退出。

**自测题**：信号处理函数里调用 `printf` 会出什么问题？`volatile` 能保证原子性吗？
K8s 删 Pod 时容器收到什么信号、等多久、之后会发生什么（SIGKILL）？

### 2.2 config.cpp — 配置、校验与依赖注入

**代码要点**：`from_getenv(getenv_fn)` 接受一个"取环境变量的函数"参数——生产路径传
`std::getenv`，测试路径传自己的 lambda。这是**依赖注入**的最小形式：把"从环境读数"
这个副作用抽出来，纯逻辑才能脱离操作系统测试。

**设计取舍**：环境变量解析失败（比如端口写成 `abc`）**不报错，回落默认值**，最后由
独立的 `validate()` 统一裁决。这是 fail-soft 解析 + fail-fast 校验的组合：让配置层
宽容、让校验层严格，错误信息集中在一处。另一种流派是解析即报错（fail-fast 到底），
你能说出两者的适用场景吗？

**关键知识点**：容器应用用环境变量做配置是 12-Factor App 的第一条；
`std::function` 的开销与 `auto&&` 模板参数的取舍。

**自测题**：为什么 `validate()` 是自由函数而不是成员函数？`AGENT_BACKEND_IDS` 为什么要
同时兼容单数形式的 `AGENT_BACKEND_ID`？

### 2.3 json_writer / flat_json — 零依赖 JSON 的取舍

**代码要点**：序列化器保插入序、输出紧凑 JSON；解析器只支持一层扁平对象，
遇到 `{`/`[` 嵌套直接报错。

**为什么不用 nlohmann/json**：报文结构固定、一层扁平，自写约 300 行换来
零依赖、可控、每行可测可讲。代价是功能边界必须写进注释和文档。
**面试官问"为什么造轮子"时的标准答案**：先讲需求边界（扁平、字段固定），
再讲收益（构建链零依赖、镜像更小、无供应链风险），最后承认边界（不支持嵌套，
需要时换库）——承认边界比假装全能更加分。

**关键知识点**：
- JSON 转义规则：`"`、`\`、控制字符（`\u00XX`）、为什么 UTF-8 字节可以原样输出
  （合法 UTF-8 序列不包含 `"` 和 `\`，也不含 <0x20 的字节）。
- NaN/Infinity 在 JSON 中不存在，序列化器统一落成 0——保证报文永远可解析，
  这是"报文有效性优先于数值保真"的取舍。
- `std::from_chars` 严格解析：`parse_ll("12abc")` 返回 false（要求整段消耗完），
  这是它相对 `atoi`（静默吞掉尾随垃圾）、`strtod`（需要保证 NUL 结尾）的核心优势。

**自测题**：解析器遇到重复 key 会怎样（后写覆盖）？为什么解析器对 `{"a":1,}`
（尾逗号）报错？`\uXXXX` 如果是代理对（emoji）我们的处理有什么缺陷？

### 2.4 workload_stats.cpp — 状态文件契约与降级

**代码要点**：三种情形三分处理——路径未配置/文件不存在（正常，上报空闲心跳）、
文件存在但 JSON 坏（error 上抛，主循环打日志，用默认值继续）、字段值非法
（单字段回落默认值并追加 warning，其他字段照常用）。

**学习点**：这是**降级策略**的典型案例。agent 的本职是"把观测到的东西报上去"，
状态文件坏掉不应该让采集停摆。对比：如果是计费场景，同样的坏文件就应该
fail-fast 拒绝上报。**容错粒度取决于业务语义**——这句话面试很值钱。

### 2.5 resource_reader.cpp — 本项目最有面试密度的模块

这是重点，分四块讲。

**(a) 为什么容器内读 /sys/fs/cgroup 就是本容器的用量**

Linux 容器 = namespace（看什么）+ cgroup（能用多少）。其中 **cgroup namespace**
让容器内的 `/proc/self/cgroup` 显示 `0::/`（本容器自己的根），`/sys/fs/cgroup`
挂载的就是容器自己的 cgroup 子树。于是读 `memory.current` 得到的就是本容器的
内存用量，不需要任何特权。这解释了 manifest 里为什么不需要 privileged/hostPath。

**(b) cgroup v2 关键文件**

| 文件 | 含义 | 本项目的解析函数 |
| --- | --- | --- |
| `/proc/self/cgroup` | `0::/路径` 行是 v2 层级 | `extract_v2_cgroup_path` |
| `cpu.stat` | `usage_usec` 是累计 CPU 时间（微秒） | `parse_usage_usec` |
| `cpu.max` | `quota period`，如 `400000 100000` = 4 核；`max` = 不限 | `parse_cpu_max_cores` |
| `memory.current` | 当前内存用量字节 | 直接 `parse_ll` |
| `memory.max` | 内存限额；`max` = 不限 | `parse_memory_max_bytes` |

**(c) CPU 百分比为什么必须差分**

`usage_usec` 是**累计值**，单次读它没有意义。相邻两次采样相减除以墙钟时间差：

~~~
cpu% = Δusage_usec / Δwall_time_usec × 100     （相对单核，可超过 100）
~~~

由此推出两个实现细节：首次采样没有基准点，CPU% 为 0（面试高频）；墙钟必须用
**单调时钟** `steady_clock`——`system_clock` 会被 NTP 校时回拨，差分可能算出负数。
`ts` 字段则用 `system_clock`，因为报文时间戳要的是墙上时间。**哪个时钟干什么用**
是标准面试题。

**(d) /proc/self/stat 解析的坑**

文件格式是 `pid (comm) state ppid ... utime stime ...`，而 **comm 是进程名，可以含
空格甚至括号**。所以不能按空格天真切字段，必须先定位**最后一个** `)`，其后从
state（第 3 字段）开始数，utime 是第 14 字段、stime 第 15 字段（即去掉前导空格后的
index 11、12）。时间单位是 tick，要除以 `sysconf(_SC_CLK_TCK)`（通常 100）换算成秒。

**补课重点**（外部学习，见第 4 章）：cgroup v2 内核文档、namespace 七种类型、
man 5 proc。这部分是容器岗面试的必考区。

### 2.6 reporter.cpp — UDP、DNS 与 Pimpl

**UDP 的使用方式**：`socket(AF_INET, SOCK_DGRAM, 0)` 建套接字 → `sendto()` 直接发，
**没有 connect、没有三次握手、没有重传**。发送失败只计数不重试——指标是可容忍
丢失的时效数据，丢了下个周期就有新的，重试反而把旧数据挤占带宽。**什么时候必须
TCP**：命令下发、配置同步、任何"丢了会出事"的报文。

**DNS 重试设计**：首次解析失败（receiver Pod 还没就绪、DNS 还没注册）不致命，
`resolved` 标记保持 false，下次 `send()` 时重新 `getaddrinfo`。发送失败也会把
`resolved` 复位——覆盖 receiver 重启换 IP 的场景。

**Pimpl 惯用法**（pointer to implementation）：

- `reporter.hpp` 里只有 `struct Impl;` 前向声明和一个 `Impl*`，POSIX 头文件
  （`<sys/socket.h>` 等）全部收进 `reporter.cpp`。
- 收益一：**编译防火墙**——改 POSIX 相关实现不用重编所有包含此头的文件；
  收益二：**可移植性**——头文件在 Windows 上也能被包含（测试目标不含 reporter）。
- 代价：每次访问 impl_ 多一层间接；`new/delete` 一次堆分配（本例一个对象一个周期，
  可忽略；严格来说可用 `std::unique_ptr<Impl>`，析构需要注意完整类型问题）。
- 拷贝构造和拷贝赋值**显式 delete**（rule of five）：socket 句柄不能被浅拷贝双关。

**自测题**：UDP sendto 一个未就绪的 receiver，调用会失败吗（大多数情况不会——
UDP 无连接，除非返回 ICMP 不可达）？为什么 close 放在析构函数里而不是 run() 里？

### 2.7 agent.cpp — 主循环与 selfcheck

**单线程的理由**：采集（读几个小文件）和上报（一次 sendto）都是微秒级，
一个定时循环绰绰有余；而且 CPU% 差分依赖"采样间隔由同一流程产生"，
单线程天然保证。**面试引导**：主动说"我知道这里可以多线程/事件循环，
但复杂度必须匹配需求"——这句话体现的是工程判断力，比堆技术名词值钱。

**日志节流**：运行状态每 30 秒输出一次，错误即时输出。因为容器场景 stdout/stderr
会被日志系统采集，每 100ms 打一行等于制造日志洪水。

**selfcheck 的边界**：只校验"本机的事"（配置合法、资源能采到），**故意不探测
receiver**——否则 receiver 一重启，所有 agent 的 livenessProbe 一起失败、一起重启，
故障被放大。探针只应该反映"我自己还能不能干活"。

### 2.8 测试设计（test_agent.cpp / smoke_local.sh）

两个可测性原则，值得迁移到任何项目：

1. **纯函数与 IO 分离**：所有解析函数接收"文件内容字符串"而不是"文件路径"，
   文件读取只有一层薄封装。于是 109 项检查不需要 mock 文件系统。
2. **两层测试**：单元测试验证逻辑（快、无副作用、每次构建都跑）；冒烟脚本验证
   真实链路（起真 receiver、真 agent、curl 快照、python 断言）——它回答的问题
   单测回答不了："两个真实进程在真实 UDP 网络上对得上吗"。

## 3. 部署文件里的容器知识点（daemonset.yaml / Dockerfile）

| 配置 | 含义 | 为什么这么写 |
| --- | --- | --- |
| `kind: DaemonSet` | 每个节点跑一个 Pod | node agent 的天然形态：观测是节点维度的 |
| `runAsNonRoot: true` + `runAsUser: 65532` | 非 root 运行 | agent 只读 /proc 和 cgroup，root 是多余攻击面；65532 是 distroless 内置 nonroot 用户 |
| `capabilities.drop: ["ALL"]` | 删除全部 Linux capabilities | 同上，最小权限原则 |
| `readOnlyRootFilesystem: true` | 根文件系统只读 | agent 无本地状态，强制不可写可防篡改 |
| `automountServiceAccountToken: false` | 不挂 K8s ServiceAccount 令牌 | agent 不调 API Server，这个 token 就是泄露面 |
| `livenessProbe.exec: --selfcheck` | 探活 | 见 2.7：只反映自身健康，不探测外部依赖 |
| `resources.requests/limits` | 调度保证与硬上限 | limits 会转成容器 cgroup 的 cpu.max/memory.max——**所以 agent 自己也被 cgroup 管**，与 2.5 呼应 |
| `terminationGracePeriodSeconds: 5` | SIGTERM 后的宽限期 | 必须大于优雅停机所需时间；超时 SIGKILL |
| `tolerations: control-plane` | 允许调度到控制面节点 | 实验环境节点少；生产按需取舍 |

**Dockerfile 两个考点**：多阶段构建（构建链 `debian:bookworm-slim` + g++/cmake，
构建期跑全部单测；运行镜像只带走一个二进制）；distroless 运行时**没有 shell**，
所以 HEALTHCHECK 和探针都必须用 **exec 形式**（`CMD ["/node_agent","--selfcheck"]`）
而不是 shell 形式。distroless 的调试方式是 `kubectl debug`（临时排错容器），
这也是常见面试题。

## 4. 关键点外部学习地图

按"项目里出现 → 去哪补"整理。**优先级排序：第 1 梯队是 cgroup/namespace/UDP/信号**
（容器 C++ 岗最高频），第 2 梯队是 C++ 语言层，第 3 梯队是工程化。

| 知识点 | 项目落点 | 推荐资源 |
| --- | --- | --- |
| cgroup v2 | resource_reader.cpp 全部 | 内核文档 *cgroup-v2*（kernel.org，Documentation/admin-guide/cgroup-v2.rst）；`man 7 cgroups` |
| namespace 与容器原理 | 2.5(a) 的原理 | `man 7 namespaces`；B 站搜"容器原理 namespace cgroup"；动手向：搜 "build containers from scratch"（用 Go/C 手写迷你容器） |
| /proc 文件系统 | /proc/self/stat、status 解析 | `man 5 proc` |
| UDP/socket API | reporter.cpp | Beej's Guide to Network Programming（beej.us/guide/bgnet，入门经典）；APUE 第 16 章或 TLPI 第 57~61 章 |
| 信号 | main.cpp | `man 7 signal-safety`；APUE 第 10 章 |
| 时钟语义 | CPU% 差分 | `man 7 time`；cppreference 搜 steady_clock |
| 现代 C++（RAII/智能指针/atomic/Pimpl/from_chars） | 全部代码 | learncpp.com（系统过一遍）；cppreference.com（当字典）；《Effective Modern C++》条款 18~22、31~40 |
| 内存序 acquire/release | g_shutdown | cppreference memory_order 页；面试层面讲到"release 写、acquire 读、可见性配对"即可，不必深入模型 |
| Kubernetes 对象与安全 | daemonset.yaml | kubernetes.io/zh-cn 官方中文文档：DaemonSet、探针、Security Context、Pod 生命周期（termination）页 |
| Docker 多阶段/distroless | agent/Dockerfile | Docker 官方 docs "Multi-stage builds"；distroless GitHub（GoogleContainerTools/distroless） |
| 中文八股体系化 | 面试总复习 | 小林 coding（xiaolincoding.com）《图解网络》《图解系统》——网络与 OS 面试题的主粮 |
| CMake | CMakeLists.txt | 官方 Tutorial（cmake.org），过一遍 Step1~Step5 足够 |

**学习方法建议**：不要从第一本从头读到尾。每读完本文档一个模块，直接去对应
资源把**那一个点**吃透，回来把自测题口述答一遍。零散时间刷小林 coding，
整块时间读内核文档和动手。

## 5. 面试自测清单（全部要求口述作答）

**C++ 语言层**
1. 为什么信号处理函数里只能做原子写？async-signal-safe 是什么？
2. `memory_order_release`/`acquire` 配对保证了什么？默认的 seq_cst 更强在哪？
3. Pimpl 是什么、解决什么问题、代价是什么？
4. 为什么 Reporter 删除了拷贝构造？rule of five 是什么？
5. `std::from_chars` 比 `atoi`/`strtod` 强在哪？
6. JSON 里为什么没有 NaN？我们怎么处理的？

**Linux/系统层**
7. cgroup v2 的 `cpu.max` 怎么读出"4 核"这个数？
8. 为什么容器内 `/sys/fs/cgroup` 就是本容器的根？（cgroup namespace）
9. `usage_usec` 是什么口径的数？CPU 百分比为什么要两次采样？首次为什么是 0？
10. `steady_clock` 和 `system_clock` 的区别，本项目各自用在哪？
11. `/proc/self/stat` 为什么不能直接按空格切字段？utime 在哪个位置？
12. UDP 无连接是什么意思？sendto 到不存在的地址会失败吗？
13. getaddrinfo 的作用？为什么解析失败后要允许重试？

**容器/K8s 层**
14. namespace 和 cgroup 各管什么？
15. DaemonSet 和 Deployment 的区别？node agent 为什么用 DaemonSet？
16. K8s 删一个 Pod 的完整流程（SIGTERM → grace period → SIGKILL）？
17. readOnlyRootFilesystem、drop ALL capabilities、非 root 各防什么？
18. livenessProbe 失败会发生什么？为什么 selfcheck 故意不探测 receiver？
19. requests 和 limits 分别影响什么？limits 落到容器里变成了什么（cgroup）？
20. distroless 镜像没有 shell，出了故障怎么调试（kubectl debug）？

**工程层**
21. 为什么自写 JSON 而不用库？边界在哪？
22. 状态文件损坏为什么选择降级而不是崩溃？什么时候应该反过来？
23. 单元测试和冒烟测试各自回答什么问题？
24. 日志为什么要节流？

答不上来的题，回第 2、3 章找对应小节，再顺着第 4 章补课。

## 6. 吃透之后的动手练习（选做，按收益排序）

1. 给 `flat_json` 补上 `\uD8xx\uDCxx` 代理对解析（练 UTF-16 → UTF-8 编码）。
2. 把 agent 的资源采集扩展到 `io.stat`（磁盘读写）和 `memory.stat` 的
   page_faults（练 cgroup 文件族）。
3. 给 receiver 写一个并发压测脚本，统计接收 PPS 与丢包率（练 UDP 语义与
   socket 缓冲区）。
4. 用纯 C++ 给 agent 加一个只读 HTTP `/stats` 接口——这是你将来学 epoll/
   Reactor 模式时的天然入口（练 socket server、accept、解析 HTTP GET）。
5. 把 agent_tests 迁移到 GoogleTest 并接入 GitHub Actions（练 CI 与主流测试框架）。

每做完一个，简历对应 bullet 就多一句真话。
