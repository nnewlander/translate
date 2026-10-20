[CmdletBinding()]
param(
    [string]$Root = "D:\translate",
    [string]$RunPath,
    [ValidateRange(3, 5)][int]$RefreshSeconds = 4,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$run = if ($RunPath) { $RunPath } else { Join-Path $Root "runs\full-book-20260916T000200Z-decb09" }
$statePath = Join-Path $run "production_campaign_state.json"
$statusPath = Join-Path $run "production_campaign_supervisor_status.json"
$metricsPath = Join-Path $run "production_campaign_metrics.json"
$lockPath = Join-Path $run "production_campaign_worker.lock"
$supervisorLog = Join-Path $run "production_campaign_logs\supervisor.log"
$handoffPath = Join-Path $run "production_campaign_worker_handoff.json"

function Read-JsonOrNull([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json } catch { return $null }
}

function Test-ProcessAlive($ProcessId) {
    if ($null -eq $ProcessId) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Find-CurrentWorkerProcess([string]$Batch, [string]$Checkpoint) {
    if (-not $Batch -or -not $Checkpoint) { return $null }
    # Standalone workers do not create supervisor status.  Their immutable
    # scope is carried in their command line, so bind liveness to the
    # authoritative state rather than to the last supervisor record.
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match [regex]::Escape('--batch-label') -and
            $_.CommandLine -match [regex]::Escape($Batch) -and
            $_.CommandLine -match [regex]::Escape('--checkpoint') -and
            $_.CommandLine -match [regex]::Escape($Checkpoint)
        } | Select-Object -First 1)[0]
    } catch { return $null }
}

