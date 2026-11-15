# OP-3 三份冻结快照评测命令

计划记录：`reports/op3-op2-authorized-plan-076b2ef2-4e61-4d65-9622-ecaab02268d1.json`。

该计划仅离线校验快照、路由与预算。DeepSeek 余额未知，尚未推理。执行下面命令时，runner 先请求余额与模型列表；只有余额、项目累计上限、三个 SHA 和 0 次推理的预检结果全部通过，才会启动评测。

在 `E:\job\opspilot` 的 PowerShell 执行：

```powershell
$runId = '076b2ef2-4e61-4d65-9622-ecaab02268d1'
$runner = 'scripts/evaluate_op2_frozen.py'
$cases = @('normal_slow_sql', 'missing_pool_evidence', 'contradictory_payment')
$strategies = @('rules', 'single', 'multi', 'auto')
$reserve = '0.0230226'
$expected = @{
  normal_slow_sql = 'd450db5fc6851dabf7d732cd07ea7e229a872c76e29cfc39a3add2466749c9d8'
  missing_pool_evidence = 'd244e00980dfb160831df77ae187208c186a4ba0489831d1b4bda81ef68bbbe7'
  contradictory_payment = 'de76add2b1dc15e4da2207632d44e5cfa4c849abe9a82604a34cfc49423ba813'
}

$preflightText = & python $runner --execute --preflight-only --run-id $runId `
  --case-ids $cases --strategies $strategies --max-run-reserve-usd $reserve
if ($LASTEXITCODE -ne 0) { throw 'Provider or local preflight failed; no inference started.' }
$preflight = $preflightText | ConvertFrom-Json
if ($preflight.run_id -ne $runId -or $preflight.status -ne 'preflight_passed') { throw 'Preflight identity/status mismatch.' }
if ($preflight.provider_balance_available -ne $true -or $preflight.inference_calls -ne 0) { throw 'Balance unavailable or preflight sent inference.' }
if ([decimal]$preflight.max_run_reserve_usd -ne [decimal]$reserve -or
    [decimal]$preflight.cumulative_worst_case_usd -gt [decimal]$preflight.project_cap_usd) { throw 'Budget check failed.' }
if ($preflight.request_plan.max_provider_requests -ne 18) { throw 'Provider request plan changed.' }
foreach ($caseId in $expected.Keys) {
  if ($preflight.case_sha256.$caseId -ne $expected[$caseId]) { throw "Snapshot SHA mismatch: $caseId" }
}

$output = "reports/op3-op2-authorized-$runId.json"
& python $runner --execute --run-id $runId --output $output `
  --case-ids $cases --strategies $strategies --max-run-reserve-usd $reserve
if ($LASTEXITCODE -ne 0) { throw 'Evaluation stopped; inspect the error without rerunning under a new run ID.' }
```

报告路径带唯一 `run_id`。勿改用 R3 报告路径。命令不会显示 `.env` 或 API key；只发送已授权的三份合成冻结快照，不发送 gold 或真实业务数据。
