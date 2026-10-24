# OP-1 本机重放冒烟记录

## 环境与命令

- Docker Engine `29.1.3`；所需 9 个 Compose 镜像均已在本机。
- 主机 Python `.venv` 为 `3.14.5`；Compose 服务镜像此前核实为 Python `3.11.17`。本次没有拉取或构建镜像。
- 场景：`dependency_timeout`。用 `lab/compose.yaml` 启动服务，分别重建 `orders`/`payment` 开启故障，再关闭故障并验证恢复。
- 重放入口：`lab/run-op1-case.ps1` 会先核本地镜像，只执行 `docker compose up --no-build`，并保存 telemetry 快照后恢复。
- MCP：通过 `Client(build_live_telemetry_server())` 调用只读 `investigate_live_incident`；工具清单 5 项，无动作工具。
- 停止命令：`./lab/stop.ps1`；Compose `down` 不删除数据库卷。

## 本次结果

- 健康 checkout：HTTP 200。
- `dependency_timeout` 注入 checkout：HTTP 502。
- MCP 返回 metrics/logs `ok`，traces/deployments `empty`，根因列表为空。该窗口 trace 未就绪，因此本次只证明注入和 MCP 只读查询可运行，不证明 MCP 完成根因定位。
- 关闭故障并重建服务后，恢复 checkout：HTTP 200。
- 所有服务已停止并移除；卷保留。没有模型调用。

## 验收

命令：

```powershell
.\.venv\Scripts\python.exe -m opspilot investigate pool-01 --no-state --no-audit
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q opspilot scripts tests
git diff --check
```

此前 CLI 主流程成功；测试为 `103 passed, 26 subtests passed`。该阶段未发现需修改的代码缺陷。Python 3.12 Compose 镜像仍未验证。
