# OP-3：模型策略与费用对照

## 固定实验口径

`scripts/evaluate_lab.py` 先一次读取同一时间窗的真实服务遥测，再把同一 snapshot SHA-256 输入四种策略：规则、单 Agent、全量多 Agent、自动路由。真值文件只在模型调用结束后用于评分，不进入模型输入。多 Agent 对四个来源分别并行分析，再用独立裁决调用汇总；来源专家不会看到其他来源原始记录。

模型固定为 `.env` 的 API ID `deepseek-v4-flash`，温度为 0。DeepSeek 当前官方文档说明旧 ID 继续接受，但请求转到 V4.1 Flash 并按 Flash 费率计价；评估器使用峰时保守费率：缓存命中输入 `$0.006/M`、未缓存输入 `$0.30/M`、输出 `$1.20/M`。[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)

`RunBudget` 从逐请求账本读取历史峰值，所有运行共用 `$0.50` 累计上限；超额请求在发送前拒绝。OP-3 后峰值 `$0.43784833`；随后 OP-1 单场景垂直切片将峰值提高到 `$0.46394690`，剩余 `$0.03605310`。预算上限不是账户余额。失败请求按预留上限计入峰值账本，成功请求按模型返回 tokens 估算。无模型调用的 rules 路线费用为零。

输出 Top-1、Top-3 根因召回、完整根因集合命中、误报根因数、证据 precision/recall、每例模型调用数、估算成本/成功例、P95 与失败例。每例的成本成功数只对完整根因集合命中计数。证据 gold 由冻结机制对应的实测遥测记录在评分器中选取，评价报告保存引用 ID；机制标签不交给 Agent。

自动路由只在规则没有结论且存在强歧义候选时升级。已被跨源证据充分支持的规则结论直接保留。模型提出的根因还必须引用规则账本中同一根因的两条真实支持记录、来自两个独立来源；否则该声明会被抑制。

## 重放

启动 Docker Desktop 并确认 `.env` 仍配置固定模型后：

```powershell
.\lab\evaluate-all.ps1
```

脚本每次只启动一个故障、重置实验数据库、发送一笔订单、在同一时间窗运行四策略，再转到下一故障。单案例结果及汇总分别写入 `reports/lab-strategy-<case>.json` 和 `reports/lab-strategy-summary.json`。重复运行会继续使用同一 `$0.50` 累计账本，因此可能在后续案例停止模型请求；失败和停止原因保留在报告中。

已有六个冻结故障窗口的对照结果，Multi 六例均失败，不能比较其准确率或声称收益。一个较早的非冻结开发窗口花费 `$0.0045072`，复现了 `finish_reason=length`。最终升级验收在另一个全新本地依赖超时窗口对照 rules、single 与 Multi：10 次尝试的最坏预留 `$0.038859`，实际 7 次请求、账本估算 `$0.01365257`。Multi 专家首次 320-token 截断后，512-token 重试成功。本例根因及证据都命中独立 oracle，但单例不能证明泛化或生产收益。详细轨迹和限制见[最终升级验收](../../reports/final-upgrade-acceptance.md)。冻结集未重跑。

离线路由边界回归重新运行 S15 mock harness 的 30 个冻结 test 案例，覆盖五类机制、每类 6 例。每案保留 `query_count` 与 `query_budget_exhausted`，两者也进入确定性 fingerprint。默认查询预算 8 时，auto 选 rules 18 例、single-agent 6 例、multi-agent 6 例；6 个 insufficient/conflicted 案例均留在 rules 并弃答。模拟器 model_calls 与费用都为 0。将查询预算降至 2 后，Investigator 标出未完成 follow-up；修复后的 auto 对 30/30 案例降级至 rules，均无根因输出。预算参数为 2 的运行清单在内存中通过校验。此测试证明冻结输入下的路由降级与报告契约，不证明 Agent 质量或模型收益。

另外用本地 fake call gate 拒绝 Auto single 路由预算申请：不调用 FakeChatModel，记录 `fallback_route=rules`，输入全空时保持 `insufficient_evidence`。真实 RunBudget/provider 行为仍需单独受控验证；本轮不访问模型 API。

