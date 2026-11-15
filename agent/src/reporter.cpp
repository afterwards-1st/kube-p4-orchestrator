#include "agent/reporter.hpp"

#include <cstdio>
#include <cstring>
#include <memory>

#if !defined(_WIN32)
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

namespace agent {

struct Reporter::Impl {
  std::string host;
  std::string port;
  int fd = -1;
  bool resolved = false;
  sockaddr_storage dest{};
  socklen_t dest_len = 0;
};

Reporter::Reporter(const AgentConfig& config) : impl_(new Impl()) {
  impl_->host = config.report_host;
  impl_->port = std::to_string(config.report_port);
#if !defined(_WIN32)
  impl_->fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (impl_->fd < 0) {
    std::fprintf(stderr, "[AGENT] 创建 UDP socket 失败\n");
  }
#endif
}

Reporter::~Reporter() {
#if !defined(_WIN32)
  if (impl_->fd >= 0) {
    ::close(impl_->fd);
  }
#endif
  delete impl_;
}

#if !defined(_WIN32)
bool Reporter::resolve_target() {
  addrinfo hints{};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_DGRAM;
  addrinfo* result = nullptr;
  const int rc = ::getaddrinfo(impl_->host.c_str(), impl_->port.c_str(), &hints, &result);
  if (rc != 0 || result == nullptr) {
    return false;
  }
  std::memcpy(&impl_->dest, result->ai_addr, result->ai_addrlen);
  impl_->dest_len = result->ai_addrlen;
  ::freeaddrinfo(result);
  return true;
}
#endif

bool Reporter::send(const std::string& payload) {
#if defined(_WIN32)
  (void)payload;
  ++stats_.failed_reports;
  return false;
#else
  if (impl_->fd < 0) {
    ++stats_.failed_reports;
    return false;
  }
  if (!impl_->resolved) {
    if (!resolve_target()) {
      ++stats_.failed_reports;
      return false;
    }
    impl_->resolved = true;
  }
  const ssize_t sent = ::sendto(impl_->fd, payload.data(), payload.size(), 0,
                                reinterpret_cast<const sockaddr*>(&impl_->dest),
                                impl_->dest_len);
  if (sent < 0) {
    // 发送失败后重新解析，覆盖 receiver 重启换了 IP 的场景。
    impl_->resolved = false;
    ++stats_.failed_reports;
    return false;
  }
  ++stats_.sent_reports;
  return true;
#endif
}

}  // namespace agent
