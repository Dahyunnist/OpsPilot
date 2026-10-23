# OP-1 垂直切片：慢 SQL 故障、遥测、恢复与对照

## 目标与真值边界

本切片在本地 gateway → orders → PostgreSQL/payment 链路注入慢 SQL。注入控制器只把 `LAB_FAULT=slow_sql` 交给 orders 服务；服务对 `postgres.query` 执行 `pg_sleep(1.5)`。服务没有注入或清除 HTTP 路由。

独立 oracle 定义在 [`lab/scenarios/vertical-slow-sql.json`](../../lab/scenarios/vertical-slow-sql.json)：根因为 `slow_database_query`，受影响服务为 gateway、orders、postgres；问题是定位主延迟操作，并区分数据库延迟与支付超时。控制器只读取注入配置，评分器按 trace/metric 谓词读取 oracle。Compose 只挂载 `lab/telemetry`；MCP 只读 HTTP 遥测，不读取场景文件。`tests/test_vertical_slice.py` 验证该边界。

## 重放步骤

使用本机已经构建的 Python 3.11 服务镜像。控制器用 `docker compose up --no-build`，先发正常 baseline，再注入慢 SQL，采集故障时间窗，关闭注入并验证恢复。它会生成唯一订单 ID，保存 baseline、fault、recovery 时间点和响应。

```powershell
.\lab\run-vertical-slice.ps1
.\.venv\Scripts\python.exe scripts\evaluate_vertical_slice.py --max-completion-tokens 512
```

第一条评测命令只采集遥测、冻结快照并预估 `rules/single/multi/auto` 与一次专家截断重试的最坏预留，不调用模型。只有 `worst_case_usd <= remaining_usd` 时，再加 `--execute` 执行；`RunBudget` 为每个并发请求先预留，累计硬上限 `$0.50`。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_vertical_slice.py --max-completion-tokens 512 --execute
.\lab\stop.ps1
```

服务在 `127.0.0.1` 提供端口；本地 images 缺失时先停止，不运行 build/pull。停止命令保留数据库卷。评分 oracle 不进入 MCP 请求。

## 本次冻结的运行

Case `op1-vertical-slow-sql-20261023-01`。故障窗 `2026-10-23T12:40:37.7726364Z`–`2026-10-23T12:40:43.3542103Z`；assessment window 从 `12:40:29.6471845Z` 开始，包含健康基线。baseline checkout 200、99.3 ms；慢 SQL checkout 200、1538.0 ms；关闭注入后 recovery checkout 200、28.3 ms。

从服务采到 66 metrics、2 logs、9 traces、1 deployment。独立 oracle 由 `postgres.query` trace 1509 ms 和 orders `duration_ms_sum` 从 27 ms 到 1532 ms 支持；payment spans 仅 25/29 μs，支持排除支付超时。MCP 看不到 `slow_sql` 或根因标签。

| 策略 | 模型调用 | 延迟 | 完整根因命中 | 证据 precision | 证据 recall | 峰值费用估算 |
|---|---:|---:|---:|---:|---:|---:|
| rules | 0 | 0.00 ms | 是 | 0.0256 | 1.0 | `$0` |
| single | 1 | 2607.97 ms | 是 | 1.0 | 1.0 | `$0.0035373` |
| always-multi | 5 | 3202.17 ms | 是 | 1.0 | 1.0 | `$0.0048723` |
| auto | 0（选 rules） | 0.04 ms | 是 | 0.0256 | 1.0 | `$0` |

四路都命中本例根因，没有模型请求失败。Single 与 Multi 各引用两条 oracle 证据；Multi 没有增加根因或证据命中，费用多 `$0.001335`，延迟多约 594 ms。Rules/auto 引用较宽，根因正确但证据 precision 低。

第一次短窗口只有单条 DB trace，规则无法形成跨源结论；模型路线均弃答。该次成本估算 `$0.0040364`。随后控制器加入健康 baseline，再执行一轮新鲜注入；最终对照成本 `$0.0084096`。两轮垂直切片合计 `$0.012446`。项目账本由 `$0.4515009` 增至 `$0.4639469 / $0.50`，剩余 `$0.0360531`。单例结果不代表泛化。

## 产物与限制

- [`vertical-slow-sql-window.json`](../../reports/vertical-slow-sql-window.json)：baseline/fault/recovery 时间线。
- [`vertical-slow-sql-evaluation.json`](../../reports/vertical-slow-sql-evaluation.json)：oracle、逐路指标、成本和失败。
- [`vertical-slow-sql-snapshot.json`](../../reports/vertical-slow-sql-snapshot.json)：服务原始遥测快照；不含真值标签。
- [`vertical-slow-sql-short-window-evaluation.json`](../../reports/vertical-slow-sql-short-window-evaluation.json)：首轮不足窗口的真实模型结果。
- [`api-cost-ledger.jsonl`](../../reports/api-cost-ledger.jsonl)：逐请求预留与累计费用估算。

完整回归：`95 passed, 14 subtests passed`；`compileall` 与 `git diff --check` 通过。测试宿主 Python 3.14.5。Docker 本地实验服务运行 Python 3.11.17；正式 `python:3.12-slim` 镜像未验证，之前 Docker Hub 认证超时，本阶段没有重试拉取。
