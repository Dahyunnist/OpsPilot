# S15：按强证据选择调查路线

## 审计结论

上一轮全量多 Agent 的成本主要来自对所有事故都调用 4 个专家、交叉质询和裁决。A2A 本身没有失败：旧测试集 30 个任务全部完成，120 次专家交接、695 次轮询、0 重试/取消。单 Agent 的 6 个漏因样本全是双故障：它找出配置根因，却漏掉独立连接池根因。规则报告对这 6 例同时列出两个跨源强候选，给出了可用于节流的实时信号。

没有改 Agent 结构或 A2A 协议。`auto` 现在按规则证据选择路线：

- 多个不同根因都各有至少两条、跨至少两个来源的强支持：多 Agent 交叉检查并给出根因集合。
- 规则仍弃答，但只有一个根因满足上述强支持条件：单 Agent 复核因果方向。
- 其他情况：保留规则结论或弃答，不调用模型。

该规则不读取标签或场景名。每个强候选的支持 ID 来自实时查询；来源数由记录 ID 回连查询来源计算。原有只读查询、证据引用门控和审批闸没有放宽。

## 同验证集路线对照

S13 validation 有 20 例。Auto 选择 rules 12 例、single-agent 4 例、multi-agent 4 例，共 28 次模型调用。全量 multi-agent 为 120 次。

| 方法 | 多根因 Recall@3 | 完整根因集 | 单根因准确率 | 弃答 | 金证据 P/R | 调用 | 均值 / P95 | 费用估算 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 全量 multi-agent | 20/20 | 16/16 | 10/12 | 4/4 | 100%/85.0% | 120 | 7.80 / 8.51 秒 | `$0.0465318` |
| auto | 20/20 | 16/16 | 12/12 | 4/4 | 82.0%/91.2% | 28 | 2.00 / 7.83 秒 | `$0.0114671` |

该验证集上 auto 保持活动根因 Top-1、完整集合和弃答全对；估算费用降低 75.3%，平均延迟降低 74.3%。证据 precision 下降，recall 上升；规则分支会引用较宽的支持集合。多 Agent p95 仍决定 auto 的尾延迟。

## 新 seed 复测

冻结 seed `20261031` 独立生成的 100 例数据集；未覆盖原 S13 数据。只对 30 例测试样本运行冻结 auto 策略，路由为 rules 18、single-agent 6、multi-agent 6。

活动根因 Top-1 为 24/24，Top-3 和 Recall@3 均 30/30，完整根因集命中 24/24；证据不足样本弃答 6/6，错误确定 0，模型/A2A 失败 0。金证据 P/R 为 82.4%/93.3%。共 42 次模型调用、6 个 A2A 任务、24 次专家交接、157 次轮询；均值 2.08 秒，P95 8.68 秒，费用估算 `$0.0193058`。

这次新 seed 复测证明相同生成规则下路由没有漏掉任一根因；它不是外部真实事故数据，也不证明模板外泛化。S13 测试结果已在之前阶段查看，因此本报告不把它冒充本路由的未见盲测。下一步若需更强泛化证据，应取得许可明确的外部集或收集新故障机制，而非继续改当前模板。

## 累计费用与边界

执行前账本已知完整运行峰值估算 `$0.166635`。validation 新运行 `$0.13450153`，seed 复测 `$0.0386115`；累计峰值估算 `$0.33974803`，低于本轮 `$0.50` 上限。按运行时段官方费率估算三次共 `$0.169874`。估算不是账单；历史报告估算可能重复，服务商账户 `$4` 余额不可从本地仓库读取。费用账本按每请求记账，并把新 `run_summary` 纳入后续预算闸；中断运行的请求也会保留保守峰值预留。

费用来源：[DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)。逐请求记录见 `reports/api-cost-ledger.jsonl`。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts\generate_causal_benchmark.py --seed 20261031 --output data\causal-benchmark-replication
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\evaluate_agents.py --benchmark-dir data\causal-benchmark-replication --split test --strategy auto --max-cost-usd 0.50 --cost-ledger reports\api-cost-ledger.jsonl --output reports\causal-routing-replication.json
```

测试输入 SHA-256：`fc392cc3f0218ceaf0c7b0aadc3f1f2452d92202efead7d78397c954645d7e50`。逐例模型输出、证据、路由、费用和哈希见 `reports/causal-routing-replication.json`。
