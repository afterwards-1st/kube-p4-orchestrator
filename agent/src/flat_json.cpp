#include "agent/flat_json.hpp"

#include <charconv>
#include <cstdlib>

namespace agent {
namespace {

bool is_space(char ch) {
  return ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r';
}

void skip_space(std::string_view& text) {
  while (!text.empty() && is_space(text.front())) {
    text.remove_prefix(1);
  }
}

bool parse_hex4(std::string_view& text, unsigned& out) {
  if (text.size() < 4) {
    return false;
  }
  unsigned value = 0;
  for (int i = 0; i < 4; ++i) {
    const char ch = text[static_cast<std::size_t>(i)];
    value <<= 4;
    if (ch >= '0' && ch <= '9') {
      value |= static_cast<unsigned>(ch - '0');
    } else if (ch >= 'a' && ch <= 'f') {
      value |= static_cast<unsigned>(ch - 'a' + 10);
    } else if (ch >= 'A' && ch <= 'F') {
      value |= static_cast<unsigned>(ch - 'A' + 10);
    } else {
      return false;
    }
  }
  text.remove_prefix(4);
  out = value;
  return true;
}

void append_utf8(std::string& out, unsigned code_point) {
  if (code_point < 0x80) {
    out += static_cast<char>(code_point);
  } else if (code_point < 0x800) {
    out += static_cast<char>(0xC0 | (code_point >> 6));
    out += static_cast<char>(0x80 | (code_point & 0x3F));
  } else {
    out += static_cast<char>(0xE0 | (code_point >> 12));
    out += static_cast<char>(0x80 | ((code_point >> 6) & 0x3F));
    out += static_cast<char>(0x80 | (code_point & 0x3F));
  }
}

bool parse_string(std::string_view& text, std::string& out, std::string& error) {
  text.remove_prefix(1);  // 吃掉开头的引号
  out.clear();
  while (!text.empty()) {
    const char ch = text.front();
    text.remove_prefix(1);
    if (ch == '"') {
      return true;
    }
    if (ch != '\\') {
      out += ch;
      continue;
    }
    if (text.empty()) {
      break;
    }
    const char esc = text.front();
    text.remove_prefix(1);
    switch (esc) {
      case '"': out += '"'; break;
      case '\\': out += '\\'; break;
      case '/': out += '/'; break;
      case 'b': out += '\b'; break;
      case 'f': out += '\f'; break;
      case 'n': out += '\n'; break;
      case 'r': out += '\r'; break;
      case 't': out += '\t'; break;
      case 'u': {
        unsigned code_point = 0;
        if (!parse_hex4(text, code_point)) {
          error = "无效的 \\u 转义";
          return false;
        }
        append_utf8(out, code_point);
        break;
      }
      default:
        error = "不支持的转义字符";
        return false;
    }
  }
  error = "字符串未闭合";
  return false;
}

bool parse_number_token(std::string_view& text, std::string& out) {
  std::size_t length = 0;
  while (length < text.size()) {
    const char ch = text[length];
    const bool numeric = (ch >= '0' && ch <= '9') || ch == '-' || ch == '+' ||
                         ch == '.' || ch == 'e' || ch == 'E';
    if (!numeric) {
      break;
    }
    ++length;
  }
  if (length == 0) {
    return false;
  }
  out.assign(text.substr(0, length));
  text.remove_prefix(length);
  return true;
}

}  // namespace

bool parse_flat_json(std::string_view text, std::map<std::string, std::string>& out,
                     std::string& error) {
  out.clear();
  skip_space(text);
  if (text.empty() || text.front() != '{') {
    error = "状态文件根节点必须是 JSON 对象";
    return false;
  }
  text.remove_prefix(1);
  skip_space(text);
  if (!text.empty() && text.front() == '}') {
    text.remove_prefix(1);
    skip_space(text);
    if (!text.empty()) {
      error = "对象结束后存在多余内容";
      return false;
    }
    return true;
  }

  while (true) {
    skip_space(text);
    if (text.empty() || text.front() != '"') {
      error = "缺少字段名字符串";
      return false;
    }
    std::string key;
    if (!parse_string(text, key, error)) {
      return false;
    }
    skip_space(text);
    if (text.empty() || text.front() != ':') {
      error = "字段名后缺少冒号";
      return false;
    }
    text.remove_prefix(1);
    skip_space(text);
    if (text.empty()) {
      error = "缺少字段值";
      return false;
    }

    std::string value;
    const char lead = text.front();
    if (lead == '"') {
      if (!parse_string(text, value, error)) {
        return false;
      }
    } else if (lead == 't' || lead == 'f' || lead == 'n') {
      static const char* kLiterals[] = {"true", "false", "null"};
      bool matched = false;
      for (const char* literal : kLiterals) {
        if (text.substr(0, std::char_traits<char>::length(literal)) == literal) {
          value = literal;
          text.remove_prefix(std::char_traits<char>::length(literal));
          matched = true;
          break;
        }
      }
      if (!matched) {
        error = "无法解析字段值";
        return false;
      }
    } else if (lead == '{' || lead == '[') {
      error = "状态文件不支持嵌套结构";
      return false;
    } else if (!parse_number_token(text, value)) {
      error = "无法解析字段值";
      return false;
    }
    out[std::move(key)] = std::move(value);

    skip_space(text);
    if (!text.empty() && text.front() == ',') {
      text.remove_prefix(1);
      continue;
    }
    if (!text.empty() && text.front() == '}') {
      text.remove_prefix(1);
      skip_space(text);
      if (!text.empty()) {
        error = "对象结束后存在多余内容";
        return false;
      }
      return true;
    }
    error = "字段之间缺少逗号或对象未闭合";
    return false;
  }
}

bool parse_ll(std::string_view text, long long& out) {
  const char* begin = text.data();
  const char* end = begin + text.size();
  long long value = 0;
  const auto result = std::from_chars(begin, end, value);
  if (result.ec != std::errc() || result.ptr != end) {
    return false;
  }
  out = value;
  return true;
}

bool parse_double(std::string_view text, double& out) {
  const char* begin = text.data();
  const char* end = begin + text.size();
  double value = 0.0;
  const auto result = std::from_chars(begin, end, value);
  if (result.ec != std::errc() || result.ptr != end) {
    return false;
  }
  out = value;
  return true;
}

}  // namespace agent
