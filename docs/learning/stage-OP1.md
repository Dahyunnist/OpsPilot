# OP-1：本地真实服务故障实验台

## 交付

`lab/compose.yaml` 编排 gateway、orders、inventory、payment、PostgreSQL、Redis、OpenTelemetry Collector、Jaeger 和 Prometheus。四个业务服务共用一个轻量 HTTP 服务镜像，通过角色配置组成真实网络调用链；订单写入 PostgreSQL 和 Redis，服务指标由 Prometheus 抓取，跨服务 trace 通过 OTLP 导入 Jaeger，结构化日志由各服务提供有界只读查询。

`lab/scenarios/truth.json` 冻结六个独立故障机制：数据库连接占满、支付依赖超时、错误服务配置发布、慢 SQL、重试放大、慢 SQL 与依赖超时双根因。真值文件不挂载进容器。服务没有故障注入或清理 HTTP 路由；只有本机 PowerShell 启动脚本可选择故障并重建实验服务。

冻结真值清单 SHA-256：`48bcd313367f32c06d6bf408b6e1a6c5b3eee654646eec31b1c6c6232c16bf14`。

启动一个场景：

```powershell
.\lab\run-scenario.ps1 -Scenario slow_sql
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18080/checkout -ContentType 'application/json' -Body '{"order_id":"demo-001"}'
```

运行六场景服务烟测：

```powershell
.\lab\smoke.ps1
```

停止服务并保留 PostgreSQL/Redis 卷：

```powershell
.\lab\stop.ps1
```

所有映射端口只绑定 `127.0.0.1`。日志、Prometheus、Jaeger 和部署记录限于本机实验数据；不连接生产系统。

## 验证状态

2026-10-23 收尾复核：Docker Engine 可用。Python 3.11 Compose override 下六个服务故障场景烟测通过，日志、部署、指标和 trace 均采集成功；结果见 [Docker 烟测](../../reports/docker-smoke.md)。Python 3.12 镜像不在本机，且此前 Docker Hub 认证请求超时；正式镜像仍未验证。

随后在一个非冻结开发窗口完成 rules、single、Multi 真实模型对照。Multi 一个 specialist 首次截断后，在 512-token 重试中成功；总调用费率估算 `$0.01365257`。这不是六案例重新评测或泛化证据，见[最终升级验收](../../reports/final-upgrade-acceptance.md)。
