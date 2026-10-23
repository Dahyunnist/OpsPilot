# OpsPilot：证据驱动的本地故障调查原型

核心是规则驱动的只读证据采集与故障调查。`rules` 不调用模型。`auto` 在多个根因各有跨源强证据时调用多 Agent；单个强候选仍被规则弃答时调用单 Agent；其余保留规则结果。新合成复测中，这种选择性升级保住多根因召回，调用次数与费用明显低于全量多 Agent。默认仍是 `rules`；`multi-agent` 可显式调用。模式都不连接生产系统，也不执行修复操作。

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
python -m opspilot investigate pool-01 --mode multi-agent --token-budget 12000
python -m opspilot investigate pool-01 --mode auto
```

调查器先查指标，再依指标选择 logs、traces、deployments。报告含假设账本、跨信号关联、支持/反证引用、查询参数、原始记录和成本。`--missing-source` 模拟来源缺失；`--inject timeout:logs`、`duplicate:traces` 或 `out_of_order:logs` 模拟故障。异常演练加 `--no-state`，避免复用之前的成功查询。单次行数上限为 1–1000，总查询预算为 1–12。

默认检查点写入 `var/state/<incident_id>.json`，查询与审批审计写入 `var/audit.jsonl`。这些运行文件已加入 Git 忽略规则。

多 Agent 模式需要 `.env` 设置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。专家按来源隔离证据上下文，进行独立推断；交叉质询与裁决由额外模型调用完成。规则模式仍是离线基线。详见 `docs/learning/stage-S5.md`。

首次配置可复制 `.env.example` 为 `.env` 并填写模型服务配置。密钥只放在本地 `.env`，不要提交。

交叉质询通过本机 A2A v0.3 JSON-RPC Agent Card 和 task 生命周期完成。MCP 只负责只读工具访问；A2A 负责 Agent 任务委派。详见 `docs/learning/stage-S6.md`。

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

`.\scripts\run_investigation.ps1 -IncidentId downstream-01` 可运行本地调查；`.\scripts\run_mcp.ps1 -Source logs` 可启动日志 MCP 服务。

实验台启动后，`.\scripts\run_mcp.ps1 -Source live` 暴露只读实时遥测 MCP。工具要求 UTC 起止时间和最多 1000 条上限；`investigate_live_incident` 返回根因候选、正反证据引用、来源缺失、时间漂移与 trace 覆盖提示。启发式置信度不是校准概率，证据不足时弃答。

## 微服务故障实验台

`lab/compose.yaml` 提供 gateway/orders/inventory/payment、PostgreSQL、Redis、OTLP、Jaeger 与 Prometheus。六种可重放故障与双根因清单见 `lab/scenarios/truth.json`。需先启动 Docker Desktop，再运行 `.\lab\run-scenario.ps1 -Scenario slow_sql`；六场景服务烟测为 `.\lab\smoke.ps1`。端口仅绑定本机。详见 `docs/learning/stage-OP1.md`。

## 测试

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/evaluate.py
```

## 目录

