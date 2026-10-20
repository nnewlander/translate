[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$HandoffPath,
    [Parameter(Mandatory)][ValidateSet('CONTINUE', 'RECOVERY_REQUIRED', 'BLOCKED')][string]$Status,
    [Parameter(Mandatory)][string]$WorkerId,
    [Parameter(Mandatory)][string]$ActiveBatch,
    [string]$Checkpoint,
    [string]$NextCheckpoint,
    [string]$NextLogicalWorkOrderId,
    [string]$CompletedCheckpoint,
    [string]$PartialCheckpointPath,
    [string]$StageReached,
    [string]$FailureClassification,
    [string[]]$FailingWorkOrderId = @(),
    [string[]]$RelevantQaArtifactPath = @(),
    [Nullable[bool]]$RawExists,
    [string]$RawSha256,
    [string]$EvidenceRequirementJson,
    [ValidateSet('', 'CONTINUE', 'RECOVERY_REQUIRED', 'DEFERRED_SEMANTIC_DEBT', 'HARD_STOP')][string]$ProductionDisposition,
    [string]$DispositionReason,
    [string[]]$DispositionEvidenceReference = @(),
    [string]$RecoveryFamily
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
if ($Status -eq 'RECOVERY_REQUIRED') {
    $isPrecheckpointEvidenceRecovery = $FailureClassification -eq 'EVIDENCE_PROVIDER_UNAVAILABLE'
    foreach ($pair in @{
        checkpoint = $Checkpoint; stage_reached = $StageReached
        failure_classification = $FailureClassification
    }.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace([string]$pair.Value)) { throw "RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:$($pair.Key)" }
    }
    if (-not $isPrecheckpointEvidenceRecovery -and [string]::IsNullOrWhiteSpace($PartialCheckpointPath)) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:partial_checkpoint_path' }
    if ($isPrecheckpointEvidenceRecovery -and [string]::IsNullOrWhiteSpace($EvidenceRequirementJson)) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:evidence_requirement' }
    if ($null -eq $RawExists) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:raw_exists' }
    if ($FailingWorkOrderId.Count -eq 0) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:failing_work_order_ids' }
    if ($RelevantQaArtifactPath.Count -eq 0) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:relevant_qa_artifact_paths' }
    if ($RawExists -and [string]::IsNullOrWhiteSpace($RawSha256)) { throw 'RECOVERY_HANDOFF_REQUIRED_FIELD_MISSING:raw_sha256' }
}
$evidenceRequirement = $null
if ($EvidenceRequirementJson) {
    try { $evidenceRequirement = $EvidenceRequirementJson | ConvertFrom-Json -ErrorAction Stop } catch { throw 'EVIDENCE_REQUIREMENT_JSON_INVALID' }
}
$record = [ordered]@{
    schema_version = 1; worker_id = $WorkerId; status = $Status; active_batch = $ActiveBatch
    checkpoint = $Checkpoint; next_checkpoint = $NextCheckpoint; next_logical_work_order_id = $NextLogicalWorkOrderId
    completed_checkpoint = $CompletedCheckpoint; partial_checkpoint_path = $PartialCheckpointPath; stage_reached = $StageReached
    failure_classification = $FailureClassification; failing_work_order_ids = @($FailingWorkOrderId)
    relevant_qa_artifact_paths = @($RelevantQaArtifactPath); raw_exists = $RawExists; raw_sha256 = $RawSha256
    evidence_requirement = $evidenceRequirement
    production_disposition = if ($ProductionDisposition) { $ProductionDisposition } else { $null }
    disposition_reason = $DispositionReason
    disposition_evidence_references = @($DispositionEvidenceReference)
    recovery_family = $RecoveryFamily
    created_at = [DateTime]::UtcNow.ToString('o')
}
$directory = Split-Path -Parent $HandoffPath
if (-not (Test-Path -LiteralPath $directory)) { throw "HANDOFF_DIRECTORY_MISSING:$directory" }
$temporary = Join-Path $directory ('.production_campaign_worker_handoff.' + [guid]::NewGuid().ToString('N') + '.tmp')
$payload = ($record | ConvertTo-Json -Depth 4) + "`n"
[System.IO.File]::WriteAllText($temporary, $payload, $utf8)
try {
    if ([System.IO.File]::Exists($HandoffPath)) { [System.IO.File]::Replace($temporary, $HandoffPath, $null) }
    else { [System.IO.File]::Move($temporary, $HandoffPath) }
} catch {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    throw
}
Write-Output $HandoffPath
