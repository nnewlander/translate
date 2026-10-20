[CmdletBinding()]
param([string]$Root = "D:\translate", [switch]$ReleaseBoundaryOnly)

$ErrorActionPreference = "Stop"
$utf8 = [System.Text.UTF8Encoding]::new($false)
$python = Join-Path $Root ".venv\Scripts\python.exe"
$testPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$utilityModule = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1'
$supervisor = Join-Path $Root "scripts\production\production_campaign_supervisor.ps1"
$watcher = Join-Path $Root "Watch-Booktrans-Production.ps1"
$source = Join-Path $Root "Software Engineering at Google.docx"
$testRoot = Join-Path $Root ("runs\_test_tmp\campaign-control-" + [guid]::NewGuid().ToString("N"))

# The fake Codex executable is a real Windows PowerShell subprocess.  Pin its
# module source so a Core-host PSModulePath cannot make SHA-256 validation
# depend on module auto-loading behavior.
if (-not (Test-Path -LiteralPath $testPowerShell) -or -not (Test-Path -LiteralPath $utilityModule)) { throw 'WINDOWS_POWERSHELL_UTILITY_HOST_MISSING' }
Import-Module -Name $utilityModule -Force -ErrorAction Stop
if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) { throw 'GET_FILE_HASH_UNAVAILABLE_IN_TEST_HOST' }

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION_FAILED:$Message" }
}

function Write-Json([string]$Path, $Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [System.IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 10) + "`n"), $utf8)
}

function New-Fixture([string]$Name, [int]$InitialBatch = 1, [int]$InitialCheckpointCount = 0) {
    $run = Join-Path $testRoot $Name
    $sha = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $previousBatch = $InitialBatch - 1
    $batchId = "FULL_BOOK_BATCH_{0:D3}" -f $InitialBatch
    $batchDirectory = "full_book_batch_{0:D3}" -f $InitialBatch
    $previousBatchId = "FULL_BOOK_BATCH_{0:D3}" -f $previousBatch
    Write-Json (Join-Path $run "full_book_progress_v1.json") ([ordered]@{ artifact_type = "FULL_BOOK_TRANSLATION_PROGRESS"; source_sha256 = $sha; status = "FULL_BOOK_PRODUCTION_IN_PROGRESS"; latest_completed_batch = $previousBatchId; unresolved_deterministic_fail = 0; human_required = 0; recovery_queue = @() })
    $ids = if ($InitialCheckpointCount -gt 0) { @("one", "two", "three", "four", "five", "six", "seven", "eight") } else { @("one", "two", "three", "four") }
    Write-Json (Join-Path $run "$batchDirectory`_selection.json") ([ordered]@{ batch = $batchId; selected_logical_work_order_ids = $ids })
    $checkpoints = @()
    for ($number = 1; $number -le $InitialCheckpointCount; $number++) {
        $checkpointName = "checkpoint_{0:D2}" -f $number
        $checkpointPath = Join-Path $run "$batchDirectory\$checkpointName"
        New-Item -ItemType Directory -Force -Path $checkpointPath | Out-Null
        $raw = Join-Path $checkpointPath 'translation_raw_response.jsonl'
        [System.IO.File]::WriteAllText($raw, "{}$([Environment]::NewLine)", $utf8)
        $rawSha = (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Json (Join-Path $checkpointPath 'checkpoint_evidence.json') ([ordered]@{ logical_work_orders = @($ids[$number - 1]); d050_evidence_artifacts = @() })
        Write-Json (Join-Path $checkpointPath 'translation_final_qa_report.json') ([ordered]@{ pass_count = 1; fail_count = 0 })
        $checkpoints += [ordered]@{ id = $checkpointName; actual_count = 1; final_pass = 1; raw_sha256 = $rawSha }
    }
    $next = if ($InitialCheckpointCount -eq $ids.Count) { $null } else { "checkpoint_{0:D2}" -f ($InitialCheckpointCount + 1) }
    Write-Json (Join-Path $run "$batchDirectory\batch_manifest.json") ([ordered]@{ batch = $batchId; target_count = $ids.Count; completed_count = $InitialCheckpointCount; checkpoint_count = $InitialCheckpointCount; next_checkpoint = $next; status = "OPEN_PROGRESS"; checkpoints = $checkpoints })
    # State is intentionally derived, never inferred by the fake worker.  The
    # real supervisor requires this normal precondition before its first
    # readiness check, so create it through the production rebuild path.
    & $python (Join-Path $Root 'scripts\production\rebuild_production_campaign_state.py') --run $run --source $source | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "FIXTURE_STATE_REBUILD_FAILED:$Name" }
    return $run
}

function New-FakeCodex([string]$Run, [string[]]$Sequence) {
    $modePath = Join-Path $Run "fake-codex-sequence.txt"
    [System.IO.File]::WriteAllLines($modePath, [string[]]$Sequence, $utf8)
    $fakeImplementationPath = Join-Path $Run "fake-codex-impl.ps1"
$body = @'
param([Parameter(ValueFromRemainingArguments = $true)]$Arguments)
$ErrorActionPreference = 'Stop'
Import-Module -Name '__UTILITY_MODULE__' -Force -ErrorAction Stop
$utf8 = [System.Text.UTF8Encoding]::new($false)
$run = '__RUN__'
$modePath = '__MODE__'
$sequence = @(Get-Content -LiteralPath $modePath -Encoding utf8)
if ($sequence.Count -eq 0) { throw 'FAKE_SEQUENCE_EMPTY' }
$mode = $sequence[0]
$remaining = @()
if ($sequence.Count -gt 1) { $remaining = @($sequence[1..($sequence.Count - 1)]) }
[System.IO.File]::WriteAllLines($modePath, [string[]]$remaining, $utf8)
function Write-Json([string]$Path, $Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [System.IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 8) + "`n"), $utf8)
}

