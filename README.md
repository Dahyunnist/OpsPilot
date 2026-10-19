# OpsPilot：证据驱动的本地故障调查原型

当前包含 S0 故障数据和 S1 本地只读调查闭环。原型不调用模型、不连接生产系统，也不执行修复操作。

## 生成数据

S0 数据生成器需要 Python 3.10 或更新版本。MCP SDK 需要 Python 3.11 或更新版本。

运行：

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
python -m opspilot investigate downstream-01 --inject timeout:logs --no-state
```

调查器先查指标，再依指标选择 logs、traces、deployments。报告含假设账本、跨信号关联、支持/反证引用、查询参数、原始记录和成本。`--missing-source` 模拟来源缺失；`--inject timeout:logs`、`duplicate:traces` 或 `out_of_order:logs` 模拟故障。异常演练加 `--no-state`，避免复用之前的成功查询。单次行数上限为 1–1000，总查询预算为 1–12。

默认检查点写入 `var/state/<incident_id>.json`，查询与审批审计写入 `var/audit.jsonl`。这些运行文件已加入 Git 忽略规则。

## 审批模拟

```powershell
python -m opspilot simulate-action config-01 --approval reject --event-id demo-reject-001
python -m opspilot simulate-action config-01 --approval approve --event-id demo-approve-001
```

两种决定都只记入本地审计。批准结果标为 `simulated_execution`，不会连接或修改外部系统。相同事件 ID 重复提交会返回 `duplicate_ignored`。

## MCP 只读服务

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m opspilot.mcp_servers logs
```

用 `traces` 替换 `logs` 可启动追踪服务。两者均通过 stdio 暴露带时间窗与行数上限的只读工具。

## 测试

```powershell
\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 目录

- `docs/incident-contract.md`：故障因果模型、观测契约和评测口径。
- `data/incidents.jsonl`：调查输入与合成原始观测。
- `data/labels.json`：根因真值及证据充分性标签。
- `scripts/generate_dataset.py`：固定种子的生成器和结构校验。
- `docs/learning/stage-S0.md`：S0 技术讲解、生成验证结果、亲手实验和面试问题。
- `docs/learning/stage-S1.md`：S1 技术讲解、调查实测、亲手实验和面试问题。
- `docs/learning/stage-S2.md`：S2 跨信号调查、反证和实测记录。
- `docs/learning/stage-S3.md`：S3 可靠性、审批模拟、MCP 联调和实测记录。
- `docs/threat-model.md`：本地原型威胁模型与安全不变量。
- `opspilot/`：只读查询、假设账本与 CLI。
- `tests/`：各阶段闭环、可靠性和 MCP 联调测试。
