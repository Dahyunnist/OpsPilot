# OP-1/2/3 路线余项与离线阶段（2026-11-15）

对照 [2026-10-23 最终升级验收](../../reports/final-upgrade-acceptance.md)，只推进不需要 Docker 或新模型请求的回归阶段。

## 当前余项

| 项目 | 仍待完成 | 本阶段边界 |
|---|---|---|
| OP-1 | 修复后的 `verify-bad-release.ps1` 仍待用户宿主用新 run ID 重跑；正式 Python 3.12 镜像也未验证。 | 新 PS 5.1 断言测试只验证本地数组计数，不启动 Compose；原失败报告保持 `failed`。 |
| OP-2 | 尚无新 Docker 故障窗口上的 MCP 全链路验收，也未接入生产遥测。 | 注册冻结快照用于离线契约测试，不当作新实时事件。 |
| OP-3 | 三案真实 Multi 对照仍为 partial、0/3；近期 `ValueError` 运行与账本审计保留。当前实时余额未知，不能据历史人民币账单推断可用余额。 | 用确定性 fake 验证四策略可以消费同一冻结输入、遵守 prompt 数据边界；不评价策略准确率或收益。 |

## 新增离线阶段

`tests/test_op3_offline_frozen_strategies.py` 从注册清单加载原有三份冻结快照，逐案运行 rules、single、multi、auto。fake 对 model routes 返回空预测，usage 使用整数数字字符串；所有四条路由完成，rules 调用数为 0，single 与 multi 通过本地 fake。每个 prompt 都经 `_validate_prompt_payload` 检查，只能引用当前快照记录，不能包含 gold 字段。

定向命令：`\.venv\Scripts\python.exe -B -m unittest tests.test_op3_offline_frozen_strategies -v`。结果：1 项测试通过，含 3 个冻结案例子测试。未启动模型、网络或 Docker；未读写 `.tmp/`、replay 目录、账本或 checkpoint。

这三份固定案例不是总体样本，也不能视为由不同随机种子构造出的独立故障机制。fake 主动弃答，因此测试只验证执行路径、输入一致性与数据隔离；真实模型结果、策略质量和泛化结论均不变。下一步仍需用户在宿主重跑 OP-1，并在新的余额与费用预留预检满足条件后再考虑 OP-3 真实重试。
