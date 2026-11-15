#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstring>

#include "agent/agent.hpp"

namespace {

std::atomic<bool> g_shutdown{false};

void handle_signal(int) {
  // 信号处理函数里只做原子写，真正的清理在主循环里完成。
  g_shutdown.store(true, std::memory_order_release);
}

void print_usage() {
  std::fprintf(stderr,
               "kube-p4 node-agent\n"
               "用法: node_agent [--selfcheck | --help]\n"
               "环境变量:\n"
               "  AGENT_BACKEND_IDS        必填，逗号分隔的 Worker 槽位，须在 receiver 白名单内\n"
               "  AGENT_REPORT_HOST        默认 metrics-receiver.kube-orchestrator.svc\n"
               "  AGENT_REPORT_PORT        默认 9999\n"
               "  AGENT_REPORT_INTERVAL_MS 默认 200\n"
               "  AGENT_STATUS_FILE        可选，本地工作负载状态 JSON\n"
               "  AGENT_CGROUP_ROOT        默认 /sys/fs/cgroup\n");
}

}  // namespace

int main(int argc, char** argv) {
  std::signal(SIGINT, handle_signal);
  std::signal(SIGTERM, handle_signal);

  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--selfcheck") == 0) {
      return agent::selfcheck();
    }
    if (std::strcmp(argv[i], "--help") == 0 || std::strcmp(argv[i], "-h") == 0) {
      print_usage();
      return 0;
    }
    std::fprintf(stderr, "未知参数: %s\n", argv[i]);
    print_usage();
    return 2;
  }

  const agent::AgentConfig config = agent::AgentConfig::from_environment();
  const std::string error = agent::validate(config);
  if (!error.empty()) {
    std::fprintf(stderr, "[AGENT] 配置错误: %s\n", error.c_str());
    return 2;
  }

  agent::NodeAgent node(config);
  return node.run(g_shutdown);
}
