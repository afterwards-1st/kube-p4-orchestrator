#pragma once

#include <string>
#include <vector>

#include "agent/resource_reader.hpp"
#include "agent/workload_stats.hpp"

namespace agent {

// 与 time.time() 对齐的 epoch 秒（含毫秒精度）。
double epoch_seconds();

// 构造一条与 batch_server_report_sim.py 字段口径一致的 UDP 上报报文。
// 资源采样可用时，额外附加容器资源字段（metrics_receiver 会白名单透传）。
std::string build_report_payload(const std::string& backend_id, const WorkloadStats& stats,
                                 const ResourceSample& resources, const std::string& reason,
                                 double ts);

// 把字符串列表序列化为 JSON 数组（用于 selfcheck 输出）。
std::string string_list_json(const std::vector<std::string>& items);

}  // namespace agent
