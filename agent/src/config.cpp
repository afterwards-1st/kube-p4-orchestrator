#include "agent/config.hpp"

#include <cstdlib>

#include "agent/file_util.hpp"
#include "agent/flat_json.hpp"

namespace agent {
namespace {

std::vector<std::string> split_comma_list(const std::string& text) {
  std::vector<std::string> items;
  std::size_t begin = 0;
  while (begin <= text.size()) {
    std::size_t end = text.find(',', begin);
    if (end == std::string::npos) {
      end = text.size();
    }
    std::string item = trim_copy(std::string_view(text).substr(begin, end - begin));
    if (!item.empty()) {
      items.push_back(std::move(item));
    }
    if (end == text.size()) {
      break;
    }
    begin = end + 1;
  }
  return items;
}

}  // namespace

AgentConfig AgentConfig::from_environment() {
  return from_getenv([](const char* name) { return std::getenv(name); });
}

AgentConfig AgentConfig::from_getenv(
    const std::function<const char*(const char*)>& getenv_fn) {
  AgentConfig config;

  const char* ids = getenv_fn("AGENT_BACKEND_IDS");
  const char* single = getenv_fn("AGENT_BACKEND_ID");
  if (ids != nullptr) {
    config.backend_ids = split_comma_list(ids);
  } else if (single != nullptr) {
    config.backend_ids = split_comma_list(single);
  }

  if (const char* host = getenv_fn("AGENT_REPORT_HOST"); host != nullptr && *host != '\0') {
    config.report_host = host;
  }
  if (const char* port = getenv_fn("AGENT_REPORT_PORT"); port != nullptr && *port != '\0') {
    long long parsed = 0;
    if (parse_ll(port, parsed) && parsed > 0 && parsed <= 65535) {
      config.report_port = static_cast<uint16_t>(parsed);
    }
  }
  if (const char* interval = getenv_fn("AGENT_REPORT_INTERVAL_MS");
      interval != nullptr && *interval != '\0') {
    long long parsed = 0;
    if (parse_ll(interval, parsed) && parsed > 0 && parsed <= 3600000) {
      config.report_interval_ms = static_cast<int>(parsed);
    }
  }
  if (const char* status_file = getenv_fn("AGENT_STATUS_FILE");
      status_file != nullptr && *status_file != '\0') {
    config.status_file = status_file;
  }
  if (const char* cgroup_root = getenv_fn("AGENT_CGROUP_ROOT");
      cgroup_root != nullptr && *cgroup_root != '\0') {
    config.cgroup_root = cgroup_root;
  }
  return config;
}

std::string validate(const AgentConfig& config) {
  if (config.backend_ids.empty()) {
    return "缺少 AGENT_BACKEND_IDS（或 AGENT_BACKEND_ID），无法确定要上报的 Worker 槽位";
  }
  for (const std::string& id : config.backend_ids) {
    if (id.empty()) {
      return "backend 编号不能为空";
    }
    for (char ch : id) {
      if (ch < '0' || ch > '9') {
        return "backend 编号必须是纯数字: " + id;
      }
    }
  }
  if (config.report_host.empty()) {
    return "上报地址不能为空";
  }
  if (config.report_port == 0) {
    return "上报端口必须是 1-65535";
  }
  if (config.report_interval_ms < 10) {
    return "上报间隔过小（至少 10ms）";
  }
  return "";
}

}  // namespace agent
