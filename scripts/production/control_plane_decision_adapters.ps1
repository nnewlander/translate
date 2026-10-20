# Pure, side-effect-free PowerShell ports of the Phase-2 decision adapters.
# The Supervisor supplies already-collected facts; these functions never read
# disk, launch a process, acquire a lock, or write production metadata.

function ConvertTo-ControlPlaneCompactJson($Value) {
    if ($null -eq $Value) { return 'null' }
    return ($Value | ConvertTo-Json -Depth 12 -Compress)
}

function Invoke-CursorDecisionAdapter($Before, $After, $Evidence) {
    if ($Evidence.integrity_failure) { return [pscustomobject]@{ decision = 'INTEGRITY_FAILURE'; reason = [string]$Evidence.integrity_failure; evidence = @{ integrity_failure = $true } } }
    if ($Evidence.source_sha_unchanged -eq $false) { return [pscustomobject]@{ decision = 'INTEGRITY_FAILURE'; reason = 'SOURCE_SHA_CHANGED'; evidence = @{ source_sha_unchanged = $false } } }
    $partial = if ($Evidence.current_partial) { [string]$Evidence.current_partial } else { 'NONE' }
    if ($partial -notin @('NONE', 'PARTIAL', 'SEALED')) { return [pscustomobject]@{ decision = 'INTEGRITY_FAILURE'; reason = 'PARTIAL_FACT_INVALID'; evidence = @{ current_partial = $partial } } }
    if ($null -ne $After.target_count -and $After.completed_count -eq $After.target_count) { return [pscustomobject]@{ decision = 'BATCH_COMPLETE'; reason = 'TARGET_COUNT_REACHED'; evidence = @{ completed_count = $After.completed_count } } }
    $changed = @()
    foreach ($field in @('active_batch', 'completed_count', 'next_checkpoint', 'next_logical_work_order_id', 'completed_checkpoints')) {
        if ((ConvertTo-ControlPlaneCompactJson $Before.$field) -ne (ConvertTo-ControlPlaneCompactJson $After.$field)) { $changed += $field }
    }
    if ((ConvertTo-ControlPlaneCompactJson $Before.latest_authoritative_progress) -ne (ConvertTo-ControlPlaneCompactJson $After.latest_authoritative_progress)) { $changed += 'latest_authoritative_progress' }
    if ($changed.Count) { return [pscustomobject]@{ decision = 'PROGRESS'; reason = 'AUTHORITATIVE_CURSOR_ADVANCED'; evidence = @{ changed_fields = $changed } } }
    if ($partial -eq 'SEALED') { return [pscustomobject]@{ decision = 'SEALED_STATE_DRIFT'; reason = 'NEXT_CHECKPOINT_ALREADY_SEALED'; evidence = @{ current_partial = $partial } } }
    if ($partial -eq 'PARTIAL') { return [pscustomobject]@{ decision = 'CURRENT_PARTIAL'; reason = 'CURRENT_CHECKPOINT_UNSEALED'; evidence = @{ current_partial = $partial } } }
    return [pscustomobject]@{ decision = 'NO_PROGRESS'; reason = 'AUTHORITATIVE_CURSOR_UNCHANGED'; evidence = @{ changed_fields = @() } }
}

function Get-HandoffAdapterCheckpoint($Handoff) {
    $checkpoint = [string]$Handoff.checkpoint
    if ([string]::IsNullOrWhiteSpace($checkpoint) -and [string]$Handoff.status -eq 'CONTINUE') { $checkpoint = [string]$Handoff.next_checkpoint }
    if ([string]::IsNullOrWhiteSpace($checkpoint)) { return $null }
    return $checkpoint
}

