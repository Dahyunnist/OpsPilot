# Docker 六场景烟测报告

- 日期：2026-10-23
- Docker Engine：29.1.3。
- 正式运行时仍为 Python 3.12；`python:3.12-slim` 未缓存，Docker Hub 认证请求超时，见 [原始失败日志](docker-smoke.log)。
- 临时验证运行时：Python 3.11。服务依赖的最低 Python 版本为 3.8（psycopg）、3.9（Redis 与 OpenTelemetry）；Python 3.11 满足这些约束。[psycopg](https://pypi.org/project/psycopg/3.2.12/)、[psycopg-binary](https://pypi.org/project/psycopg-binary/3.2.12/)、[Redis](https://pypi.org/project/redis/6.4.0/)、[OpenTelemetry SDK](https://pypi.org/project/opentelemetry-sdk/1.38.0/)
- 验证方式：临时 Compose override 指向 Python 3.11 Dockerfile；正式 Dockerfile 与 Python 3.12 运行时未改。
- 结果：6/6 场景通过。pool 502、dependency timeout 502、bad release 504、slow SQL 200、retry storm 200、double root 502；每个场景均采到日志、部署、指标、trace。
- 首次 Python 3.11 烟测发现 pool fault 有竞态，连接池日志虽出现，订单仍成功。故障实验现等待压力建立并占满 20 个连接；重跑后通过。首次失败日志见 [attempt 1](docker-smoke-python311-attempt1.log)。
- 原始通过日志：[docker-smoke-python311.log](docker-smoke-python311.log)。
- 清理：烟测及策略对照后均运行 `lab/stop.ps1`；容器和网络移除，`docker compose ps --all` 无容器。数据库 named volumes 保留。
- 限制：本次验证的是 Python 3.11 服务镜像，不代表 Python 3.12 镜像构建已验证。

策略对照和费用结果见 [lab-strategy-comparison.md](lab-strategy-comparison.md)。
