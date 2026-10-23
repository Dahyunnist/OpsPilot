# Docker 六场景烟测记录

- 日期：2026-10-23
- 结果：未完成；未进入场景执行。
- Docker Engine：29.1.3，可由提权执行环境访问。
- Compose 配置：通过。
- 阻塞阶段：首次应用镜像构建，Docker Hub 认证请求超时。
- 原始错误：`failed to fetch oauth token: Post "https://auth.docker.io/token": dial tcp 31.13.82.33:443: connectex: A connection attempt failed because the connected party did not properly respond after a period of time, or established connection failed because connected host has failed to respond.`
- 清理检查：`docker compose ps --all` 无项目容器；Compose 项目标记筛选也无残留容器。
- 模型费用：未发模型请求。账本累计峰值估算 `$0.33974803 / $0.50`，剩余 `$0.16025197`。
- 结论：真实服务烟测和模型策略对照均未验收；不将本地模拟结果视为 Docker 服务验证。

完整原始输出见 [docker-smoke.log](docker-smoke.log)。
