#pragma once

#include <chrono>
#include <string>
#include <string_view>

namespace agent {

struct ResourceSample {
  bool available = false;
  std::string source = "unavailable";  // cgroup-v2 / proc / unsupported / unavailable
  std::string cgroup_version;          // "v2" 或空
  double cpu_percent = 0.0;            // 相对单核的百分比，可超过 100；首次采样为 0
  long long mem_usage_bytes = 0;
  long long mem_limit_bytes = 0;       // 0 表示未知或无限制
  double cpu_limit_cores = 0.0;        // 0 表示未知或无限制
};

// 容器资源采样器。优先走 cgroup v2（容器内挂载 private cgroup namespace 时
// /sys/fs/cgroup 就是本容器的根）；cgroup 不可用时回退到 /proc/self/stat + VmRSS。
// CPU 百分比由相邻两次采样的 usage 增量除以墙钟时间差得到。
class ResourceReader {
 public:
  explicit ResourceReader(std::string cgroup_root = "/sys/fs/cgroup");

  ResourceSample sample();

 private:
  std::string cgroup_root_;
  bool have_prev_ = false;
  long long prev_usage_usec_ = 0;
  std::chrono::steady_clock::time_point prev_wall_;
};

// —— 纯函数，文件读取与解析分离，便于单元测试 ——

// 从 /proc/self/cgroup 内容提取 v2 层级路径（"0::/xxx" 行），无 v2 行返回空串。
std::string extract_v2_cgroup_path(std::string_view proc_cgroup_content);

// 从 cpu.stat 内容解析 usage_usec。
bool parse_usage_usec(std::string_view cpu_stat_content, long long& out);

// 从 cpu.max 内容解析 CPU 限额核数；"max" 或格式非法返回 false。
bool parse_cpu_max_cores(std::string_view cpu_max_content, double& out);

// 解析 memory.max 内容；"max"（无限制）返回 false。
bool parse_memory_max_bytes(std::string_view memory_max_content, long long& out);

// 由 usage 增量与墙钟增量计算单核 CPU 百分比。
double cpu_percent_from_delta(long long usage_delta_usec, long long wall_delta_usec);

// 从 /proc/<pid>/stat 内容提取 utime + stime（时钟 tick 数）。
bool parse_proc_stat_cpu_ticks(std::string_view proc_stat_content, long long& out);

}  // namespace agent
