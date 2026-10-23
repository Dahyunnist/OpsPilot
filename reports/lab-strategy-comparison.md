# OP-3 实时故障实验策略对照

- 日期：2026-10-23
- 数据：本地 Docker 故障实验的 6 个真实服务窗口；每个窗口的 metrics、logs、traces、deployments 全部有数据。
- 运行时：临时 Python 3.11 Compose override；正式 Python 3.12 配置未改。
- 模型：`deepseek-v4-flash`，账本按峰值单价估算。
- 对照：rules、single、always-multi、auto；费用闸累计上限 `$0.50`。

| 策略 | 案例 | 完整根因命中 | Top-1 | Top-3 根因召回 | 模型调用 | 峰值费用估算 | P95 延迟 | 失败 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| rules | 6 | 4/6 | 66.7% | 66.7% | 0 | `$0` | 0.01 ms | 0 |
| single | 6 | 2/6 | 50.0% | 41.7% | 6 | `$0.02154960` | 3496.21 ms | 0 |
| always-multi | 6 | 2/6 | 33.3% | 33.3% | 30 | `$0.07119570` | 6095.29 ms | 6 个案例各有 1 次 `ModelError` |
| auto | 6 | 4/6 | 66.7% | 66.7% | 2 | `$0.00084780` | 2686.12 ms | 0 |

自动路由 4 案例选择 rules，2 案例选择 single；本轮没有选择 multi。Multi 结果存在每案一次模型调用失败，不能视为无故障的完整对照。未重试，避免超出最小对照范围。

策略费用合计 `$0.09359310`。账本累计峰值估算从 `$0.33974803` 增至 `$0.43334113`；总上限 `$0.50`，剩余 `$0.06665887`。费用包含失败请求的最坏情况预留。

原始输出见 [策略运行日志](lab-strategy-python311.log)、[逐案 JSON](lab-strategy-summary.json) 和 [费用账本](api-cost-ledger.jsonl)。服务清理证据见 [清理日志](lab-strategy-python311-cleanup.log)。

这次端到端验证使用 Python 3.11 临时覆盖。正式 Python 3.12 镜像仍因 Docker Hub token 请求超时未构建；未将 Python 3.11 结果表述为 Python 3.12 验收。
