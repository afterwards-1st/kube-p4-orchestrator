#pragma once

#include <string>
#include <string_view>

namespace agent {

// 读取整个文本文件；失败（不存在、无权限、是目录）返回 false。
bool read_file_to_string(const std::string& path, std::string& out);

std::string trim_copy(std::string_view text);

}  // namespace agent
