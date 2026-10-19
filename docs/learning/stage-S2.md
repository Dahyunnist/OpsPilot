# S2 学习记录：跨信号关联与反证

## 故障故事

`downstream-01` 的日志里先看到数据库池告警，容易把根因归到连接池。指标显示池利用率只有 9/40，追踪把慢 span 定位到 `order.downstream.call`，并且 trace ID 与超时日志一致。账本保留数据库假设，同时用指标和 span 明确反证；最终报告改判下游超时。

## 信号怎样关联

- **日志**包含错误文本；带 `trace_id` 时可与请求 span 对齐。
- **指标**先查询，以连接池、配置错误和下游延迟指标选择下一步来源。
- **追踪**按 trace ID 分组，并保留 span 名称、服务、耗时和状态。
- **部署**按版本与指标、日志、追踪中的 `version` 字段关联。

`query_strategy` 记录首查指标、后续来源顺序和理由。每个来源仍按 incident 时间窗和最大行数限制查询。`correlations` 只在同一 trace ID 或版本有多条记录时生成链接。

## 关键数据结构与文件

- `opspilot/investigator.py`：指标驱动的后续查询排序、跨来源关联、假设账本、日志单源比较和引用校验。
- 每个假设继续保留 `support`、`against`、`open_questions` 和分数。已被反证的候选进入 `rejected_hypotheses`，不会从最终报告消失。
- `comparison.logs_only` 是只看日志的初始候选；`comparison.multi_signal` 是完整调查结论。
- `citation_validation.all_references_exist` 检查每个支持与反证引用都在本次查询返回记录中。
- `tests/test_s2.py` 验证日志误导案例、trace/version 关联、查询顺序和引用有效性。

## 假设如何被证实或证伪

下游案例的日志单源候选是连接池耗尽，依据 `downstream-01-l2`。完整查询后，`downstream-01-m3` 反映池使用率低，`downstream-01-t1` 反映慢 span 位于下游调用，因此连接池假设被明确反证。`downstream-01-l1` 与 span 使用同一 trace ID，进一步支持下游超时。

配置案例通过 `order-v15` 关联部署 `config-01-d1`、指标、配置日志和校验 span。部署时间早于失败观测，版本相同；引用仍能回到每条原始记录。

## 两条异常链

1. **误导日志链**：数据库池告警文字 → 日志单源候选为连接池 → 指标显示池利用率低 → trace 慢 span 位于下游 → 连接池假设被反证，下游超时成为结论。
2. **配置版本链**：部署产生新版本 → 该版本配置校验失败 → 同版本错误率升高 → trace 失败阶段为配置校验 → 配置错误假设得到多源支持。

## 测试命令与真实结果

```powershell
python -m unittest discover -s tests -v
python -m opspilot investigate downstream-01
python -m opspilot investigate config-01 --format json
```

2026-10-19 实测 9 项测试全部通过。`downstream-01` 的日志单源候选为 `database connection pool exhausted`；多信号结论为 `downstream service exceeded request timeout`。连接池假设仍在账本中，反证引用为 `downstream-01-m3` 和 `downstream-01-t1`。trace 关联含 `downstream-01-l1`、`downstream-01-t1`、`downstream-01-t2`，引用校验为 `True`。`config-01` 的版本关联跨 metrics、logs、traces、deployments。

## 亲手实验：追踪怎样推翻日志判断（约 10–20 分钟）

1. 运行 `python -m opspilot investigate downstream-01 --format json`，先读 `comparison.logs_only`，再读多信号结论。
2. 在报告中找 `downstream-01-l2`、`downstream-01-m3` 和 `downstream-01-t1`，说明数据库假设为何被降权。
3. 复制数据行到临时文件，把 `downstream-01-t1` 的 `span` 改成 `db.connection.acquire`。观察因果证据变化；不要覆盖基准数据。
4. 运行原始调查，核对 `citation_validation.all_references_exist` 为 `True`。

## 面试追问

**问：为何先查询指标？**

答：指标提供低成本的异常方向，可选择下一条最能区分假设的日志、追踪或部署查询。

**问：`trace_id` 关联能证明因果吗？**

答：不能。它证明观测属于同一请求；仍需检查 span 的服务边界、状态和耗时。

**问：为什么被否定的假设还留在报告里？**

答：便于审阅调查过程，显示原始线索及推翻它的证据，避免结论看起来像事后猜测。

## 未覆盖边界

关联依赖精确字段相等，未处理采样、时钟偏差、缺失 trace parent 或跨服务版本映射。日志单源结果是确定性对照规则，不代表模型的真实日志判断能力。引用存在性校验不等于引用语义一定正确。
