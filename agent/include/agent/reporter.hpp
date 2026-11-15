#pragma once

#include <string>

#include "agent/config.hpp"

namespace agent {

struct ReporterStats {
  long long sent_reports = 0;
  long long failed_reports = 0;
};

// UDP 上报器：尽力而为，失败只计数不重试。
// 指标链路可容忍丢失，时效性由 receiver 的 stale_after_seconds 过期过滤兜底；
// 首次解析 DNS 失败（比如 receiver 还没就绪）会在后续每次发送时自动重试解析。
class Reporter {
 public:
  explicit Reporter(const AgentConfig& config);
  ~Reporter();

  Reporter(const Reporter&) = delete;
  Reporter& operator=(const Reporter&) = delete;

  bool send(const std::string& payload);
  const ReporterStats& stats() const { return stats_; }

 private:
  struct Impl;
  bool resolve_target();

  Impl* impl_ = nullptr;
  ReporterStats stats_;
};

}  // namespace agent
