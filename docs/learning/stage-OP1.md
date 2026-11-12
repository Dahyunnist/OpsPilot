# OP-1：本地真实服务故障实验台

## 交付

`lab/compose.yaml` 编排 gateway、orders、inventory、payment、PostgreSQL、Redis、OpenTelemetry Collector、Jaeger 和 Prometheus。四个业务服务共用一个轻量 HTTP 服务镜像，通过角色配置组成真实网络调用链；订单写入 PostgreSQL 和 Redis，服务指标由 Prometheus 抓取，跨服务 trace 通过 OTLP 导入 Jaeger，结构化日志由各服务提供有界只读查询。

`lab/scenarios/truth.json` 当前列出十二个独立故障场景，包含数据库连接占满、支付依赖超时、错误服务配置发布、慢 SQL、重试放大、双根因、库存延迟、支付拒绝、陈旧库存、重复投递、Redis 不可用和库存耗尽。真值文件不挂载进容器。服务没有故障注入或清理 HTTP 路由；只有本机 PowerShell 启动脚本可选择故障并重建实验服务。

当前真值清单 SHA-256：`9c08b4abbef988c76a0fdf9d77f81a515d439b06fbc712c0d66a882064cb9694`。

启动一个场景：

```powershell
.\lab\run-scenario.ps1 -Scenario slow_sql
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18080/checkout -ContentType 'application/json' -Body '{"order_id":"demo-001"}'
```

历史六场景服务烟测（2026-10-23）：

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

后续完成 [slow SQL 实时垂直切片](OP1-vertical-slice.md)：采集健康基线，注入 `pg_sleep(1.5)`，用 metrics/trace 交叉定位，再关闭注入并验证恢复。该切片冻结了时间线、oracle、独立问题和四路指标；不扩增合成 JSON。

十二场景宿主矩阵由 `lab/run-host-matrix.ps1` 执行，结果写入 `reports/op1-host-matrix.json`；需用户本机启动 Docker Desktop 后运行。该 runner 会逐场调用 `run-scenario.ps1`，后者执行 `docker compose up --build`；运行前确认镜像构建条件、七个映射端口空闲，并预留矩阵运行时间。矩阵采用独立 Compose project，结束时停止该 project，不删除数据卷。

2026-11-12 执行环境前置检查：七个映射端口均空闲，但本执行环境无法连接 Docker Desktop Linux Engine，镜像状态无法读取；矩阵未启动、未注入故障、未生成 live 证据。用户此前提供的 Docker 版本输出显示其桌面 Engine 可运行；两者是不同执行环境，不能互相代替。
