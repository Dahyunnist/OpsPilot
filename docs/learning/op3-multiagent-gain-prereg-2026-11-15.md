# OP-3 多 Agent 增益对照：离线预登记

本记录对应 2026-10-23 升级路线中的多 Agent 增益对照，不是 OpsPilot 内部 S3/OP3 审批安全阶段。

## 离线状态

`reports/op3-multiagent-offline-prereg-2026-11-15.json` 绑定三份已授权合成快照 SHA、四类遥测来源及 `rules/single/always-multi/auto`。Gold 标签不进入该报告。全策略路由复用相同 prompt 时合并计数，最多 18 次 provider 请求；授权 worst-case reserve 为 `$0.02302260`。已记录累计峰值 `$0.52119297`，预留后 `$0.54421557/$0.60`。这是本地账本证据，不代表 provider 当前余额。

离线预登记和关联安全回归共 39 项通过。未读取 `.env`，未请求 provider 余额/模型列表，未调用模型；真实评测尚未开始。Provider 余额门禁仍待证据。

预定指标含完整根因集命中、Top-1、证据 precision/recall、费用、p95、失败轨迹、超时和取消。统一计数器读取脱敏 trace：`transport_timeout` 计超时；`tasks/cancel` 或取消状态计取消。报告附相同输入 trace 的四策略离线指标夹具，结果标为测试夹具，不冒充实验结果。Fake timeout/cancel 回归不调用模型。

性能对照仍未运行：根因、Top-1、证据 P/R、真实费用和 p95 均无结果。余额门禁未检查；不执行推理。A2A localhost 仍受 Windows `WinError 10013` 阻断，Linux 协议验证需在提交推送后运行。

## 环境阻塞

Windows A2A localhost 测试遇 `WinError 10013`；MCP 标准 SDK 测试挂起，本轮不跳过或记为通过。Linux 复核命令：`python -m unittest tests.test_s3 tests.test_action_safety tests.test_action_cli tests.test_agents tests.test_mcp -v`。GitHub Actions run `43673391511` 仍只有 failure 状态且无 job 记录；具体错误待用户提供，未推测根因。

未推送本提交。Push 后由 Linux Actions 验证；未新增模型授权，也未扩大样本范围。

## 2026-11-15 离线故障指标与 Actions 复核

策略结果现在统一带 `failure_outcomes`，从相同脱敏 trace 计超时和取消；汇总按每策略累加。新增无 provider 调用的 fake timeout、fake cancellation 和 A2A `tasks/cancel` trace 测试。离线报告附四策略相同的单超时/单取消计数夹具，并标明它不是实验结果。定向测试 57 项通过。

重新查询 Actions run `43673391511` 的 jobs，API 返回 `jobs: []`。无 job ID，无法取 step 或日志；失败原因仍未知。账户余额仍未检查；性能指标仍未运行。
