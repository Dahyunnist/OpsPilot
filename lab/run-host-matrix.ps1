param([string]$OutputFile = (Join-Path (Split-Path -Parent $PSScriptRoot) "reports/op1-host-matrix.json"))

$ErrorActionPreference = "Continue"
$compose = Join-Path $PSScriptRoot "compose.yaml"
$truth = Get-Content (Join-Path $PSScriptRoot "scenarios/truth.json") -Raw | ConvertFrom-Json
$priorProject = $env:COMPOSE_PROJECT_NAME
$env:COMPOSE_PROJECT_NAME = "opspilot-matrix-$([guid]::NewGuid().ToString('N').Substring(0,8))"`n$projectName = $env:COMPOSE_PROJECT_NAME
$rows = @()
try {
    foreach ($case in $truth.cases) {
        $id = "matrix-$($case.fault)-$([guid]::NewGuid())"
        $start = [DateTime]::UtcNow.AddSeconds(-2).ToString("o")
        $status = $null; $message = $null; $logs = @(); $traceCount = 0
        try {
            & (Join-Path $PSScriptRoot "run-scenario.ps1") -Scenario $case.fault
            if ($LASTEXITCODE -ne 0) { throw "scenario startup failed ($LASTEXITCODE)" }
            try { $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/checkout" -ContentType "application/json" -Body (@{order_id=$id}|ConvertTo-Json) -TimeoutSec 15; $status=200 }
            catch { $message=$_.Exception.Message; if ($_.Exception.Response) { $status=[int]$_.Exception.Response.StatusCode } }
            Start-Sleep -Seconds 2
            $end = [DateTime]::UtcNow.AddSeconds(2).ToString("o")
            $from=[uri]::EscapeDataString($start); $to=[uri]::EscapeDataString($end)
            $logs=Invoke-RestMethod "http://127.0.0.1:18081/logs?start=$from&end=$to&limit=100"
            $traces=Invoke-RestMethod "http://127.0.0.1:16686/api/traces?service=orders&limit=100"
            $traceCount=@($traces.data).Count
        } catch { $message=$_.Exception.Message }
        $rows += [pscustomobject]@{case_id=$case.case_id;scenario=$case.fault;order_id=$id;start=$start;end=$end;http_status=$status;error=$message;log_count=$logs.count;trace_count=$traceCount;expected_signals=$case.must_observe;live_run=($null -ne $status)}
    }
} finally {
    & (Join-Path $PSScriptRoot "stop.ps1")
    $stopCode=$LASTEXITCODE
    $remaining=docker compose -p $env:COMPOSE_PROJECT_NAME -f $compose ps --all --format json
    $clean=($LASTEXITCODE -eq 0 -and -not $remaining)
    if ($null -eq $priorProject) { Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue } else { $env:COMPOSE_PROJECT_NAME=$priorProject }
}
$result=@{project=$projectName;cases=$rows;cleanup=@{stop_exit=$stopCode;empty=$clean};volume_deletion=$false}
$parent=Split-Path -Parent $OutputFile; New-Item -ItemType Directory -Force $parent|Out-Null
$result|ConvertTo-Json -Depth 10|Set-Content -Encoding utf8 $OutputFile
if (-not $clean) { exit 1 }