$workerId = if ($env:BOOKTRANS_PRODUCTION_WORKER_ID) { $env:BOOKTRANS_PRODUCTION_WORKER_ID } else { [regex]::Match(($Arguments -join "`n"), 'worker_id:\s*(\S+)').Groups[1].Value }
function Write-Handoff([string]$Status, [string]$Checkpoint = 'checkpoint_01') {
    if (-not $workerId) { throw 'FAKE_WORKER_ID_MISSING' }
    $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $batchNumber = ([regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')).Groups[1].Value
    $partial = Join-Path $run ("full_book_batch_{0}\{1}" -f $batchNumber, $state.next_checkpoint)
    Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') ([ordered]@{
        schema_version = 1; status = $Status; worker_id = $workerId; active_batch = $state.active_batch; checkpoint = $state.next_checkpoint; next_logical_work_order_id = $state.next_logical_work_order_id
        partial_checkpoint_path = $partial; stage_reached = 'NORMALIZED_QA'; failure_classification = 'CHECKPOINT_DETERMINISTIC_QA_FAILED'
        failing_work_order_ids = @('one'); relevant_qa_artifact_paths = @((Join-Path $partial 'translation_normalized_qa_report.json'))
        raw_exists = $true; raw_sha256 = 'test-raw-sha'; created_at = (Get-Date -Format o)
    })
}
function Seal-FakeCheckpoint([int]$Number, [string]$Id) {
    $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $batchNumber = ([regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')).Groups[1].Value
    $batch = Join-Path $run ("full_book_batch_{0}" -f $batchNumber)
    $checkpointName = "checkpoint_{0:D2}" -f $Number
    $checkpoint = Join-Path $batch $checkpointName
    New-Item -ItemType Directory -Force -Path $checkpoint | Out-Null
    $raw = Join-Path $checkpoint 'translation_raw_response.jsonl'
    [System.IO.File]::WriteAllText($raw, "{}$([Environment]::NewLine)", $utf8)
    $rawSha = (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash.ToLowerInvariant()
    $selection = Get-Content -LiteralPath (Join-Path $run ("full_book_batch_{0}_selection.json" -f $batchNumber)) -Raw -Encoding utf8 | ConvertFrom-Json
    Write-Json (Join-Path $checkpoint 'checkpoint_evidence.json') ([ordered]@{ logical_work_orders = @($selection.selected_logical_work_order_ids[$Number - 1]); d050_evidence_artifacts = @() })
    Write-Json (Join-Path $checkpoint 'translation_final_qa_report.json') ([ordered]@{ pass_count = 1; fail_count = 0 })
    $manifestPath = Join-Path $batch 'batch_manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    $manifest.completed_count = $Number; $manifest.checkpoint_count = $Number; $manifest.next_checkpoint = "checkpoint_{0:D2}" -f ($Number + 1)
    $manifest.checkpoints = @($manifest.checkpoints) + @([ordered]@{ id = $checkpointName; actual_count = 1; final_pass = 1; raw_sha256 = $rawSha })
    Write-Json $manifestPath $manifest
}
function Write-DirectHandoff([string]$Status, [string]$Checkpoint, [string]$Identity = $workerId, [string]$ProductionDisposition = $null, $RecoveryFingerprint = $null) {
    $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $batchNumber = ([regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')).Groups[1].Value
    $partial = Join-Path $run ("full_book_batch_{0}\{1}" -f $batchNumber, $Checkpoint)
    $recoveryFamily = if ($RecoveryFingerprint) { $RecoveryFingerprint.recovery_family } else { $null }
    $dispositionReason = if ($RecoveryFingerprint) { $RecoveryFingerprint.canonical_reason } else { $null }
    Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') ([ordered]@{
        schema_version = 1; status = $Status; worker_id = $Identity; active_batch = $state.active_batch; checkpoint = $Checkpoint; next_logical_work_order_id = $null
        partial_checkpoint_path = $partial; stage_reached = 'MULTI_CHECKPOINT_TEST'; failure_classification = 'TEST_RECOVERY'
        failing_work_order_ids = @('eight'); relevant_qa_artifact_paths = @(); raw_exists = $false; raw_sha256 = $null; created_at = (Get-Date -Format o)
        production_disposition = $ProductionDisposition; recovery_fingerprint = $RecoveryFingerprint
        recovery_family = $recoveryFamily; disposition_reason = $dispositionReason
    })
}
function Write-CanonicalRecovery([int]$BudgetUsed, [string]$Family = 'SEMANTIC_CORRECTIVE') {
    $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $batchNumber = ([regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')).Groups[1].Value
    $partial = Join-Path $run ("full_book_batch_{0}\{1}" -f $batchNumber, $state.next_checkpoint)
    New-Item -ItemType Directory -Force -Path $partial | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $partial 'translation_raw_response.jsonl'), "{}$([Environment]::NewLine)", $utf8)
    $isRereview = $Family -eq 'SEMANTIC_REREVIEW'
    $headSha = if ($isRereview) { 'b' * 64 } else { 'a' * 64 }
    $canonicalReason = if ($isRereview) { 'CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR' } else { 'CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE' }
    $fingerprint = [ordered]@{
        batch = $state.active_batch; checkpoint = $state.next_checkpoint; work_order = 'one'; current_head_sha256 = $headSha
        recovery_family = $Family; canonical_reason = $canonicalReason
        affected_segment_indices = @(0); recovery_budget_used = $BudgetUsed
    }
    Write-DirectHandoff 'RECOVERY_REQUIRED' $state.next_checkpoint $workerId 'RECOVERY_REQUIRED' $fingerprint
}
switch ($mode) {
    'transient' {
        [Console]::Error.WriteLine('codex_models_manager::manager: failed to refresh available models: timeout waiting for child process to exit')
        exit 5
    }
    'auth' {
        [Console]::Error.WriteLine('401 unauthorized')
        exit 5
    }
    'success_stop' {
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        Write-Output '{"type":"test.success"}'
        exit 0
    }
    'clean' { exit 0 }
    'clean_partial' {
        New-Item -ItemType Directory -Force -Path (Join-Path $run 'full_book_batch_001\checkpoint_01') | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $run 'full_book_batch_001\checkpoint_01\partial.txt'), 'partial', $utf8)
        exit 0
    }
    'recovery_required' {
        $partial = Join-Path $run 'full_book_batch_001\checkpoint_01'
        New-Item -ItemType Directory -Force -Path $partial | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $partial 'translation_raw_response.jsonl'), "{}$([Environment]::NewLine)", $utf8)
        Write-Handoff 'RECOVERY_REQUIRED'
        exit 0
    }
    'partial' {
        New-Item -ItemType Directory -Force -Path (Join-Path $run 'full_book_batch_001\checkpoint_01') | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $run 'full_book_batch_001\checkpoint_01\partial.txt'), 'partial', $utf8)
        [Console]::Error.WriteLine('codex_models_manager::manager: failed to refresh available models: timeout waiting for child process to exit')
        exit 5
    }
    'artifact' {
        Remove-Item -LiteralPath (Join-Path $run 'full_book_batch_001\batch_manifest.json') -Force
        [Console]::Error.WriteLine('codex_models_manager::manager: failed to refresh available models: timeout waiting for child process to exit')
        exit 5
    }
    'advance' {
        $checkpoint = Join-Path $run 'full_book_batch_001\checkpoint_01'
        New-Item -ItemType Directory -Force -Path $checkpoint | Out-Null
        $raw = Join-Path $checkpoint 'translation_raw_response.jsonl'
        [System.IO.File]::WriteAllText($raw, "{}$([Environment]::NewLine)", $utf8)
        Write-Json (Join-Path $checkpoint 'checkpoint_evidence.json') ([ordered]@{ logical_work_orders = @('one'); d050_evidence_artifacts = @() })
        Write-Json (Join-Path $checkpoint 'translation_final_qa_report.json') ([ordered]@{ pass_count = 1; fail_count = 0 })
        $rawSha = (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Json (Join-Path $run 'full_book_batch_001\batch_manifest.json') ([ordered]@{ batch = 'FULL_BOOK_BATCH_001'; target_count = 4; completed_count = 1; checkpoint_count = 1; next_checkpoint = 'checkpoint_02'; status = 'OPEN_PROGRESS'; checkpoints = @([ordered]@{ id = 'checkpoint_01'; actual_count = 1; final_pass = 1; raw_sha256 = $rawSha }) })
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 5
    }
    'advance_batch' {
        $progress = Get-Content -LiteralPath (Join-Path $run 'full_book_progress_v1.json') -Raw -Encoding utf8 | ConvertFrom-Json
        Write-Json (Join-Path $run 'full_book_progress_v2.json') ([ordered]@{ artifact_type = 'FULL_BOOK_TRANSLATION_PROGRESS'; source_sha256 = $progress.source_sha256; status = 'FULL_BOOK_PRODUCTION_IN_PROGRESS'; latest_completed_batch = 'FULL_BOOK_BATCH_010'; unresolved_deterministic_fail = 0; human_required = 0; recovery_queue = @() })
        Write-Json (Join-Path $run 'full_book_batch_011_selection.json') ([ordered]@{ batch = 'FULL_BOOK_BATCH_011'; selected_logical_work_order_ids = @('batch11-one', 'batch11-two') })
        Write-Json (Join-Path $run 'full_book_batch_011\batch_manifest.json') ([ordered]@{ batch = 'FULL_BOOK_BATCH_011'; target_count = 2; completed_count = 0; checkpoint_count = 0; next_checkpoint = 'checkpoint_01'; status = 'OPEN_PROGRESS'; checkpoints = @() })
        Write-Handoff 'CONTINUE'
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 0
    }
    'multi_recovery' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
        $batchNumber = ([regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')).Groups[1].Value
        New-Item -ItemType Directory -Force -Path (Join-Path $run ("full_book_batch_{0}\checkpoint_08" -f $batchNumber)) | Out-Null
        Write-DirectHandoff 'RECOVERY_REQUIRED' 'checkpoint_08'
        exit 0
    }
    'multi_stale_recovery' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        Write-DirectHandoff 'RECOVERY_REQUIRED' 'checkpoint_06'
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 0
    }
    'multi_continue' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        Write-DirectHandoff 'CONTINUE' 'checkpoint_06'
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 0
    }
    'multi_blocked_current' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        Write-DirectHandoff 'BLOCKED' 'checkpoint_08' $workerId 'HARD_STOP'
        exit 0
    }
    'multi_blocked_legacy_human_required' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        New-Item -ItemType Directory -Force -Path (Join-Path $run 'full_book_batch_001\checkpoint_08') | Out-Null
        Write-DirectHandoff 'BLOCKED' 'checkpoint_08'
        exit 0
    }
    'multi_blocked_stale' {
        Seal-FakeCheckpoint 6 'six'; Seal-FakeCheckpoint 7 'seven'
        Write-DirectHandoff 'BLOCKED' 'checkpoint_06'
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 0
    }
    'mismatch_handoff' {
        Write-DirectHandoff 'CONTINUE' 'checkpoint_06' 'different-worker'
        [System.IO.File]::WriteAllText((Join-Path $run 'production_campaign_stop.request'), '{"request":"GRACEFUL_STOP"}', $utf8)
        exit 0
    }
    'future_recovery' { Write-DirectHandoff 'RECOVERY_REQUIRED' 'checkpoint_09'; exit 0 }
    'evidence_provider_unavailable' {
        $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
        Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') ([ordered]@{
            schema_version = 1; status = 'RECOVERY_REQUIRED'; worker_id = $workerId; active_batch = $state.active_batch; checkpoint = $state.next_checkpoint; next_logical_work_order_id = $state.next_logical_work_order_id
            partial_checkpoint_path = ''; stage_reached = 'D050_PDF_CAPABILITY'; failure_classification = 'EVIDENCE_PROVIDER_UNAVAILABLE'
            failing_work_order_ids = @('one'); relevant_qa_artifact_paths = @((Join-Path $run 'full_book_verified_footnote_evidence_operation_rule_v1.json'))
            raw_exists = $false; raw_sha256 = ''; evidence_requirement = [ordered]@{ pdf_sha256 = '4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e'; page_numbers = @(458); logical_work_order_ids = @('one'); evidence_requirement = 'D050_PDF_CORROBORATION' }; created_at = (Get-Date -Format o)
        })
        exit 0
    }
    'canonical_recovery_budget_0' { Write-CanonicalRecovery 0; exit 3 }
    'canonical_recovery_budget_1' { Write-CanonicalRecovery 1; exit 3 }
    'canonical_recovery_r1' { Write-CanonicalRecovery 0 'SEMANTIC_CORRECTIVE'; exit 3 }
    'canonical_recovery_r2' { Write-CanonicalRecovery 1 'SEMANTIC_REREVIEW'; exit 3 }
    default { throw "FAKE_SEQUENCE_UNKNOWN:$mode" }
}
'@
    $body = $body.Replace('__RUN__', $Run.Replace("'", "''")).Replace('__MODE__', $modePath.Replace("'", "''")).Replace('__UTILITY_MODULE__', $utilityModule.Replace("'", "''"))
    [System.IO.File]::WriteAllText($fakeImplementationPath, $body, $utf8)
    $fakePath = Join-Path $Run "fake-codex.cmd"
    [System.IO.File]::WriteAllText($fakePath, "@echo off`r`n`"$testPowerShell`" -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fake-codex-impl.ps1`" %*`r`nexit /b %ERRORLEVEL%`r`n", $utf8)
    return $fakePath
}

