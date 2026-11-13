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

十二场景宿主矩阵由 `lab/run-host-matrix.ps1` 执行，结果写入 `reports/op1-host-matrix.json`；需用户本机启动 Docker Desktop 后运行。启动前检查 Engine、七个映射端口、Compose 配置及渲染后的全部服务镜像和 Dockerfile 基础镜像；缺镜像时拒绝运行、不自动拉取。该 runner 先跑 `none` 正常基线，再跑十二个故障；每次启动后最多等待 60 秒，检查 gateway/orders/inventory health 与 payment ready，再发 checkout，排除服务启动竞争。runner 会逐场调用 `run-scenario.ps1`，后者执行 `docker compose up --build`；预检通过后仍需预留矩阵构建与运行时间。矩阵采用独立 Compose project，结束时停止该 project，不删除数据卷。每个案例检查 HTTP 状态、orders 版本，并逐项核对 `must_observe` 的日志与 trace 信号。

当前矩阵进一步逐项评估全部 `must_observe`：数据库池压力/错误与订单日志匹配；支付延迟、SQL 和订单延迟与同一订单 ID 的 trace span 匹配；重试风暴要求 payment 503 和 orders 失败日志各至少 3 条；发布版本查 deployment；缓存、库存、拒付与重复投递查服务日志及订单 ID。每项写入 `signal_results`，附证据来源和记录引用；缺失项列入 `missing_signals`。缺少窗口内日志、同订单 trace span、或任一必需信号均判失败。Jaeger trace 需带 `opspilot.order_id` tag，避免邻近案例的 trace 冒充当前证据。

宿主验收只需运行一条命令：

```powershell
.\lab\run-host-matrix.ps1
```

离线静态与 PowerShell 合约测试不代表宿主矩阵已运行。

2026-11-13 宿主矩阵首次运行：正常基线通过；12 个故障案例均未通过完整验收，尽管 HTTP/部署检查通过。报告见 [首次失败矩阵](../../reports/op1-host-matrix-039e015c-failed.json)。主要缺口是 Jaeger 查询未等待异步导出，且案例窗口回看 2 秒会混入前一案例发布记录。已将窗口起点改为注入前即时刻，并增加当前订单 trace 最多 10 秒轮询。此次执行环境无法连接宿主 Docker Desktop，因此修复后的矩阵尚未复跑；不得视为通过。

后续宿主复跑 4/12 案例通过，8/12 失败。报告现保存 `query_scope`、`raw_matches`；此次发现轮询在任一 trace 出现时提前结束，未等齐场景要求的子 span。现在轮询直到所有 `must_observe` 日志/trace 信号出现或 10 秒上限，并在每案保存去敏窗口日志与 span 摘要。修复仍待用户宿主复跑确认。

2026-11-13 宿主复跑 `opspilot-matrix-52df8c65`：6/12 通过。报告显示 `inventory_latency` 只有约 6 ms 的正常 inventory span，`stale_inventory` 与 `inventory_depleted` 只有正常请求日志；三者注入在 inventory 服务，但 Compose 未向 inventory 传 `LAB_FAULT`。另 `bad_release`、`retry_storm`、`redis_unavailable` 的业务信号已齐，但轮询因 trace 专属信号列表为空而提前退出。已给 inventory 注入配置传递故障环境，并要求所有场景至少取得当前订单 trace 后才结束轮询。此次修复未经过宿主复跑；此前 6/12 报告须保留并仍判失败。

矩阵在每个故障窗口内用 gateway `127.0.0.1:18080/deployments` 取 orders 发布记录，用 orders `127.0.0.1:18081/logs` 取日志；二者共享同一实际 UTC `start/end` 窗口。`bad_release` 最多轮询日志 10 秒，等待下游 DNS 错误记录落盘。`18081` 是 orders 服务，`/deployments` 只由 gateway 提供，访问 `18081/deployments` 会得到 404。结果保留部署记录及版本是否匹配。正常基线要求 HTTP 200 和 v1 发布记录；`bad_release` 接受 gateway 的 502/504 上游失败状态，并要求 v2 发布记录及 inventory-v2 DNS 故障日志。

用户宿主回传中 gateway HTTP 504、orders `inventory-v2` DNS 失败和 BrokenPipe，与配置故障一致；BrokenPipe 可能是 gateway 超时后下游才结束请求的次生现象。旧 gateway 对 orders 等待上限为 3 秒，可能早于下游 DNS 错误完成。现在上限改为 10 秒；orders 在 inventory 请求失败时也会记录脱敏后的目标 hostname 和错误原因。启动流程先做有界 readiness；目的是让下游失败先返回，避免过早断开连接。该解释基于源码和用户日志，修复效果仍需宿主复跑确认。故意错误的 `inventory-v2` 仍保留为 bad_release 注入。

2026-11-12 执行环境前置检查：七个映射端口均空闲，但本执行环境无法连接 Docker Desktop Linux Engine，镜像状态无法读取；矩阵未启动、未注入故障、未生成 live 证据。用户此前提供的 Docker 版本输出显示其桌面 Engine 可运行；两者是不同执行环境，不能互相代替。
