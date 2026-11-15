#pragma once

#include <atomic>
#include <string>
#include <vector>

#include "agent/config.hpp"
#include "agent/reporter.hpp"
#include "agent/resource_reader.hpp"
#include "agent/workload_stats.hpp"

namespace agent {

// 单线程采集-上报主循环：
//   每个 tick 读取工作负载状态文件与容器资源采样，为每个 backend 编号构造并
//   发送一份数据报；shutdown 被置位后补发一条 agent_shutdown 最终上报再退出。
class NodeAgent {
 public:
  explicit NodeAgent(AgentConfig config);

  // 阻塞运行直到 shutdown 置位；正常退出返回 0。
  int run(const std::atomic<bool>& shutdown);

  const ReporterStats& reporter_stats() const { return reporter_.stats(); }

 private:
  void tick(const std::string& reason);

  AgentConfig config_;
  Reporter reporter_;
  ResourceReader resources_;
};

// --selfcheck：校验配置、读取一次资源采样并输出 JSON 诊断，不发送任何报文。
// 被 Docker HEALTHCHECK 和 K8s livenessProbe 复用。
int selfcheck();

}  // namespace agent
