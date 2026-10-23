# OP-2：只读遥测与因果证据账本

## 交付

`opspilot/telemetry.py` 用 GET 读取本地 Prometheus、Jaeger、业务服务日志和部署记录。端点必须指向 HTTP loopback；请求限制 24 小时时间窗、1–1000 条、3 秒超时和 2 MB 响应。适配器归一化记录，生成稳定 source reference；真值清单不参与查询。

`opspilot/live_investigation.py` 建立五类机制的候选账本：连接池耗尽、支付超时、错误发布、慢 SQL、重试放大。支持与反证逐条保留来源引用。Jaeger parent span 构成服务依赖边。至少两个独立来源支持、至少两条支持记录且无反证时才标高置信；否则不报告根因。该分数是规则排序值，不是概率或统计校准结果。

质量检查报告：来源缺失、日志缺 trace ID、采样覆盖率粗估、同 trace 日志/span 时钟偏差、部署记录顺序异常。系统不将缺失观测当作否定证据。当前时间偏差检查只覆盖同 trace 日志与 span；采样覆盖率是粗略比例，需真实实验校验。

通过 `.\scripts\run_mcp.ps1 -Source live` 启动实时 MCP。工具只有 `query_live_metrics`、`query_live_logs`、`query_live_traces`、`query_live_deployments`、`investigate_live_incident`。它们都要求显式 UTC 时间窗和行数上限；端点只接受 loopback。MCP 不提供注入、清除或其他写操作。

## 验证

单元测试使用固定 HTTP 响应替身，检查 GET 方法、loopback 限制、时间/数量边界、稳定引用、全局行数上限、反证弃答、超时与重试分离、部署乱序识别，以及实时 MCP 工具清单。它们验证适配逻辑，不代替 Docker 服务集成测试。OP-1 的引擎阻塞仍未解除，故本阶段未读取真实运行中的 Prometheus/Jaeger。

本阶段没有模型 API 调用；模型仍固定为 `.env` 中的 `deepseek-v4-flash`。峰值账本未变化：`$0.33974803 / $0.50`，剩余预算 `$0.16025197`。
