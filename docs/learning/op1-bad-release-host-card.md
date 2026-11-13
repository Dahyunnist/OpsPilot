# OP-1 单场景宿主验收卡：bad_release

此卡只提供用户本机的合成场景步骤；本次代理没有运行 Docker、启动/停止容器或重触发故障。

## 顺序

`run-scenario.ps1` 在服务 readiness 后写入 orders deployment 记录。因此先记录 UTC 起点，再启动场景；执行 checkout 并等待 DNS 日志出现后，再记录终点。不要事后按当前时间重新计算相对窗口。

```powershell
Set-Location E:\job\opspilot
$start = [DateTime]::UtcNow.ToString('o')
.\lab\run-scenario.ps1 -Scenario bad_release
$order = 'op1-bad-release-' + [guid]::NewGuid().ToString()
try {
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18080/checkout -ContentType 'application/json' -Body (@{order_id=$order}|ConvertTo-Json) -TimeoutSec 15
} catch {
    $_.Exception.Message
}

$dnsRecords = @()
for ($attempt = 0; $attempt -le 10; $attempt++) {
    $pollEnd = [DateTime]::UtcNow.ToString('o')
    $from = [uri]::EscapeDataString($start); $to = [uri]::EscapeDataString($pollEnd)
    $ordersLogs = Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$to&limit=100"
    $dnsRecords = @($ordersLogs.records | Where-Object {
        $_.service -eq 'orders' -and ($_.dependency_host -eq 'inventory-v2' -or
        $_.error_reason -match 'No address associated|Name or service not known' -or
        $_.message -match 'inventory-v2|No address associated')
    })
    if ($dnsRecords.Count -gt 0) { break }
    if ($attempt -lt 10) { Start-Sleep -Seconds 1 }
}

$end = [DateTime]::UtcNow.ToString('o')
$from = [uri]::EscapeDataString($start); $to = [uri]::EscapeDataString($end)
$ordersLogs = Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$to&limit=100"
$deploymentResult = Invoke-RestMethod "http://127.0.0.1:18080/deployments?start=$from&end=$to&limit=100"
$startUtc = [DateTimeOffset]::Parse($start).UtcDateTime
$endUtc = [DateTimeOffset]::Parse($end).UtcDateTime
$v2Deployments = @($deploymentResult.records | Where-Object {
    if ($_.service -ne 'orders' -or $_.version -ne 'v2') { return $false }
    $timestamp = [DateTimeOffset]::Parse($_.timestamp).UtcDateTime
$timestamp -ge $startUtc -and $timestamp -lt $endUtc
})
[pscustomobject]@{ start=$start; end=$end; dns_records=$dnsRecords; v2_deployments=$v2Deployments }
```

`18081/logs` 是 orders 日志端点；`18080/deployments` 是 gateway 部署端点。`18081/deployments` 不存在。两个查询必须用同一次运行的 UTC 半开区间 `[start,end)`；确认 `start <= deployment timestamp < end`。服务端和离线校验共用此边界，恰好等于 end 的记录不属于窗口。用户回传的 `inventory-v2` DNS 错误能证明请求失败线索；后来用五分钟窗口查到 0 条 deployment，是因为该窗不含 `13:47:23Z`，不能据此判定部署证据缺失。

若记录为空，先核对该次场景记录中的 UTC 时间窗、服务、版本和 host 端口，再判断数据是否缺失。不得把不同运行的时间戳拼成一条证据链。
