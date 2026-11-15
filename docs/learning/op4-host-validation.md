# OP-4 宿主离线验收工具

## bad_release 一次请求验收

在 Windows PowerShell 中从仓库根目录运行：

```powershell
Set-Location (git rev-parse --show-toplevel)
.\lab\verify-bad-release.ps1
```

该脚本由操作者显式启动。它先确认 Docker Compose 可解析、所需 loopback 端口空闲，再创建带随机 project name 的独立合成实验。它不会接管默认 `opspilot-lab` project，也不会执行 `down -v`。成功启动后，它只发送一次 `POST /checkout`，并要求 HTTP 502、唯一 order ID 的 `inventory-v2` DNS 日志，以及同一 UTC 半开窗口内唯一 `orders/v2` deployment。查询地址为 `18081/logs` 与 `18080/deployments`。

脚本结束时只检查和停止本次随机 project；保留其 PostgreSQL/Redis volumes。若启动命令失败，脚本只查询这个随机 project；仅发现其中容器时才停止。daemon 不可访问时会报告清理未验证，不会碰其他 project。端口冲突或 `OPSPILOT_COMPOSE_OVERRIDE` 已设置时，启动前中止。

结果 JSON 写到 `reports/op4-bad-release-host-<run_id>.json`，包含唯一 run/order ID、请求状态、部署版本、匹配日志与清理状态。deployment JSONL 追加到被忽略的 `lab/telemetry/deployments.jsonl`；该合成记录保留，后续查询必须使用自己的 UTC 窗口。报告只有在脚本实际通过所有断言时才标 `passed`；Docker 权限阻塞不是通过。

Windows PowerShell 5.1 对单条管道输出的 `.Count` 与 PowerShell 7 行为不同。DNS 断言已改为先将筛选结果显式收进数组，并明确匹配完整的 `No address associated with hostname` 错误文本。失败 run `8a23a2cd-779d-4d65-9027-bb12cc9e673b` 的原报告保留不变：502、唯一 orders/v2 deployment、订单日志和清理证据都有效，报告因 DNS 断言失败而标记 failed。该报告生成于脚本提交 `c09c634` 之后、兼容修复提交 `59b464d` 之前；它不含脚本 hash，不能进一步断言是 regex 还是 PowerShell 5.1 单条结果 `.Count` 导致断言失败。

修复后宿主 run `4830fe63-a445-4c8d-b47f-6951d45dc5f2` 完整通过：checkout 返回 502，唯一订单的日志含 `inventory-v2` 和 `[Errno -5] No address associated with hostname`；orders/v2 deployment 的 run/order ID 匹配。cleanup 确认容器清空、卷保留。报告见[宿主复验结果](../../reports/op4-bad-release-host-4830fe63-a445-4c8d-b47f-6951d45dc5f2.json)。Windows PowerShell 5.1 定向回归 4 项通过，覆盖 `error_reason` 单独命中；独立 Docker 宿主复跑也通过。

2026-11-15 又完成一次可访问 Docker 的 Windows PowerShell 宿主复核。报告 [cd8d4365 宿主验收](../../reports/op4-bad-release-host-cd8d4365-6be4-424c-845a-235dc148ceaf.json) 为 `passed`：合成数据、一次请求、HTTP 502；唯一订单对应 orders/v2 deployment，且同一订单日志记录 `inventory-v2` DNS 错误 `[Errno -5] No address associated with hostname`。脚本报告容器已清空、卷保留。受限执行环境遇到 Docker pipe `Access Denied` 的失败不代表项目故障；该轮未把失败环境报告当成验收结果。对应宿主脚本回归命令：`pytest tests/test_op4_host_validation_script.py`。本报告未记录脚本 hash；这里只记录报告中的实际验收字段，不推断运行时源码版本。OP-3 仍停在授权计划准备阶段，没有发送模型请求。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\lab\verify-bad-release.ps1
```

本机单元回归只运行 Windows PowerShell 5.1 断言片段，不启动 Docker；它不替代上面的宿主重跑。

## A2A/MCP socket 用例逐项复测

先确认依赖已装：A2A 用例需要仓库固定的 `requirements-a2a-interop.txt`；MCP 用例需要开发依赖。逐条运行，不要把挂起误报为通过；某项超时或权限拒绝后，保留完整错误，再运行下一条。

```powershell
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_a2a_sdk_interop.py::OfficialSdkInteropTests::test_official_sdk_client_discovers_and_uses_local_agent'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_agents.py::AgentTests::test_a2a_card_message_task_poll_and_message_idempotency'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_agents.py::AgentTests::test_a2a_deadline_cancels_late_result'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_agents.py::AgentTests::test_a2a_rejects_non_object_params'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_agents.py::AgentTests::test_a2a_transport_retry_reuses_idempotent_message'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_agents.py::AgentTests::test_independent_agents_handoff_and_adjudicate_with_validated_refs'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_mcp.py::MCPInteroperabilityTests::test_live_mcp_exposes_only_read_tools'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_mcp.py::MCPInteroperabilityTests::test_standard_client_calls_read_only_logs_and_trace_servers'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider 'tests/test_mcp.py::MCPInteroperabilityTests::test_stdio_subprocess_interop_with_standard_client'
```

These nine tests bind local loopback sockets or create asyncio socketpairs. Their result proves runtime behavior only on the host where the command completed. `tests/test_local_protocol_static_contract.py` is a separate source-level check and cannot replace these runtime tests.

## Codex 静态验收记录（2026-11-15）

新 PowerShell 脚本通过 Windows PowerShell 与 PowerShell 7 parser。脚本静态测试、协议源码契约、UTC 时间窗、OP-3 与 OP-4 离线测试共 `122 passed`、`13 subtests passed`。全套排除 9 个受 socket 环境阻塞的用例后为 `292 passed`、`76 subtests passed`、`9 deselected`。Codex 没有运行此宿主脚本；Docker Desktop pipe 权限仍需用户宿主联调。上述测试不证明现场 Docker、A2A 或 MCP runtime 互通。
