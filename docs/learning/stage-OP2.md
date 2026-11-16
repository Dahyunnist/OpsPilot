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

## 2026-11-16 调查入口时间窗与条数边界

复核发现 adapter 会拒绝无时区时间、超过 24 小时的窗口及 limit 不在
1–1000 的查询；investigate_live 自身却只检查 start/end 顺序。它可在自定义
telemetry 实现下接受无时区时间，并由宿主时区解释；也会把非法 limit 传入查询。
现在调查入口在创建/调用 adapter 前统一调用 LabTelemetry._bounds，复用同一
UTC 时区、24 小时窗口与 limit 契约。回归以禁止任何查询的 fake 验证所有非法输入
均先失败。定向测试：45 passed、19 subtests passed。未启动 Docker、未调用模型或外网。

仍需用户宿主复测 Docker 合成服务、只读 MCP 工具和同窗调查流程；离线 fake 不证明
现场容器与遥测链路可用。


## 2026-11-14 证据快照清单

`capture_lab_snapshot.py` 对成功快照及缺少来源的失败快照均写入共享 `op_run_manifest` 侧车记录，绑定原始快照哈希、来源数量、共同查询窗口和 Git revision；快照不适用真值，模型调用与费用均为 0。现有 OP-2 子集的三份冻结快照已在本地核对哈希、来源结构及查询窗口，逐例与汇总清单见 [快照清单审计](../../reports/op2-snapshot-manifest-audit-2026-11-15.json)。原始采集时间未记录，审计不评价外部授权，也没有调用模型。

## 2026-11-14 冻结证据只读复核

新增 `tests/test_op2_frozen_snapshot_readonly.py`，直接用登记的 `missing_pool_evidence` 与 `contradictory_payment` 快照驱动只读调查；查询必须沿用快照共同时间窗与条数上限，所有 evidence reference 必须能解析回输入记录。两案规则调查均保持弃答；把来源记录清空后，要求无根因、无引用。已有 `test_live_telemetry.py` 与 `test_op2_source_status.py` 覆盖半开窗、筛窗后限条、stable source pointers、来源错误/空结果和矛盾发布引用。离线汇总见 [OP-2/OP-3 冻结输入审计](../../reports/op2-op3-offline-preflight-audit-2026-11-15.json)。不改变模型 Agent 报告或根因标签；这不是 live service 验收。

## 2026-11-15 bad_release 跨信号关联回归

用户宿主输出确认本次 checkout 返回 HTTP `502`，同一报告时间窗内 orders 日志有 `inventory-v2` DNS 失败，deployment 记录为 orders `v2`。这是用户回传的宿主事实；本机未重启或查询容器。此前另一轮 checkout 的 HTTP `504`，以及错误 API 端口/时间窗查询得到的 deployment `0`，都保持为旧记录，不并入本次结论；背景见[宿主取证记录](op-audit-2026-11-11.md)。

`tests/test_live_telemetry.py` 新增固定合成 bad_release 证据：同一 UTC 窗口内先有 orders/v2 deployment，再有绑定唯一 order ID 的 inventory DNS 错误日志；日志与 orders/v2 span 共享 trace ID，orders span 由 gateway checkout span 调用。调查报告分别列出 deployment→同版本错误和日志→同请求 span 两条关联，并把 span 纳入证据引用。该 trace 关联是合成回归证据，不冒充用户宿主输出。

反例覆盖 trace ID、order ID、service、版本不一致，以及 span 恰好位于窗口 end。此时不创建同请求 trace link，报告写入 `unlinked_request_trace` 缺口；deployment/log 时间关联与 trace 关联分开呈现。调查入口会再次执行 UTC 半开窗过滤，避免错误窗口记录参与推断；已有反例继续验证错误早于 deployment 时保留反证并弃答。定向回归 `tests.test_live_telemetry tests.test_op2_source_status tests.test_op2_telemetry_robustness tests.test_op2_frozen_snapshot_readonly tests.test_lab_checkout_e2e tests.test_strategy_eval`：63 项通过；未调用模型或外部 API，未启动 Docker。

## 2026-11-15 宿主矩阵半开时间窗边界修复

审计发现 PowerShell `Test-InEvidenceWindow` 和 Jaeger `Get-WindowSpans` 将 end 边界也纳入窗口，与 Python adapter 的 `[start,end)` 不一致。现在日志/deployment 时刻用 `instant < end`；Jaeger microsecond `startTime` 转为 .NET ticks 比较，避免先降到毫秒导致边界漂移。新增 PowerShell 回归检查 start 前、start、end 前和 end 精确值；只有 `[start,end)` 内信号保留。

同轮离线复核现有 OP-2 adapter：请求限 24h 和 1–1000 条、先筛窗再全局限量、stable source pointer、source missing/partial/error、乱序 deployment、矛盾信号弃答都有既有测试。`tests/test_op1_host_matrix.py tests/test_live_telemetry.py tests/test_op2_telemetry_robustness.py tests/test_op2_source_status.py tests/test_op2_frozen_snapshot_readonly.py tests/test_lab_time_window.py`：59 项和 18 子测试通过；未启动 MCP/服务或模型。

## 2026-11-15 新 bad_release 窗口 MCP 宿主验收

`.\lab\verify-bad-release.ps1 -VerifyMcp` 在全新隔离项目中启动合成 `bad_release`，通过 Python MCP 标准客户端的 stdio transport 完成 initialize 与 tools/list。验证确认只暴露既有五个只读工具，然后对同一 UTC 窗口调用 logs、deployments、traces 与 `investigate_live_incident`。结果绑定唯一 order ID、run ID、orders/v2 deployment、inventory-v2 DNS 错误日志和同订单/同 trace 的 orders span；只读调查结果引用了这三类记录。

完整结构化结果见[新窗口 MCP 宿主验收报告](../../reports/op2-mcp-bad-release-host-a06d8717-5d71-497c-af8b-04dfbea9029f.json)。cleanup 显示容器清空，卷保留。测试只访问本地合成实验台，没有调用模型或生产遥测；这补齐新 Docker 故障窗口上的 MCP 全链路证据，不构成生产连接验收。离线回归 `tests.test_op2_mcp_host_validation tests.test_mcp tests.test_live_telemetry tests.test_op2_telemetry_robustness tests.test_op2_source_status`：49 项通过。

## 2026-11-16 缺 trace 与矛盾发布负例

扩展 `test_missing_trace_and_conflicting_release_signals_force_abstention`：orders DNS 错误和 v2 deployment 提供相互冲突的发布线索，同时 trace 查询返回空集且日志缺 trace context。调查必须保持 `confidence=abstain`、无根因，保留可解析的日志/部署引用，并明确 `traces=empty` 与 `missing_trace_context`。纯合成离线回归，不请求服务或模型。

同日宿主 MCP bad_release 报告 `op2-mcp-bad-release-host-5ff7d94f-01ed-4453-94b2-4551e347d2da.json` 通过：HTTP 502、orders/v2 部署与 `inventory-v2` DNS 日志匹配；5 个 MCP 工具均为只读查询/调查工具。结束时容器数为 0，未删除卷。此为本机合成宿主证据，不证明生产系统或真实 MCP 主机安全。
