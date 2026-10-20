$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$watcher = Join-Path $root 'Watch-Booktrans-Production.ps1'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('booktrans-watch-' + [guid]::NewGuid().ToString('N'))

function Write-Json([string]$Path, $Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
}
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function New-Run([string]$Name, [string]$HandoffStatus = 'CONTINUE') {
    $run = Join-Path $testRoot $Name; New-Item -ItemType Directory -Force -Path $run | Out-Null
    Write-Json (Join-Path $run 'production_campaign_state.json') @{active_batch='FULL_BOOK_BATCH_037';completed_count=75;target_count=471;next_checkpoint='checkpoint_04';next_logical_work_order_id='body.p.00182';campaign_status='CONTINUE';latest_authoritative_progress=@{path='progress.json'}}
    Write-Json (Join-Path $run 'production_campaign_supervisor_status.json') @{supervisor_pid=999999;operational_status='BLOCKED';last_failure_classification='RECOVERY_NO_PROGRESS';last_handoff=@{next_batch='FULL_BOOK_BATCH_037';next_checkpoint='checkpoint_01'};latest_worker_log='old-cp1.jsonl'}
    $handoff = @{active_batch='FULL_BOOK_BATCH_037';checkpoint='checkpoint_04';status=$HandoffStatus;production_disposition=$HandoffStatus}
    if ($HandoffStatus -ne 'CONTINUE') { $handoff.disposition_reason='CURRENT_CP4_BLOCKER' }
    Write-Json (Join-Path $run 'production_campaign_worker_handoff.json') $handoff
    return $run
}
try {
    $idle = New-Run 'idle'
    $idleOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $watcher -Root $root -RunPath $idle -Once | Out-String
    Assert-True ($idleOutput -match 'Campaign status: CONTINUE' -and $idleOutput -match 'Execution liveness: IDLE' -and $idleOutput -match 'Current blocker: NONE') 'idle current scope must not inherit CP1 blocker'
    Assert-True ($idleOutput -match 'Historical blocker: RECOVERY_NO_PROGRESS') 'old CP1 blocker must remain historical'

    $blocked = New-Run 'blocked' 'HARD_STOP'
    $blockedOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $watcher -Root $root -RunPath $blocked -Once | Out-String
    Assert-True ($blockedOutput -match 'Status: RECOVERY_REQUIRED' -and $blockedOutput -match 'Current blocker: CURRENT_CP4_BLOCKER') 'current CP4 handoff must be shown as current'

    $active = New-Run 'active'
    # Inject the OS process query only for this isolated watcher test; the
    # production watcher still calls Get-CimInstance directly.
    $command = "function Get-CimInstance { param([Parameter(ValueFromRemainingArguments=`$true)][object[]]`$ignored); [pscustomobject]@{ ProcessId = 4242; CommandLine = '--batch-label FULL_BOOK_BATCH_037 --checkpoint checkpoint_04' } }; & '$watcher' -Root '$root' -RunPath '$active' -Once"
    $activeOutput = & powershell -NoProfile -ExecutionPolicy Bypass -Command $command | Out-String
    Assert-True ($activeOutput -match 'Status: RUNNING' -and $activeOutput -match 'Execution liveness: ACTIVE') 'current command-line-bound worker wins over older CP1 log'
    'WATCH_CURRENT_BINDING_TESTS_PASS'
} finally { if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force } }
