// agent 单元测试：覆盖可移植核心逻辑（JSON、配置、状态文件、cgroup 解析、报文构造）。
// 自带微型断言框架，仅依赖标准库，ctest 直接运行。
#include <cmath>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

#include "agent/config.hpp"
#include "agent/flat_json.hpp"
#include "agent/json_writer.hpp"
#include "agent/payload.hpp"
#include "agent/resource_reader.hpp"
#include "agent/workload_stats.hpp"

namespace {

int g_checks = 0;
int g_failures = 0;

#define EXPECT(cond)                                        \
  do {                                                      \
    ++g_checks;                                             \
    if (!(cond)) {                                          \
      ++g_failures;                                         \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    }                                                       \
  } while (0)

#define EXPECT_EQ_STR(actual, expected)                                       \
  do {                                                                        \
    const std::string a_ = (actual);                                          \
    const std::string e_ = (expected);                                        \
    ++g_checks;                                                               \
    if (a_ != e_) {                                                           \
      ++g_failures;                                                           \
      std::fprintf(stderr, "FAIL %s:%d\n  actual  : %s\n  expected: %s\n",    \
                   __FILE__, __LINE__, a_.c_str(), e_.c_str());               \
    }                                                                         \
  } while (0)

#define EXPECT_NEAR(actual, expected, eps) \
  do {                                     \
    ++g_checks;                            \
    if (std::fabs((actual) - (expected)) > (eps)) {              \
      ++g_failures;                                              \
      std::fprintf(stderr, "FAIL %s:%d: %f vs %f\n", __FILE__, __LINE__, \
                   static_cast<double>(actual), static_cast<double>(expected)); \
    }                                     \
  } while (0)

void test_json_escape() {
  EXPECT_EQ_STR(agent::json_escape("plain"), "plain");
  EXPECT_EQ_STR(agent::json_escape("a\"b\\c\nd\te"),
                "a\\\"b\\\\c\\nd\\te");
  EXPECT_EQ_STR(agent::json_escape(std::string(1, '\x01')), "\\u0001");
  // UTF-8 字节保持原样
  EXPECT_EQ_STR(agent::json_escape("节点"), "节点");
}

void test_json_object() {
  agent::JsonObject obj;
  obj.set("reason", "heartbeat")
      .set_number("queue_depth", -3)
      .set_real("eff", 1.5, 3)
      .set_bool("ok", true)
      .set_raw("backend_id", "216");
  EXPECT_EQ_STR(obj.dump(),
                R"({"reason":"heartbeat","queue_depth":-3,"eff":1.500,"ok":true,"backend_id":216})");
  // 非有限浮点统一落 0
  agent::JsonObject bad;
  bad.set_real("x", std::nan(""), 2);
  EXPECT_EQ_STR(bad.dump(), R"({"x":0.00})");
}

void test_parse_flat_json() {
  std::map<std::string, std::string> values;
  std::string error;
  const bool ok = agent::parse_flat_json(
      R"({"queue_depth": 5, "reason": "batch_flush", "eff": 0.5, "flag": true, "nothing": null})",
      values, error);
  EXPECT(ok);
  EXPECT(values.at("queue_depth") == "5");
  EXPECT(values.at("reason") == "batch_flush");
  EXPECT(values.at("eff") == "0.5");
  EXPECT(values.at("flag") == "true");
  EXPECT(values.at("nothing") == "null");
  EXPECT_EQ_STR(error, "");

  // 转义引号回环
  std::map<std::string, std::string> escaped;
  EXPECT(agent::parse_flat_json(R"({"reason":"say \"hi\""})", escaped, error));
  EXPECT(escaped.at("reason") == "say \"hi\"");

  // 空对象与前后空白
  std::map<std::string, std::string> empty;
  EXPECT(agent::parse_flat_json("  { }\n ", empty, error));
  EXPECT(empty.empty());

  struct FailureCase {
    std::string text;
  };
  const std::string bad_inputs[] = {
      "",           "[1]",        "{",         "{\"a\":{}}",  "{\"a\":1",
      "{\"a\" 1}",  "{\"a\":1}}", "{\"a\":\"x\\q\"}", "{\"a\":1,}",
  };
  for (const std::string& text : bad_inputs) {
    std::map<std::string, std::string> out;
    std::string reason;
    EXPECT(!agent::parse_flat_json(text, out, reason));
    EXPECT(!reason.empty());
  }
}

void test_workload_stats_mapping() {
  std::string warning;
  const agent::WorkloadStats stats = agent::workload_stats_from_values(
      {
          {"queue_depth", "7"},
          {"completed_tasks", "42"},
          {"total_batches", "6"},
          {"last_flush_size", "8"},
          {"last_flush_reason", "size"},
          {"last_process_time_ms", "180.5"},
          {"last_avg_wait_ms", "3.25"},
          {"last_batch_efficiency", "0.85"},
      },
      warning);
  EXPECT_EQ_STR(warning, "");
  EXPECT(stats.queue_depth == 7);
  EXPECT(stats.completed_tasks == 42);
  EXPECT(stats.total_batches == 6);
  EXPECT(stats.last_flush_size == 8);
  EXPECT(stats.last_flush_reason == "size");
  EXPECT_NEAR(stats.last_process_time_ms, 180.5, 1e-9);
  EXPECT_NEAR(stats.last_batch_efficiency, 0.85, 1e-9);

  // 非法数值回落默认值并给出告警；负数被钳制为 0
  std::string warning2;
  const agent::WorkloadStats fallback = agent::workload_stats_from_values(
      {
          {"queue_depth", "-3"},
          {"completed_tasks", "oops"},
      },
      warning2);
  EXPECT(fallback.queue_depth == 0);
  EXPECT(fallback.completed_tasks == 0);
  EXPECT(fallback.last_flush_reason == "none");
  EXPECT(!warning2.empty());

  // 缺文件：found=false 且不报错
  bool found = true;
  std::string error;
  const agent::WorkloadStats missing =
      agent::read_workload_stats("/nonexistent/agent_status.json", found, error);
  EXPECT(!found);
  EXPECT_EQ_STR(error, "");
  EXPECT(missing.queue_depth == 0);
}

#if !defined(_WIN32)
void test_read_workload_stats_file() {
  const std::string path = "/tmp/agent_test_status.json";
  {
    std::ofstream file(path, std::ios::trunc);
    file << R"({"queue_depth": 3, "completed_tasks": 10, "last_flush_reason": "timeout"})";
  }
  bool found = false;
  std::string error;
  const agent::WorkloadStats stats = agent::read_workload_stats(path, found, error);
  EXPECT(found);
  EXPECT_EQ_STR(error, "");
  EXPECT(stats.queue_depth == 3);
  EXPECT(stats.completed_tasks == 10);
  EXPECT(stats.last_flush_reason == "timeout");

  {
    std::ofstream file(path, std::ios::trunc);
    file << R"({"queue_depth": })";
  }
  found = false;
  const agent::WorkloadStats corrupt = agent::read_workload_stats(path, found, error);
  EXPECT(found);
  EXPECT(!error.empty());
  EXPECT(corrupt.queue_depth == 0);
}
#endif

void test_cgroup_parsers() {
  EXPECT_EQ_STR(agent::extract_v2_cgroup_path("12:pids:/docker/x\n0::/system.slice/foo\n"),
                "/system.slice/foo");
  EXPECT_EQ_STR(agent::extract_v2_cgroup_path("0::/\n"), "/");
  EXPECT_EQ_STR(agent::extract_v2_cgroup_path("0::/\r\n"), "/");
  EXPECT_EQ_STR(agent::extract_v2_cgroup_path("1:name=systemd:/pod\n"), "");

  long long usage = 0;
  EXPECT(agent::parse_usage_usec("usage_usec 123456\ncoredump 0\n", usage));
  EXPECT(usage == 123456);
  EXPECT(!agent::parse_usage_usec("other 1\n", usage));

  double cores = 0.0;
  EXPECT(agent::parse_cpu_max_cores("400000 100000", cores));
  EXPECT_NEAR(cores, 4.0, 1e-9);
  EXPECT(agent::parse_cpu_max_cores("1500000 1000000", cores));
  EXPECT_NEAR(cores, 1.5, 1e-9);
  EXPECT(!agent::parse_cpu_max_cores("max 100000", cores));
  EXPECT(!agent::parse_cpu_max_cores("", cores));

  long long mem = 0;
  EXPECT(agent::parse_memory_max_bytes("268435456", mem));
  EXPECT(mem == 268435456);
  EXPECT(!agent::parse_memory_max_bytes("max", mem));

  EXPECT_NEAR(agent::cpu_percent_from_delta(200000, 1000000), 20.0, 1e-9);
  EXPECT_NEAR(agent::cpu_percent_from_delta(-1, 1000000), 0.0, 1e-9);
  EXPECT_NEAR(agent::cpu_percent_from_delta(100, 0), 0.0, 1e-9);
  EXPECT_NEAR(agent::cpu_percent_from_delta(2500000, 1000000), 250.0, 1e-9);

  long long ticks = 0;
  EXPECT(agent::parse_proc_stat_cpu_ticks(
      "42 (java agent with space) R 1 2 3 4 5 6 7 8 9 10 11 12 13\n", ticks));
  // 去掉前导空格后 index 11 = utime，index 12 = stime，即 11 + 12 = 23 个 tick。
  EXPECT(ticks == 23);
  EXPECT(!agent::parse_proc_stat_cpu_ticks("42 (x)", ticks));
}

void test_config() {
  agent::AgentConfig config = agent::AgentConfig::from_getenv(
      [](const char* name) -> const char* {
        if (std::string(name) == "AGENT_BACKEND_IDS") return "216, 217 ,";
        if (std::string(name) == "AGENT_REPORT_PORT") return "7777";
        if (std::string(name) == "AGENT_REPORT_INTERVAL_MS") return "50";
        return nullptr;
      });
  EXPECT(config.backend_ids.size() == 2);
  EXPECT(config.backend_ids[0] == "216");
  EXPECT(config.backend_ids[1] == "217");
  EXPECT(config.report_port == 7777);
  EXPECT(config.report_interval_ms == 50);
  EXPECT_EQ_STR(agent::validate(config), "");

  // 未设置任何 backend 编号 → 校验失败
  const agent::AgentConfig none = agent::AgentConfig::from_getenv(
      [](const char*) -> const char* { return nullptr; });
  EXPECT(!agent::validate(none).empty());

  // 非法端口字符串回落默认值
  const agent::AgentConfig bad_port = agent::AgentConfig::from_getenv(
      [](const char* name) -> const char* {
        if (std::string(name) == "AGENT_BACKEND_ID") return "216";
        if (std::string(name) == "AGENT_REPORT_PORT") return "abc";
        return nullptr;
      });
  EXPECT(bad_port.backend_ids.size() == 1);
  EXPECT(bad_port.report_port == 9999);
  EXPECT_EQ_STR(agent::validate(bad_port), "");

  // 过小的上报间隔被校验拦截
  agent::AgentConfig too_fast;
  too_fast.backend_ids = {"216"};
  too_fast.report_interval_ms = 5;
  EXPECT(!agent::validate(too_fast).empty());
}

void test_build_report_payload() {
  agent::WorkloadStats stats;
  stats.queue_depth = 4;
  stats.completed_tasks = 20;
  stats.total_batches = 3;
  stats.last_flush_size = 8;
  stats.last_flush_reason = "size";
  stats.last_process_time_ms = 180.5;
  stats.last_avg_wait_ms = 2.5;
  stats.last_batch_efficiency = 0.85;

  agent::ResourceSample resources;
  resources.available = true;
  resources.source = "cgroup-v2";
  resources.cgroup_version = "v2";
  resources.cpu_percent = 12.345;
  resources.mem_usage_bytes = 1048576;
  resources.mem_limit_bytes = 268435456;

  const std::string payload = agent::build_report_payload(
      "216", stats, resources, "heartbeat", 1000.5);
  std::map<std::string, std::string> values;
  std::string error;
  EXPECT(agent::parse_flat_json(payload, values, error));
  EXPECT_EQ_STR(error, "");
  EXPECT(values.at("backend_id") == "216");
  EXPECT(values.at("queue_depth") == "4");
  EXPECT(values.at("reason") == "heartbeat");
  EXPECT(values.at("ts") == "1000.500");
  EXPECT(values.at("last_flush_reason") == "size");
  EXPECT(values.at("last_batch_efficiency") == "0.8500");
  EXPECT(values.at("agent_source") == "cpp-node-agent");
  EXPECT(values.at("cgroup_version") == "v2");
  EXPECT(values.at("container_cpu_percent") == "12.35");
  EXPECT(values.at("container_mem_usage_bytes") == "1048576");
  EXPECT(values.at("container_mem_limit_bytes") == "268435456");

  // 资源不可用时不输出容器字段
  agent::ResourceSample unavailable;
  const std::string plain =
      agent::build_report_payload("217", stats, unavailable, "start", 1.0);
  std::map<std::string, std::string> plain_values;
  EXPECT(agent::parse_flat_json(plain, plain_values, error));
  EXPECT(plain_values.find("container_cpu_percent") == plain_values.end());
  EXPECT(plain_values.find("agent_source") == plain_values.end());

  EXPECT_EQ_STR(agent::string_list_json({"216", "217"}), R"(["216","217"])");
}

}  // namespace

int main() {
  test_json_escape();
  test_json_object();
  test_parse_flat_json();
  test_workload_stats_mapping();
#if !defined(_WIN32)
  test_read_workload_stats_file();
#endif
  test_cgroup_parsers();
  test_config();
  test_build_report_payload();

  std::fprintf(stderr, "agent tests: %d checks, %d failures\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
