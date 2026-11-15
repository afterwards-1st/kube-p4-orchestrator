#pragma once

#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace agent {

// 极简 JSON 对象序列化器：保持插入顺序，输出紧凑 JSON。
// 只覆盖本项目需要的标量类型，不支持嵌套结构；
// 组件间报文都是一层扁平对象，这正是接收端（metrics_receiver）的解析口径。
std::string json_escape(std::string_view text);

class JsonObject {
 public:
  JsonObject& set(std::string key, std::string_view value);
  JsonObject& set_number(std::string key, long long value);
  JsonObject& set_real(std::string key, double value, int precision = 3);
  JsonObject& set_bool(std::string key, bool value);
  // 写入已序列化好的 JSON 值片段，例如把数字 ID 保持为数字类型。
  JsonObject& set_raw(std::string key, std::string raw_value);

  std::string dump() const;

 private:
  std::vector<std::pair<std::string, std::string>> entries_;
};

}  // namespace agent
