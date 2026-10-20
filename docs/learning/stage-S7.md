# S7：冻结盲测集与事故记忆

## 数据集

`scripts/generate_benchmark.py` 用种子 `20261020` 生成 48 个跨服务事故变体，按类别分层切为 train 24、validation 8、blind test 16。每类含噪声、冲突、来源缺失、跨服务关联、时间乱序与干净变体。样本全为合成数据。

调查输入与真值分文件保存。Agent 只收到事故观测；评测器读取 `data/benchmark/labels/` 真值。manifest 固定各 split 数据与标签 SHA-256。重新生成后哈希必须一致。

## 事故记忆

`opspilot/memory.py` 只从 train split 检索历史案例，不读 validation/test 标签。检索依据简单词项重合，最多返回三条。每条记忆显式标为历史结论与提示；最终裁决提示要求实时遥测和实时反证优先。记忆不能充当实时证据引用。

## 验证

`tests/test_benchmark.py` 检查 split 数量、ID 不交叉、标签隔离、数据哈希、重复生成一致性和历史检索来源。完整多 Agent 评测与费用分析在 S8 执行，不能从规则基线推断模型效果。

## 限制

测试集仅 16 个合成案例，结果置信度有限。当前检索是词项重合，不是向量检索；不会自动写入事故记忆。生成器按模板变体扩充，不能代表真实生产数据分布。

## 亲手练习

运行 `python scripts/generate_benchmark.py` 并比对 manifest 哈希。新增一种时序错乱变体，确认 train/validation/test 之间没有重复 incident ID，且 Agent 输入不含测试标签。

## 面试问题

1. 为什么不能用盲测集调提示词或奖励权重？
2. 历史事故结论与当前遥测冲突时，系统如何避免记忆泄漏成“事实”？
