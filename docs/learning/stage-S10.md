# S10：独立复杂冲突集

## 目的

旧 16 例盲测里规则、单 Agent、多 Agent 根因准确率相同。新集单独冻结，不改旧样本、标签或报告。它增加跨服务因果链、跨源冲突、噪声、缺源和弃答，用于检验协作是否带来净收益。

## 来源与边界

遥测字段参考 OpenTelemetry 服务语义约定中的 `service.name`、`service.version`，以及项目既有的 `trace_id`、span、时间戳与指标模式。参考资料只提供字段语义，不提供事故文本或真值：[OpenTelemetry Service semantic conventions](https://opentelemetry.io/docs/specs/semconv/resource/service/)。

所有事件、服务、标识、数值和事故因果关系均由本项目固定种子生成。数据不是公开事故复刻，也不是匿名化生产数据。它是基于公开字段惯例的人造挑战集。标签与输入分文件保存，模型输入不含根因或金证据标签。

## 冻结

```powershell
python scripts/generate_complex_benchmark.py
```

固定种子 `20261029`，共 64 例：train 32、validation 16、test 16。每个 split 的输入与标签各自记录 SHA-256 于 `data/complex-benchmark/manifest.json`。生成器只写入新目录，不接触 `data/benchmark/`。

可判定样本要求从服务/版本/trace 关联中识别因果链；无因果桥或关键信号移除时标为证据不足。竞争信号可来自不同服务或无关 trace，避免把时间上的共现当作根因。标签列出金证据 ID 与因果 trace ID。

## 解读限制

冻结只保证样本不可被评测运行改写，不保证题型覆盖真实生产分布。生成规则仍围绕三个原型根因，测试集 16 例不足以推断普遍 Agent 优势。必须同时报告根因、弃答、金证据引用、失败、交接、延迟、token 和费用；若多 Agent 没有准确率/证据收益，额外开销就是负收益。
