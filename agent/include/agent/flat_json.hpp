#pragma once

#include <map>
#include <string>
#include <string_view>

namespace agent {

// 解析一层扁平 JSON 对象（字符串/数字/布尔/null），不支持嵌套与数组。
// 用于读取本项目组件间的轻量状态文件，不是通用 JSON 库：
//  - 字符串值已去引号并处理转义（含 \uXXXX 的基本多文种平面）；
//  - 数字/布尔/null 保存原始文本，由调用方用 parse_ll/parse_double 转型。
// 成功返回 true；失败返回 false 并给出中文错误原因。
bool parse_flat_json(std::string_view text, std::map<std::string, std::string>& out,
                     std::string& error);

// 严格数字解析：整个片段必须是合法数字且无剩余字符。
bool parse_ll(std::string_view text, long long& out);
bool parse_double(std::string_view text, double& out);

}  // namespace agent
