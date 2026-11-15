#include "agent/payload.hpp"

#include <chrono>

#include "agent/json_writer.hpp"

namespace agent {

double epoch_seconds() {
  using namespace std::chrono;
  return duration<double>(system_clock::now().time_since_epoch()).count();
}

std::string build_report_payload(const std::string& backend_id, const WorkloadStats& stats,
                                 const ResourceSample& resources, const std::string& reason,
                                 double ts) {
  JsonObject obj;
  // backend_id 与 Python 模拟器一致使用数字类型；接收端按 int(str(backend_id)) 归一。
  bool all_digits = !backend_id.empty();
  for (char ch : backend_id) {
    if (ch < '0' || ch > '9') {
      all_digits = false;
      break;
    }
  }
  if (all_digits) {
    obj.set_raw("backend_id", backend_id);
  } else {
    obj.set("backend_id", backend_id);
  }

  obj.set_number("queue_depth", stats.queue_depth);
  obj.set("reason", reason);
  obj.set_real("ts", ts, 3);
  obj.set_number("completed_tasks", stats.completed_tasks);
  obj.set_number("total_batches", stats.total_batches);
  obj.set_number("last_flush_size", stats.last_flush_size);
  obj.set("last_flush_reason", stats.last_flush_reason);
  obj.set_real("last_process_time_ms", stats.last_process_time_ms, 3);
  obj.set_real("last_avg_wait_ms", stats.last_avg_wait_ms, 3);
  obj.set_real("last_batch_efficiency", stats.last_batch_efficiency, 4);

  if (resources.available) {
    obj.set("agent_source", "cpp-node-agent");
    obj.set("cgroup_version", resources.cgroup_version);
    obj.set("resource_source", resources.source);
    obj.set_real("container_cpu_percent", resources.cpu_percent, 2);
    obj.set_number("container_mem_usage_bytes", resources.mem_usage_bytes);
    obj.set_number("container_mem_limit_bytes", resources.mem_limit_bytes);
  }
  return obj.dump();
}

std::string string_list_json(const std::vector<std::string>& items) {
  std::string out = "[";
  for (std::size_t i = 0; i < items.size(); ++i) {
    if (i != 0) {
      out += ",";
    }
    out += "\"" + json_escape(items[i]) + "\"";
  }
  out += "]";
  return out;
}

}  // namespace agent
