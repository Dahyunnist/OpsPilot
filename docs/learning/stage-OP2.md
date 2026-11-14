# OP-2：只读遥测与因果证据账本

## 交付

`opspilot/telemetry.py` 用 GET 读取本地 Prometheus、Jaeger、业务服务日志和部署记录。端点必须指向 HTTP loopback；请求限制 24 小时时间窗、1–1000 条、3 秒超时和 2 MB 响应。所有 adapter 使用半开 UTC 窗口 `[start,end)`，先筛窗再套全局 limit。适配器归一化记录，生成稳定 source reference；真值清单不参与查询。

`opspilot/live_investigation.py` 建立五类机制的候选账本：连接池耗尽、支付超时、错误发布、慢 SQL、重试放大。支持与反证逐条保留来源引用。Jaeger parent span 构成服务依赖边。通常至少两个独立来源支持、至少两条支持记录且无反证时才标高置信；支付超时还必须有同一请求的 orders→payment parent 链。该分数是规则排序值，不是概率或统计校准结果。

质量检查报告：来源缺失、日志缺 trace ID、采样覆盖率粗估、同 trace 日志/span 时钟偏差、部署记录顺序异常。系统不将缺失观测当作否定证据。当前时间偏差检查只覆盖同 trace 日志与 span；采样覆盖率是粗略比例，需真实实验校验。

通过 `.\scripts\run_mcp.ps1 -Source live` 启动实时 MCP。工具只有 `query_live_metrics`、`query_live_logs`、`query_live_traces`、`query_live_deployments`、`investigate_live_incident`。它们都要求显式 UTC 时间窗和行数上限；端点只接受 loopback。MCP 不提供注入、清除或其他写操作。

## 验证

单元测试用固定 HTTP 响应替身检查 GET、loopback、时间/数量边界、稳定引用、全局行数上限、反证弃答、超时与重试分离、部署乱序和 MCP 工具清单。Python 3.11 Docker 服务烟测及后续新开发窗口已实际读取 Prometheus、日志、Jaeger trace 和部署记录；窗口和费用条件见[最终升级验收](../../reports/final-upgrade-acceptance.md)。这只证明本地合成实验集成，不证明生产遥测覆盖。

trace adapter 要求 Jaeger span 同时带非空 `traceID` 与 `spanID`。缺任一来源身份的 malformed span 会被丢弃；只有这类 span 时调查必须弃答，且不得生成 evidence reference。该边界由合成 HTTP fixture 离线验证，不代表生产 Collector 验收。

调查报告对每类来源都输出 `sources` 详情：状态 `ok`、`empty`、`partial` 或 `error`，请求时间窗、查询参数、记录数、source/record ID 引用、服务级状态与失败类型。HTTP 200 且筛窗后无匹配记录标记 `empty`；部分端点失败即使没有记录也标 `partial`；全部日志/trace 子端点失败标 `error`。整类 logs 503 与单服务 503 都由离线 HTTP fake 覆盖。冲突的 v2 deployment 与其前置 DNS 错误保留支持/反证和两条来源引用，报告弃答，不拼出确定根因。未启动 Docker 或模型；这是 adapter 集成回归，不是 live 遥测验收。

新增 adapter 边界回归：metrics 窗外点不占全局 limit，limit=1 仅返回窗内记录，重复查询 source ID 稳定；缺 `record_id` 的 deployment 由规范化内容生成稳定 `deployments:` 引用。既有 fixture 同时覆盖 logs/metrics/Jaeger/deployments 时间窗、全局 limit、乱序部署、trace ID/span ID 缺失和同 trace 时钟漂移。缺 trace 时必须弃答，不能生成 trace 引用。

支付超时根因另要求请求级因果链：同一 trace 的 orders 超时日志，关联到 payment/charge span，且该 span 的 parent 链经过 orders/orders，日志与 span 起始时间偏差不超过 2 秒。报告将此列为 `causal_trace_link` 并保留两条 source refs。不同 trace 或超出偏差的日志/span 只标 `cross_source_correlation_only`，同时警告并拒绝把 payment timeout 列为 root cause。其余 source 支持仍按各自显式机制规则判断，不把同窗共现自动等同于因果。

定向回归 `tests/test_live_telemetry.py tests/test_op2_telemetry_robustness.py tests/test_op2_source_status.py`：`41 passed, 8 subtests passed`。不启动 Docker/模型。宿主 Docker 验证先运行 `.\lab\smoke.ps1`，再运行 `.\scripts\run_mcp.ps1 -Source live`，并对同一 UTC 窗口调用 `investigate_live_incident`；本轮未跑宿主流程。

MCP 工具清单与标准客户端互操作有离线测试；本次未再启动 MCP 服务对真实 Docker 遥测执行工具调用。生产连接仍不支持。


## 2026-11-14 证据快照清单

`capture_lab_snapshot.py` 对成功快照及缺少来源的失败快照均写入共享 `op_run_manifest` 侧车记录，绑定原始快照哈希、来源数量、共同查询窗口和 Git revision；快照不适用真值，模型调用与费用均为 0。现有 OP-2 子集的三份冻结快照已在本地核对哈希、来源结构及查询窗口，逐例与汇总清单见 [快照清单审计](../../reports/op2-snapshot-manifest-audit-2026-11-14.json)。原始采集时间未记录，审计不评价外部授权，也没有调用模型。
