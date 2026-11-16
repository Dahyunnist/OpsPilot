# OP-3 授权对照结果（2026-11-15）

本轮只用原授权的三份合成冻结快照和 `rules/single/multi/auto` 四策略。评测为 `partial`，共 17 次 provider request attempts，上限 18；本轮成本估值 `$0.01464531`，run reserve `$0.0230226`，项目 cap `$0.60`。脱敏机器可读结果：[全案评分与路由审计](../../reports/op3-multiagent-authorized-replay-summary-audit-c1c10951-2b5d-40aa-a9c0-7f2d31aff5ef.json)。原始运行报告、请求 checkpoint 和账本保留本地，未纳入公开提交。

在有本地原始报告时，可一键离线重算脱敏汇总；该命令只读输入报告，不读账本/checkpoint，也不发 provider 请求：

```powershell
$rawReport = "reports/op3-multiagent-authorized-replay-c1c10951-2b5d-40aa-a9c0-7f2d31aff5ef.json"
$publicSummary = "reports/op3-multiagent-authorized-replay-summary-audit-c1c10951-2b5d-40aa-a9c0-7f2d31aff5ef.json"
python -m scripts.audit_op3_partial_run --input $rawReport --output $publicSummary
```

## 全案结果

评分把失败案例计入全案 coverage、Top-1 和 exact-root-set rate；失败案计零。completed-only 指标另列，不能替代全案分母。

| 策略 | 完成 | Coverage | 全案 Top-1 | 全案 exact-root-set |
|---|---:|---:|---:|---:|
| rules | 3/3 | 1.00 | 1/3 | 1/3 |
| single | 2/3 | 0.67 | 1/3 | 1/3 |
| multi | 1/3 | 0.33 | 1/3 | 1/3 |
| auto | 2/3 | 0.67 | 1/3 | 1/3 |

multi 的 completed-only Top-1 是 1/1。它只代表完成的一案；全案 Top-1 是 1/3。三案结果不足以证明 multi 优于 single。

失败样本：single 的 `contradictory_payment` 被 `BudgetLimitExceeded` 阻止；multi 的 `missing_pool_evidence` 两次遇到 `truncated_json`，`contradictory_payment` 被预算闸阻止；auto 的 `contradictory_payment` 也被预算闸阻止。rules 三案均完成。成本是 usage-based 估值，不是账单。报告 p95 来自完成案例的端到端 `duration_ms`：rules n=3、single n=2、multi n=1、auto n=2；失败案例没有延迟样本，provider-only p95 不可得。

## 失败与 auto 路由

- `normal_slow_sql`：auto 选择 rules；四策略都完成。
- `missing_pool_evidence`：auto 选择 single；single 完成但弃答。multi 两次得到 `truncated_json` 后失败。
- `contradictory_payment`：auto 选择 single；single、multi、auto 均被 `BudgetLimitExceeded` 阻止。rules 完成。

auto 按现有选择规则路由：已有受支持根因时用 rules；只有单个受支持候选时用 single。这里没有观察到 auto 选择 multi。

本轮后不重放失败付费提示，不增加请求、题目或数据源。新增 scorer 测试和脱敏报告测试，验证失败案计入全案分母、completed-only 指标单独标注，并确保公开摘要不含历史账单、余额或付费提示键清单。定向测试：63 项及 4 个子测试通过。
