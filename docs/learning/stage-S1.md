# S1 学习记录：最小调查闭环

## 故障故事

值班人员拿到 `pool-01` 或 `config-01` 告警后，CLI 在限定时间窗内读取指标、日志、追踪和部署事件。规则生成三个结构化假设，保留支持证据、反证和未查证问题，再给出根因或证据不足状态。整个过程只读。

## 四类信号怎样进入调查

- **日志**提供错误原文和上下文；日志查询返回原始记录及记录 ID。
- **指标**提供阈值、错误率和延迟变化；S1 的连接池规则识别利用率接近上限与等待时间。
- **追踪**提供 span 类型、耗时、状态和 trace ID；S1 用 span 类型辅助分类，跨源 trace 关联留到 S2。
- **部署**提供版本与变更时间；与配置错误日志和指标共现时，支持部署配置假设。

缺失来源与空结果分开表示：`status=missing` 是数据源不可用；`status=ok, record_count=0` 表示查询成功但时间窗内无记录。

## 关键数据结构与文件

- `opspilot/investigator.py`：`query_source` 执行带时间窗、最大行数的只读查询；`investigate` 生成假设账本和成本摘要。
- `opspilot/cli.py`：加载 JSONL，校验案例 ID，并渲染文本或 JSON 报告。
- `opspilot/__main__.py`：支持 `python -m opspilot`。
- 每条 `query_trace` 保存来源、完整参数、状态、原始返回记录及耗时；假设引用原始 `record_id`。
- `tests/test_s1.py`：核对可判定案例、不确定案例、缺失来源、时间窗和行数边界。

## 假设证实与证伪

连接池假设需要连接利用率接近上限、等待升高或获取连接失败等证据。数据库健康只能说明数据库仍响应，不能单独反证连接池耗尽。

配置假设依赖部署版本、配置错误和错误率同版本关联。出现“downstream”一词的配置错误日志不能因此支持下游超时；调查规则按错误语义与结构化字段匹配。

`unknown-01` 只有 5xx 指标和通用错误日志。`unknown-02` 同时有池利用率偏高、下游延迟和不完整追踪，三个假设无法拉开足够差距，因此都返回 `insufficient_evidence`。

## 两条异常链

1. **池耗尽**：`db_pool_in_use` 达上限 → 等待时间升高 → 获取连接超时 → 5xx 告警。支持引用分别来自指标、日志和追踪。
2. **配置错误**：配置包部署 → 新版本配置校验失败 → 新版本 5xx 增长。若缺少版本对应关系或日志来源，假设可信度应下降。

## 测试命令与真实结果

```powershell
python -m unittest discover -s tests -v
python -m opspilot investigate pool-01
python -m opspilot investigate unknown-01
python -m opspilot investigate pool-01 --missing-source logs
```

2026-10-19 实测 5 项单元测试全部通过。`pool-01` 得出 `database connection pool exhausted`，支持引用包含 `pool-01-m1`、`pool-01-m2`、`pool-01-l1` 和 `pool-01-t1`。`config-01` 得出 `deployment introduced invalid order service configuration`，包含部署、日志、指标和追踪引用。`unknown-01` 返回 `insufficient_evidence` 且无结论。模拟缺失日志时，查询轨迹明确显示 `status=missing`。

报告每例执行 4 次查询。耗时按本机实测计时；token 数是 `null`，因为没有模型调用。该数值只表示本地读取开销，不代表生产查询延迟。

## 亲手实验：观察缺失来源对结论的影响（约 10–20 分钟）

1. 运行 `python -m opspilot investigate pool-01 --format json`，记录结论及支持的 `record_id`。
2. 运行 `python -m opspilot investigate pool-01 --missing-source metrics --format json`。检查指标查询的状态、各假设的未查证问题和结论变化。
3. 对比正常和缺失来源报告，说明为什么空结果不能代表来源不可用。
4. 运行 `python -m opspilot investigate unknown-02`，指出冲突信号，并解释为什么系统不猜。

## 面试追问

**问：为何查询有时间窗和行数上限？**

答：限定调查范围和数据量，避免无界读取；查询参数进入报告，方便复现。

**问：`status=ok` 且零条记录，与 `status=missing` 有何区别？**

答：前者查询成功但没有匹配记录；后者数据源不可用。两者需要不同的调查状态处理。

**问：为何 `unknown-02` 不输出分数最高的假设？**

答：候选证据冲突且差距不足。强行选 Top1 会把排序误说成根因结论。

## 未覆盖边界

规则基于本地合成信号，尚未验证真实遥测的字段差异。分类分数是透明启发式，不是校准概率。查询当前直接读文件；尚无超时恢复、预算限制、审计持久化、trace 跨源校验或 MCP。
