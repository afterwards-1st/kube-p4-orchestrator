#include "agent/workload_stats.hpp"

#include "agent/file_util.hpp"
#include "agent/flat_json.hpp"

namespace agent {
namespace {

void read_ll_field(const std::map<std::string, std::string>& values, const char* key,
                   long long& target, std::string& warning) {
  const auto it = values.find(key);
  if (it == values.end()) {
    return;
  }
  long long parsed = 0;
  if (parse_ll(it->second, parsed)) {
    target = parsed > 0 ? parsed : 0;
  } else {
    warning += std::string(warning.empty() ? "" : "; ") + "字段 " + key + " 不是合法整数";
  }
}

void read_double_field(const std::map<std::string, std::string>& values, const char* key,
                       double& target, std::string& warning) {
  const auto it = values.find(key);
  if (it == values.end()) {
    return;
  }
  double parsed = 0.0;
  if (parse_double(it->second, parsed)) {
    target = parsed > 0.0 ? parsed : 0.0;
  } else {
    warning += std::string(warning.empty() ? "" : "; ") + "字段 " + key + " 不是合法数字";
  }
}

}  // namespace

WorkloadStats workload_stats_from_values(const std::map<std::string, std::string>& values,
                                         std::string& warning) {
  WorkloadStats stats;
  read_ll_field(values, "queue_depth", stats.queue_depth, warning);
  read_ll_field(values, "completed_tasks", stats.completed_tasks, warning);
  read_ll_field(values, "total_batches", stats.total_batches, warning);
  read_ll_field(values, "last_flush_size", stats.last_flush_size, warning);
  read_double_field(values, "last_process_time_ms", stats.last_process_time_ms, warning);
  read_double_field(values, "last_avg_wait_ms", stats.last_avg_wait_ms, warning);
  read_double_field(values, "last_batch_efficiency", stats.last_batch_efficiency, warning);

  const auto reason = values.find("last_flush_reason");
  if (reason != values.end() && !reason->second.empty()) {
    stats.last_flush_reason = reason->second;
  }
  return stats;
}

WorkloadStats read_workload_stats(const std::string& path, bool& found, std::string& error) {
  WorkloadStats stats;
  found = false;
  error.clear();
  if (path.empty()) {
    return stats;
  }
  std::string content;
  if (!read_file_to_string(path, content)) {
    // 文件不存在属于正常情况：agent 尚未对接工作负载时以空闲心跳上报。
    return stats;
  }
  found = true;
  std::map<std::string, std::string> values;
  std::string parse_error;
  if (!parse_flat_json(content, values, parse_error)) {
    error = "状态文件解析失败: " + parse_error;
    return stats;
  }
  std::string warning;
  stats = workload_stats_from_values(values, warning);
  if (!warning.empty()) {
    error = warning;
  }
  return stats;
}

}  // namespace agent
