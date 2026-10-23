# OP-1–OP-4 最终升级验收

- 日期：2026-10-23
- 工作树：`E:\job\opspilot`
- 冻结集：未重跑旧六案例；旧六次 Multi 错误根因仍不可恢复。
- 依赖超时对照曾发 7 次请求：运行前 worst-case 预留 `$0.038859`，实际账本峰值增加 `$0.01365257`；当时累计 `$0.45150090`。单例硬上限 `$0.04`、累计硬闸 `$0.47784833` 均未触及。
- 后续 OP-1 slow SQL 垂直切片花费估算 `$0.01244600`；最新账本峰值 `$0.46394690 / $0.50`，余额 `$0.03605310`。两轮垂直评估均在预留及累计硬闸内。
- Docker：Engine `29.1.3`。本机有 `python:3.11-slim` 服务镜像，无 `python:3.12-slim`。此前 Hub 认证超时；本轮不拉取。六案例服务烟测沿用已验证 Python 3.11 边界；本次容器启动使用本地镜像且已清理，`docker compose ps --all` 为空。

## 路线验收

| 路线 | 验收证据 | 结论与边界 |
|---|---|---|
| 多 Agent 调度 | `tests/test_routing.py`、`tests/test_agents.py`、`tests/test_strategy_eval.py` | 路由、专家并行、裁决、失败 fallback、截断重试和预算拒绝有 stub 回归。真实模型效果只在有限开发样本上验证。 |
| MCP/A2A 工具通信 | `tests/test_mcp.py`、`tests/test_a2a_sdk_interop.py` | 标准 MCP 客户端及官方 A2A SDK 本地互操作通过；live MCP 只暴露只读工具。本轮没有对新 Docker 窗口执行 MCP 工具链或真实 A2A 模型交接。 |
| 证据归因 | `tests/test_live_telemetry.py`、`tests/test_s1.py`、`tests/test_s2.py`、`tests/test_agents.py` | 记录引用可解析；跨来源支持、反证、单源猜测抑制有回归。只证明合成遥测规则。 |
| 人工审批 | `tests/test_action_safety.py`、`tests/test_s3.py` | 签名、角色、环境、过期、重放和拒绝路径通过；动作仍是本地模拟，无身份提供者或真实执行验证。 |
| 长任务恢复 | `tests/test_s3.py`、`tests/test_agents.py` | 已完成查询可复用；遥测快照变更会拒绝恢复；A2A task 轮询、取消终态和幂等重试通过。不是跨进程生产持久化验收。 |
| 评测与预算 | `tests/test_s4.py`、`tests/test_benchmark.py`、`tests/test_causal_benchmark.py`、`tests/test_complex_benchmark.py`、`tests/test_strategy_eval.py` | 冻结评测结构、评分、失败样本剔除、费用账本和并发预留已有回归；本轮不重放冻结集。 |

## 全新开发窗口与费用闸

新窗口 `upgrade-live-dev-20261023-01` 使用本地依赖超时故障和唯一订单 ID。时间窗为 `2026-10-23T10:24:21.7375823Z` 至 `2026-10-23T10:24:30.2287796Z`。只读采到 60 metrics、4 logs、4 traces、1 deployment。详见 [窗口记录](upgrade-live-dev-window.json)。

调用前用真实窗口、`.env` 的 `LLM_MAX_COMPLETION_TOKENS=1024` 和 `RunBudget` 公式离线估算 `single + multi` 最坏费用。包括单 Agent、4 个来源专家、4 次专家截断重试和裁决，共 10 次尝试，最坏预留 `$0.038859`。在单例 `$0.04` 和累计 `$0.50` 硬闸内执行；每次调用先由 RunBudget 加锁预留，单例最多 10 次尝试。

实际发出 7 次请求：rules 0、single 1、multi 6。一次 specialist 首次输出在 320 tokens 截断，分类为 `truncated_json`；预算由 320 提高到 512 后成功。其余请求完成；无 fallback 或未处理模型错误。账本对截断请求按完整预留计费，其余按 provider usage 估算。实际单例峰值估算 `$0.01365257`，低于 `$0.04`；累计峰值 `$0.45150090 / $0.50`，剩余 `$0.04849910`。

独立 oracle 来自本地注入故障 `dependency_timeout`，根因为 `payment_dependency_timeout`；金证据是 orders `TimeoutError` 日志与 payment/charge 慢 trace。rules、single、multi 本例都命中根因。single 和 multi 各引用 2 条 oracle 证据，precision/recall 均为 1.0；rules 引用 69 条，precision 为 0.029、recall 为 1.0。本例只证明本场景端到端行为，不说明泛化或策略增益。模型请求、脱敏原始响应、重试、费用和逐例证据见 [结构化评测结果](upgrade-live-dev-evaluation.json) 与 [调用轨迹](upgrade-live-dev-trace.jsonl)。较早开发窗口的 `$0.0045072` 诊断亦保留；旧冻结六例未重跑。

## 后续 OP-1 slow SQL 垂直切片

后续在真实服务执行健康 baseline → `pg_sleep(1.5)` → 恢复，并完成 rules/single/Multi/auto 对照。四路均命中此例独立 oracle；Multi 没有相对 single 的命中增益。时间线、独立问题、逐路 precision/recall、成本和复现步骤见[OP-1 垂直切片学习记录](../docs/learning/OP1-vertical-slice.md)。该实验不扩增同模板 JSON、不访问生产系统，也不代表泛化。

## 可复现检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q opspilot scripts tests
git diff --check
docker info --format '{{.ServerVersion}}'
docker image ls --format '{{.Repository}}:{{.Tag}}' python
docker compose -f lab\compose.yaml ps --all
```

结果：`95 passed, 14 subtests passed`；测试宿主 `.venv` 为 Python 3.14.5，编译和 diff 检查通过；容器列表为空。没有 Python 3.12 本地解释器或镜像，正式 Python 3.12 仍未验证。不要用 `evaluate-all.ps1` 重放旧冻结集；不要用 `docker pull` 重试已超时的认证。

OP-1 扩展两例评测账本累计峰值为 `.47833002 / .50`，剩余 `.02166998`；连接池耗尽与支付依赖超时的逐路失败详见 [垂直切片记录](../docs/learning/OP1-vertical-slice.md)。
