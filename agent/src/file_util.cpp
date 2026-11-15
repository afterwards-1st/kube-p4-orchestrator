#include "agent/file_util.hpp"

#include <fstream>
#include <sstream>

namespace agent {

bool read_file_to_string(const std::string& path, std::string& out) {
  std::ifstream file(path, std::ios::in | std::ios::binary);
  if (!file) {
    return false;
  }
  std::ostringstream buffer;
  buffer << file.rdbuf();
  out = buffer.str();
  return true;
}

std::string trim_copy(std::string_view text) {
  std::size_t begin = 0;
  std::size_t end = text.size();
  while (begin < end &&
         (text[begin] == ' ' || text[begin] == '\t' || text[begin] == '\n' ||
          text[begin] == '\r')) {
    ++begin;
  }
  while (end > begin &&
         (text[end - 1] == ' ' || text[end - 1] == '\t' || text[end - 1] == '\n' ||
          text[end - 1] == '\r')) {
    --end;
  }
  return std::string(text.substr(begin, end - begin));
}

}  // namespace agent
