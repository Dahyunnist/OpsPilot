# OP-3 多 Agent 增益对照：离线预登记

本记录对应 2026-10-23 升级路线中的多 Agent 增益对照，不是 OpsPilot 内部 S3/OP3 审批安全阶段。

## 离线状态

`reports/op3-multiagent-offline-prereg-2026-11-15.json` 绑定三份已授权合成快照 SHA、四类遥测来源及 `rules/single/always-multi/auto`。Gold 标签不进入该报告。全策略路由复用相同 prompt 时合并计数，最多 18 次 provider 请求；授权 worst-case reserve 为 `$0.02302260`。已记录累计峰值 `$0.52119297`，预留后 `$0.54421557/$0.60`。这是本地账本证据，不代表 provider 当前余额。

离线预登记和关联安全回归共 39 项通过。未读取 `.env`，未请求 provider 余额/模型列表，未调用模型；真实评测尚未开始。Provider 余额门禁仍待证据。

后续离线契约回归用固定 fake client，覆盖 rules、single、multi 在缺失支持和矛盾证据下的弃答。fake outputs 由本地测试直接返回，未经过 provider；结果只证明证据闸门行为，不是模型对照，也不产生准确率、成本或延迟结论。当前余额未知，真实模型调用暂停。

预定指标含完整根因集命中、Top-1、证据 precision/recall、费用、p95、失败轨迹、超时和取消。统一计数器读取脱敏 trace：`transport_timeout` 计超时；`tasks/cancel` 或取消状态计取消。报告附相同输入 trace 的四策略离线指标夹具，结果标为测试夹具，不冒充实验结果。Fake timeout/cancel 回归不调用模型。

性能对照仍未运行：根因、Top-1、证据 P/R、真实费用和 p95 均无结果。余额门禁未检查；不执行推理。A2A localhost 仍受 Windows `WinError 10013` 阻断，Linux 协议验证需在提交推送后运行。

## 环境阻塞

Windows A2A localhost 测试遇 `WinError 10013`；MCP 标准 SDK 测试挂起，本轮不跳过或记为通过。Linux 复核命令：`python -m unittest tests.test_s3 tests.test_action_safety tests.test_action_cli tests.test_agents tests.test_mcp -v`。GitHub Actions run `43673391511` 仍只有 failure 状态且无 job 记录；具体错误待用户提供，未推测根因。

未推送本提交。Push 后由 Linux Actions 验证；未新增模型授权，也未扩大样本范围。

## 2026-11-15 离线故障指标与 Actions 复核

策略结果现在统一带 `failure_outcomes`，从相同脱敏 trace 计超时和取消；汇总按每策略累加。新增无 provider 调用的 fake timeout、fake cancellation 和 A2A `tasks/cancel` trace 测试。离线报告附四策略相同的单超时/单取消计数夹具，并标明它不是实验结果。定向测试 57 项通过。

重新查询 Actions run `43673391511` 的 jobs，API 返回 `jobs: []`。无 job ID，无法取 step 或日志；失败原因仍未知。账户余额仍未检查；性能指标仍未运行。

## 2026-11-15 provider 余额预检阻断

本轮执行只读余额预检，Windows 网络层返回 `URLError` / `WinError 10013`，在收到 provider 响应前中止。余额未知；模型列表未查询；未发起推理，模型调用 `0` 次，新增费用 `$0`。未写评测结果或请求 checkpoint，未携带 Gold 标签。

本地门禁通过：三份已授权快照 hash 匹配；冻结 spec hash 匹配；策略为 `rules/single/always-multi/auto`；请求上限 `18`；累计 worst-case `$0.54421557/$0.60`。provider 余额门禁未通过，因此没有策略比较结果。机器可读记录：`reports/op3-multiagent-provider-preflight-2026-11-15.json` 及其 `.run-manifest.json`。

## 2026-11-15 推理执行环境与审批阻断

用户回报授权 PowerShell 的只读预检通过：余额可用、模型族可用、三份冻结 SHA 匹配；18 次请求上限；累计 worst-case `$0.54421557/$0.60`。随后在本执行环境启动同 run ID 的对照，官方 provider GET 遇 `WinError 10013`，在推理前中止。账本无该 run ID 记录，模型调用 `0`、新增费用 `$0`，没有结果或请求 checkpoint；未重试。

网络提权请求被自动审批拒绝，理由为可信用户消息未明确授权本次具体推理请求或目的地，且暂停状态使授权不明确。该记录只描述本执行环境的失败尝试；根工作流随后在授权通道完成唯一 run。最终请求、费用、失败分类和一致性审计见 `reports/op3-multiagent-run-audit-2026-11-15.json`。

## 2026-11-15 OP-3 唯一 run 审计

run `fff674b6-f292-46fb-afe5-beeb5c64b563` 完成 partial 评测。三份冻结快照、冻结清单及实际 runner/scorer/model-client 哈希均匹配运行时 HEAD。账本与 checkpoint 对应：新增 `completed_request` 1 条、`run_summary` 1 条；实际 provider inference 1 次，费用 `$0.00033502`，累计峰值 `$0.52152799/$0.60`。授权 worst-case reserve `$0.02302260` 覆盖实际费用；全程 worst-case `$0.54421557/$0.60`。未超 18 请求上限。

