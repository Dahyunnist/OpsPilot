# OpsPilot 路由策略奖励实验

训练 episodes：24；验证：8；独立测试：16。

| split | 学到的路线 | 平均 reward | 根因准确率 | 弃答召回 | tokens | 估算费用 USD |
|---|---|---:|---:|---:|---:|---:|
| validation | {'rules': 8, 'single-agent': 0, 'multi-agent': 0} | 1.3646 | 6/6 | 2/2 | 0 | None |
| test | {'rules': 16, 'single-agent': 0, 'multi-agent': 0} | 1.3738 | 12/12 | 4/4 | 0 | None |

策略只在 train 上更新。Validation 与 test 上更新数均为 0。Test 复用 S8 冻结输出，避免二次模型调用。

Q values update from train rewards only. Independent validation/test matched the static rules baseline; report this as reward and routing-policy evaluation, not an Agentic-RL capability.
