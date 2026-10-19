# OpsPilot：证据驱动的本地故障调查原型

当前包含 S0 故障数据和 S1 本地只读调查闭环。原型不调用模型、不连接生产系统，也不执行修复操作。

## 生成数据

需要 Python 3.10 或更新版本。运行：

```powershell
python scripts/generate_dataset.py
```

脚本固定随机种子 `20261019`，生成 `data/incidents.jsonl`。文件包含 6 个已标注故障案例（每类 2 个）和 2 个证据不足案例。根因标签保存在独立的 `data/labels.json`，供评测读取；调查工具不应把标签作为输入。

重复生成后，数据内容和 SHA-256 文件哈希应完全一致。脚本以退出码 1 报告数据验证失败。

## 数据边界

所有服务、事件和观测数据均为本地合成样本，不含真实用户数据。时间使用 UTC。每条观测都有稳定的记录 ID。案例只表达可观测信号，不假装是真实生产事故。

## 调查案例

```powershell
python -m opspilot investigate pool-01
python -m opspilot investigate unknown-01 --format json
python -m opspilot investigate pool-01 --missing-source logs
```

调查器在 incident 时间窗内查询 metrics、logs、traces、deployments，输出假设账本、支持/反证引用、查询参数、原始返回记录和查询成本。使用 `--missing-source` 可显式模拟来源不可用。`--max-rows` 可把单次查询上限设为 1–1000。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 目录

- `docs/incident-contract.md`：故障因果模型、观测契约和评测口径。
- `data/incidents.jsonl`：调查输入与合成原始观测。
- `data/labels.json`：根因真值及证据充分性标签。
- `scripts/generate_dataset.py`：固定种子的生成器和结构校验。
- `docs/learning/stage-S0.md`：S0 技术讲解、生成验证结果、亲手实验和面试问题。
- `docs/learning/stage-S1.md`：S1 技术讲解、调查实测、亲手实验和面试问题。
- `opspilot/`：只读查询、假设账本与 CLI。
- `tests/`：S1 闭环与证据状态测试。
- `docs/learning/stage-S1.md`：S1 技术讲解和实测记录。
