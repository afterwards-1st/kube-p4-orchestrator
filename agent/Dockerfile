# ---- 构建阶段：完整工具链 + 单元测试 ----
FROM debian:bookworm-slim AS build
# make 是 cmake 默认生成器（Unix Makefiles）的依赖，--no-install-recommends 不会自动带上
RUN apt-get update && apt-get install -y --no-install-recommends g++ cmake make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY CMakeLists.txt ./
COPY include ./include
COPY src ./src
COPY tests ./tests
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j \
    && ctest --test-dir build --output-on-failure

# ---- 运行阶段：distroless 极小镜像，非 root 运行 ----
FROM gcr.io/distroless/base-debian12
COPY --from=build /src/build/node_agent /node_agent
# HEALTHCHECK 用 exec 形式，distroless 无 shell 也可执行；
# selfcheck 只校验配置与本机采集，不依赖 receiver 可达，避免误杀。
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/node_agent", "--selfcheck"]
ENTRYPOINT ["/node_agent"]
