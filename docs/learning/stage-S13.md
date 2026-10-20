# S13：冻结因果链与多根因盲测集

## 目标与设计

旧复杂集保留不动。新集独立生成，事故 ID 和记录 ID 随机且不编码标签；输入与标签分文件保存。固定种子 `20261030`，每个场景族按 10/4/6 划分 train/validation/test，共 100 例，盲测 30 例。

五类场景：连接池根因、配置根因及下游症状、下游根因及池压力副作用、互相独立的双故障、证据不足或来源缺失。标签含每个根因的证据 ID、干扰证据 ID、活动根因集合和有向因果边。盲测包含 24 个可判定样本，其中 6 个双故障，共 30 个活动根因；另有 6 个不可判定样本。

输入字段沿用项目事故契约，并采用 OpenTelemetry 常见的 `service.name`、`service.version`、`trace_id`、`span_id`、`parent_span_id` 字段。数据全为项目新写的合成数据，不含生产记录或外部复制遥测。场景名、根因标签和金证据不进入 Agent 输入。

## 外部数据与许可

[RootMosaic](https://github.com/springflowerbaby/RootMosaic) 是更贴合多故障目标的公开候选基准：发布说明列出 TrainTicket 与 RecShop 共 595 个单/多故障样本，含 metrics、logs、traces 与根因标签；[数据卡](https://huggingface.co/datasets/RootMosaic/Anonymous)显示压缩包总量约 2.42 GB。上游说明许可按组件区分，数据许可由独立发布包说明；当前可见数据卡未给出明确数据许可。因此本阶段不下载或重分发该数据，也不声称新集是真实事故集。获得明确许可后，优先适配其公开集作外部验证。

新集因此只用于可复现的端到端比较，不能替代真实生产遥测或公开基准复现。生成器和结构不变量见 `scripts/generate_causal_benchmark.py`、`tests/test_causal_benchmark.py`。生成器检查字节级重复性、标签隔离、哈希、引用完整性、双根因独立性和缺源声明。

## 复现

```powershell
.\.venv\Scripts\python.exe scripts\generate_causal_benchmark.py
.\.venv\Scripts\python.exe -m unittest tests.test_causal_benchmark -v
```

冻结文件：`data/causal-benchmark/manifest.json`、各 split JSONL 和 `labels/`。测试标签只在评测程序中读取，不进入诊断上下文。旧 S0–S12 数据与报告保持原样。