之后另完成 OP-1 slow SQL 实时切片：rules、single、Multi、auto 均命中该例 oracle；Multi 与 single 没有增加根因或证据命中。完整计时、precision/recall 和成本见[垂直切片学习记录](OP1-vertical-slice.md)。这仍是单例证据，不代表泛化。

## 2026-11-13 OP-3 离线评测边界复核

冻结 S15 test 已有 30 例，五个机制族各 6 例：pool 根因、配置根因伴随下游症状、下游超时伴随 pool 压力、独立双故障、证据不足/冲突。每族的独立真值记录根因及 gold evidence ID；输入含 metrics/logs/traces，deployments 按机制变化。四种策略共用相同冻结输入，manifest/data/labels SHA-256 已锁定。生成器复用共同记录构造器和干扰项，所以这是因果机制与遥测来源层面的合成异质性，不是独立真实事件或生成来源多样性的证明。

`labels/test.json` 与 `test.jsonl` 分离。离线 runner 先运行 investigator 和策略预测，再将 label 接入评分；labels 不传给 investigator、route policy 或预测适配器。报告 manifest 明确 `truth_access=post_prediction_scoring_only`，每族的机制及来源覆盖计数也进入 `dataset_audit`。预测仍由确定性 mock policy 生成，不是 Agent/模型输出。

runner 逐例保留 investigate、预测或注入取消/超时/失败的 trajectory；汇总包含取消、超时、失败和 query budget exhausted/最大/总查询数。注入超时使用 `--inject-timeout STRATEGY:INCIDENT_ID`。固定集默认预算 8 时各策略均未耗尽，最大 4 次查询、总计 120 次。mock 固定零模型调用与零费用；本轮禁止模型请求、Docker 和推送。

定向 `python -m unittest tests.test_s15_mock_ablation -v`：8 项通过。该阶段补强证明评测隔离与 runner 报告契约，不构成真实模型质量比较。真实模型对照仍须用户明确授权。

## 2026-11-13 对照 harness 复现

复用 `scripts/evaluate_s15_mock_ablation.py` 作为 OP-3 离线对照 harness：策略标签 `rules`、`single`、`always-multi`、`auto`；30 个冻结案例同输入配对，真值预测后评分，重复运行校验确定性结果 SHA-256。回归命令：`.\.venv\Scripts\python.exe -m unittest tests.test_s15_mock_ablation tests.test_strategy_eval -q`。mock runner 固定 0 模型调用、0 费用；只验证输入/评分隔离和路由控制，不评估模型质量或泛化。本轮未调模型。

## 2026-11-13 OP-3 正式评测预检

新增 `scripts/evaluate_op2_frozen.py`，为 OP-2 三个冻结快照执行真实策略对照。运行前校验注册快照 SHA-256、共用时间窗、固定模型、512 completion cap、DeepSeek 余额/模型只读预检及累计 `$0.60` 账本。报告保留逐案根因命中、证据 precision/recall、费用、p95 与失败。默认拒绝运行；必须显式传 `--execute`。

离线核验：三份快照哈希与注册清单匹配；账本峰值 `$0.47833002`，注册最坏成本 `$0.0230226`，剩余预算 `$0.12166998`。DeepSeek `/user/balance` 返回 `is_available=true`；账户余额不写入仓库。尚无模型推理调用。自动审批拒绝将冻结遥测及派生内容发送给外部模型服务；真实 Agent 结果待明确批准后再跑。OP-3 mock harness 不替代此真实评测。

`tests.test_op3_frozen_eval` 覆盖固定哈希清单和 `--execute` 显式闸门。宿主复跑命令：`.\.venv\Scripts\python.exe scripts\evaluate_op2_frozen.py --execute`。

## 2026-11-14 OP-3 合成快照真实模型部分评测

用户授权只发送 3 份登记 SHA-256 的合成遥测快照及由固定 evaluator 生成的提示；不发送 gold、密钥、私人文献或真实客户数据。首次运行发现通用脱敏器重写了 `record_id`，与原快照 allowlist 不匹配。修复后增加精确快照哈希校验、gold 字段拒绝、来源/记录逐条比对，以及每请求费用预留。

