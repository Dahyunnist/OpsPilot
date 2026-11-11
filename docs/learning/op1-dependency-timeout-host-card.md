# OP-1 单场景宿主操作卡：dependency_timeout

仅限本地合成实验；不读/提供 `lab/scenarios/truth.json` 给 Agent，不调用模型。每次使用新 GUID。所有端口只绑定 loopback。

```powershell
Set-Location E:\job\opspilot
.\lab\run-scenario.ps1 -Scenario dependency_timeout
$start = [DateTime]::UtcNow.AddMinutes(-2).ToString('o')
$order = 'op1-dependency-' + [guid]::NewGuid().ToString()
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18080/checkout -ContentType 'application/json' -Body (@{order_id=$order}|ConvertTo-Json) -TimeoutSec 10 } catch { $_.Exception.Message }
$end = [DateTime]::UtcNow.AddMinutes(2).ToString('o')
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

`/logs` 走 orders:18081，`/deployments` 走 gateway:18080；Prometheus 区间查询 API 用时间窗构造 `start/end`，Jaeger trace 检索按 service 查询。真实请求后应将 `start` 重设到请求前数分钟，确保窗口覆盖故障。完成后确认 `ps --all` 无实验容器；不要删 PostgreSQL/Redis 数据卷。此卡只提供步骤，不表示场景已运行。