function New-FakeCanonicalProjector([string]$Run, [string[]]$Sequence) {
    $sequencePath = Join-Path $Run 'fake-projector-sequence.txt'
    [System.IO.File]::WriteAllLines($sequencePath, [string[]]$Sequence, $utf8)
    $path = Join-Path $Run 'fake-projector.py'
    $body = @"
from pathlib import Path
import json
p=Path(r'$($sequencePath.Replace("'", "\'"))')
rows=p.read_text(encoding='utf-8').splitlines(); mode=rows.pop(0) if rows else 'NONE'
p.write_text('\n'.join(rows)+('\n' if rows else ''),encoding='utf-8')
if mode == 'INVALID': raise SystemExit(7)
recovery = None if mode == 'NONE' else {'schema_version':1,'production_disposition':'RECOVERY_REQUIRED','disposition_reason':'CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE' if mode == 'R1' else 'CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR','recovery_family':'SEMANTIC_CORRECTIVE' if mode == 'R1' else 'SEMANTIC_REREVIEW','failing_work_order_ids':['one'],'disposition_evidence_references':[],'recovery_fingerprint':{'batch':'FULL_BOOK_BATCH_001','checkpoint':'checkpoint_01','work_order':'one','current_head_sha256':'a'*64,'recovery_family':'SEMANTIC_CORRECTIVE' if mode == 'R1' else 'SEMANTIC_REREVIEW','canonical_reason':'CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE' if mode == 'R1' else 'CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR','affected_segment_indices':[0],'recovery_budget_used':0 if mode == 'R1' else 1}}
print(json.dumps({'recovery':recovery}))
"@
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return $path
}

function New-FakeBatchReady([string]$Run, [string[]]$Sequence = @('PASS')) {
    $path = Join-Path $Run 'fake-batch-ready.py'
    $sequencePath = Join-Path $Run 'fake-batch-ready-sequence.txt'
    [System.IO.File]::WriteAllLines($sequencePath, $Sequence, $utf8)
    $body = "from pathlib import Path`nsequence = Path(r'$($sequencePath.Replace("'", "\'"))')`nrows = sequence.read_text(encoding='utf-8').splitlines()`nvalue = rows.pop(0) if rows else 'PASS'`nsequence.write_text('\n'.join(rows) + ('\n' if rows else ''), encoding='utf-8')`nprint('BATCH_READY ' + value + ' batch=FULL_BOOK_BATCH_001')`nraise SystemExit(0 if value == 'PASS' else 2)`n"
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return $path
}