首次 5 次成功请求的响应正文未写入评测报告，但逐条费用账本保留了 token 用量和提示 SHA-256。修复后续跑将成功响应立即写入本地 JSONL checkpoint；下次运行先复用成功响应，把存在记录但正文不可恢复的请求标记为 `PriorPaidResponseUnavailable`，不会再次计费。检查点文件匹配 `reports/*.requests.jsonl` 并被 Git 忽略，不能提交到远端。

本次共 3 个合成案例，评测状态为 **partial**，不是完整策略对照：规则策略 3/3 可评分，single 与 auto 各有 2/3 完成，multi 0/3 完成；失败包含预算余量不足与早期已付但响应不可恢复。成功根因集合统计不适合比较策略优劣。真实模型推理累计峰值估算为 `$0.02099070`，批准子集 cap `$0.02302260`，余量 `$0.00203190`；项目累计峰值 `$0.49932072 / $0.60`。续跑在额外推理前因余量不足而 fail-closed。服务余额和模型清单预检成功；没有超出授权快照范围。

验证命令 `\.venv\Scripts\python.exe -m unittest tests.test_op3_frozen_eval tests.test_benchmark -v`：`21` 项通过。全套 pytest 本轮启动后无输出，未完成退出；不得把它计为通过。报告为 `reports/op3-op2-subset-evaluation-fixed-2026-11-14.json`。响应检查点和未跟踪 `.tmp/` 不提交。该小样本只证明数据边界、费用闸和部分请求可执行，不证明 Multi-Agent 收益或真实事故泛化。

## 2026-11-14 OP-3 partial report and test stall

Authoritative report is `reports/op3-op2-subset-evaluation-fixed-2026-11-14.json`; do not use the earlier unfixed report. Status is `partial`, with 3 synthetic cases only: rules completed 3/3, single 2/3, always-multi 0/3, auto 2/3. This is not a strategy win or generalization result. Current run added `$0`; prior approved subset spend is `$0.02099070`, leaving `$0.00203190` under its `$0.02302260` cap. Failures are `PriorPaidResponseUnavailable` for missing historical response bodies and `BudgetLimitExceeded` for requests whose peak reservation exceeds remaining subset allocation. `model_calls=0` in this fixed report; 22 payload audit entries include rejected/replayed attempts, not successful provider calls. P95 values (rules 0.02 ms, single 22.71 ms, multi null, auto 8.37 ms) measure local replay/orchestration only; they are not provider inference latency. No quality comparison is valid across these partial results.

Full test collection found 219 tests. `pytest -vv -s` stalled first at `tests/test_a2a_sdk_interop.py::OfficialSdkInteropTests::test_official_sdk_client_discovers_and_uses_local_agent`; faulthandler located the stall in Windows Proactor self-pipe initialization at `socket.accept` / `_fallback_socketpair`. Restricted run did not complete. Authorized targeted rerun passed 1 test in 1.70 s, classifying the stall as execution-environment socket permission, not a code regression. Full-suite status remains incomplete, not passed.

User host `bad_release` JSONL confirms nine services started, healthy baseline passed, orders/v2 deployment observed, checkout HTTP 502, logs returned, then cleanup removed 10 containers and one network; `compose ps --all` was empty. Source is user-provided host summary. It lacks exact window, order ID, record contents/counts and detailed failure cause, so this does not establish an Agent root-cause hit. Evidence: `reports/op1-bad-release-host-result-2026-11-13.json`.

## 2026-11-14 OP-3 冻结响应复用与离线重评分

仅检查三份登记快照及其受控请求 checkpoint；三份本地快照 SHA 均匹配 manifest。用户授权引用按文件名对应，但第三个授权哈希文本 `de76add2b1dc15e4da2207632d44c39c39ba813` 与本地 SHA-256 `de76add2b1dc15e4da2207632d44e5cfa4c849abe9a82604a34cfc49423ba813` 不同，因此标记 `authorization_hash_match=false`，不得称三份哈希均获逐字授权。checkpoint 含 6 份历史成功 provider 响应正文（normal_slow_sql 4、missing_pool_evidence 1、contradictory_payment 1），仅用于离线重评分；没有新模型调用。使用 fixed 报告已保存的策略预测、冻结 truth 和快照派生 gold 证据运行 `score_case`，审计结果见 `reports/op3-op2-response-audit-2026-11-14.json`。本轮模型调用 0、新增费用 `$0`；OP-3 仍为 partial，只有 complete 项计为有效评分，失败策略不纳入命中比较。未访问 `.tmp/`。

