# OP-5：只读来源与标准事件验收

## 目标与范围

补齐 OP-1–OP-4 的共享运行来源与事件关联证据。只做离线、只读和本地合成验收；不调用模型、不读取或修改费用账本、不执行 Docker 或业务系统写入。

OP-5 不重跑历史实验，也不把 `partial`、`blocked`、缺失来源或模拟结果改写为通过。历史原始报告保持不变；修正或补充时写入独立派生文件。

## 工作顺序

### 1. 为后续 OP-1/OP-2 运行采集共享 manifest

- 新运行在开始时固定 `run_id`、时间窗、环境和输入来源；结束时记录结果状态、实现与数据哈希、运行时信息及实际 trace 关联。
- OP-1 公共 manifest 当前有 8 项 provenance 缺口：`budget.cap_usd`、`environment`、`evaluation_id`、`runtime.digest_sha256`、`runtime.docker_engine_version`、`runtime.lock_sha256`、`runtime.platform`、`scorer_sha256`。只能从运行现场采集；没有记录就保持 `null` 并标记 `blocked` 或 `not_recorded`。
- OP-2 MCP 宿主报告增加独立共享 manifest。保留只读工具范围、UTC 查询窗、条数上限和证据引用；工具调用记录必须能回到对应查询结果。
- 运行报告与 truth 使用现有冻结哈希。文本哈希按 Git canonical LF 验证；不重写输入文件或补造宿主事实。

### 2. 为历史 OP-3/OP-4 报告生成独立规范化记录

- `op3-full-offline-suite-2026-11-15.run-manifest.json`、`op3-multi-r3-gate-2026-11-15.run-manifest.json` 和 `op4-verification-2026-11-15.run-manifest.json` 保留原样。
- 只有原报告明确记载的字段可以复制到新派生记录。缺字段保持缺失；不把 `verified`、`timed_out` 等未支持值映射成 `completed`。
- 无法满足 schema 时输出 `blocked` 及具体缺项，不能声称该历史运行已通过 manifest 验收。

### 3. 把六类标准事件接到实际本地运行

事件类型为 `timeout`、`retry`、`budget_exceeded`、`unsafe_action_blocked`、`cancel`、`checkpoint_resume`。

- 每条事件都带与 manifest 一致的 `run_id`、`trace_id` 和非空 `span_id`。
- 每条事件的 `(run_id, trace_id, span_id)` 必须命中 `telemetry_evidence` 中完整、有效的 `span_observed` 记录；不能从汇总计数、日志文字或测试名称推断事件已发生。
- 离线测试可用临时目录和确定性替身触发边界；不得访问模型、费用账本或外部业务系统。
- 没有实际观测到的事件继续标为 `count=0` / `not_covered`。

### 2026-11-16 合成关联切片

`validate_run_manifest` 现拒绝没有对应遥测 span 的标准事件。`tests/test_run_manifest.py::RunManifestTests.test_all_standard_events_link_to_observed_run_trace_span` 为六类事件分别构造内存 manifest 和 span 记录，并验证孤立 span 会失败。该测试证明 schema 关联规则，不证明 OP-1–OP-4 的运行时已实际发出这些事件。

定向 manifest 测试为 11 项通过。全套离线测试为 366 项通过、2 项账本读取测试排除、101 个子测试通过；耗时 20.40 秒。测试使用仓库内的 pytest basetemp；事件用例本身不创建文件、不改数据或账本，也未调用模型、Docker、生产遥测或外部服务。

### 4. 形成阶段验收记录

记录每项 manifest 和事件的校验结果、来源文件、哈希口径、运行范围及明确缺项。保留失败、部分完成和阻塞结果，给出可复现的离线命令。

## 完成条件

1. 新生成的 OP-1/OP-2 manifest 通过共享 schema 校验，且每个字段都有来源或明确缺失状态。
2. 历史 OP-3/OP-4 原件未覆盖；派生记录不会猜测未记录字段或改变原状态。
3. 六类事件的 schema 关联规则有确定性离线回归；实际 OP-1–OP-4 运行时事件采集和跨阶段 trace/span 仍需验收，未发生事件仍明确为未覆盖。
4. 全套获准离线测试通过；账本读取用例保持排除，且无模型调用、Docker 操作或外部写入。
5. 验收文档区分本地合成覆盖和真实宿主/生产覆盖。

## 明确未验收边界

真实企业 IdP、独立人工审批、业务系统真实回滚、生产遥测与 OTLP 导出、跨主机/网络文件系统锁语义、多 Agent 真实质量收益和付费模型成本均不属于 OP-5。它们需要单独的运行授权与证据，不能由本阶段离线测试替代。

## 2026-11-16 历史 OP-3 manifest 来源审计

按工作顺序第 2 项检查 `op3-full-offline-suite-2026-11-15.run-manifest.json`。源报告 SHA 经 Git canonical LF 规则核对；因 15 个必填字段缺失、gold/artifact 声明缺失且状态值不受 schema 支持，独立记录为 `blocked`，不生成有效 manifest。原件未改写。审计、哈希和回归见 [OP-3 阻塞记录](../../reports/op3-full-offline-suite-2026-11-15.normalization-audit.json)。`op3-multi-r3-gate` 仍需可解析的原始来源；OP-1/OP-2 现场 provenance 与事件 trace 仍需宿主证据。
