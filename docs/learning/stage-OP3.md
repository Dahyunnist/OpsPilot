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
