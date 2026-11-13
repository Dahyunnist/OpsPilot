# OP-1 单场景宿主操作卡：dependency_timeout

仅限本地合成实验；不读/提供 `lab/scenarios/truth.json` 给 Agent，不调用模型。每次使用新 GUID。所有端口只绑定 loopback。

```powershell
Set-Location E:\job\opspilot
$start = [DateTime]::UtcNow.ToString('o')
.\lab\run-scenario.ps1 -Scenario dependency_timeout
$order = 'op1-dependency-' + [guid]::NewGuid().ToString()
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18080/checkout -ContentType 'application/json' -Body (@{order_id=$order}|ConvertTo-Json) -TimeoutSec 10 } catch { $_.Exception.Message }
$end = [DateTime]::UtcNow.ToString('o')
$from = [uri]::EscapeDataString($start); $to = [uri]::EscapeDataString($end)
Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$to&limit=100"
Invoke-RestMethod "http://127.0.0.1:18080/deployments?start=$from&end=$to&limit=100"
Invoke-RestMethod "http://127.0.0.1:18081/metrics"
Invoke-RestMethod "http://127.0.0.1:18083/metrics"
Invoke-RestMethod "http://127.0.0.1:19090/api/v1/query?query=opspilot_http_errors_total"
Invoke-RestMethod "http://127.0.0.1:16686/api/traces?service=orders&limit=20"
.\lab\stop.ps1
docker compose -f .\lab\compose.yaml ps --all
```

先记录 UTC `start`，再运行 `run-scenario.ps1`；它在 readiness 后写入 deployment 记录。请求结束后记 UTC `end`，两个查询必须复用同一窗口。`/logs` 走 orders:18081，`/deployments` 走 gateway:18080；不要用 `18081/deployments`。任意当前时间短窗未覆盖该次 deployment timestamp 时，空结果不能证明记录缺失。完成后确认 `ps --all` 无实验容器；不要删 PostgreSQL/Redis 数据卷。此卡只提供步骤，不表示场景已运行。
