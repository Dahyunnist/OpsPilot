# OpsPilot 本地原型架构

```mermaid
flowchart TD
    CLI[CLI: incident ID] --> ORCH[只读调查 orchestrator]
    ORCH --> MET[Metrics 查询]
    MET --> PLAN[指标驱动的后续查询策略]
    PLAN --> LOG[Logs 查询]
    PLAN --> TRACE[Traces 查询]
    PLAN --> DEP[Deployments 查询]
    LOG --> LEDGER[假设账本]
    TRACE --> LINK[trace ID / version 关联]
    DEP --> LINK
    MET --> LEDGER
    LINK --> LEDGER
    LEDGER --> REPORT[结论 / 无法判定 + 引用]
    REPORT --> AUDIT[查询与报告审计]
    REPORT --> CARD[审批卡片]
    CARD --> APPROVE{人工批准?}
    APPROVE -->|拒绝| DENY[拒绝审计，无动作]
    APPROVE -->|批准| SIM[本地模拟器]
    SIM --> AUDIT
    ORCH --> STATE[本地检查点]
    REPORT --> ROUTER[成本感知路由: auto]
    ROUTER -->|默认 / 无净冲突收益| RULES[规则结论或弃答]
    ROUTER -->|多根因各有跨源强证据| SINGLE[单 Agent 复核]
    ROUTER -->|人工显式升级| MET_AGENT[指标专家 Agent]
    ROUTER -->|人工显式升级| LOG_AGENT[日志专家 Agent]
    ROUTER -->|人工显式升级| TRACE_AGENT[追踪专家 Agent]
    ROUTER -->|人工显式升级| DEP_AGENT[部署专家 Agent]
    MET_AGENT -->|独立模型推断 / 类型化交接| EXAM[A2A 交叉质询 Agent]
    LOG_AGENT --> EXAM
    TRACE_AGENT --> EXAM
    DEP_AGENT --> EXAM
    EXAM --> JUDGE[模型裁决 Agent]
    MCP[MCP: 只读数据与工具访问] --> LOG
    MCP --> TRACE
    MCP --> MET
    MCP --> DEP
```

检查点和审计文件只保存在本机 `var/`。MCP 服务只读本地合成数据；批准动作只进入模拟器。

`auto` 只在多个根因各自获得至少两个独立来源的强证据时调用单 Agent；其他情况保持规则结论或安全弃答。多 Agent 保留显式调用，用于人工要求独立专家交叉质询的跨团队争议。复杂集验证与测试没有测出多 Agent 相对单 Agent 的根因或引用收益，因此它不在自动路由里。

MCP 描述 Agent 如何访问工具与数据；A2A 描述独立 Agent 如何发现能力、委派 task、传递 message 和接收 artifact。OpsPilot 交叉质询边界在 `127.0.0.1` 上使用 A2A v0.3 JSON-RPC，不承载工具访问。当前只实现项目所需方法，不宣称完整 A2A 合规。
