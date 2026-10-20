# S11：复杂集评测与 A2A 互通

## 评测器

`scripts/evaluate_agents.py --benchmark-dir data/complex-benchmark --output reports/complex-agent-evaluation.json` 可复用规则、单 Agent、多 Agent 的相同测试集。启动前校验输入与标签哈希；Agent 只收到 incident，评测真值留在本地汇总器。失败调用也保留已发生的 token、耗时与费用。

报告保留逐例预测、支持引用、可用记录、模型原始结果与调用数，并汇总错误确定、根因准确率、弃答、金证据 P/R、安全闸、专家交接、A2A `message/send`、`tasks/get`、重试、取消、延迟 P95、tokens 与费用。规则路线费用为零。

## 协作上下文

专家仍只读各自来源。跨询专家收到模型引用记录的受限投影：record ID、时间、服务、trace、版本、span、peer service、指标与错误码；不转发原始日志正文。这样可以比较同一 trace 是否贯穿服务边界，也减少不可信日志指令进入协作提示。

## 官方 A2A SDK 互通

可选依赖锁定 `a2a-sdk==0.3.8`，运行 `python -m unittest tests.test_a2a_sdk_interop -v`。官方 SDK 客户端读取 Agent Card，发送 JSON-RPC `message/send`，再通过 `tasks/get` 取回终态和 DataPart artifact。测试真实本机 HTTP 往返，不依赖模型服务。

互通测试发现并修正 JSON-RPC 返回层级：v0.3 的 `message/send`、`tasks/get`、`tasks/cancel` 的 `result` 是 Task 本身，不是 `{task: Task}` 包装。协议依据：[A2A v0.3.0 specification](https://a2a-protocol.org/v0.3.0/specification/)。

## 安全审计结论

服务只监听回环地址，并限制请求体 1 MB；仍没有 TLS、HTTP 认证、持久存储或多租户隔离。官方规范要求认证每个请求，生产 transport 必须使用 HTTPS。当前边界是本机演示，不可宣称生产 A2A 合规；更多边界见 `docs/threat-model.md`。