multi 三案均因 `ValueError` 降级到 rules，不能作为有效多 Agent 对照。错误分类：本次 provider response/JSON 错误 `0`；旧 checkpoint usage 解析错误 `11`；其余 runner 错误 `0`。根因不是新响应 JSON 解析失败：旧 checkpoint 的 14 份安全响应均把 `prompt_tokens`、`completion_tokens`、`total_tokens` 脱敏成 `[REDACTED]`；usage parser 要求非负整数，因此专家结果读取时报 `ValueError`。本轮唯一新响应成功解析为 JSON 对象，usage 数字有效。rules 无新模型调用；single 复用既有模型 checkpoint；auto 一案走 rules、两案走 single 且复用 checkpoint。因此不能声称本轮完成四策略新鲜对照或有多 Agent 增益。

安全审计未发现响应中的邮箱、Bearer 字符串、电话或原始 prompt 字段；Gold 仅用于本地评分。未重跑已见题、未改 Gold。新增纯离线 checkpoint usage 回归，确保用量脱敏或畸形的历史响应不被复用；定向 4 项测试通过。机器可读审计列出文件哈希和计数，不含原始响应正文或 Gold。

## 2026-11-15 provider CSV 用量对账补充

仅在本地聚合用户提供的两份 CSV，未复制原文件，未读取或保存 `api_key`、`user_id` 原值。重新核算 amount 全表：除 `request_count` 外，各行 `amount × price` 合计 ¥10.91277486，与 cost 全表精确相等。`api_key_name=OpsPilot` 的 amount 表有 18 行、1,305 次请求；缓存命中输入 114,556 tokens、¥0.00327408；缓存未命中输入 1,494,968 tokens、¥1.931891；输出 345,510 tokens、¥1.861372。按导出单价推算合计 ¥3.79653708。记录日期为 10/20、10/23、11/14，末端到 11/15 00:00。

两份导出文件窗口均为 2026-10-17 至 2026-11-15。OpsPilot 的 ¥3.79653708 只代表该 CSV 区间，不是终身累计、当前 provider 余额或可用额度。导出省略零用量日；cost 表的六个有金额日窗覆盖完整导出窗口，不表示周期不完整。cost 是账号级 CNY，缺少 `api_key_name`，不可归到 OpsPilot；¥10.91277486 不与 OpsPilot 的 ¥3.79653708 相加，以免重复计费。本地 USD ledger 仍单独记录，不做无汇率依据的换算。原始 CSV 未复制入库或外发；细项见离线审计 `reports/op3-multiagent-run-audit-2026-11-15.json`。

用户另回报一次新的只读 provider 预检以 `URLError` / `WinError 10013` 中止，推理请求未发出。摘要没有 run ID 或 endpoint，故保留为空；不重试、不推断余额。记录见 `reports/op3-provider-preflight-host-blocked-2026-11-15.json`。

## 2026-11-15 OP-3 重放授权审计与阻断

provider usage redaction 曾改写含数字片段的冻结 record ID，导致 adjudicator 的 `all_evidence_ids` 与冻结快照不符。现在 redactor 保留已登记格式的 record ID；离线 payload harness 验证 3 例、18 个唯一提示（15 个固定请求、3 个响应依赖 adjudicator），0 provider 请求。静态请求 body hash、动态裁决器模板、冻结 SHA 与 reserve 见 `reports/op3-multiagent-payload-review-2026-11-15.json` 和复测计划。

定向 OP-3 测试 60 项通过。全套中可执行的其余 286 项与 76 子测试通过；9 个 A2A/MCP 测试因 Windows socket 权限或 asyncio socketpair 初始化未完成。重放命令随后被自动审批拒绝，进程未启动、provider 请求 0、推理 0、费用 0。脱敏阻断记录为 `reports/op3-multiagent-run-blocked-approval-review-2026-11-15.json`。不得绕过自动审批。

## 2026-11-15 OP-3 新授权重放计划准备

按用户后续明确指示，仅准备新计划，不执行推理。计划 `reports/op3-multiagent-authorized-replay-plan-9d8dbf6b-ddb5-4c7f-9d9d-bcabe122d5ad.json` SHA-256 为 `6f11271d6ccc4cfe1b9ee50c674aab60b1e35da99b618000168b5068b39f6c1a`，绑定独立 run ID、输出和 checkpoint 路径、OP-3 spec、三份冻结快照、四策略及精确静态提示 hash / 动态裁决器模板。执行器要求单独提供此计划路径及 SHA-256，并逐项检查快照、提示、账本、checkpoint、旧阻断产物和运行代码 hash。

预检来源为 `reports/op3-multiagent-final-6b03e236-6a28-4f40-b752-a6325f522cb3.json`，SHA-256 `4284cdfeb053123c7ee8c3e6d70d1ce98895d0e66a23da19f9199a4838c28665`，观测时间 `2026-11-15T13:30:19.705807+00:00`。证据显示 CNY 余额可用、`GET /models` 成功且列出 DeepSeek Flash；计划准备及校验时均在一小时有效窗内。来源 run 的推理请求数和实际费用均为 0。

当前 ledger 峰值 `$0.52152799`，OP-3 子集历史费用 `$0.04319797`；run reserve `$0.02302260`，累计 worst-case `$0.54455059/$0.60`。计划最多 18 次请求，只允许重放 17 个 usage 不可验证的精确历史付费提示键；其余有效 usage checkpoint 可复用。RunBudget 按请求锁定预算，提示或账本 hash 不符即停止。定向 OP-3、策略、模型错误和授权计划测试共 62 项通过，计划和 provider 预检绑定校验通过，`git diff --check` 通过。本阶段 provider 请求 0、推理 0、费用 `$0`；OP-3 实验仍未执行，旧阻断报告及 ledger 未改写。
