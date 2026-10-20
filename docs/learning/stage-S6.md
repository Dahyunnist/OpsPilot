# S6：A2A 交接与可恢复任务

## A2A 与 MCP

A2A Agent Card 声明独立 Agent 身份、技能和 JSON-RPC 地址。`message/send` 创建 task，`tasks/get` 轮询状态和 artifact，`tasks/cancel` 取消仍在执行的 task。OpsPilot 的协调器通过 loopback HTTP 客户端发现 Agent Card 并委派交叉质询；跨 Agent 数据以 DataPart 发送。

MCP 面向工具与上下文提供者：客户端发现工具并调用只读指标、日志、追踪或部署查询。A2A 面向独立 Agent：客户端发现能力并委派工作，Agent 以 task/artifact 交付结果。MCP 不等于 Agent 间任务协议，A2A 也不替代 MCP 数据工具。

参考规范：[A2A 0.3.0](https://a2a-protocol.org/v0.3.0/specification/)，[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。

## 生命周期与韧性

- task ID 关联父调查；A2A message ID 提供幂等键。相同 message ID 重放会返回同一个 task。
- 任务以 `working` 开始，再进入 `completed`、`failed` 或 `canceled` 终态。
- 客户端有截止时间、最多三次传输尝试；超时发送 `tasks/cancel`。取消会阻止迟到结果覆盖终态；底层同步模型请求无法强制中断，可能继续消耗 token。
- 请求体限制 1 MB；服务只绑定 loopback。Agent Card 和任务轨迹不写入 API 密钥。
- 专家交接包含每位 Agent 唯一的工具白名单和来源引用。工具数据查询仍由调查端运行，MCP 继续只做只读工具访问。

## 验证

`tests/test_agents.py` 启动真实本地 HTTP 服务，读取 Agent Card，发送 A2A JSON-RPC `message/send`，轮询 `tasks/get`，接收 artifact，并验证相同 message ID 幂等。另一项测试验证取消终态。多 Agent 主路径也通过 A2A 往返交叉质询结果。

此实现只覆盖 OpsPilot 需要的 A2A v0.3 JSON-RPC 方法，不支持流式传输、认证或完整一致性套件；不能宣传为完整协议合规。当前服务只在本机随机端口运行。

## 亲手练习

在 `tests/test_agents.py` 里把 A2A handler 延迟改长，运行 deadline 测试；确认 task 进入 `canceled` 后，迟到 artifact 不会覆盖终态。再重放相同 message ID，检查 task ID 不变。

## 面试问题

1. MCP 工具调用与 A2A task 委派分别解决什么问题？
2. 重试 `message/send` 时，幂等键放在哪里？为什么？
