# OP-3 实时故障实验策略对照

- 日期：2026-10-23
- 数据：6 个本地 Docker 故障实验窗口；每窗 metrics、logs、traces、deployments 均有数据。
- 运行时：临时 Python 3.11 Compose override；正式 Python 3.12 配置未改。
- 模型：`deepseek-v4-flash`。累计费用闸 `$0.50`。
- 方法：同一案例快照对照 rules、single、always-multi、auto。

| 策略 | 案例 | 有效评估 | 完整根因命中 | 模型调用 | 峰值费用估算 | P95 延迟 | 失败 |
|---|---:|---:|---:|---:|---:|---:|---|
| rules | 6 | 6 | 4/6 | 0 | `$0` | 0.01 ms | 0 |
| single | 6 | 6 | 2/6 | 6 | `$0.02154960` | 3496.21 ms | 0 |
| always-multi | 6 | 0 | 不可评分 | 30 | `$0.07119570` | 不可评分 | 6 个案例均有 `ModelError` |
| auto | 6 | 6 | 4/6 | 2 | `$0.00084780` | 2686.12 ms | 0 |

Multi 的 6 个案例全部失败，失败样本不计入性能分数；因此本轮不能判断 Multi 的根因命中率，也不能宣称它优于其他策略。费用仍计入，含失败请求最坏情况预留。自动路由 4 案例选择 rules，2 案例选择 single；没有选择 multi。

累计峰值估算 `$0.43334113 / $0.50`，剩余 `$0.06665887`。本轮后未再发模型请求。

离线复核发现旧摘要曾把 2 个带 `ModelError` 的 Multi 部分结果计为命中。已将这 6 个样本改标为失败，逐案 JSON 的原始部分预测保留；原始提交记录也保留修正前文件。新 evaluator 在 specialist 或 adjudicator 失败时转用 rules 输出，同时保留失败标记；失败案例不计入策略准确率。失败错误今后会记录安全的 `ModelError` 详情。

已有归档只保存 `ModelError` 类型和费用预留，没有 provider 状态码、超时类型或响应体，无法离线判定 6 次错误的具体原因。没有重发请求。固定快照的 specialist/adjudicator 故障回归与失败评分测试均通过。

汇总见 [lab-strategy-summary.json](lab-strategy-summary.json)，逐案结果见 [lab-strategy-lab-pool-01.json](lab-strategy-lab-pool-01.json) 等文件；费用见 [api-cost-ledger.jsonl](api-cost-ledger.jsonl)。运行时边界与服务烟测见 [docker-smoke.md](docker-smoke.md)。
