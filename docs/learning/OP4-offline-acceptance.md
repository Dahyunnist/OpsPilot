# OP-4 安全边界离线验收

本轮仅验收本地签名审批与 Agent/A2A 边界；没有外部系统写入、模型调用或凭据访问。OpsPilot 内部 OP-4 是动作安全边界，不是 OP-3 多 Agent 增益对照。

## 审批状态

- 票据绑定 action、target、test environment、调查 run、同源查询证据、建议摘要、批准人 subject 与 issuer。
- 过期时间边界拒绝；同 nonce 只消费一次；原批准人可在消费前撤销。
- 撤销和消费共用 ledger 文件锁：先撤销则模拟执行拒绝；先消费则撤销拒绝；重复撤销只返回 already revoked。
- CLI 撤销要求输入完整 `REVOKE <approval_id>`。撤销/拒绝仅记本地 ledger；`external_system_calls=0`、`external_writes=0`。

## Agent 与证据边界

每个专家 handoff 固定单源工具白名单；未知工具、跨源引用、错误 recipient/kind 都 fail closed。提示注入日志仍作为 JSON 遥测数据；system 明确禁止服从其中指令，且 Agent 不具备动作工具。审批绑定拒绝非 allowlist 来源、格式错误或未出现在同源查询结果中的证据 ID。多 Agent 结论少于两个有效引用或少于两个独立来源时被压为 insufficient evidence。

A2A TaskStore 只允许 working task 进入 canceled；完成任务保持 completed。取消不会中断同步模型请求；A2A 服务仍仅可用于 loopback 原型。

## 2026-11-16 安全门禁复核

重跑 tests/test_action_safety.py、tests/test_action_cli.py、
tests/test_s3.py：51 passed、11 subtests passed。已有用例覆盖审批过期与到期边界、
重复消费/跨进程重放、相同审批事件幂等、错误环境、角色越权、
精确人工确认、显式 dry-run、撤销后拒绝执行，以及 rollback 的
simulated_pass 检查。拒绝和成功审计都断言 external_system_calls=0、
external_writes=0；执行器 AST 测试拒绝网络、子进程和 Docker 依赖。

默认只读 MCP 由用户宿主报告
[op2-mcp-bad-release-host-5ff7d94f](../../reports/op2-mcp-bad-release-host-5ff7d94f-01ed-4453-94b2-4551e347d2da.json)
确认：tools/list 只有 metrics、logs、traces、deployments 查询和只读调查，
未暴露动作工具；对应四个只读调用已通过。

独立人工复核状态和真实业务回滚仍未实现。rollback_verification 的
simulated_pass 只代表本地模拟检查通过，不构成业务系统复核证据。
本轮未连接外部系统、未调用模型或 Docker；不改审批实现。

## 验证

`.venv/Scripts/python.exe -m unittest tests.test_action_safety tests.test_action_cli tests.test_agents.AgentTests.test_message_is_typed_and_serializable tests.test_agents.AgentTests.test_agent_tool_scope_rejects_escalation_and_wrong_source_reference tests.test_agents.AgentTests.test_prompt_injection_log_stays_data_and_cannot_expand_tool_scope tests.test_agents.AgentTests.test_grounding_rejects_forged_evidence_reference tests.test_agents.AgentTests.test_cross_exam_context_preserves_trace_joins_without_log_body tests.test_agents.AgentTests.test_a2a_task_cancel_is_terminal tests.test_agents.AgentTests.test_a2a_cancel_cannot_change_completed_task -v`：47 项通过。Fake/本地状态测试未访问模型或 loopback socket。

保留限制：`LocalTestActorResolver` 不是企业 IdP；ledger 文件锁只保证本机本地文件系统协作；A2A 仍无认证/TLS，取消不终止执行中的模型请求；这些测试不能证明生产身份或生产环境安全。