function New-FakeReleaseBoundary([string]$Run, [string[]]$Sequence) {
    $path = Join-Path $Run 'fake-release-boundary.py'
    $sequencePath = Join-Path $Run 'fake-release-boundary-sequence.txt'
    $callsPath = Join-Path $Run 'fake-release-boundary-calls.txt'
    [System.IO.File]::WriteAllLines($sequencePath, [string[]]$Sequence, $utf8)
    $body = @"
from pathlib import Path
import json, sys
sequence = Path(r'$($sequencePath.Replace("'", "\'"))')
calls = Path(r'$($callsPath.Replace("'", "\'"))')
rows = sequence.read_text(encoding='utf-8').splitlines()
expected = rows.pop(0) if rows else 'MISSING'
sequence.write_text('\n'.join(rows) + ('\n' if rows else ''), encoding='utf-8')
is_finalize = '--finalize-only' in sys.argv
actual = expected
if (is_finalize and not actual.startswith('FINALIZE_')) or (not is_finalize and not actual.startswith('APPLY_')):
    print('EXPECTED_' + expected + '_FOR_WRONG_MODE', file=sys.stderr)
    raise SystemExit(19)
calls.write_text((calls.read_text(encoding='utf-8') if calls.exists() else '') + actual + '\n', encoding='utf-8')
if actual == 'APPLY_PASS':
    result = {'mode':'APPLY', 'initial_current_release_debt_total':0, 'gate':{'verdict':'PASS','unresolved_ids':[]}}
elif actual == 'APPLY_BLOCKED':
    result = {'mode':'APPLY', 'initial_current_release_debt_total':1, 'gate':{'verdict':'BLOCKED_HUMAN_ADJUDICATION_REQUIRED','unresolved_ids':['body.p.test']}}
else:
    result = {'mode':'FINALIZE_ONLY', 'initial_current_release_debt_total':0, 'gate':{'verdict':'PASS','unresolved_ids':[],'finalize_only':True,'model_calls':0,'provider_dispatches':0}}
print(json.dumps(result))
"@
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return [pscustomobject]@{ Script = $path; Calls = $callsPath }
}

function New-FakeTerminalClosure([string]$Run) {
    $path = Join-Path $Run 'fake-terminal-closure.py'
    $callsPath = Join-Path $Run 'fake-terminal-closure-calls.txt'
    $body = @"
from pathlib import Path
calls = Path(r'$($callsPath.Replace("'", "\'"))')
calls.write_text((calls.read_text(encoding='utf-8') if calls.exists() else '') + 'CLOSE\n', encoding='utf-8')
Path(r'$($Run.Replace("'", "\'"))').joinpath('production_campaign_stop.request').write_text('{"request":"GRACEFUL_STOP"}', encoding='utf-8')
print('FAKE_CANONICAL_CLOSE_PASS')
"@
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return [pscustomobject]@{ Script = $path; Calls = $callsPath }
}

function New-FakeStateRebuild([string]$Run, [string[]]$Sequence) {
    $path = Join-Path $Run 'fake-state-rebuild.py'
    $sequencePath = Join-Path $Run 'fake-state-rebuild-sequence.txt'
    [System.IO.File]::WriteAllLines($sequencePath, [string[]]$Sequence, $utf8)
    $real = Join-Path $Root 'scripts\production\rebuild_production_campaign_state.py'
    $body = @"
import json, subprocess, sys
from pathlib import Path
sequence = Path(r'$($sequencePath.Replace("'", "\'"))')
rows = sequence.read_text(encoding='utf-8').splitlines()
mode = rows.pop(0) if rows else 'PASS'
sequence.write_text('\n'.join(rows) + ('\n' if rows else ''), encoding='utf-8')
if mode == 'FAIL_DERIVED_STATE':
    print('TEST_DERIVED_STATE_REFRESH_FAILURE', file=sys.stderr)
    raise SystemExit(17)
if mode not in ('PASS', 'BATCH_BOUNDARY_READY'):
    print('TEST_UNKNOWN_STATE_REBUILD_MODE:' + mode, file=sys.stderr)
    raise SystemExit(18)
exit_code = subprocess.call([sys.executable, r'$($real.Replace("'", "\'"))', *sys.argv[1:]])
if exit_code or mode != 'BATCH_BOUNDARY_READY':
    raise SystemExit(exit_code)
run = Path(sys.argv[sys.argv.index('--run') + 1])
state_path = run / 'production_campaign_state.json'
state = json.loads(state_path.read_text(encoding='utf-8'))
state['campaign_status'] = 'BATCH_BOUNDARY_READY'
state_path.write_text(json.dumps(state) + '\n', encoding='utf-8')
"@
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return $path
}

function Invoke-ControlCase([string]$Name, [string[]]$Sequence, [int]$InitialBatch = 1, [int]$InitialCheckpointCount = 0, [string[]]$BatchReadySequence = @('PASS'), [string[]]$StateRebuildSequence = @(), [string[]]$ProjectorSequence = @('NONE'), [string]$ReleaseBoundaryScript, [string]$TerminalClosureScript, [switch]$StaleHandoff, [switch]$InitialCanonicalRecovery) {
    $run = New-Fixture $Name $InitialBatch $InitialCheckpointCount
    $fake = New-FakeCodex $run $Sequence
    $fakeBatchReady = New-FakeBatchReady $run $BatchReadySequence
    $fakeProjector = New-FakeCanonicalProjector $run $ProjectorSequence
    $fakeStateRebuild = if ($StateRebuildSequence.Count) { New-FakeStateRebuild $run $StateRebuildSequence } else { $null }
    if ($StaleHandoff) {
        $staleBatch = "FULL_BOOK_BATCH_{0:D3}" -f $InitialBatch
        $stalePartial = Join-Path $run (("full_book_batch_{0:D3}\checkpoint_01") -f $InitialBatch)
        Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') ([ordered]@{ schema_version = 1; status = 'RECOVERY_REQUIRED'; worker_id = 'old-worker'; active_batch = $staleBatch; checkpoint = 'checkpoint_01'; next_logical_work_order_id = 'one'; partial_checkpoint_path = $stalePartial; stage_reached = 'OLD'; failure_classification = 'OLD'; failing_work_order_ids = @('one'); relevant_qa_artifact_paths = @(); raw_exists = $false; created_at = (Get-Date -Format o) })
    }
    if ($InitialCanonicalRecovery) {
        $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
        $partial = Join-Path $run 'full_book_batch_001\checkpoint_01'
        New-Item -ItemType Directory -Force -Path $partial | Out-Null
        $raw = Join-Path $partial 'translation_raw_response.jsonl'
        [System.IO.File]::WriteAllText($raw, "{}$([Environment]::NewLine)", $utf8)
        $rawSha = (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') ([ordered]@{
            schema_version = 1; status = 'RECOVERY_REQUIRED'; worker_id = 'prior-canonical-worker'; active_batch = $state.active_batch; checkpoint = $state.next_checkpoint; next_logical_work_order_id = 'one'
            partial_checkpoint_path = $partial; stage_reached = 'SEMANTIC_TERMINAL'; failure_classification = 'CURRENT_HEAD_RECOVERY_REQUIRED'; failing_work_order_ids = @('one'); relevant_qa_artifact_paths = @(); raw_exists = $true; raw_sha256 = $rawSha; created_at = (Get-Date -Format o)
            production_disposition = 'RECOVERY_REQUIRED'; recovery_fingerprint = [ordered]@{ batch = $state.active_batch; checkpoint = $state.next_checkpoint; work_order = 'one'; current_head_sha256 = ('a' * 64); recovery_family = 'SEMANTIC_REREVIEW'; canonical_reason = 'CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR'; affected_segment_indices = @(); recovery_budget_used = 0 }
        })
    }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $supervisor, '-Root', $Root, '-RunPath', $run, '-SourcePath', $source, '-BatchReadyScript', $fakeBatchReady, '-CanonicalRecoveryProjectorScript', $fakeProjector, '-CodexExecutable', $fake, '-TransientRetryBackoffSeconds', '0,0,0', '-NoProgressRetryBackoffSeconds', '0,0', '-ShadowControlPlane')
    if ($fakeStateRebuild) { $arguments += @('-StateRebuildScript', $fakeStateRebuild) }
    if ($ReleaseBoundaryScript) { $arguments += @('-ReleaseBoundaryScript', $ReleaseBoundaryScript) }
    if ($TerminalClosureScript) { $arguments += @('-TerminalClosureScript', $TerminalClosureScript) }
    & $testPowerShell @arguments
    return [pscustomobject]@{ Run = $run; ExitCode = $LASTEXITCODE; Log = Get-Content -LiteralPath (Join-Path $run 'production_campaign_logs\supervisor.log') -Raw -Encoding utf8; Status = (Get-Content -LiteralPath (Join-Path $run 'production_campaign_supervisor_status.json') -Raw -Encoding utf8 | ConvertFrom-Json) }
}

