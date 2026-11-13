function Get-SpanService($Trace, $Span) {
    $process = $Trace.processes.PSObject.Properties[$Span.processID]
    if ($process) { return $process.Value.serviceName }
    return $null
}

function Get-SpanTag($Span, [string]$Key) {
    $tag = @($Span.tags | Where-Object { $_.key -eq $Key } | Select-Object -First 1)
    if ($tag.Count) { return $tag[0].value }
    return $null
}

function Get-WindowSpans($TraceData, [string]$Start, [string]$End, [string]$OrderId) {
    $from = ([DateTimeOffset]::Parse($Start)).ToUnixTimeMilliseconds() * 1000
    $to = ([DateTimeOffset]::Parse($End)).ToUnixTimeMilliseconds() * 1000
    $spans = @()
    foreach ($trace in @($TraceData)) {
        foreach ($span in @($trace.spans)) {
            if ($span.startTime -ge $from -and $span.startTime -le $to) {
                $spanOrderId = Get-SpanTag $span "opspilot.order_id"
                if ($OrderId -and $spanOrderId -ne $OrderId) { continue }
                $spans += [pscustomobject]@{
                    service = (Get-SpanService $trace $span)
                    trace_id = $trace.traceID
                    span_id = $span.spanID
                    operation = $span.operationName
                    start = [long]$span.startTime
                    duration = [long]$span.duration
                    status = Get-SpanTag $span "http.response.status_code"
                    order_id = $spanOrderId
                }
            }
        }
    }
    return $spans
}

function Get-ObservedSignals($Case, [string]$OrderId, $Logs, $Deployments, $TraceData, [string]$Start, [string]$End) {
    $spans = @(Get-WindowSpans $TraceData $Start $End $OrderId)
    $results = @()
    foreach ($signal in $Case.must_observe) {
        $matches = @()
        switch ($signal) {
            "orders_db_errors" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.severity -eq "error" -and $_.message -eq "request failed" -and $_.error_type }) }
            "db_session_pressure" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "database sessions remain occupied" }) }
            "payment_latency" { $matches = @($spans | Where-Object { $_.service -eq "payment" -and $_.operation -match "charge" -and $_.duration -ge 500000 }) }
            "orders_timeout" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "payment attempt failed" -and $_.error_type -eq "TimeoutError" }) }
            "orders_v2" { $matches = @($Deployments | Where-Object { $_.service -eq "orders" -and $_.version -eq "v2" }) }
            "inventory_connection_errors" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "inventory dependency failed" -and ($_.dependency_host -eq "inventory-v2" -or $_.error_reason -match "No address associated|Name or service not known") }) }
            "postgres_slow_query" { $matches = @($spans | Where-Object { $_.operation -eq "postgres.query" }) }
            "orders_latency" { $matches = @($spans | Where-Object { $_.service -eq "orders" -and $_.operation -match "orders" -and $_.duration -ge 750000 }) }
            "payment_attempts" { $matches = @($Logs | Where-Object { $_.service -eq "payment" -and $_.order_id -eq $OrderId -and $_.message -eq "request returned an error response" -and [int]$_.status -eq 503 }) }
            "orders_retry_logs" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "payment attempt failed" }) }
            "inventory_duration" { $matches = @($spans | Where-Object { $_.service -eq "inventory" -and $_.operation -match "reserve" -and $_.duration -ge 750000 }) }
            "payment_503" { $matches = @($Logs | Where-Object { $_.service -eq "payment" -and $_.order_id -eq $OrderId -and $_.message -eq "request returned an error response" -and [int]$_.status -eq 503 }) }
            "orders_payment_attempt_failure" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "payment attempt failed" -and $_.error_type -eq "HTTPError" }) }
            "inventory_stale_replica_response" { $matches = @($Logs | Where-Object { $_.service -eq "inventory" -and $_.order_id -eq $OrderId -and $_.message -eq "inventory stale replica response" }) }
            "inventory_remaining_20" { $matches = @($Logs | Where-Object { $_.service -eq "inventory" -and $_.order_id -eq $OrderId -and $_.message -eq "inventory stale replica response" -and [int]$_.remaining -eq 20 }) }
            "duplicate_delivery_replayed_payment" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.message -eq "duplicate delivery replayed payment" -and $_.order_id -eq $OrderId }) }
            "same_order_id" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.message -eq "duplicate delivery replayed payment" -and $_.order_id -eq $OrderId }) }
            "cache_connection_failed" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "cache connection failed" }) }
            "orders_cache_error" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.order_id -eq $OrderId -and $_.message -eq "request failed" -and $_.error_type -match "ConnectionError" }) }
            "inventory_remaining_zero" { $matches = @($Logs | Where-Object { $_.service -eq "inventory" -and $_.order_id -eq $OrderId -and $_.message -eq "inventory exhausted" -and [int]$_.remaining -eq 0 }) }
            "reservation_rejected" { $matches = @($Logs | Where-Object { $_.service -eq "orders" -and $_.message -eq "inventory reservation rejected" -and $_.order_id -eq $OrderId }) }
            default { throw "No observable-signal mapping for: $signal" }
        }
        $minimumCount = if ($signal -in @("payment_attempts", "orders_retry_logs")) { 3 } else { 1 }
        $source = if ($signal -eq "orders_v2") { "deployments" } elseif ($signal -in @("payment_latency", "postgres_slow_query", "orders_latency", "inventory_duration")) { "traces" } else { "logs" }
        $references = @($matches | ForEach-Object {
            $recordId = if ($_.record_id) { $_.record_id }
                elseif ($source -eq "traces") { "$($_.trace_id):$($_.span_id)" }
                else { "$($_.service):$($_.timestamp):$($_.message):$($_.order_id)" }
            [pscustomobject]@{source=$source;record_id=$recordId;service=$_.service;timestamp=$_.timestamp;operation=$_.operation;message=$_.message;order_id=$_.order_id}
        })
        $results += [pscustomobject]@{name=$signal;observed=($matches.Count -ge $minimumCount);match_count=$matches.Count;minimum_count=$minimumCount;evidence=$references}
    }
    return $results
}

function Test-MatrixScenarioPass($Evidence) {
    return ($Evidence.ExpectedStatusObserved -and $Evidence.VersionObserved -and $Evidence.DnsObserved -and @($Evidence.Logs).Count -gt 0 -and @($Evidence.TraceSpans).Count -gt 0 -and @($Evidence.SignalResults | Where-Object { -not $_.observed }).Count -eq 0)
}
