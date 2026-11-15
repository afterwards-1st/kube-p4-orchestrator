#include "agent/json_writer.hpp"

#include <cmath>
#include <cstdio>

namespace agent {

std::string json_escape(std::string_view text) {
  std::string out;
  out.reserve(text.size() + 8);
  for (char ch : text) {
    switch (ch) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(ch) < 0x20) {
          char buffer[8];
          std::snprintf(buffer, sizeof(buffer), "\\u%04x",
                        static_cast<unsigned>(static_cast<unsigned char>(ch)));
          out += buffer;
        } else {
          out += ch;
        }
    }
  }
  return out;
}

JsonObject& JsonObject::set(std::string key, std::string_view value) {
  entries_.emplace_back(std::move(key), "\"" + json_escape(value) + "\"");
  return *this;
}

JsonObject& JsonObject::set_number(std::string key, long long value) {
  entries_.emplace_back(std::move(key), std::to_string(value));
  return *this;
}

JsonObject& JsonObject::set_real(std::string key, double value, int precision) {
  if (!std::isfinite(value)) {
    // JSON 没有 NaN/Infinity 表示，非有限值统一落成 0，保证报文始终可解析。
    value = 0.0;
  }
  char buffer[64];
  std::snprintf(buffer, sizeof(buffer), "%.*f", precision, value);
  entries_.emplace_back(std::move(key), buffer);
  return *this;
}

JsonObject& JsonObject::set_bool(std::string key, bool value) {
  entries_.emplace_back(std::move(key), value ? "true" : "false");
  return *this;
}

JsonObject& JsonObject::set_raw(std::string key, std::string raw_value) {
  entries_.emplace_back(std::move(key), std::move(raw_value));
  return *this;
}

std::string JsonObject::dump() const {
  std::string out = "{";
  for (std::size_t i = 0; i < entries_.size(); ++i) {
    if (i != 0) {
      out += ",";
    }
    out += "\"";
    out += json_escape(entries_[i].first);
    out += "\":";
    out += entries_[i].second;
  }
  out += "}";
  return out;
}

}  // namespace agent
