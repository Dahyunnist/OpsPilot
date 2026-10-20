# S5：独立模型专家与类型化交接

## 本阶段目标

把 S0–S4 的确定性调查基线保留下来，新增真正独立调用模型的指标、日志、追踪、部署专家，以及交叉质询和裁决角色。专家按来源隔离上下文；不能调用写操作。

## 消息与证据

`AgentMessage` 携带 task ID、消息 ID、发送方/接收方、消息类型、证据来源、token 预算、尝试次数和截止时间。`EvidenceRef` 明确来源与记录 ID。专家只能在自己的来源记录中引用；裁决再次检查引用是否存在。

协调器先运行原只读调查，再把不同来源分别交给四个专家。专家通过模型 API 独立推断并交接结果。交叉质询 Agent 检查冲突，裁决 Agent 汇总支持、反证和不确定性。所有角色调用同一模型服务，但有独立提示词、上下文和证据包；这不是多个规则函数改名。

## 边界与预算

项目 `.env` 读取 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_COMPLETION_TOKENS`。密钥不写入消息、轨迹或报告。上下文按来源与字符预算截断，并对常见密钥、邮箱和电话字段脱敏。token 总预算均分给四位专家、质询者与裁决者；记录服务端返回的实际 token 用量。

运行：

```powershell
python -m opspilot investigate pool-01 --mode multi-agent --token-budget 12000
```

默认 `rules` 模式仍离线、确定性。多 Agent 模式需要 `.env` 中有可用的 OpenAI-compatible Chat Completions 服务。任何建议动作都只作为建议；`approval_required` 强制为真，执行仍局限在旧的本地审批模拟器。

## 验证

- `tests/test_agents.py` 用假模型验证四份独立交接、类型化消息、引用校验和审批标记，不触碰网络或密钥。
- 本阶段没有把 8 个规则案例结果称为模型成绩。真实模型准确率、调用成本和跨运行稳定性要等盲测评测阶段报告。
- 已完成：`python -m unittest discover -s tests -q`，20 项通过。

## 限制

当前重试只记录失败并保留基线，不会重试失败专家；超时参数受模型服务连接控制。A2A 生命周期和取消交由 S6 单独实现。Agent 共享一个模型端点，故供应商层并非独立；逻辑推断与上下文彼此隔离。