- `docs/incident-contract.md`：故障因果模型、观测契约和评测口径。
- `data/incidents.jsonl`：调查输入与合成原始观测。
- `data/labels.json`：根因真值及证据充分性标签。
- `data/benchmark/`：48 个冻结合成事故及分开的 train/validation/test 标签；test 标签不进入 Agent 上下文。
- `data/complex-benchmark/`：独立的 64 例跨服务冲突集；与旧 benchmark 分开冻结，含噪声、缺证与不可判定样本。
- `data/causal-benchmark/`：独立的 100 例因果链与多根因集，含 30 例冻结盲测；训练/验证/测试标签分开保存。数据全为新写合成样本，外部公开集的许可状态见 `docs/learning/stage-S13.md`。
- `data/causal-benchmark-replication/`：新随机种子生成的独立 100 例复测集；用于冻结 auto 路由复测，仍属同模板合成数据。
- `opspilot/memory.py`：仅从 train 案例检索历史提示，实时遥测始终优先。
- `scripts/evaluate_agents.py`、`reports/agent-evaluation.json`、`reports/agent-evaluation.md`：规则、单 Agent、多 Agent 盲测、引用、安全、延迟、token、费用和故障恢复报告。
- `scripts/evaluate_agents.py --benchmark-dir data/complex-benchmark`：独立复杂集规则/单/多 Agent 对照；`scripts/evaluate_complex_routing.py`：仅用 validation 结果回放 auto 路由。
- `reports/complex-agent-evaluation.json`、`reports/complex-validation-evaluation.json`：复杂集逐例模型输出、A2A 交接、调用数、tokens、费用与分策略统计；`reports/complex-routing-validation.json`：validation 路由回放。
- `reports/causal-agent-evaluation.json`、`reports/causal-agent-evaluation.md`：30 例新因果盲测三路实测、Top-K/多根因、证据质量、延迟、失败和费用。
- `reports/causal-routing-validation.json`、`reports/causal-routing-replication.json`：成本感知 auto 路由验证集实测与新 seed 复测。
- `reports/api-cost-ledger.jsonl`：历史报告估算与本轮逐次请求峰值费用记录；不是服务商账单。
- `scripts/train_routing_policy.py`、`scripts/evaluate_routing_policy.py`：从轨迹奖励学习上下文路由策略并做独立评估。
- `reports/reward-training.json`、`reports/reward-evaluation.json`、`reports/reward-evaluation.md`：路由 policy 的训练轨迹和未更新留出评估。
- `scripts/generate_dataset.py`：固定种子的生成器和结构校验。
- `scripts/generate_complex_benchmark.py`：复杂事故集独立生成器，不覆盖旧真值。
- `docs/learning/stage-S0.md`：S0 技术讲解、生成验证结果、亲手实验和面试问题。
- `docs/learning/stage-S1.md`：S1 技术讲解、调查实测、亲手实验和面试问题。
- `docs/learning/stage-S2.md`：S2 跨信号调查、反证和实测记录。
- `docs/learning/stage-S3.md`：S3 可靠性、审批模拟、MCP 联调和实测记录。
- `docs/learning/stage-S4.md`：S4 固定集评测、结果解释和投递材料。
- `docs/learning/stage-S5.md`、`docs/learning/stage-S6.md`、`docs/learning/stage-S7.md`：多 Agent、A2A 生命周期、盲测与历史记忆阶段说明。
- `docs/learning/stage-S8.md`：冻结盲测对比、模型成本与结果边界。
- `docs/learning/stage-S9.md`：trajectory reward 与 contextual-bandit policy 学习边界。
- `docs/learning/stage-S10.md`：复杂冲突集来源、合成边界、冻结哈希与盲测设计。
- `docs/learning/stage-S11.md`：复杂集盲测字段、官方 A2A SDK 互通和安全边界。
- `docs/learning/stage-S12.md`：复杂集结果、成本感知自动路由与简历边界。
- `docs/learning/stage-S13.md`：独立多根因盲测集、许可审计和复现方式。
- `docs/learning/stage-S14.md`：冻结 30 例实测结果、费用边界及 Multi-Agent 路线决策。
- `docs/learning/stage-S15.md`：根据根因证据强度进行选择性升级，并在新 seed 测试集复测。
- `docs/learning/stage-OP1.md`：本地微服务故障实验台、重放方式与验证边界。
- `docs/learning/stage-OP2.md`：只读真实遥测适配、因果证据账本和弃答边界。
- `docs/threat-model.md`：本地原型威胁模型与安全不变量。
- `docs/architecture.md`：系统架构图。
- `docs/resume-bullets.md`：带样本量限制的简历 bullet 草稿。
- `reports/evaluation.json`、`reports/evaluation.md`：固定集两轮运行的逐例原始结果和指标摘要。
- `scripts/setup.ps1`、`scripts/run_investigation.ps1`、`scripts/run_mcp.ps1`：Windows 本地安装与运行入口。
- `opspilot/`：只读查询、假设账本与 CLI。
- `tests/`：各阶段闭环、可靠性和 MCP 联调测试。

复杂集互通测试需额外安装官方 A2A SDK：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-a2a-interop.txt
.\.venv\Scripts\python.exe -m unittest tests.test_a2a_sdk_interop -v
```