try {
    $workerPrompt = Get-Content -LiteralPath (Join-Path $Root 'scripts\production\production_worker_prompt.txt') -Raw -Encoding utf8
    $recoveryPrompt = Get-Content -LiteralPath (Join-Path $Root 'scripts\production\production_recovery_worker_prompt.txt') -Raw -Encoding utf8
    $supervisorText = Get-Content -LiteralPath $supervisor -Raw -Encoding utf8
    Assert-True ($workerPrompt -match 'no reader-visible footnote markers') 'D050 guard skips no-footnote chapter'
    Assert-True ($workerPrompt -match 'bounded chapter-specific D050 preflight' -and $workerPrompt -match 'exact logical_work_order_id plus unit_uid') 'D050 guard requires exact chapter-entry preflight'
    Assert-True ($workerPrompt -match 'create or reuse the exact verified evidence registry' -and $workerPrompt -match 'duplicate an already verified registry') 'D050 guard reuses registry without duplication'
    Assert-True ($recoveryPrompt -match 'DOI_PRESERVATION policy' -and $recoveryPrompt -match 'Never query a network source') 'DOI recovery prompt remains closed-world'
    Assert-True ($supervisorText -match 'serialized_repair_contract_json' -and $supervisorText -match 'REGISTRY_POLICY_DECISION') 'supervisor injects registry repair contracts into recovery workers'
    Assert-True ($workerPrompt -match 'probe_pdf_evidence_capability.py' -and $workerPrompt -match 'EVIDENCE_PROVIDER_UNAVAILABLE') 'worker preflights provider without PATH dependence'

    $releaseFinalizeRun = New-Fixture 'case-release-finalize-only' 1 8
    $releaseFinalize = New-FakeReleaseBoundary $releaseFinalizeRun @('APPLY_PASS', 'FINALIZE_PASS')
    $releaseClosure = New-FakeTerminalClosure $releaseFinalizeRun
    $releaseFinalizeCase = Invoke-ControlCase 'case-release-finalize-only' @('success_stop') 1 8 @('PASS') @('BATCH_BOUNDARY_READY', 'BATCH_BOUNDARY_READY') @('NONE') $releaseFinalize.Script $releaseClosure.Script
    Assert-True ($releaseFinalizeCase.ExitCode -eq 0) 'release finalize-only path closes cleanly'
    Assert-True ((@(Get-Content -LiteralPath $releaseFinalize.Calls -Encoding utf8) -join ',') -eq 'APPLY_PASS,FINALIZE_PASS') 'initial PASS gate is finalized before close'
    Assert-True ((@(Get-Content -LiteralPath $releaseClosure.Calls -Encoding utf8) -join ',') -eq 'CLOSE') 'canonical close consumes finalized gate once'

    $releaseHumanRun = New-Fixture 'case-release-human-boundary' 1 8
    $releaseHuman = New-FakeReleaseBoundary $releaseHumanRun @('APPLY_BLOCKED')
    $releaseHumanClosure = New-FakeTerminalClosure $releaseHumanRun
    $releaseHumanCase = Invoke-ControlCase 'case-release-human-boundary' @('success_stop') 1 8 @('PASS') @('BATCH_BOUNDARY_READY') @('NONE') $releaseHuman.Script $releaseHumanClosure.Script
    Assert-True ($releaseHumanCase.Status.operational_status -eq 'BLOCKED' -and $releaseHumanCase.Status.detail -eq 'BATCH_RELEASE_GATE:BLOCKED_HUMAN_ADJUDICATION_REQUIRED') 'unresolved current debt blocks at consolidated Human boundary'
    Assert-True ((@(Get-Content -LiteralPath $releaseHuman.Calls -Encoding utf8) -join ',') -eq 'APPLY_BLOCKED') 'Human boundary does not prematurely finalize'
    Assert-True (-not (Test-Path -LiteralPath $releaseHumanClosure.Calls)) 'Human boundary does not prematurely close'
    if ($ReleaseBoundaryOnly) { 'PRODUCTION_CAMPAIGN_SUPERVISOR_RELEASE_BOUNDARY_TESTS_PASS'; return }

    $case0 = Invoke-ControlCase 'case0-advancing-worker' @('advance_batch') 10
    $case0State = Get-Content -LiteralPath (Join-Path $case0.Run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($case0State.active_batch -eq 'FULL_BOOK_BATCH_011') 'case0 advances from batch010 to batch011'
    Assert-True ($case0.Log -match 'SEALED state batch=FULL_BOOK_BATCH_011') 'case0 records progress'
    Assert-True ($case0.Log -notmatch 'sameFingerprint=2') 'case0 resets cross-worker no-progress guard'

    $caseStateRebuild = Invoke-ControlCase 'case-state-rebuild' @('success_stop') 1 0 @('FAIL_DERIVED_STATE', 'PASS')
    Assert-True ($caseStateRebuild.Log -match 'BATCH_STATE_REBUILT' -and $caseStateRebuild.Log -match 'BATCH_READY_RECOVERED') 'derived readiness drift gets one rebuild and recovery'

    $caseInitialDerivedRefreshRecovered = Invoke-ControlCase 'case-initial-derived-refresh-recovered' @('success_stop') 1 0 @('PASS') @('FAIL_DERIVED_STATE', 'PASS')
    Assert-True ($caseInitialDerivedRefreshRecovered.Log -match '(?s)DERIVED_STATE_REFRESH_FAILED attempt=1.*phase=before_worker') 'initial refresh preserves derived failure diagnostics'
    Assert-True ($caseInitialDerivedRefreshRecovered.Log -match 'DERIVED_STATE_REBUILD_RECOVERED attempt=1 phase=before_worker') 'initial derived refresh has one deterministic rebuild'
    Assert-True ($caseInitialDerivedRefreshRecovered.Log -notmatch 'ARTIFACT_CORRUPTION') 'initial derived refresh is not artifact corruption'

    $caseNoProgress = Invoke-ControlCase 'case-no-progress-recovered' @('clean', 'advance_batch')
    Assert-True ($caseNoProgress.Log -match 'NO_PROGRESS checkpoint=checkpoint_01 attempt=1/3') 'clean no-progress is classified'
    Assert-True ($caseNoProgress.Log -match 'RETRY_START fresh worker checkpoint=checkpoint_01 attempt=1/3') 'first clean no-progress retries'
    Assert-True ($caseNoProgress.Log -match 'NO_PROGRESS_RECOVERED state_progressed=true') 'progress clears no-progress counter'
    Assert-True ($caseNoProgress.Log -notmatch 'NO_PROGRESS_RETRY_EXHAUSTED') 'first clean no-progress does not guard'

    $caseNoProgressExhausted = Invoke-ControlCase 'case-no-progress-exhausted' @('clean', 'clean', 'clean')
    Assert-True ($caseNoProgressExhausted.Log -match 'RUNTIME_GUARD detail=NO_PROGRESS_RETRY_EXHAUSTED') 'bounded clean no-progress guard'
    Assert-True ($caseNoProgressExhausted.Status.operational_status -eq 'BLOCKED') 'clean no-progress exhaustion blocks'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $caseNoProgressExhausted.Run 'production_campaign_worker.lock'))) 'clean no-progress lock released'

    $caseNoProgressPartial = Invoke-ControlCase 'case-no-progress-partial' @('clean_partial')
    Assert-True ($caseNoProgressPartial.Log -match 'RECOVERY_REQUIRED.*RECOVERY_PROTOCOL_VIOLATION') 'clean no-progress partial is recovery protocol violation'
    Assert-True ($caseNoProgressPartial.Status.operational_status -eq 'RECOVERY_REQUIRED') 'clean no-progress partial is marked recovery required'
    Assert-True ($caseNoProgressPartial.Log -notmatch 'NO_PROGRESS checkpoint') 'partial clean exit does not retry'

    $caseRecovery = Invoke-ControlCase 'case-recovery-required' @('recovery_required', 'success_stop')
    Assert-True ($caseRecovery.Log -match 'RECOVERY_REQUIRED batch=FULL_BOOK_BATCH_001 checkpoint=checkpoint_01 stage=NORMALIZED_QA failure=CHECKPOINT_DETERMINISTIC_QA_FAILED') 'recovery handoff is consumed explicitly'
    Assert-True ($caseRecovery.Status.operational_status -eq 'RECOVERY_REQUIRED') 'recovery handoff has dedicated status'
    Assert-True ((Get-Content -LiteralPath (Join-Path $caseRecovery.Run 'fake-codex-sequence.txt') -Encoding utf8).Count -eq 1) 'recovery handoff does not launch next worker'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $caseRecovery.Run 'production_campaign_worker.lock'))) 'recovery handoff releases writer lock'

    $caseDerivedRefreshRecovered = Invoke-ControlCase 'case-derived-refresh-recovered' @('recovery_required') 1 0 @('PASS') @('PASS', 'FAIL_DERIVED_STATE', 'PASS')
    Assert-True ($caseDerivedRefreshRecovered.Log -match 'DERIVED_STATE_REFRESH_FAILED attempt=1.*exit=17') 'derived refresh preserves child exit diagnostics'
    Assert-True ($caseDerivedRefreshRecovered.Log -match 'DERIVED_STATE_REBUILD_RECOVERED.*current_handoff_preserved=true') 'one rebuild restores only derived state'
    Assert-True ($caseDerivedRefreshRecovered.Log -match 'RECOVERY_REQUIRED batch=FULL_BOOK_BATCH_001 checkpoint=checkpoint_01') 'current recovery handoff remains actionable after rebuild'
    Assert-True ($caseDerivedRefreshRecovered.Log -notmatch 'ARTIFACT_CORRUPTION') 'derived refresh does not relabel valid evidence as artifact corruption'

    $caseDerivedRefreshFailed = Invoke-ControlCase 'case-derived-refresh-failed' @('recovery_required') 1 0 @('PASS') @('PASS', 'FAIL_DERIVED_STATE', 'FAIL_DERIVED_STATE')
    Assert-True ($caseDerivedRefreshFailed.Log -match 'DERIVED_STATE_VALIDATION_FAILED') 'repeated derived validation failure stays precise'
    Assert-True ($caseDerivedRefreshFailed.Log -notmatch 'BLOCKER ARTIFACT_CORRUPTION') 'repeated derived failure is not immutable corruption'

    $caseStartupCanonicalRecovery = Invoke-ControlCase 'case-startup-canonical-recovery' @('advance') 1 0 @('FAIL', 'FAIL') @('PASS') @('R1') -InitialCanonicalRecovery
    Assert-True ($caseStartupCanonicalRecovery.Log -match 'CURRENT_CANONICAL_RECOVERY_RESUME CURRENT_CANONICAL_RECOVERY:checkpoint_01') 'valid current canonical handoff overrides ordinary partial readiness only for its existing recovery transition'
    Assert-True ($caseStartupCanonicalRecovery.Status.operational_status -eq 'STOPPED') 'current canonical recovery launches the existing worker lifecycle'
    Assert-True ($caseStartupCanonicalRecovery.Log -notmatch 'ARTIFACT_CORRUPTION|BATCH_TRANSITION_REPAIR_REQUIRED') 'valid canonical handoff is neither corrupted nor discarded'

    $caseInvalidProjection = Invoke-ControlCase 'case-invalid-canonical-projection' @('advance') 1 0 @('FAIL', 'FAIL') @('PASS') @('INVALID') -InitialCanonicalRecovery
    Assert-True ($caseInvalidProjection.Status.operational_status -eq 'BLOCKED') 'invalid canonical projection fails closed'
    Assert-True ($caseInvalidProjection.Log -match 'BATCH_TRANSITION_REPAIR_REQUIRED') 'invalid projection is not collapsed into normal partial resume'
    Assert-True (($caseInvalidProjection.Log | Select-String -AllMatches 'START fresh worker').Matches.Count -eq 0) 'invalid projection never launches a worker'

    $caseCanonicalRecoveryNoProgress = Invoke-ControlCase 'case-canonical-recovery-no-progress' @('canonical_recovery_budget_0', 'canonical_recovery_budget_0') 1 0 @('PASS') @() @('R1')
    Assert-True ($caseCanonicalRecoveryNoProgress.Log -match 'CANONICAL_RECOVERY_REQUIRED') 'first canonical recovery consumes its bounded state transition'
    Assert-True ($caseCanonicalRecoveryNoProgress.Log -match 'RECOVERY_NO_PROGRESS') 'unchanged canonical recovery fingerprint fails closed rather than looping'
    Assert-True ($caseCanonicalRecoveryNoProgress.Status.detail -eq 'RECOVERY_NO_PROGRESS') 'no-progress recovery is operational, not a semantic disposition'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $caseCanonicalRecoveryNoProgress.Run 'production_campaign_worker.lock'))) 'no-progress recovery releases writer lock'

    $caseCanonicalRecoveryBudgetAdvance = Invoke-ControlCase 'case-canonical-recovery-budget-advance' @('canonical_recovery_budget_0', 'canonical_recovery_budget_1', 'advance') 1 0 @('PASS') @() @('R1', 'R2')
    Assert-True ($caseCanonicalRecoveryBudgetAdvance.Log -notmatch 'RECOVERY_NO_PROGRESS') 'a changed recovery budget is an authoritative recovery transition'
    Assert-True ($caseCanonicalRecoveryBudgetAdvance.Status.operational_status -eq 'STOPPED') 'advanced canonical recovery can continue to the next worker'

    # L2-04: the first recovery transport is consumed, the functional
    # projector admits exactly one corrective successor, that successor emits
    # a distinct rereview transition, and only the same projector admits the
    # rereview successor.  The final worker seals normal lifecycle progress.
    $caseL204MultiHop = Invoke-ControlCase 'case-l2-04-canonical-multi-hop' @('canonical_recovery_r1', 'canonical_recovery_r2', 'advance') 1 0 @('PASS') @() @('R1', 'R2')
    Assert-True (($caseL204MultiHop.Log | Select-String -AllMatches 'CURRENT_CANONICAL_RECOVERY_RESUME .*family=SEMANTIC_CORRECTIVE').Matches.Count -eq 1) 'L2-04 projector admits exactly one R1 successor'
    Assert-True (($caseL204MultiHop.Log | Select-String -AllMatches 'CURRENT_CANONICAL_RECOVERY_RESUME .*family=SEMANTIC_REREVIEW').Matches.Count -eq 1) 'L2-04 projector admits exactly one R2 successor'
    Assert-True ($caseL204MultiHop.Log -match 'CANONICAL_RECOVERY_REQUIRED.*family=SEMANTIC_CORRECTIVE' -and $caseL204MultiHop.Log -match 'CANONICAL_RECOVERY_REQUIRED.*family=SEMANTIC_REREVIEW') 'L2-04 consumes both immutable transport handoffs without making either authoritative'
    Assert-True (($caseL204MultiHop.Log | Select-String -AllMatches 'START fresh worker').Matches.Count -eq 3) 'L2-04 dispatches only initial, R1, and R2 workers'
    Assert-True ($caseL204MultiHop.Log -match 'SEALED state.*checkpoint=checkpoint_02') 'L2-04 R2 successor progresses normal lifecycle'
    Assert-True ($caseL204MultiHop.Status.operational_status -eq 'STOPPED') 'L2-04 ends after authoritative progress'
    $recoveryWatch = & $testPowerShell -NoProfile -ExecutionPolicy Bypass -File $watcher -Root $Root -RunPath $caseRecovery.Run -Once | Out-String
    Assert-True ($recoveryWatch -match 'Status: RECOVERY_REQUIRED' -and $recoveryWatch -match 'Recovery stage: NORMALIZED_QA' -and $recoveryWatch -match 'Failing work orders: one') 'watcher displays recovery status'
    Assert-True ((Get-Content -LiteralPath $watcher -Raw -Encoding utf8) -match 'recoveryIsCurrent' -and (Get-Content -LiteralPath $watcher -Raw -Encoding utf8) -match 'resolved by later authoritative state') 'watcher suppresses stale recovery metadata after authoritative progress'

    $caseMultiRecovery = Invoke-ControlCase 'case-multi-recovery' @('multi_recovery') 1 5
    Assert-True ($caseMultiRecovery.Log -match 'SEALED state batch=FULL_BOOK_BATCH_001 completed=7/8 checkpoint=checkpoint_08') 'multi worker progress reaches post-worker checkpoint08'
    Assert-True ($caseMultiRecovery.Log -match 'RECOVERY_REQUIRED batch=FULL_BOOK_BATCH_001 checkpoint=checkpoint_08 stage=MULTI_CHECKPOINT_TEST failure=TEST_RECOVERY') 'post-worker checkpoint08 recovery handoff is accepted'
    Assert-True ($caseMultiRecovery.Status.operational_status -eq 'RECOVERY_REQUIRED') 'multi worker checkpoint08 partial is recovery required'

    $caseMultiStale = Invoke-ControlCase 'case-multi-stale-recovery' @('multi_stale_recovery') 1 5
    Assert-True ($caseMultiStale.Log -match 'HANDOFF_IGNORED reason=RECOVERY_CHECKPOINT_ALREADY_SEALED') 'sealed checkpoint06 recovery handoff is stale after checkpoint08 progress'
    Assert-True ($caseMultiStale.Status.operational_status -eq 'STOPPED') 'stale recovery does not block authoritative progress'

    $caseMultiContinue = Invoke-ControlCase 'case-multi-continue' @('multi_continue') 1 5
    Assert-True ($caseMultiContinue.Log -match 'HANDOFF_IGNORED reason=AUTHORITATIVE_STATE_PROGRESS status=CONTINUE') 'continue handoff accepts authoritative progress beyond launch checkpoint'
    Assert-True ($caseMultiContinue.Status.operational_status -eq 'STOPPED') 'continue handoff follows authoritative state'

    $caseMultiBlocked = Invoke-ControlCase 'case-multi-blocked-current' @('multi_blocked_current') 1 5
    Assert-True ($caseMultiBlocked.Log -match 'BLOCKER worker_handoff batch=FULL_BOOK_BATCH_001 checkpoint=checkpoint_08') 'canonical hard-stop handoff remains valid after worker progress'
    Assert-True ($caseMultiBlocked.Status.operational_status -eq 'BLOCKED') 'canonical hard-stop disposition stops production'

    $caseLegacyHuman = Invoke-ControlCase 'case-multi-blocked-legacy-human' @('multi_blocked_legacy_human_required', 'success_stop') 1 5
    Assert-True ($caseLegacyHuman.Log -match 'HANDOFF_REQUIRES_CURRENT_HEAD_EVALUATION') 'legacy HUMAN_REQUIRED handoff cannot directly block production'
    Assert-True ($caseLegacyHuman.Status.operational_status -eq 'RECOVERY_REQUIRED') 'legacy local terminal is replayed into the normal partial protocol rather than promoted to hard stop'

    $caseMultiBlockedStale = Invoke-ControlCase 'case-multi-blocked-stale' @('multi_blocked_stale') 1 5
    Assert-True ($caseMultiBlockedStale.Log -match 'HANDOFF_IGNORED reason=HANDOFF_CHECKPOINT_ALREADY_SEALED') 'sealed historical blocked handoff is stale after later progress'
    Assert-True ($caseMultiBlockedStale.Status.operational_status -eq 'STOPPED') 'stale historical blocked handoff cannot stop current batch'

    $caseWorkerMismatch = Invoke-ControlCase 'case-worker-id-mismatch' @('mismatch_handoff') 1 5
    Assert-True ($caseWorkerMismatch.Log -match 'HANDOFF_IGNORED reason=WORKER_ID_MISMATCH') 'worker identity mismatch is rejected and rotated'

    $caseFutureRecovery = Invoke-ControlCase 'case-future-recovery' @('future_recovery') 1 5
    Assert-True ($caseFutureRecovery.Log -match 'BLOCKER handoff_protocol=RECOVERY_HANDOFF_POST_STATE_MISMATCH') 'future or unrelated recovery checkpoint fails closed'
    Assert-True ($caseFutureRecovery.Status.operational_status -eq 'BLOCKED') 'future recovery handoff blocks'

    $caseStale = Invoke-ControlCase 'case-stale-handoff' @('advance_batch') 10 -StaleHandoff
    Assert-True ($caseStale.Log -match 'HANDOFF_ROTATED reason=before_new_worker') 'stale handoff is rotated before a fresh worker'
    Assert-True ($caseStale.Status.operational_status -eq 'STOPPED') 'stale handoff cannot block later authoritative progress'

    $case1 = Invoke-ControlCase 'case1-recovered' @('transient', 'success_stop')
    Assert-True ($case1.Log -match 'RETRYABLE_RUNTIME.*CODEX_MODEL_REFRESH_TIMEOUT') 'case1 classified retryable'
    Assert-True ($case1.Log -match 'RETRY_START') 'case1 starts retry'
    Assert-True ($case1.Log -match 'RETRY_RECOVERED') 'case1 recovers'
    Assert-True ($case1.Status.operational_status -eq 'STOPPED') 'case1 clean stop'

    $case2 = Invoke-ControlCase 'case2-exhausted' @('transient', 'transient', 'transient')
    Assert-True ($case2.Log -match 'RUNTIME_GUARD detail=CODEX_MODEL_REFRESH_TIMEOUT_RETRY_EXHAUSTED') 'case2 bounded guard'
    Assert-True ($case2.Status.operational_status -eq 'BLOCKED') 'case2 blocked after exhaustion'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $case2.Run 'production_campaign_worker.lock'))) 'case2 lock released'

    $caseEvidenceProvider = Invoke-ControlCase 'case-evidence-provider-recovered' @('evidence_provider_unavailable', 'success_stop')
    Assert-True ($caseEvidenceProvider.Log -match 'REGISTRY_POLICY_DECISION family=EVIDENCE_INFRASTRUCTURE policy=PDF_EVIDENCE_PROVIDER_RECOVERY eligible=True') 'provider failure selects canonical infrastructure policy'
    Assert-True ($caseEvidenceProvider.Log -match 'EVIDENCE_PROVIDER_RECOVERED policy=PDF_EVIDENCE_PROVIDER_RECOVERY') 'provider probe is deterministically recovered before a fresh worker'
    Assert-True ($caseEvidenceProvider.Log -match 'EVIDENCE_PROVIDER_RETRY policy=PDF_EVIDENCE_PROVIDER_RECOVERY') 'provider recovery remains bounded retry control'
    Assert-True ($caseEvidenceProvider.Status.operational_status -eq 'STOPPED') 'provider recovery resumes without a content recovery worker'

    $case3 = Invoke-ControlCase 'case3-partial' @('partial')
    Assert-True ($case3.Log -match 'RECOVERY_REQUIRED.*RECOVERY_PROTOCOL_VIOLATION') 'case3 partial is fail-closed'
    Assert-True ($case3.Status.operational_status -eq 'RECOVERY_REQUIRED') 'case3 awaits explicit recovery'
    Assert-True ($case3.Log -notmatch 'RETRY_WAIT') 'case3 does not retry'

    $case4 = Invoke-ControlCase 'case4-auth' @('auth', 'success_stop')
    Assert-True ($case4.Log -notmatch 'RETRYABLE_RUNTIME') 'case4 does not misclassify auth'
    Assert-True ($case4.Status.operational_status -eq 'STOPPED') 'case4 continues legacy guarded path until safe stop'

    $case5 = Invoke-ControlCase 'case5-artifact' @('artifact')
    Assert-True ($case5.Log -match 'BLOCKER DERIVED_STATE_VALIDATION_FAILED' -and $case5.Status.operational_status -eq 'BLOCKED') 'case5 runtime evidence validation failure is fail-closed'
    Assert-True ($case5.Log -notmatch 'RETRY_WAIT') 'case5 does not retry'

    $sourceFailureRun = New-Fixture 'case5-source-failure'
    $sourceFailureReady = New-FakeBatchReady $sourceFailureRun
    $missingSource = Join-Path $sourceFailureRun 'missing-source.docx'
    & $testPowerShell -NoProfile -ExecutionPolicy Bypass -File $supervisor -Root $Root -RunPath $sourceFailureRun -SourcePath $missingSource -BatchReadyScript $sourceFailureReady -WhatIf
    $sourceFailureLog = Get-Content -LiteralPath (Join-Path $sourceFailureRun 'production_campaign_logs\supervisor.log') -Raw -Encoding utf8
    Assert-True ($sourceFailureLog -match 'CAMPAIGN_STATE_VALIDATION_FAILED') 'case5 source validation failure is blocker'
    Assert-True ($sourceFailureLog -notmatch 'RETRY_WAIT') 'case5 source validation failure does not retry'

    $case6 = Invoke-ControlCase 'case6-progress' @('advance')
    $case6State = Get-Content -LiteralPath (Join-Path $case6.Run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($case6State.next_checkpoint -eq 'checkpoint_02') 'case6 follows authoritative progress'
    Assert-True ($case6.Log -match 'SEALED state.*checkpoint=checkpoint_02') 'case6 never repeats checkpoint_01'

    $watch = & $testPowerShell -NoProfile -ExecutionPolicy Bypass -File $watcher -Root $Root -RunPath $case1.Run -Once | Out-String
    Assert-True ($watch -notmatch 'running since') 'case7 watcher never reports stale worker as running'
    Assert-True ($case1.Status.worker_alive -eq $false -and $null -eq $case1.Status.current_worker) 'case7 stopped metadata clears worker'

    $startLauncher = Get-Content -LiteralPath (Join-Path $Root 'Start-Booktrans-Production.cmd') -Raw
    $watchLauncher = Get-Content -LiteralPath (Join-Path $Root 'Watch-Booktrans-Production.cmd') -Raw
    $stopLauncher = Get-Content -LiteralPath (Join-Path $Root 'Stop-Booktrans-Production.cmd') -Raw
    Assert-True ($startLauncher -match 'cmd\.exe /k' -and $startLauncher -notmatch 'WhatIf') 'case8 start launcher is persistent and real'
    Assert-True ($watchLauncher -match 'cmd\.exe /k' -and $watchLauncher -match 'Watcher finished') 'case8 watcher launcher keeps a persistent host'
    Assert-True ($stopLauncher -match 'pause' -and $stopLauncher -match 'Stop command finished') 'case9 stop launcher leaves visible result'
    'PRODUCTION_CAMPAIGN_SUPERVISOR_CONTROL_TESTS_PASS'
} finally {
    if (-not $env:BOOKTRANS_KEEP_CONTROL_TEST_FIXTURE -and (Test-Path -LiteralPath $testRoot)) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
