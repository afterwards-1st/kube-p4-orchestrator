#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace agent {

struct AgentConfig {
  // 上报的逻辑 Worker 编号（每个都必须在 metrics_receiver 的 WORKER_SLOTS 白名单内）。
  // 一个节点可承载多个槽位，agent 会为每个编号各发一份报文。
  std::vector<std::string> backend_ids;
  std::string report_host = "metrics-receiver.kube-orchestrator.svc";
  uint16_t report_port = 9999;
  int report_interval_ms = 200;  // 与 Python worker 模拟器的默认上报周期一致
  std::string status_file;       // 可选：本地工作负载状态文件路径
  std::string cgroup_root = "/sys/fs/cgroup";

  // 从环境变量构建配置；非法/缺失的值回落到默认值，最终由 validate() 统一裁决。
  static AgentConfig from_environment();
  static AgentConfig from_getenv(const std::function<const char*(const char*)>& getenv_fn);
};

// 返回空串表示配置合法，否则返回人类可读的错误原因。
std::string validate(const AgentConfig& config);

}  // namespace agent
