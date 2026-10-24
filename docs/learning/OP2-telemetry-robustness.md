# OP-2 遥测鲁棒性离线集

复用三种真实故障的冻结服务快照：slow SQL、DB 连接池耗尽、payment 依赖超时。新增完整、缺失日志与 trace、来源乱序、同 trace 日志时间漂移、payment 快慢 span 矛盾五类输入变体。变体由 `tests/test_op2_telemetry_robustness.py` 确定性构造，规格见 `reports/op2-telemetry-robustness.json`。

真值只用于测试断言。输入快照不含 fault、root cause 或 oracle 字段；测试的冻结 adapter 只返回 telemetry。完整样本验证已有规则检测；缺失两个关键来源验证弃答；乱序验证结果稳定；矛盾 payment spans 验证不宣称 timeout；auto 路由测试覆盖 rules/single/multi 选择边界。支付超时 auto 的 `ConnectionResetError` 失败记录保留在原始 evaluation JSON。

本阶段不调模型。现有两例 Model API 输出已暴露 single/multi 空根因，保留为失败样本，不改 gold。当前账本峰值 `$0.47833002`，上限 `$0.60`，余额 `$0.12166998`。之后如要做实时对照，必须先按完整 worst-case 预留核算；超余额不调用。

验证：102 passed, 22 subtests passed；compileall 与 git diff --check 通过。真实模型最坏费用未获可执行预留，本阶段未调用模型；新增 cap $0.60 下剩余额度 $0.12166998。

## 真实评测子集预登记

`reports/op2-model-subset.json` 冻结三例：正常 slow SQL、移除连接池正例证据后的缺失样本、保留 payment 快慢 span 的矛盾样本。每例保存独立问题、遥测快照 SHA-256、注入前 oracle、移除记录 ID、策略及逐请求上限；快照本身无真值。

用当前 token 预算和官方峰价估算四策略完整评测：5/9/7 次尝试，逐例最坏 `$0.0062070 / $0.0083088 / $0.0085068`，合计 `$0.0230226`。账本 `$0.47833002`，cap `$0.60`，执行后最坏余额 `$0.09864738`。尚未调用模型；DeepSeek 账户实际余额无法从本地账本读取。官方 Flash 峰价为 cache miss 输入 `$0.30/M`、cache hit `$0.006/M`、输出 `$1.20/M`。