do {
    $state = Read-JsonOrNull $statePath
    $status = Read-JsonOrNull $statusPath
    $metrics = Read-JsonOrNull $metricsPath
    $handoff = Read-JsonOrNull $handoffPath
    $lockExists = Test-Path -LiteralPath $lockPath
    $supervisorAlive = if ($status) { Test-ProcessAlive $status.supervisor_pid } else { $false }
    $currentWorker = if ($state) { Find-CurrentWorkerProcess ([string]$state.active_batch) ([string]$state.next_checkpoint) } else { $null }
    $workerIsActuallyRunning = $null -ne $currentWorker
    $handoffIsCurrent = $handoff -and $state -and
        [string]$handoff.active_batch -eq [string]$state.active_batch -and
        [string]$handoff.checkpoint -eq [string]$state.next_checkpoint
    $currentBlocker = if ($handoffIsCurrent -and $handoff.production_disposition -and $handoff.production_disposition -ne 'CONTINUE') {
        if ($handoff.disposition_reason) { [string]$handoff.disposition_reason } else { [string]$handoff.failure_classification }
    } else { 'NONE' }
    $recoveryIsCurrent = $false
    if ($status -and $status.recovery -and $state) {
        $recoveryPath = [string]$status.recovery.partial_checkpoint_path
        $recoveryIsCurrent = (
            [string]$status.recovery.batch -eq [string]$state.active_batch -and
            [string]$status.recovery.checkpoint -eq [string]$state.next_checkpoint -and
            $recoveryPath -and (Test-Path -LiteralPath $recoveryPath)
        )
    }
    # A dead supervisor's old BLOCKED record is operational history.  If the
    # immutable state has advanced beyond its recorded handoff, retain the
    # history below without presenting it as a current blocker.
    $blockedIsCurrent = $true
    if ($status -and $status.operational_status -eq "BLOCKED" -and -not $supervisorAlive -and -not $lockExists -and $state -and $status.last_handoff) {
        $blockedIsCurrent = (
            [string]$status.last_handoff.next_batch -eq [string]$state.active_batch -and
            [string]$status.last_handoff.next_checkpoint -eq [string]$state.next_checkpoint
        )
    }
    $historicalBlocker = if ($status -and $status.operational_status -eq 'BLOCKED' -and -not $blockedIsCurrent) {
        [string]$status.last_failure_classification
    } else { $null }
    if ($currentBlocker -ne 'NONE') { $displayStatus = "RECOVERY_REQUIRED" }
    elseif ($workerIsActuallyRunning) { $displayStatus = "RUNNING" }
    elseif ($lockExists) { $displayStatus = "BLOCKED" }
    else { $displayStatus = "STOPPED" }

    # Clear-Host needs an interactive console handle; keep -Once usable by
    # focused tests and redirected diagnostics as well.
    try { Clear-Host } catch { }
    Write-Host "BOOKTRANS PRODUCTION"
    Write-Host ""
    Write-Host "Status: $displayStatus"
    Write-Host "Active Batch: $(if ($state) { $state.active_batch } else { 'unavailable' })"
    Write-Host "Completed: $(if ($state) { "$($state.completed_count)/$($state.target_count)" } else { 'unavailable' })"
    Write-Host "Checkpoint: $(if ($state) { $state.next_checkpoint } else { 'unavailable' })"
    Write-Host "Next work order: $(if ($state) { $state.next_logical_work_order_id } else { 'unavailable' })"
    Write-Host "Campaign status: $(if ($state) { $state.campaign_status } else { 'unavailable' })"
    Write-Host "Execution liveness: $(if ($workerIsActuallyRunning) { 'ACTIVE' } else { 'IDLE' })"
    Write-Host "Current blocker: $currentBlocker"
    Write-Host "Historical blocker: $(if ($historicalBlocker) { $historicalBlocker } else { 'none' })"
    Write-Host "Latest authoritative progress: $(if ($state) { $state.latest_authoritative_progress.path } else { 'unavailable' })"
    Write-Host "Last state update: $(if (Test-Path -LiteralPath $statePath) { (Get-Item -LiteralPath $statePath).LastWriteTime.ToString('o') } else { 'unavailable' })"
    Write-Host "Current/last worker: $(if ($workerIsActuallyRunning) { "running pid=$($currentWorker.ProcessId) bound=$($state.active_batch)/$($state.next_checkpoint)" } else { 'no live current worker' })"
    Write-Host "Last handoff: $(if ($handoff) { "status=$($handoff.status), scope=$($handoff.active_batch)/$($handoff.checkpoint)" } else { 'unavailable' })"
    Write-Host "Supervisor PID if available: $(if ($status) { "$($status.supervisor_pid) (alive=$supervisorAlive)" } else { 'unavailable' })"
    Write-Host "Worker alive: $workerIsActuallyRunning"
    Write-Host "Supervisor ended: $(if ($status -and $status.ended_at) { $status.ended_at } else { 'unavailable' })"
    Write-Host "Last exit / failure: $(if ($status) { "$($status.last_exit_code) / $($status.last_failure_classification)" } else { 'unavailable' })"
    if ($status -and $status.operational_status -eq "BLOCKED" -and -not $blockedIsCurrent) {
        Write-Host "Historical blocker: resolved by later authoritative state"
    }
    if ($status -and $status.retry) {
        Write-Host "Retry: $($status.retry.attempt)/$($status.retry.max_attempts)"
        Write-Host "Reason: $($status.retry.reason)"
        Write-Host "Next retry: $($status.retry.next_retry_at)"
    }
    if ($status -and $status.recovery -and $recoveryIsCurrent) {
        Write-Host "Recovery batch/checkpoint: $($status.recovery.batch) / $($status.recovery.checkpoint)"
        Write-Host "Recovery stage: $($status.recovery.stage_reached)"
        Write-Host "Recovery failure: $($status.recovery.failure_classification)"
        if ($status.recovery.canonical_failure_classification) { Write-Host "Canonical recovery class: $($status.recovery.canonical_failure_classification) / $($status.recovery.repair_policy)" }
        Write-Host "Partial path: $($status.recovery.partial_checkpoint_path)"
        Write-Host "Failing work orders: $(@($status.recovery.failing_work_order_ids) -join ', ')"
        if ($displayStatus -eq 'AUTO_RECOVERING') { Write-Host "Recovery attempt: $($status.recovery.attempt)/$($status.recovery.max_attempts); worker alive: $workerIsActuallyRunning" }
    } elseif ($status -and $status.recovery) {
        Write-Host "Recovery metadata: resolved by later authoritative state"
    }
    Write-Host "Writer lock: $(if ($lockExists) { 'present' } else { 'absent' })"
    if ($metrics) {
        Write-Host "Throughput: $(if ($null -ne $metrics.units_per_hour) { "$($metrics.units_per_hour) units/hour" } else { 'pending checkpoint timing' })"
        Write-Host "Current batch ETA: $(if ($null -ne $metrics.current_batch_eta_seconds) { "$($metrics.current_batch_eta_seconds) sec" } else { 'unavailable' })"
        Write-Host "Last checkpoint duration: $(if ($null -ne $metrics.last_checkpoint_duration_seconds) { "$($metrics.last_checkpoint_duration_seconds) sec" } else { 'unavailable' })"
        Write-Host "Auto recovery: $($metrics.auto_recovery_count) (pass=$($metrics.auto_recovery_success_count), fail=$($metrics.auto_recovery_failure_count))"
        Write-Host "QA triage / eligible / human escalation: $($metrics.triaged_failure_count) / $($metrics.auto_recovery_eligible_count) / $($metrics.human_escalation_count)"
        Write-Host "Handoff normalization: $($metrics.recovery_handoff_normalization_count) (unresolvable=$($metrics.recovery_handoff_normalization_failure_count))"
        Write-Host "Recovery duration / success rate: $(if ($null -ne $metrics.recovery_duration_seconds) { "$($metrics.recovery_duration_seconds)s" } else { 'unavailable' }) / $(if ($null -ne $metrics.auto_recovery_success_rate) { $metrics.auto_recovery_success_rate } else { 'unavailable' })"
        Write-Host "Productive / blocked: $($metrics.productive_duration_seconds)s / $($metrics.blocked_duration_seconds)s"
    }
    Write-Host ""
    Write-Host "Recent supervisor milestones"
    if (Test-Path -LiteralPath $supervisorLog) {
        Get-Content -LiteralPath $supervisorLog -Tail 10 -Encoding utf8
    } else {
        Write-Host "(no supervisor milestone log yet)"
    }
    if (-not $Once) { Start-Sleep -Seconds $RefreshSeconds }
} while (-not $Once)
