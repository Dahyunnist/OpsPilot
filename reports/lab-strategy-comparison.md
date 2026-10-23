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

冻结实验累计峰值估算 `$0.43334113 / $0.50`。随后只对一个新开发窗口做受限诊断，累计峰值升至 `$0.43784833`，仍低于 `$0.50`。

离线复核发现旧摘要曾把 2 个带 `ModelError` 的 Multi 部分结果计为命中。已将这 6 个样本改标为失败，逐案 JSON 的原始部分预测保留；原始提交记录也保留修正前文件。新 evaluator 在 specialist 或 adjudicator 失败时转用 rules 输出，同时保留失败标记；失败案例不计入策略准确率。失败错误今后会记录安全的 `ModelError` 详情。

已有归档只保存 `ModelError` 类型和费用预留，没有 provider 状态码、超时类型或响应体，无法判定旧 6 次错误的具体原因。没有重跑冻结案例。一个新开发窗口（18 metrics、6 logs、4 traces）在输出预算 512 下复现 Multi JSON 截断：`finish_reason=length`、128 completion tokens、`response_schema:invalid_json_content`。这只说明该新样本在该预算下的失败机制，不代表旧案例原因。新诊断实际花费 `$0.0045072`，总额仍在 `$0.50` 闸内。

客户端现按 HTTP、超时、响应结构等类别记录脱敏诊断；不会记录 provider 响应正文或异常消息。新增离线回归覆盖 HTTP 503、超时、畸形 JSON 和非对象 JSON。后续收尾验收完整测试为 91 项及 14 个子测试；容器已停止并保留卷。正式 Python 3.12 仍未验证。

后续修正：`finish_reason=length` 现在分类为 `truncated_json`，诊断保留 completion token 数与本次 token 上限。只允许一次自适应重试：初始预算翻倍，最高 512 tokens；重试再次通过 `RunBudget`，账本逐次预留，预算不足即在请求前拒绝。非截断 JSON 错误不重试。Stub 覆盖完整 JSON、截断 JSON、普通错误 JSON、单次重试和重试后 rules fallback。没有再发真实模型请求；旧实验容器已关闭，当前环境无法重新读取该开发窗口遥测，因此本次改动仅做 stub 验证，未声称实测恢复成功。账本累计峰值仍为 `$0.43784833 / $0.50`，新增费用 `$0`。

汇总见 [lab-strategy-summary.json](lab-strategy-summary.json)，逐案结果见 [lab-strategy-lab-pool-01.json](lab-strategy-lab-pool-01.json) 等文件；费用见 [api-cost-ledger.jsonl](api-cost-ledger.jsonl)。运行时边界与服务烟测见 [docker-smoke.md](docker-smoke.md)。
