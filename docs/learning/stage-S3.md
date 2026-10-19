# S3 学习记录：可靠性、审批与 MCP

## 故障故事

数据源可能超时、重复返回记录或乱序返回；调查也可能因预算耗尽而中断。调查器要保留失败状态、清理重复和乱序数据，并允许从本地检查点续跑。结论建议若涉及高风险动作，必须先进入审批卡；批准后也只调用本地模拟器。

## 四类信号与异常处理

- **日志**提供错误原文；日志源超时会标记 `timeout`，不会伪装成零条结果。
- **指标**作为首查信号消耗查询预算；预算不足时，未查询来源会出现在假设的 `open_questions`。
- **追踪**可注入重复或乱序记录；响应按稳定记录 ID 去重，再按 UTC 时间与 ID 排序。
- **部署**仍按版本参与调查。来源超时会让相关假设保留未查证问题。

## 关键文件与数据结构

- `opspilot/investigator.py`：查询预算、故障注入、检查点读写、恢复和推荐卡片。
- `opspilot/safety.py`：查询审计、报告哈希、审批决定及事件 ID 去重。
- `opspilot/mcp_servers.py`：独立日志和追踪只读 MCP 服务，通过 stdio 暴露查询工具。
- `requirements.txt`：锁定 `mcp==2.2.0`；SDK 在项目 `.venv` 中安装。
- `docs/threat-model.md`：本地原型边界、威胁控制和安全不变量。
- `var/state/` 与 `var/audit.jsonl`：默认本地检查点和审计运行数据；均被 Git 忽略。

官方 Python SDK 文档使用 `MCPServer` 注册工具，并支持把 `Client` 直接连接到服务器对象做联调；stdio 用于本地子进程传输。[MCP Python SDK 首步教程](https://py.sdk.modelcontextprotocol.io/get-started/first-steps/)、[服务运行说明](https://py.sdk.modelcontextprotocol.io/run/)。

## 假设怎样被确认或否定

故障数据与 S2 相同。S3 保证即使来源超时或请求预算不足，S2 的跨信号引用仍保留状态。动作建议只出现在确定结论中，含 `action_id`、风险、审批要求和回滚说明。拒绝审批后写入 `rejected_no_execution`，批准后写入 `simulated_execution`；两个结果都声明 `external_write=false`。

## 两条异常链

1. **来源超时**：日志查询 timeout → 假设记录日志未取得 → 追踪与指标仍能支持下游判断 → 报告保留日志超时状态和审批卡，不把缺失日志当成“日志未见异常”。
2. **动作审批**：配置错误结论 → 生成回滚建议卡 → 人工拒绝 → 只写拒绝审计 → 同一事件 ID 再次批准被识别为重复，不执行第二次模拟动作。

## 测试命令与真实结果

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m opspilot investigate downstream-01 --inject timeout:logs --no-state --format json
.\.venv\Scripts\python.exe -m opspilot simulate-action config-01 --approval reject --event-id s3-demo-reject
```

2026-10-19 实测 17 项测试全部通过。标准 MCP Python 客户端分别发现 `query_logs` 与 `query_traces`，并成功调用两个本地服务器；另用标准客户端通过 stdio 子进程调用日志工具成功。预算为 2 时只发出两次查询，报告因预算未完成返回 `insufficient_evidence`。检查点续跑复用 metrics 查询，再完成剩余三次查询。日志 timeout 被记录为 `status=timeout`；重复记录被去除 1 条；乱序日志重新按时间与 ID 排序。

审批拒绝返回 `rejected_no_execution`、`executed=false`、`external_write=false`。随后用相同事件 ID 提交批准，返回 `duplicate_ignored`，审计文件中只有一条该 ID 的事件。批准不同事件 ID 时结果为 `simulated_execution`，仍不写外部系统。审计记录保存四类查询参数、返回记录 ID、状态和报告 SHA-256。

## 亲手实验：预算恢复与审批拒绝（约 10–20 分钟）

1. 运行 `.\.venv\Scripts\python.exe -m opspilot investigate pool-01 --query-budget 1`，查看 `var/state/pool-01.json` 中的 metrics 检查点。
2. 再运行同一案例并提高预算：`.\.venv\Scripts\python.exe -m opspilot investigate pool-01 --query-budget 8`。检查报告显示复用 1 次查询，并完成剩余来源。
3. 运行拒绝命令：`.\.venv\Scripts\python.exe -m opspilot simulate-action config-01 --approval reject --event-id hands-on-001`。
4. 用相同事件 ID 再提交 `--approval approve`。确认结果为 `duplicate_ignored`，并在 `var/audit.jsonl` 核对仅有拒绝事件。

## 面试追问

**问：为何超时不能当作空结果？**

答：空结果表示查询成功但无匹配记录；超时表示没有拿到数据。两者对假设的影响不同。

**问：审批拒绝后怎样证明未执行？**

答：拒绝路径只写 `rejected_no_execution` 审计记录；报告字段 `executed=false`，且动作执行层是纯本地模拟器。

**问：MCP server 为什么拆成日志与追踪两个？**

答：每个服务只拥有一个只读数据源和明确查询边界；标准客户端可独立发现、调用和审计工具。

## 未覆盖边界

超时、重复和乱序当前用本地确定性注入模拟，没有远程数据源。审计文件设计为单写者本地原型，未实现并发锁、轮转或防篡改存储。模拟审批不能代替真实身份验证与权限策略；真实凭证禁止接入。MCP 联调用的是 SDK 内存客户端，尚未连接真实宿主或生产服务。
