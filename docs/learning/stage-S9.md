# S9：轨迹奖励与路由策略学习

## Reward

`opspilot/rewards.py` 对每次调查轨迹计算可解释 reward：根因/弃答结果、金证据 F1、审批安全、通信恢复、延迟和 token 消耗。错误确定、漏审批和失败任务受罚。标签只在本地奖励器中读取，不传给模型。

## 策略

`ContextualBandit` 把调查分成 `decisive`、`ambiguous`、`source_incomplete` 状态，选择 `rules`、`single-agent`、`multi-agent` 路线。训练时先覆盖每个状态的三种动作，再做 epsilon-greedy 探索；每个 episode 用真实 reward 更新状态/路线的样本均值 Q 值。

运行：

```powershell
python scripts/train_routing_policy.py
python scripts/evaluate_routing_policy.py
```

训练只用 train split 标签；Agent 检索历史记忆时排除当前训练案例。Validation 与 test 不更新 policy。test 使用 S8 同一批冻结输出，防止重复调用模型；validation 跑学到的单一路线。

## 名称边界

训练确实更新 contextual-bandit 路由 Q 值；独立集却没有超过静态规则基线。因此项目结论只称“轨迹奖励与路由策略评估”，不称 Agentic-RL 成果。没有更新 LLM 权重、prompt 或工具。

## 实测结果

训练 24 episodes：路线选择为 rules 15、single-agent 6、multi-agent 3；估算费用 `$0.01081382`。策略更新 decisive / ambiguous 两种状态的 Q 值。

Validation 选 rules 8/8：根因 6/6、证据不足弃答 2/2。Blind test 选 rules 16/16：根因 12/12、弃答 4/4；token 与模型费用均为 0。结果与静态规则基线相同，没有独立测试收益。

策略在两个留出集都选原有规则调查器。结果与静态规则基线相同；模型路线没有显示足够收益抵消成本。完整轨迹见 `reports/reward-training.json`，独立评测见 `reports/reward-evaluation.json` 与同名 Markdown。

## 亲手练习

在副本里调高 latency/token 惩罚，重跑训练；确认 Q 值变化来自 train，validation/test 的 `policy_updates` 仍为 0。再逐条核对奖励分量，防止结果分数盖过审批安全。

## 面试问题

1. contextual bandit 的状态、动作、奖励在本项目里分别是什么？
2. 为什么留出集表现等于规则基线时，不能宣称 RL 带来能力提升？
