# OP-1–OP-4 横向交付验收

## 范围与判定口径

只读核对 README、架构图、阶段复现/失败记录、现存 OP manifest、宿主报告及 manifest/event 校验器。未读取或修改费用账本、`.tmp/`、checkpoint、宿主报告；未调用模型、服务或 Docker。

OP 共享 run manifest 最低字段由 `opspilot/run_manifest.py` 的 `OP_RUN_MANIFEST_REQUIRED` 定义：schema/project/run/evaluation/dataset/split、dataset/truth/evaluator/result hashes、git/model/prompt/tool/runtime、steps/tokens/scorer/gold/cost、trace、起止时间、parameters 与 status。没有记录的来源必须显式标记；trace 缺失不能伪造 ID。标准事件若出现，必须带匹配的 run/trace/span ID；遥测 span 还要有 service、operation、order ID、起始时间和 duration。

## 阶段产物

| 阶段 | 已有证据 | 验收判定 |
| --- | --- | --- |
| OP-1 | 2026-11-15 矩阵复现 run manifest 通过共享字段校验，绑定 report SHA、truth SHA 与实现 diff；原矩阵记录 12/12、48 个 telemetry spans。Public manifest 的源运行 `status=passed`，但 `conformance_status=blocked`，缺 8 项来源/环境信息；trace ID 明确为未记录。 | 运行结果通过；公共 provenance 未完整验收。六类标准事件均为 `not_covered`。保留 0/12、4/12、6/12、11/12 等失败报告，不能只展示最终通过。 |
| OP-2 | 三份冻结快照的 2026-11-15 manifest 审计均完成，哈希与共用时间窗核对通过；最新 MCP bad_release 宿主报告 `5ff7d94f-01ed-4453-94b2-4551e347d2da` 通过并记录合成订单、窗口、部署、日志、只读工具及 cleanup。 | 快照 provenance 通过。MCP 宿主报告没有共享 run manifest，也没有标准 trace/event 映射；宿主报告运行验收通过，标准事件验收未覆盖。 |
| OP-3 | 2026-11-15 离线验证 manifest 通过共享字段校验；冻结真实策略报告维持 `partial`。`op3-full-offline-suite` 独立规范化审计确认源报告 hash，manifest 缺 15 个必填字段、状态不受支持，结果为 `blocked`。 | 合成离线验证通过；真实策略质量对照未验收。`op3-multi-r3-gate-2026-11-15.run-manifest.json` 不能解析为单个 JSON 文档。历史原件保留，缺失来源未补造。 |
| OP-4 | 2026-11-14 安全证据矩阵 run manifest 通过共享字段校验；本地审批、拒绝和 dry-run 回归记录外部调用/写入为 0。2026-11-16 新离线验证报告的 manifest 通过 schema，绑定运行代码、测试断言与结果指纹。 | 新离线验收来源完整。历史 `op4-verification-2026-11-15.run-manifest.json` 保留原样且仍校验失败；任何记录均不能证明已接真实身份提供者、独立审批人或真实修复执行。 |

## Trace 与标准事件

`EVENT_TYPES` 定义 `timeout`、`retry`、`budget_exceeded`、`unsafe_action_blocked`、`cancel`、`checkpoint_resume` 六类。抽查的 OP 阶段 manifest 没有可按 run/trace/span 核验的标准 `events` 数组。OP-1 public manifest 已明确六类均为 count 0 / `not_covered`；48 个 host telemetry spans 属于 `span_observed`，不能换算成这些事件。

OP-3 有 mock timeout/cancel 计数、查询预算耗尽结果，以及重试相关失败记录；OP-4 有本地审批拒绝记录；S3 有 checkpoint/resume 实现和回归。这些分别证明路径或聚合结果，不等同标准事件与 trace/span 的端到端关联。六类事件当前统一判为标准 trace 覆盖未验收。

## 演示、架构与复现

- README 已补五分钟离线演示：同一合成事故展示正常引用、注入日志超时、单查询预算耗尽。演示使用 `--no-state --no-audit`，不读写账本或检查点。
- 架构图存在。旧说明与 `opspilot/routing.py` 不一致；现已改为反映当前规则：多强候选走 multi，单一强候选且规则弃答走 single，查询预算耗尽不升级。
- OP-1 有十二场景复现、逐案失败报告及最终通过报告；OP-2 有只读快照和 MCP 宿主复现；OP-3 有 partial、preflight blocked 和离线 fake 验证记录；OP-4 有离线安全验收与未完成边界。各文档保留失败及限制，没有把 partial/blocked/timed out 计为通过。

## 未验收项

1. OP-1 公共 manifest 缺失字段的来源证据；OP-2 MCP 宿主报告的共享 manifest 与标准事件链接。
2. OP-3 `op3-full-offline-suite` 已有独立 blocked 审计，未伪造 manifest；`op3-multi-r3-gate` 仍无法解析，需原始来源才能形成派生记录。
3. OP-4 历史精简 verification manifest 的完整来源字段；原始时间与运行环境不可追补。新的离线验证记录已按当前 schema 完整记录。
4. 六类事件已通过合成 schema 测试核对 `(run_id, trace_id, span_id)` 与 `telemetry_evidence` span 的关联；实际 OP-1–OP-4 运行时采集和跨阶段关联仍未验收。
5. Windows 本机 MCP/回环用例此前未完成；2026-11-16 最新全套离线复核为 366 项通过、2 项费用账本测试排除、101 个子测试通过。此前卡住的用例本轮未复现；没有调用模型、启动 Docker 或读取账本。

下一阶段非模型工作与完成条件见 [OP-5 计划](stage-OP5.md)。本记录不表示生产环境、安全审批或模型收益已通过。
