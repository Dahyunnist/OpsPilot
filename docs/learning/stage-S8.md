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
| 单 Agent | 12/12 | 4/4 | 91.5% / 91.5% | 1,609.5 ms | 18,111 | 16 | $0.00473688 |
| 多 Agent | 12/12 | 4/4 | 91.5% / 91.5% | 6,673.1 ms | 88,111 | 96 | $0.04526573 |

此结果未显示多 Agent 相对单 Agent 或规则的准确率、弃答、引用质量收益。多 Agent 平均延迟约为单 Agent 的 4.1 倍；token 约 4.9 倍；估算费用约 9.6 倍。小样本不能代表真实故障能力。

测试运行没有模型调用失败。独立故障探针验证 A2A 短暂断连重试恢复、重复 message ID 幂等、deadline 到期取消。单 Agent 有 1 条、多 Agent 有 2 条建议未由模型自行标审批；应用层闸门补上全部标记。它只证明应用层闸门，不证明模型自行判断安全。

估算费用使用 DeepSeek 官方 2026-10-20 价格快照，并按实际缓存输入与运行时段计价。官方价会变；本次结果约为 `$0.0453`。配置其他服务时，费用为 `null`，除非提供其价格表。

## 实证边界

1. 16 例测试集来自少数模板的合成变体；规则基线可能因构造方式占优。
2. 单 Agent 与多 Agent 全部 12 个可判案例都正确，但不构成生产准确率证据。
3. 所有方法最终经过相同 live-evidence gate；它要求至少两个独立来源支持根因。这使弃答更安全，也可能压低 Agent 利用非规则新证据的空间。
4. Agentic-RL 尚未训练或更新 policy。轨迹 reward 需要独立于 prompt 和 baseline 的 holdout 增益证据；S9 仅做 reward/eval 可行性分析，除非真的运行训练与独立测试。

## 参考

- [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)。旧 `deepseek-v4-flash` 名称目前兼容路由至 Flash 服务并按 Flash 费率计费。
- [DeepSeek 推理模式](https://api-docs.deepseek.com/guides/thinking_mode/)。项目对该模型请求使用低成本 JSON 模式，关闭额外 thinking tokens，避免额度只消耗在 reasoning channel。
