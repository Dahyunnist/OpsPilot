param([string]$OutputFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "reports/op1-host-matrix.json"))

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "op1-matrix-contract.ps1")
$compose = Join-Path $PSScriptRoot "compose.yaml"
$truth = Get-Content (Join-Path $PSScriptRoot "scenarios/truth.json") -Raw | ConvertFrom-Json
$composeFiles = @("-f", $compose)
if ($env:OPSPILOT_COMPOSE_OVERRIDE) { $composeFiles += @("-f", $env:OPSPILOT_COMPOSE_OVERRIDE) }
$preflightError = $null
try {
    $null = Get-Command docker -ErrorAction Stop
    docker info --format "{{.ServerVersion}}" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker daemon unavailable; matrix was not started." }
    foreach ($port in @(18080, 18081, 18082, 18083, 14318, 16686, 19090)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
            throw "Required lab port $port is already in use; matrix was not started."
        }
    }
    $configOutput = docker compose @composeFiles config --format json
    if ($LASTEXITCODE -ne 0) { throw "Compose configuration invalid; matrix was not started." }
    $composeConfig = $configOutput | ConvertFrom-Json
    $requiredImages = @()
    foreach ($serviceProperty in $composeConfig.services.PSObject.Properties) {
        $serviceConfig = $serviceProperty.Value
        if ($serviceConfig.image) { $requiredImages += $serviceConfig.image }
        if ($serviceConfig.build) {
            $build = $serviceConfig.build
            $dockerfile = if ($build.dockerfile) { $build.dockerfile } else { "Dockerfile" }
            if (-not [IO.Path]::IsPathRooted($dockerfile)) { $dockerfile = Join-Path $build.context $dockerfile }
            if (-not (Test-Path -LiteralPath $dockerfile)) { throw "Compose build Dockerfile is missing: $dockerfile" }
            $baseImages = Select-String -Path $dockerfile -Pattern '^\s*FROM\s+(?:--platform=\S+\s+)?([^\s]+)' | ForEach-Object { $_.Matches[0].Groups[1].Value }
            if (-not $baseImages) { throw "Could not identify local base images in: $dockerfile" }
            if ($baseImages | Where-Object { $_ -match '^\$' }) { throw "Dynamic Dockerfile base image cannot be preflighted: $dockerfile" }
            $requiredImages += $baseImages
        }
    }
    foreach ($image in ($requiredImages | Sort-Object -Unique)) {
        docker image inspect $image --format "{{.Id}}" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Missing local image; refusing network pull: $image" }
    }
} catch {
    $preflightError = $_.Exception.Message
}
if ($preflightError) {
    $preflightResult = @{project=$null;preflight=@{passed=$false;error=$preflightError;lab_started=$false};normal_control=$null;cases=@();cleanup=@{stop_exit=$null;stop_error=$null;remaining_error=$null;empty=$true};volume_deletion=$false}
    $parent = Split-Path -Parent $OutputFile
    New-Item -ItemType Directory -Force $parent | Out-Null
    $preflightResult | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $OutputFile
    exit 1
}
$priorProject = $env:COMPOSE_PROJECT_NAME
$priorLabFault = $env:LAB_FAULT
$priorOrdersVersion = $env:LAB_ORDERS_VERSION
$priorInventoryUrl = $env:LAB_INVENTORY_URL
$env:COMPOSE_PROJECT_NAME = "opspilot-matrix-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$projectName = $env:COMPOSE_PROJECT_NAME
$runScript = Join-Path $PSScriptRoot "run-scenario.ps1"
$rows = @()
$normalControl = $null
try {
    $controlStart = [DateTime]::UtcNow.ToString("o")
    $controlStatus = $null; $controlError = $null; $controlDeploymentRecords = @()
    $controlLogs=@{records=@();count=0}; $controlEnd=$controlStart
    try {
        & $runScript -Scenario none
        if ($LASTEXITCODE -ne 0) { throw "normal control startup failed ($LASTEXITCODE)" }
        try { Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id="matrix-normal-$([guid]::NewGuid())"}|ConvertTo-Json) -TimeoutSec 15 | Out-Null; $controlStatus=200 }
        catch { $controlError=$_.Exception.Message; if ($_.Exception.Response) { $controlStatus=[int]$_.Exception.Response.StatusCode } }
        Start-Sleep -Seconds 2
        $controlEnd = [DateTime]::UtcNow.ToString("o")
        $controlFrom=[uri]::EscapeDataString($controlStart); $controlTo=[uri]::EscapeDataString($controlEnd)
        $controlLogs=Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$controlFrom&end=$controlTo&limit=100"
        $controlDeployments=Invoke-RestMethod "http://127.0.0.1:18080/deployments?start=$controlFrom&end=$controlTo&limit=100"
        $controlDeploymentRecords=@($controlDeployments.records)
    } catch { $controlError=$_.Exception.Message; $controlEnd=[DateTime]::UtcNow.ToString("o") }
    $controlVersionObserved=@($controlDeploymentRecords | Where-Object { $_.service -eq "orders" -and $_.version -eq "v1" }).Count -gt 0
    $normalControl=[pscustomobject]@{scenario="none";start=$controlStart;end=$controlEnd;http_status=$controlStatus;error=$controlError;log_count=$controlLogs.count;deployment_count=$controlDeploymentRecords.Count;deployment_records=$controlDeploymentRecords;expected_deployment_version="v1";expected_deployment_observed=$controlVersionObserved;passed=($controlStatus -eq 200 -and $controlLogs.count -gt 0 -and $controlVersionObserved)}
    if ($normalControl.passed) {
      foreach ($case in $truth.cases) {
        $id = "matrix-$($case.fault)-$([guid]::NewGuid())"
        $start = [DateTime]::UtcNow.ToString("o")
        $status = $null; $message = $null; $logs = @(); $ordersLogs = @{records=@();count=0}; $deploymentRecords = @(); $traceData = @(); $dnsObserved = $false; $end = $start
        try {
            & $runScript -Scenario $case.fault
            if ($LASTEXITCODE -ne 0) { throw "scenario startup failed ($LASTEXITCODE)" }
            try { $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$id}|ConvertTo-Json) -TimeoutSec 15; $status=200 }
            catch { $message=$_.Exception.Message; if ($_.Exception.Response) { $status=[int]$_.Exception.Response.StatusCode } }
            $from=[uri]::EscapeDataString($start)
            $logs=$null
            $dnsObserved=$false
            $settleAttempts=if ($case.fault -eq "bad_release") { 10 } else { 2 }
            for ($settle=0; $settle -le $settleAttempts; $settle++) {
                $settledEnd=[DateTime]::UtcNow.ToString("o")
                $settledTo=[uri]::EscapeDataString($settledEnd)
                $ordersLogs=Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$settledTo&limit=100"
                $dnsObserved=$case.fault -ne "bad_release" -or @($ordersLogs.records | Where-Object { $_.service -eq "orders" -and ($_.dependency_host -eq "inventory-v2" -or $_.error_reason -match "No address associated|Name or service not known" -or $_.message -match "inventory-v2|No address associated") }).Count -gt 0
                if ($dnsObserved -or $settle -eq $settleAttempts) { break }
                Start-Sleep -Seconds 1
            }
            $end = [DateTime]::UtcNow.ToString("o")
            $to=[uri]::EscapeDataString($end)
            $traceData=@()
            $logs=@()
            $deployments=Invoke-RestMethod "http://127.0.0.1:18080/deployments?start=$from&end=$to&limit=100"
            $deploymentRecords=@($deployments.records)
            $traceSignals=@($case.must_observe | Where-Object { $_ -in @("payment_latency", "postgres_slow_query", "orders_latency", "inventory_duration") })
            $logSignals=@($case.must_observe | Where-Object { $_ -notin @("payment_latency", "postgres_slow_query", "orders_latency", "inventory_duration") })
            for ($traceAttempt=0; $traceAttempt -le 10; $traceAttempt++) {
                $end=[DateTime]::UtcNow.ToString("o")
                $to=[uri]::EscapeDataString($end)
                $logs=@()
                foreach ($port in @(18081,18082,18083)) {
                    $serviceResult=Invoke-RestMethod "http://127.0.0.1:$port/logs?start=$from&end=$to&limit=100"
                    $logs+=@($serviceResult.records)
                }
                $traces=Invoke-RestMethod "http://127.0.0.1:16686/api/traces?service=orders&limit=1000"
                $traceData=@($traces.data)
                $windowSpans=@(Get-WindowSpans $traceData $start $end $id)
                $signals=@(Get-ObservedSignals $case $id $logs $deploymentRecords $traceData $start $end)
                $missingTraceSignals=@($signals | Where-Object { $_.name -in $traceSignals -and -not $_.observed })
                $missingLogSignals=@($signals | Where-Object { $_.name -in $logSignals -and -not $_.observed })
                if (($windowSpans.Count -gt 0 -and $missingTraceSignals.Count -eq 0 -and $missingLogSignals.Count -eq 0) -or $traceAttempt -eq 10) { break }
                Start-Sleep -Seconds 1
            }
        } catch { $message=$_.Exception.Message }
        $expectedVersion=if ($case.fault -eq "bad_release") { "v2" } else { "v1" }
        $versionObserved=@($deploymentRecords | Where-Object { $_.service -eq "orders" -and $_.version -eq $expectedVersion }).Count -gt 0
        $expectedFailure=$case.fault -in @("db_pool_exhaustion", "dependency_timeout", "bad_release", "double_root", "payment_reject", "redis_unavailable")
        $expectedStatuses=if ($case.fault -eq "bad_release") { @(502, 504) } elseif ($expectedFailure) { @(400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504) } else { @(200) }
        $expectedStatusObserved=$status -in $expectedStatuses
        $windowSpans=@(Get-WindowSpans $traceData $start $end $id)
        $logsObserved=$logs.Count -gt 0
        $tracesObserved=$windowSpans.Count -gt 0
        $telemetryObserved=$logsObserved -and $tracesObserved
        $signals=@(Get-ObservedSignals $case $id $logs $deploymentRecords $traceData $start $end)
        $missingSignals=@($signals | Where-Object { -not $_.observed } | ForEach-Object { $_.name })
        $scenarioPassed=Test-MatrixScenarioPass @{ExpectedStatusObserved=$expectedStatusObserved;VersionObserved=$versionObserved;DnsObserved=$dnsObserved;Logs=$logs;TraceSpans=$windowSpans;SignalResults=$signals}
        $safeLogs=@($logs | Select-Object timestamp,service,severity,message,order_id,trace_id,span_id,service_version,error_type,error_reason,dependency_host,status,attempt,remaining,path)
        $safeSpans=@($windowSpans | Select-Object service,trace_id,span_id,operation,start,duration,status,order_id,attributes)
        $rows += [pscustomobject]@{case_id=$case.case_id;scenario=$case.fault;order_id=$id;start=$start;end=$end;http_status=$status;expected_http_statuses=$expectedStatuses;error=$message;log_count=$logs.Count;logs_observed=$logsObserved;window_log_records=$safeLogs;deployment_count=$deploymentRecords.Count;deployment_records=$deploymentRecords;expected_deployment_version=$expectedVersion;expected_deployment_observed=$versionObserved;expected_failure=$expectedFailure;expected_status_observed=$expectedStatusObserved;dns_failure_observed=$dnsObserved;trace_count=$windowSpans.Count;traces_observed=$tracesObserved;window_trace_spans=$safeSpans;telemetry_observed=$telemetryObserved;expected_signals=$case.must_observe;signal_results=$signals;missing_signals=$missingSignals;scenario_passed=$scenarioPassed;live_run=($null -ne $status)}
      }
    }
} finally {
    $stopCode=1; $stopError=$null; $remaining=@(); $remainingError=$null; $clean=$false
    try {
        & (Join-Path $PSScriptRoot "stop.ps1")
        $stopCode=$LASTEXITCODE
    } catch { $stopError=$_.Exception.Message }
    try {
        $remaining=docker compose -p $env:COMPOSE_PROJECT_NAME @composeFiles ps --all --format json
        $clean=($LASTEXITCODE -eq 0 -and -not $remaining)
    } catch { $remainingError=$_.Exception.Message }
    if ($null -eq $priorProject) { Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue } else { $env:COMPOSE_PROJECT_NAME=$priorProject }
    if ($null -eq $priorLabFault) { Remove-Item Env:LAB_FAULT -ErrorAction SilentlyContinue } else { $env:LAB_FAULT=$priorLabFault }
    if ($null -eq $priorOrdersVersion) { Remove-Item Env:LAB_ORDERS_VERSION -ErrorAction SilentlyContinue } else { $env:LAB_ORDERS_VERSION=$priorOrdersVersion }
    if ($null -eq $priorInventoryUrl) { Remove-Item Env:LAB_INVENTORY_URL -ErrorAction SilentlyContinue } else { $env:LAB_INVENTORY_URL=$priorInventoryUrl }
}
$result=@{project=$projectName;preflight=@{passed=$true;error=$null;lab_started=$true};normal_control=$normalControl;cases=$rows;cleanup=@{stop_exit=$stopCode;stop_error=$stopError;remaining_error=$remainingError;empty=$clean};volume_deletion=$false}
$parent=Split-Path -Parent $OutputFile; New-Item -ItemType Directory -Force $parent|Out-Null
$result|ConvertTo-Json -Depth 10|Set-Content -Encoding utf8 $OutputFile
if (-not $clean -or -not $normalControl.passed -or @($rows | Where-Object { -not $_.scenario_passed }).Count -gt 0) { exit 1 }

