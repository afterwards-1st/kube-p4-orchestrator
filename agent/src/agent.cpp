#include "agent/agent.hpp"

#include <chrono>
#include <cstdio>
#include <thread>

#include "agent/json_writer.hpp"
#include "agent/payload.hpp"

namespace agent {
namespace {

using std::chrono::milliseconds;
using std::chrono::seconds;
using std::chrono::steady_clock;

std::string join_backend_ids(const std::vector<std::string>& ids) {
  std::string out;
  for (std::size_t i = 0; i < ids.size(); ++i) {
    if (i != 0) {
      out += ",";
    }
    out += ids[i];
  }
  return out;
}

}  // namespace

NodeAgent::NodeAgent(AgentConfig config)
    : config_(std::move(config)), reporter_(config_), resources_(config_.cgroup_root) {}

void NodeAgent::tick(const std::string& reason) {
  bool found = false;
  std::string error;
  const WorkloadStats workload = read_workload_stats(config_.status_file, found, error);
  const ResourceSample resources = resources_.sample();
  for (const std::string& id : config_.backend_ids) {
    const std::string payload =
        build_report_payload(id, workload, resources, reason, epoch_seconds());
    reporter_.send(payload);
  }
  if (!error.empty()) {
    std::fprintf(stderr, "[AGENT] 状态文件告警: %s\n", error.c_str());
  }
}

int NodeAgent::run(const std::atomic<bool>& shutdown) {
  std::fprintf(stderr,
               "[AGENT] 启动 | backends=%s | report=%s:%u | interval=%dms | status_file=%s\n",
               join_backend_ids(config_.backend_ids).c_str(), config_.report_host.c_str(),
               static_cast<unsigned>(config_.report_port), config_.report_interval_ms,
               config_.status_file.empty() ? "(未配置)" : config_.status_file.c_str());

  const milliseconds interval(config_.report_interval_ms);
  steady_clock::time_point last_status_log = steady_clock::now();
  bool first = true;

  while (!shutdown.load(std::memory_order_acquire)) {
    tick(first ? "start" : "heartbeat");
    first = false;

    const steady_clock::time_point deadline = steady_clock::now() + interval;
    while (!shutdown.load(std::memory_order_acquire)) {
      if (steady_clock::now() >= deadline) {
        break;
      }
      std::this_thread::sleep_for(milliseconds(20));
    }

    // 每 30 秒输出一次运行状态，方便容器日志排查；错误详情随状态日志节流。
    const steady_clock::time_point now = steady_clock::now();
    if (now - last_status_log >= seconds(30)) {
      const ReporterStats& stats = reporter_.stats();
      std::fprintf(stderr, "[AGENT] 运行中 | 已发送=%lld | 失败=%lld\n", stats.sent_reports,
                   stats.failed_reports);
      last_status_log = now;
    }
  }

  tick("agent_shutdown");
  const ReporterStats& stats = reporter_.stats();
  std::fprintf(stderr, "[AGENT] 退出 | 已发送=%lld | 失败=%lld\n", stats.sent_reports,
               stats.failed_reports);
  return 0;
}

int selfcheck() {
  const AgentConfig config = AgentConfig::from_environment();
  const std::string error = validate(config);

  JsonObject out;
  if (!error.empty()) {
    out.set_bool("ok", false);
    out.set("error", error);
    std::fprintf(stdout, "%s\n", out.dump().c_str());
    return 2;
  }

  bool found = false;
  std::string workload_error;
  read_workload_stats(config.status_file, found, workload_error);
  const ResourceSample sample = ResourceReader(config.cgroup_root).sample();

  out.set_bool("ok", true);
  out.set_raw("backend_ids", string_list_json(config.backend_ids));
  out.set("report_host", config.report_host);
  out.set_number("report_port", config.report_port);
  out.set_number("report_interval_ms", config.report_interval_ms);
  out.set_bool("status_file_configured", !config.status_file.empty());
  out.set_bool("status_file_found", found);
  if (!workload_error.empty()) {
    out.set("status_file_warning", workload_error);
  }
  out.set("resource_source", sample.source);
  out.set("cgroup_version", sample.cgroup_version);
  out.set_number("container_mem_usage_bytes", sample.mem_usage_bytes);
  std::fprintf(stdout, "%s\n", out.dump().c_str());
  return 0;
}

}  // namespace agent
