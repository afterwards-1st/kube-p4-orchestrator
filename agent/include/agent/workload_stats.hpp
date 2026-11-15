#pragma once

#include <map>
#include <string>

namespace agent {

// Worker 侧批处理统计，字段口径与 workers/batch_server_report_sim.py 完全一致，
// 这样 metrics_receiver 无需区分报文来自 Python 模拟器还是本 agent。
struct WorkloadStats {
  long long queue_depth = 0;
  long long completed_tasks = 0;
  long long total_batches = 0;
  long long last_flush_size = 0;
  std::string last_flush_reason = "none";
  double last_process_time_ms = 0.0;
  double last_avg_wait_ms = 0.0;
  double last_batch_efficiency = 0.0;
};

// 将扁平 JSON 的字段值映射为 WorkloadStats。
// 缺失字段使用默认值；非法数值字段回落默认值并追加 warning（不影响其他字段）。
WorkloadStats workload_stats_from_values(const std::map<std::string, std::string>& values,
                                         std::string& warning);

// 读取本地工作负载状态文件（由批处理服务或其模拟器写出）。
//  - 未配置路径或文件不存在：found=false，返回默认统计（agent 以空闲心跳继续上报）；
//  - 文件存在但损坏：found=true，返回默认统计并设置 error。
WorkloadStats read_workload_stats(const std::string& path, bool& found, std::string& error);

}  // namespace agent
