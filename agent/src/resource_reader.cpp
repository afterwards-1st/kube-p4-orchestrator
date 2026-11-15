#include "agent/resource_reader.hpp"

#include "agent/file_util.hpp"
#include "agent/flat_json.hpp"

#if defined(__linux__)
#include <unistd.h>
#endif

namespace agent {
namespace {

bool read_ll_file(const std::string& path, long long& out) {
  std::string content;
  if (!read_file_to_string(path, content)) {
    return false;
  }
  return parse_ll(trim_copy(content), out);
}

}  // namespace

ResourceReader::ResourceReader(std::string cgroup_root)
    : cgroup_root_(std::move(cgroup_root)) {}

std::string extract_v2_cgroup_path(std::string_view content) {
  std::size_t begin = 0;
  while (begin <= content.size()) {
    std::size_t end = content.find('\n', begin);
    if (end == std::string_view::npos) {
      end = content.size();
    }
    const std::string_view line = content.substr(begin, end - begin);
    if (line.rfind("0::", 0) == 0) {
      std::string path = trim_copy(line.substr(3));
      return path.empty() ? "/" : path;
    }
    if (end == content.size()) {
      break;
    }
    begin = end + 1;
  }
  return "";
}

bool parse_usage_usec(std::string_view content, long long& out) {
  std::size_t begin = 0;
  while (begin <= content.size()) {
    std::size_t end = content.find('\n', begin);
    if (end == std::string_view::npos) {
      end = content.size();
    }
    const std::string_view line = content.substr(begin, end - begin);
    if (line.rfind("usage_usec", 0) == 0) {
      return parse_ll(trim_copy(line.substr(std::string_view("usage_usec").size())), out);
    }
    if (end == content.size()) {
      break;
    }
    begin = end + 1;
  }
  return false;
}

bool parse_cpu_max_cores(std::string_view content, double& out) {
  const std::string line = trim_copy(content);
  if (line.empty()) {
    return false;
  }
  const std::size_t space = line.find(' ');
  if (space == std::string::npos) {
    return false;
  }
  if (trim_copy(line.substr(0, space)) == "max") {
    return false;
  }
  long long quota = 0;
  long long period = 0;
  if (!parse_ll(trim_copy(line.substr(0, space)), quota) ||
      !parse_ll(trim_copy(line.substr(space + 1)), period) || period <= 0) {
    return false;
  }
  out = static_cast<double>(quota) / static_cast<double>(period);
  return true;
}

bool parse_memory_max_bytes(std::string_view content, long long& out) {
  const std::string line = trim_copy(content);
  if (line.empty() || line == "max") {
    return false;
  }
  return parse_ll(line, out);
}

double cpu_percent_from_delta(long long usage_delta_usec, long long wall_delta_usec) {
  if (usage_delta_usec < 0 || wall_delta_usec <= 0) {
    return 0.0;
  }
  return 100.0 * static_cast<double>(usage_delta_usec) /
         static_cast<double>(wall_delta_usec);
}

bool parse_proc_stat_cpu_ticks(std::string_view content, long long& out) {
  // /proc/<pid>/stat 的第二段 comm 可能包含空格，因此定位最后一个 ')' 之后的字段。
  const std::size_t rparen = content.rfind(')');
  if (rparen == std::string_view::npos) {
    return false;
  }
  std::string_view rest = content.substr(rparen + 1);
  // 跳过前导空格后，rest 从第 3 个字段（state）开始；
  // utime 是第 14 个字段（index 11），stime 是第 15 个字段（index 12）。
  std::size_t index = 0;
  long long utime = 0;
  long long stime = 0;
  while (!rest.empty()) {
    while (!rest.empty() && rest.front() == ' ') {
      rest.remove_prefix(1);
    }
    if (rest.empty()) {
      break;
    }
    const std::size_t space = rest.find(' ');
    const std::string_view token =
        rest.substr(0, space == std::string_view::npos ? rest.size() : space);
    if (index == 11) {
      if (!parse_ll(token, utime)) {
        return false;
      }
    }
    if (index == 12) {
      if (!parse_ll(token, stime)) {
        return false;
      }
      out = utime + stime;
      return true;
    }
    ++index;
    if (space == std::string_view::npos) {
      break;
    }
    rest.remove_prefix(space + 1);
  }
  return false;
}

ResourceSample ResourceReader::sample() {
  ResourceSample sample;
#if defined(__linux__)
  bool have_usage = false;
  bool have_mem = false;
  long long usage_usec = 0;

  std::string proc_cgroup;
  std::string cgroup_dir;
  if (read_file_to_string("/proc/self/cgroup", proc_cgroup)) {
    const std::string rel = extract_v2_cgroup_path(proc_cgroup);
    if (!rel.empty()) {
      cgroup_dir = (rel == "/") ? cgroup_root_ : cgroup_root_ + rel;
      sample.cgroup_version = "v2";
      sample.source = "cgroup-v2";
    }
  }

  if (!cgroup_dir.empty()) {
    std::string cpu_stat;
    if (read_file_to_string(cgroup_dir + "/cpu.stat", cpu_stat)) {
      have_usage = parse_usage_usec(cpu_stat, usage_usec);
    }
    long long mem_current = 0;
    if (read_ll_file(cgroup_dir + "/memory.current", mem_current)) {
      sample.mem_usage_bytes = mem_current;
      have_mem = true;
    }
    long long mem_max = 0;
    if (read_ll_file(cgroup_dir + "/memory.max", mem_max)) {
      sample.mem_limit_bytes = mem_max;
    }
    double cores = 0.0;
    std::string cpu_max;
    if (read_file_to_string(cgroup_dir + "/cpu.max", cpu_max) &&
        parse_cpu_max_cores(cpu_max, cores)) {
      sample.cpu_limit_cores = cores;
    }
  }

  if (!have_usage) {
    // 回退：宿主机进程视角的 /proc/self/stat + VmRSS。
    std::string proc_stat;
    long long ticks = 0;
    if (read_file_to_string("/proc/self/stat", proc_stat) &&
        parse_proc_stat_cpu_ticks(proc_stat, ticks)) {
      const long clock_ticks = sysconf(_SC_CLK_TCK);
      if (clock_ticks > 0) {
        usage_usec = ticks * 1000000LL / clock_ticks;
        have_usage = true;
      }
    }
    if (sample.source == "unavailable") {
      sample.source = "proc";
    }
  }

  if (sample.source == "cgroup-v2" && !have_mem) {
    std::string status;
    if (read_file_to_string("/proc/self/status", status)) {
      std::size_t begin = 0;
      while (begin <= status.size()) {
        const std::size_t end = status.find('\n', begin);
        const std::string line =
            trim_copy(status.substr(begin, end == std::string::npos ? std::string::npos
                                                                    : end - begin));
        if (line.rfind("VmRSS:", 0) == 0) {
          long long kb = 0;
          if (parse_ll(trim_copy(line.substr(6)), kb)) {
            sample.mem_usage_bytes = kb * 1024LL;
            have_mem = true;
          }
          break;
        }
        if (end == std::string::npos) {
          break;
        }
        begin = end + 1;
      }
    }
  }

  if (have_usage || have_mem) {
    const auto now = std::chrono::steady_clock::now();
    if (have_prev_) {
      const auto wall_us =
          std::chrono::duration_cast<std::chrono::microseconds>(now - prev_wall_).count();
      long long delta = usage_usec - prev_usage_usec_;
      if (delta < 0) {
        delta = 0;
      }
      sample.cpu_percent = cpu_percent_from_delta(delta, wall_us);
    }
    prev_usage_usec_ = usage_usec;
    prev_wall_ = now;
    have_prev_ = true;
    sample.available = true;
  }
#else
  sample.source = "unsupported";
#endif
  return sample;
}

}  // namespace agent