function Invoke-HandoffDecisionAdapter([string]$CurrentWorkerId, $Handoff, $PostCursor, $Facts) {
    if ($null -eq $Handoff) { return [pscustomobject]@{ decision = 'ACCEPT_CONTINUE'; reason = 'HANDOFF_ABSENT'; evidence = @{} } }
    if ($Handoff.schema_version -ne 1) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_SCHEMA_UNSUPPORTED'; evidence = @{} } }
    if ([string]::IsNullOrWhiteSpace([string]$Handoff.worker_id)) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_WORKER_ID_MISSING'; evidence = @{} } }
    if ([string]$Handoff.worker_id -ne $CurrentWorkerId) { return [pscustomobject]@{ decision = 'STALE_ROTATE'; reason = 'WORKER_ID_MISMATCH'; evidence = @{} } }
    if ($Facts.source_sha_unchanged -eq $false) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'SOURCE_SHA_CHANGED'; evidence = @{} } }
    $status = [string]$Handoff.status
    if ($status -notin @('CONTINUE', 'RECOVERY_REQUIRED', 'BLOCKED')) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_STATUS_INVALID'; evidence = @{ status = $status } } }
    $checkpoint = Get-HandoffAdapterCheckpoint $Handoff
    if ($status -eq 'RECOVERY_REQUIRED' -and $Facts.recovery_fields_complete -eq $false) { return [pscustomobject]@{ decision = 'NORMALIZE'; reason = 'RECOVERY_FIELDS_DERIVABLE'; evidence = @{} } }
    if ([string]::IsNullOrWhiteSpace([string]$checkpoint)) {
        if ($status -eq 'CONTINUE' -and $Facts.worker_made_progress -and [string]$Facts.current_partial -eq 'NONE') { return [pscustomobject]@{ decision = 'STALE_ROTATE'; reason = 'SUPERSEDED_BY_AUTHORITATIVE_PROGRESS'; evidence = @{} } }
        return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_CHECKPOINT_INVALID'; evidence = @{} }
    }
    if ($Facts.checkpoint_format_valid -eq $false) {
        if ($status -eq 'CONTINUE' -and $Facts.worker_made_progress -and [string]$Facts.current_partial -eq 'NONE') { return [pscustomobject]@{ decision = 'STALE_ROTATE'; reason = 'SUPERSEDED_BY_AUTHORITATIVE_PROGRESS'; evidence = @{} } }
        return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_CHECKPOINT_INVALID'; evidence = @{} }
    }
    if ($Facts.checkpoint_is_sealed) { $sealedReason = if ($status -eq 'RECOVERY_REQUIRED') { 'RECOVERY_CHECKPOINT_ALREADY_SEALED' } else { 'HANDOFF_CHECKPOINT_ALREADY_SEALED' }; return [pscustomobject]@{ decision = 'STALE_ROTATE'; reason = $sealedReason; evidence = @{ checkpoint = $checkpoint } } }
    $currentBatch = [string]$PostCursor.active_batch; $currentCheckpoint = [string]$PostCursor.next_checkpoint
    $isCurrent = ([string]$Handoff.active_batch -eq $currentBatch -and $checkpoint -eq $currentCheckpoint)
    if ($status -eq 'RECOVERY_REQUIRED') {
        if ($Facts.handoff_written_during_worker -eq $false) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'RECOVERY_HANDOFF_TIMESTAMP_OUT_OF_RANGE'; evidence = @{} } }
        if ($Facts.precheckpoint_evidence_recovery -eq $true -and $isCurrent -and [string]$Facts.current_partial -eq 'NONE') { return [pscustomobject]@{ decision = 'TRIAGE'; reason = 'CURRENT_PRECHECKPOINT_EVIDENCE_RECOVERY'; evidence = @{ checkpoint = $checkpoint } } }
        if ($isCurrent -and [string]$Facts.current_partial -eq 'PARTIAL' -and $Facts.partial_path_matches) { return [pscustomobject]@{ decision = 'TRIAGE'; reason = 'CURRENT_RECOVERY_REQUIRED'; evidence = @{ checkpoint = $checkpoint } } }
        if ($isCurrent -and [string]$Facts.current_partial -eq 'PARTIAL') { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'RECOVERY_HANDOFF_PARTIAL_PATH_MISMATCH'; evidence = @{ checkpoint = $checkpoint } } }
        return [pscustomobject]@{ decision = 'BLOCK'; reason = 'RECOVERY_HANDOFF_POST_STATE_MISMATCH'; evidence = @{ checkpoint = $checkpoint } }
    }
    if ($status -eq 'BLOCKED') {
        if ($isCurrent) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'CURRENT_WORKER_BLOCKED'; evidence = @{ checkpoint = $checkpoint } } }
        return [pscustomobject]@{ decision = 'BLOCK'; reason = 'HANDOFF_POST_STATE_MISMATCH'; evidence = @{ checkpoint = $checkpoint } }
    }
    if ($Facts.worker_made_progress) { return [pscustomobject]@{ decision = 'STALE_ROTATE'; reason = 'AUTHORITATIVE_PROGRESS_SUPERSEDES_CONTINUE'; evidence = @{ checkpoint = $checkpoint } } }
    if ([string]$Handoff.active_batch -notin @([string]$Facts.launch_batch, $currentBatch)) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'CONTINUE_HANDOFF_UNRELATED_BATCH'; evidence = @{} } }
    if ($Facts.handoff_checkpoint_is_future -and [string]$Handoff.active_batch -eq $currentBatch) { return [pscustomobject]@{ decision = 'BLOCK'; reason = 'CONTINUE_HANDOFF_FUTURE_CHECKPOINT'; evidence = @{ checkpoint = $checkpoint } } }
    return [pscustomobject]@{ decision = 'ACCEPT_CONTINUE'; reason = 'CURRENT_CONTINUE'; evidence = @{ checkpoint = $checkpoint } }
}
