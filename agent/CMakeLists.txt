cmake_minimum_required(VERSION 3.16)
project(kube_p4_node_agent LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# 可移植核心：JSON、配置、采集解析、报文构造，可在任意平台构建与测试。
add_library(agent_logic STATIC
  src/json_writer.cpp
  src/flat_json.cpp
  src/file_util.cpp
  src/workload_stats.cpp
  src/resource_reader.cpp
  src/config.cpp
  src/payload.cpp
)
target_include_directories(agent_logic PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

# 节点侧运行时：UDP 上报与主循环（依赖 POSIX socket）。
add_library(agent_node STATIC
  src/reporter.cpp
  src/agent.cpp
)
target_link_libraries(agent_node PUBLIC agent_logic)

add_executable(node_agent src/main.cpp)
target_link_libraries(node_agent PRIVATE agent_node)

enable_testing()
add_executable(agent_tests tests/test_agent.cpp)
target_link_libraries(agent_tests PRIVATE agent_logic)
add_test(NAME agent_tests COMMAND agent_tests)
