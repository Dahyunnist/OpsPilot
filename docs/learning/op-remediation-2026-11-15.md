# OP-1/2/3 路线余项与离线阶段（2026-11-15）

对照 [2026-10-23 最终升级验收](../../reports/final-upgrade-acceptance.md)，记录路线余项、离线回归和后来回传的宿主证据。

## 当前余项

| 项目 | 仍待完成 | 本阶段边界 |
|---|---|---|
| OP-1 | 修复后的 PS 5.1 脚本已在用户宿主用两个新 run ID 通过单场景验收；本次 `python:3.12-slim` build/start 与故障证据均通过。 | 这是单场景 host 验收，不替代 12 场景矩阵或生产验证。报告未记录镜像 digest 与 Docker Engine Server 版本，不对其作断言。两次失败报告保持 `failed`。 |
| OP-2 | 尚无新 Docker 故障窗口上的 MCP 全链路验收，也未接入生产遥测。 | 注册冻结快照用于离线契约测试，不当作新实时事件。 |
| OP-3 | 三案真实 Multi 对照仍为 partial、0/3；近期 `ValueError` 运行与账本审计保留。当前实时余额未知，不能据历史人民币账单推断可用余额。 | 用确定性 fake 验证四策略可以消费同一冻结输入、遵守 prompt 数据边界；不评价策略准确率或收益。 |

## OP-1 宿主成功复核

用户在 Windows PowerShell 5.1 用新 run ID `7826f0be-7f7a-47a3-b9e0-6217f3ca9a4e` 完整运行隔离脚本。独立核对报告结构与字段后确认：单次 checkout 为 HTTP 502；唯一订单有两条同版本日志，其中一条明确包含 `inventory-v2` 和 DNS `errno -5`；orders/v2 deployment 与订单、run ID 一致，且位于同一半开 UTC 窗口；cleanup 显示容器清空、卷保留。扫描未发现 credential 类字段。报告见 [宿主验证结果](../../reports/op4-bad-release-host-7826f0be-7f7a-47a3-b9e0-6217f3ca9a4e.json)。此前 PS 5.1 断言缺陷报告仍保留为 `failed`，没有改写。

Python 3.12 host 验证不再需要额外 Docker 操作：较早的 12/12 宿主矩阵记录 `python:3.12-slim` / Python 3.12；本次成功脚本对当前 Dockerfile 执行 `docker compose up --build`，服务健康且场景通过。此证据证明该 tag 在本次宿主构建/启动路径可用；报告未记录镜像 digest 或 Docker Engine Server 版本。只有要求固定 digest 或特定 Engine 版本的证据时，才需要额外宿主记录。

用户随后再次运行宿主验证。新 run ID `0c906436-ab51-4b07-8180-a3ef361aa435` 的报告通过独立字段核对：HTTP 502、orders/v2 deployment、唯一订单和两条匹配日志一致；`inventory-v2` DNS 错误位于验证窗口内。cleanup 显示容器清空、卷保留。Docker Desktop pipe 权限导致的首次启动失败报告仍保留为 `failed`，没有改写。新报告见 [第二次宿主验证结果](../../reports/op4-bad-release-host-0c906436-ab51-4b07-8180-a3ef361aa435.json)。

## 新增离线阶段

`tests/test_op3_offline_frozen_strategies.py` 从注册清单加载原有三份冻结快照，逐案运行 rules、single、multi、auto。fake 对 model routes 返回空预测，usage 使用整数数字字符串；所有四条路由完成，rules 调用数为 0，single 与 multi 通过本地 fake。每个 prompt 都经 `_validate_prompt_payload` 检查，只能引用当前快照记录，不能包含 gold 字段。

定向命令：`\.venv\Scripts\python.exe -B -m unittest tests.test_op3_offline_frozen_strategies -v`。结果：1 项测试通过，含 3 个冻结案例子测试。未启动模型、网络或 Docker；未读写 `.tmp/`、replay 目录、账本或 checkpoint。

这三份固定案例不是总体样本，也不能视为由不同随机种子构造出的独立故障机制。fake 主动弃答，因此测试只验证执行路径、输入一致性与数据隔离；真实模型结果、策略质量和泛化结论均不变。OP-1 单场景宿主复核现已完成；在新的余额与费用预留预检满足条件后，再考虑 OP-3 真实重试。
