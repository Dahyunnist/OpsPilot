# S8：盲测对比与费用评估

## 冻结测试

使用 manifest 中 seed `20261020` 的 16 例 test split：12 例有根因真值，4 例证据不足。规则、单 Agent、多 Agent 在同一案例上评估。Agent 看不到 test 标签；训练集只用于历史记忆检索。标签与观测文件分开并由 SHA-256 固定。

完整逐例输入、预测、引用、token、耗时、调用数及模型响应在 `reports/agent-evaluation.json`；摘要在同名 `.md`。运行命令：

```powershell
python scripts/evaluate_agents.py
```

## 实测结果

| 方法 | 根因 | 证据不足弃答 | 金证据 P/R | 平均延迟 | Tokens | 模型调用 | 费用估算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 规则基线 | 12/12 | 4/4 | 91.5% / 91.5% | 0.2 ms | 0 | 0 | N/A |
| 单 Agent | 12/12 | 4/4 | 100% / 100% | 1,450.3 ms | 18,051 | 16 | $0.00466488 |
| 多 Agent | 12/12 | 4/4 | 100% / 100% | 6,160.3 ms | 88,743 | 96 | $0.04617720 |

本轮多 Agent 没有提高准确率或弃答率；单 Agent 与多 Agent 的金证据引用率从规则的 91.5% 升至 100%。多 Agent 平均延迟约为单 Agent 的 4.2 倍；token 约 4.9 倍；估算费用约 9.9 倍。小样本不能代表真实故障能力。

测试运行没有模型调用失败。独立故障探针验证 A2A 短暂断连重试恢复、重复 message ID 幂等、deadline 到期取消。单 Agent 与多 Agent 各有 1 条建议未由模型自行标审批；应用层闸门补上标记。它只证明应用层闸门，不证明模型自行判断安全。

估算费用使用 DeepSeek 官方 2026-10-20 价格快照，并按实际缓存输入与运行时段计价。官方价会变；本次结果约为 `$0.0453`。配置其他服务时，费用为 `null`，除非提供其价格表。

## 实证边界

1. 16 例测试集来自少数模板的合成变体；规则基线可能因构造方式占优。
2. 单 Agent 与多 Agent 全部 12 个可判案例都正确，但不构成生产准确率证据。
3. 所有方法经过 live-evidence gate；根因需有至少两条强支持证据，来自至少两个独立来源。弱 warning 不再能单独打破竞争假设平局。这使弃答更安全，也可能压低 Agent 利用非规则新证据的空间。
4. Agentic-RL 尚未训练或更新 policy。轨迹 reward 需要独立于 prompt 和 baseline 的 holdout 增益证据；S9 仅做 reward/eval 可行性分析，除非真的运行训练与独立测试。

## 参考

- [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)。旧 `deepseek-v4-flash` 名称目前兼容路由至 Flash 服务并按 Flash 费率计费。
- [DeepSeek 推理模式](https://api-docs.deepseek.com/guides/thinking_mode/)。项目对该模型请求使用低成本 JSON 模式，关闭额外 thinking tokens，避免额度只消耗在 reasoning channel。

## 亲手练习

检查 `reports/agent-evaluation.json` 中同一个 incident 的三种策略，手算一例证据 precision 与 recall。再把报告中的模型漏审批样本与最终强制审批结果对照。

## 面试问题

1. 为什么 12/12 的合成根因准确率不能外推为生产准确率？
2. 多 Agent 的额外成本在没有准确率提升时，如何判断是否值得？
