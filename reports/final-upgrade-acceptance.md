# OP-1–OP-4 最终升级验收

- 日期：2026-10-23
- 工作树：`E:\job\opspilot`
- 冻结集：未重跑旧六案例；旧六次 Multi 错误根因仍不可恢复。
- 新模型请求：0。账本峰值维持 `$0.43784833 / $0.50`，剩余 `$0.06215167`。
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

调用前用真实窗口、`.env` 的 `LLM_MAX_COMPLETION_TOKENS=1024` 和 `RunBudget` 公式离线估算 `single + multi` 最坏费用。包括单 Agent、4 个来源专家、最高 4 次专家截断重试和裁决，共 10 次请求，预留 `$0.038859`。该数高于单场景 `$0.01` 上限，因此没有调用模型；单 Agent 单次请求本身预留 `$0.0114399`。即使总预算尚余 `$0.06215167`，也不突破本场景上限。

因此，本轮提供新的服务端到端遥测证据和离线预算/故障注入证据，不提供新模型输出证据。较早开发窗口曾花费 `$0.0045072`，其中 Multi 在 128 completion tokens、`finish_reason=length` 下失败；该样本不能代表冻结旧六例，也不能证明模型修正后的恢复率。

## 可复现检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q opspilot scripts tests
git diff --check
docker info --format '{{.ServerVersion}}'
docker image ls --format '{{.Repository}}:{{.Tag}}' python
docker compose -f lab\compose.yaml ps --all
```

结果：`91 passed, 14 subtests passed`；测试宿主 `.venv` 为 Python 3.14.5，编译和 diff 检查通过；容器列表为空。没有 Python 3.12 本地解释器或镜像，正式 Python 3.12 仍未验证。不要用 `evaluate-all.ps1` 重放旧冻结集；不要用 `docker pull` 重试已超时的认证。