## 2026-11-14 两份授权快照真实评测审批阻塞

本轮仅核对授权 SHA 匹配的 `normal_slow_sql` 与 `missing_pool_evidence`。最新费用账本峰值 `$0.49932072 / $0.60`，剩余 `$0.10067928`；按两案注册峰值 `$0.01451580` 完整预留后余 `$0.08616348`。DeepSeek 余额和模型清单 GET 均成功，`is_available=true` 且 CNY 余额为正；清单列出 `deepseek-flash`，未精确列出配置 ID `deepseek-v4-flash`。真实评测启动命令被自动审批拒绝，因为审批器认为可信用户消息未明确授权这两份具体快照/提示发往 DeepSeek 并产生费用。命令进程未启动，账本行数未变（536），新模型调用 0、费用 `$0`，也未生成新响应检查点或评测报告。阻塞审计见 `reports/op3-op2-authorized-two-preflight-blocked-2026-11-14.json`。两案真实评测和 oracle 评分未完成；既有 checkpoint 仍只作为历史响应。

## 2026-11-14 授权重试预检

逐字节重算三份快照 SHA-256，均匹配冻结清单：slow SQL `d450db5fc6851dabf7d732cd07ea7e229a872c76e29cfc39a3add2466749c9d8`；missing pool `d244e00980dfb160831df77ae187208c186a4ba0489831d1b4bda81ef68bbbe7`；contradictory payment `de76add2b1dc15e4da2207632d44e5cfa4c849abe9a82604a34cfc49423ba813`。官方余额接口报告可用，模型目录可达且 Flash 可用；账户余额不写入仓库。项目账本峰值 `$0.49932072 / $0.60`，项目余量 `$0.10067928`；但注册子集累计已用 `$0.02099070 / $0.02302260`，只余 `$0.00203190`，低于完整三例最坏预留 `$0.02302260`。本次未发送推理请求，新增调用 0、费用 0。冻结预登记文件不改；正式比较仍以 fixed 报告的 partial 结果为准，不推断策略提升。

## 2026-11-14 冻结输入与失败报告离线预检

新增离线审计核对三份登记快照的 SHA、查询窗/条数、OP-2 共享快照 manifest、既有 fixed partial 评测报告及此前被自动审核拦截的预检失败报告。失败记录仍为 `blocked_before_request_egress`：进程未启动、模型调用 0、费用 0、未生成新评测报告或请求检查点。审计以共享 `op_run_manifest` 绑定冻结数据清单和审计结果；原始 OP-3 报告及其 Agent 预测/评分不改写。没有读取 `.env`、费用账本或请求检查点，也未重试出站请求。审计见 [OP-2/OP-3 冻结输入审计](../../reports/op2-op3-offline-preflight-audit-2026-11-14.json)。定向回归 72 项通过。


## 2026-11-14 三案冻结清单预检

Runner 现要求并读取冻结清单内全部哈希校验案例；成本仍按各案 worst-case 求和。报告仅含哈希/计数/样本键，未读账本、未调用模型。三案预检见 reports/op3-three-case-preflight-2026-11-14.json。第三案外发仍待用户确认完整哈希。



## 2026-11-14 预算停止状态复核

固定报告原样保留。补充审计将 BudgetLimitExceeded 标为 blocked，将所有 PriorPaidResponseUnavailable 样本标为 not_run；不计作 Agent 质量失败。三样本、失败类型、账本汇总及三份冻结 SHA 可复现；子集余量仍为 .0020319，低于三案预留 .0230226。见 reports/op3-budget-stop-status-audit-2026-11-14.json。


## 2026-11-14 三案评测预检阻断

三案 SHA 与冻结清单匹配；账本总峰值 $0.49932072，总 cap 余 $0.10067928，三案预留 $0.0230226 在总 cap 内。修复预算 float/Decimal 比较后，供应商只读预检遭 Windows 网络权限错误（URLError）；推理未启动、调用 0、费用 0。未重试网络或改走其他路径。阻断记录见 reports/op3-three-case-preflight-blocked-2026-11-14.json。
