# OP-2 遥测鲁棒性离线集

复用三种真实故障的冻结服务快照：slow SQL、DB 连接池耗尽、payment 依赖超时。新增完整、缺失日志与 trace、来源乱序、同 trace 日志时间漂移、payment 快慢 span 矛盾五类输入变体。变体由 `tests/test_op2_telemetry_robustness.py` 确定性构造，规格见 `reports/op2-telemetry-robustness.json`。

真值只用于测试断言。输入快照不含 fault、root cause 或 oracle 字段；测试的冻结 adapter 只返回 telemetry。完整样本验证已有规则检测；缺失两个关键来源验证弃答；乱序验证结果稳定；矛盾 payment spans 验证不宣称 timeout；auto 路由测试覆盖 rules/single/multi 选择边界。支付超时 auto 的 `ConnectionResetError` 失败记录保留在原始 evaluation JSON。

本阶段不调模型。现有两例 Model API 输出已暴露 single/multi 空根因，保留为失败样本，不改 gold。当前账本峰值 `$0.47833002`，上限 `$0.60`，余额 `$0.12166998`。之后如要做实时对照，必须先按完整 worst-case 预留核算；超余额不调用。
