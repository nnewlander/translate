[CmdletBinding()]
param(
    [string]$Root = "D:\translate",
    [int]$MaxConsecutiveWorkerFailures = 3,
    [int]$MaxSameFailureFingerprint = 2,
    [int]$MaxNoProgressRetries = 3,
    [int[]]$NoProgressRetryBackoffSeconds = @(3, 10),
    [int]$MaxTransientRuntimeRetries = 3,
    [int]$MaxAutoRecoveryAttempts = 2,
    [int[]]$TransientRetryBackoffSeconds = @(5, 15, 30),
    [string]$CodexExecutable = "codex",
    [string]$RunPath,
    [string]$SourcePath,
    [string]$StateRebuildScript,
    [string]$BatchReadyScript,
    [string]$ReleaseBoundaryScript,
    [string]$TerminalClosureScript,
    [string]$CanonicalRecoveryProjectorScript,
    [switch]$EnableCp04SemanticRescue,
    [string]$Cp04HumanAdjudicationPath,
    [string]$Cp04D051ApprovalPath,
    [string]$D051ReuseApprovalPath,
    [string]$D051ReuseDisplayId,
    [string]$HumanAdjudicationPath,
    [switch]$ShadowControlPlane,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$defaultRun = Join-Path $Root "runs\full-book-20260916T000200Z-decb09"
$run = if ($RunPath) { $RunPath } else { $defaultRun }
$source = if ($SourcePath) { $SourcePath } else { Join-Path $Root "Software Engineering at Google.docx" }
$stateScript = if ($StateRebuildScript) { $StateRebuildScript } else { Join-Path $Root "scripts\production\rebuild_production_campaign_state.py" }
  $batchReadyScript = if ($BatchReadyScript) { $BatchReadyScript } else { Join-Path $Root "scripts\production\validate_batch_rollover_readiness.py" }
  $terminalClosureScript = if ($TerminalClosureScript) { $TerminalClosureScript } else { Join-Path $Root "scripts\production\reconcile_terminal_batch_closure.py" }
  $releaseBoundaryScript = if ($ReleaseBoundaryScript) { $ReleaseBoundaryScript } else { Join-Path $Root "scripts\production\reconcile_release_boundary.py" }
  $finalQaTriageScript = Join-Path $Root "scripts\production\triage_final_deterministic_qa.py"
  $recoveryHandoffNormalizationScript = Join-Path $Root "scripts\production\normalize_recovery_handoff.py"
$controlPlaneShadowScript = Join-Path $Root "scripts\production\shadow_control_plane_decision.py"
$controlPlaneAdapterScript = Join-Path $Root "scripts\production\control_plane_decision_adapters.ps1"
$recoveryPolicyDecisionScript = Join-Path $Root "scripts\production\decide_recovery_policy.py"
$recoveryEvidenceBuilderScript = Join-Path $Root "scripts\production\build_recovery_evidence.py"
$canonicalRecoveryProjectorScript = if ($CanonicalRecoveryProjectorScript) { $CanonicalRecoveryProjectorScript } else { Join-Path $Root "scripts\production\project_current_canonical_recovery.py" }
$currentExecutionProjectorScript = Join-Path $Root "scripts\production\project_current_execution.py"
$pdfEvidenceProbeScript = Join-Path $Root "scripts\production\probe_pdf_evidence_capability.py"
$python = Join-Path $Root ".venv\Scripts\python.exe"
$deterministicWorkerScript = Join-Path $Root "scripts\production\run_qwen_deterministic_worker.py"
$allowlistedRecoveryScript = Join-Path $Root "scripts\production\run_allowlisted_recovery.py"
$promptPath = Join-Path $Root "scripts\production\production_worker_prompt.txt"
$recoveryPromptPath = Join-Path $Root "scripts\production\production_recovery_worker_prompt.txt"
$lockPath = Join-Path $run "production_campaign_worker.lock"
$stopRequestPath = Join-Path $run "production_campaign_stop.request"
$logDir = Join-Path $run "production_campaign_logs"
$supervisorLog = Join-Path $logDir "supervisor.log"
$statusPath = Join-Path $run "production_campaign_supervisor_status.json"
$metricsPath = Join-Path $run "production_campaign_metrics.json"
$eventsPath = Join-Path $run "production_campaign_events.jsonl"
$workerHandoffPath = Join-Path $run "production_campaign_worker_handoff.json"

if ($MaxTransientRuntimeRetries -lt 1) { throw "INVALID_MAX_TRANSIENT_RUNTIME_RETRIES" }
if ($MaxAutoRecoveryAttempts -lt 1) { throw "INVALID_MAX_AUTO_RECOVERY_ATTEMPTS" }
if ($TransientRetryBackoffSeconds.Count -lt 1 -or @($TransientRetryBackoffSeconds | Where-Object { $_ -lt 0 }).Count) { throw "INVALID_TRANSIENT_RETRY_BACKOFF" }
if ($MaxNoProgressRetries -lt 1) { throw "INVALID_MAX_NO_PROGRESS_RETRIES" }
if ($NoProgressRetryBackoffSeconds.Count -lt 1 -or @($NoProgressRetryBackoffSeconds | Where-Object { $_ -lt 0 }).Count) { throw "INVALID_NO_PROGRESS_RETRY_BACKOFF" }
if (-not (Test-Path -LiteralPath $python)) { throw "PROJECT_VENV_MISSING:$python" }
if (-not (Test-Path -LiteralPath $stateScript)) { throw "STATE_REBUILD_SCRIPT_MISSING:$stateScript" }
if (-not (Test-Path -LiteralPath $batchReadyScript)) { throw "BATCH_READY_SCRIPT_MISSING:$batchReadyScript" }
if (-not (Test-Path -LiteralPath $releaseBoundaryScript)) { throw "RELEASE_BOUNDARY_SCRIPT_MISSING:$releaseBoundaryScript" }
  if (-not (Test-Path -LiteralPath $finalQaTriageScript)) { throw "FINAL_QA_TRIAGE_SCRIPT_MISSING:$finalQaTriageScript" }
  if (-not (Test-Path -LiteralPath $recoveryHandoffNormalizationScript)) { throw "RECOVERY_HANDOFF_NORMALIZATION_SCRIPT_MISSING:$recoveryHandoffNormalizationScript" }
if (($WhatIf -or $ShadowControlPlane) -and -not (Test-Path -LiteralPath $controlPlaneShadowScript)) { throw "CONTROL_PLANE_SHADOW_SCRIPT_MISSING:$controlPlaneShadowScript" }
if (-not (Test-Path -LiteralPath $controlPlaneAdapterScript)) { throw "CONTROL_PLANE_ADAPTER_SCRIPT_MISSING:$controlPlaneAdapterScript" }
if (-not (Test-Path -LiteralPath $recoveryPolicyDecisionScript)) { throw "RECOVERY_POLICY_REGISTRY_SCRIPT_MISSING:$recoveryPolicyDecisionScript" }
if (-not (Test-Path -LiteralPath $recoveryEvidenceBuilderScript)) { throw "RECOVERY_EVIDENCE_BUILDER_MISSING:$recoveryEvidenceBuilderScript" }
if (-not (Test-Path -LiteralPath $canonicalRecoveryProjectorScript)) { throw "CANONICAL_RECOVERY_PROJECTOR_MISSING:$canonicalRecoveryProjectorScript" }
if (-not (Test-Path -LiteralPath $currentExecutionProjectorScript)) { throw "CURRENT_EXECUTION_PROJECTOR_MISSING:$currentExecutionProjectorScript" }
if (-not (Test-Path -LiteralPath $pdfEvidenceProbeScript)) { throw "PDF_EVIDENCE_PROBE_MISSING:$pdfEvidenceProbeScript" }
if (-not (Test-Path -LiteralPath $promptPath)) { throw "WORKER_PROMPT_MISSING:$promptPath" }
if (-not (Test-Path -LiteralPath $deterministicWorkerScript)) { throw "DETERMINISTIC_WORKER_MISSING:$deterministicWorkerScript" }
if (-not (Test-Path -LiteralPath $allowlistedRecoveryScript)) { throw "ALLOWLISTED_RECOVERY_SCRIPT_MISSING:$allowlistedRecoveryScript" }
if (-not (Test-Path -LiteralPath $recoveryPromptPath)) { throw "RECOVERY_WORKER_PROMPT_MISSING:$recoveryPromptPath" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
. $controlPlaneAdapterScript

$lock = $null
$ownsLock = $false
$lockAcquireFailed = $false
$writer = $null
$workerWriter = $null
$exitCodeForSupervisor = 0
$operationalStatus = "STARTING"
$finalDetail = ""
$startedAt = Get-Date -Format o
$endedAt = $null
$currentWorker = $null
$workerAlive = $false
$previousStatus = $null
if (Test-Path -LiteralPath $statusPath) {
    try { $previousStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding utf8 | ConvertFrom-Json } catch { }
}
$latestWorkerLog = if ($previousStatus) { $previousStatus.latest_worker_log } else { $null }
$lastHandoff = if ($previousStatus) { $previousStatus.last_handoff } else { $null }
$lastExitCode = if ($previousStatus) { $previousStatus.last_exit_code } else { $null }
$lastFailureClassification = if ($previousStatus) { $previousStatus.last_failure_classification } else { $null }
$retryInfo = $null
$recoveryInfo = $null
$workerCount = 0
  $autoRecoverySuccessCount = 0
  $autoRecoveryFailureCount = 0
  $autoRecoveryCount = 0
  $triagedFailureCount = 0
  $autoRecoveryEligibleCount = 0
  $recoveryDurationSeconds = 0.0
  $humanEscalationCount = 0
  $recoveryHandoffNormalizationCount = 0
  $recoveryHandoffNormalizationFailureCount = 0
  $eventAppendCount = 0
  $eventAppendFailureCount = 0
  $eventAppendTotalMilliseconds = 0.0
  $cursorDecisionCount = 0
  $cursorDecisionTotalMilliseconds = 0.0
  $handoffDecisionCount = 0
  $handoffDecisionTotalMilliseconds = 0.0
  $policyDecisionCount = 0
  $policyDecisionTotalMilliseconds = 0.0

function Write-OperationalStatus([string]$Status, [string]$Detail) {
    $record = [ordered]@{
        schema_version = 2
        artifact_type = "PRODUCTION_CAMPAIGN_SUPERVISOR_STATUS"
        derived_operational_metadata = $true
        supervisor_pid = $PID
        started_at = $startedAt
        ended_at = $endedAt
        updated_at = Get-Date -Format o
        current_worker = $currentWorker
        current_worker_start = if ($currentWorker) { $currentWorker.started_at } else { $null }
        worker_alive = $workerAlive
        latest_worker_log = $latestWorkerLog
        last_handoff = $lastHandoff
        last_exit_code = $lastExitCode
        last_failure_classification = $lastFailureClassification
        retry = $retryInfo
        recovery = $recoveryInfo
        operational_status = $Status
        detail = $Detail
    }
    $temporary = "$statusPath.$PID.tmp"
    $backup = "$statusPath.$PID.bak"
    try {
        [System.IO.File]::WriteAllText($temporary, (($record | ConvertTo-Json -Depth 6) + "`n"), $utf8NoBom)
        if (Test-Path -LiteralPath $statusPath) {
            if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
            [System.IO.File]::Replace($temporary, $statusPath, $backup)
            if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        } else {
            [System.IO.File]::Move($temporary, $statusPath)
        }
    } catch {
        Write-Host "状态元数据写入失败：$($_.Exception.Message)"
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    }
}

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    [System.IO.File]::AppendAllText($supervisorLog, "$line`n", $utf8NoBom)
    Write-Host $line
}

function Get-CanonicalChildEnvironment($Environment = [Environment]::GetEnvironmentVariables()) {
    # Windows treats environment keys case-insensitively, while a long-lived
    # parent process can expose both Path and PATH through .NET.  Build the
    # child map explicitly so ProcessStartInfo never receives duplicate keys.
    # This is process-local only; it never changes the machine or user env.
    $raw = $Environment
    $child = [hashtable]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($key in @($raw.Keys | Sort-Object { [string]$_ })) {
        $name = [string]$key
        if ($name -ieq 'Path') { continue }
        if (-not $child.ContainsKey($name)) { $child.Add($name, [string]$raw[$key]) }
    }
    $pathValue = $null
    foreach ($key in @($raw.Keys)) {
        if ([string]$key -ceq 'Path') { $pathValue = [string]$raw[$key]; break }
    }
    if ($null -eq $pathValue) {
        foreach ($key in @($raw.Keys)) {
            if ([string]$key -ieq 'Path') { $pathValue = [string]$raw[$key]; break }
        }
    }
    if ($null -ne $pathValue) { $child.Add('Path', $pathValue) }
    # Hashtable output is enumerable in Windows PowerShell.  Preserve it as a
    # single environment mapping for the ProcessStartInfo boundary.
    return ,$child
}

function ConvertTo-ChildCommandLineArgument([string]$Value) {
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $builder = [System.Text.StringBuilder]::new('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append((('\' * (($slashes * 2) + 1)) -join ''))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$builder.Append((('\' * $slashes) -join '')); $slashes = 0 }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) { [void]$builder.Append((('\' * ($slashes * 2)) -join '')) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Start-DeterministicWorkerProcess([string]$FilePath, [string[]]$ArgumentVector, [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath, [ref]$LaunchDiagnostics) {
    $LaunchDiagnostics.Value.launch_stage = 'PROCESS_ARGUMENT_BUILD'
    $LaunchDiagnostics.Value.executable = $FilePath
    $LaunchDiagnostics.Value.working_directory = $WorkingDirectory
    $quotedArguments = @($ArgumentVector | ForEach-Object { ConvertTo-ChildCommandLineArgument ([string]$_) })
    # Windows PowerShell can expose ProcessStartInfo.EnvironmentVariables as
    # null for a valid instance.  Do not use that collection: the child gets
    # an inherited environment captured at Process.Start instead.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new($FilePath, [string]::Join(' ', [string[]]$quotedArguments))
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $childEnvironment = Get-CanonicalChildEnvironment
    $environmentKeys = @($childEnvironment.Keys | ForEach-Object { [string]$_ })
    $duplicateGroups = @($environmentKeys | Group-Object { $_.ToUpperInvariant() } | Where-Object { $_.Count -gt 1 })
    $LaunchDiagnostics.Value.env_key_count = $environmentKeys.Count
    $LaunchDiagnostics.Value.case_insensitive_duplicate_count = $duplicateGroups.Count
    $LaunchDiagnostics.Value.launch_stage = 'ENVIRONMENT_BUILD'
    $environmentScope = [System.EnvironmentVariableTarget]::Process
    $normalizedKeys = @('Path', 'BOOKTRANS_PRODUCTION_WORKER_ID', 'BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH', 'BOOKTRANS_PRODUCTION_BATCH', 'BOOKTRANS_PRODUCTION_CHECKPOINT')
    $parentEnvironment = [Environment]::GetEnvironmentVariables($environmentScope)
    $affectedKeys = @($parentEnvironment.Keys | ForEach-Object { [string]$_ } | Where-Object {
        $candidate = $_
        $null -ne ($normalizedKeys | Where-Object { $_ -ieq $candidate } | Select-Object -First 1)
    })
    foreach ($name in $normalizedKeys) {
        if ($null -eq ($affectedKeys | Where-Object { $_ -ieq $name } | Select-Object -First 1)) { $affectedKeys += $name }
    }
    $parentSnapshot = @()
    foreach ($key in $affectedKeys) {
        $parentSnapshot += [ordered]@{ key = $key; value = [Environment]::GetEnvironmentVariable($key, $environmentScope) }
    }
    try {
        # Only the normalized launch keys are changed.  Every other variable
        # is inherited untouched, and the process environment is restored
        # before this worker launch returns.
        foreach ($key in $affectedKeys) { [Environment]::SetEnvironmentVariable($key, $null, $environmentScope) }
        foreach ($key in $normalizedKeys) {
            if ($childEnvironment.ContainsKey($key)) {
                [Environment]::SetEnvironmentVariable($key, [string]$childEnvironment[$key], $environmentScope)
            }
        }
        $child = [System.Diagnostics.Process]::new()
        $child.StartInfo = $startInfo
        $LaunchDiagnostics.Value.launch_stage = 'START_PROCESS_ENTER'
        if (-not $child.Start()) { throw [System.InvalidOperationException]::new('WORKER_LAUNCH_FAILED:PROCESS_START_RETURNED_FALSE') }
        $LaunchDiagnostics.Value.child_created = $true
        $LaunchDiagnostics.Value.launch_stage = 'START_PROCESS_RETURN'
        $stdoutTask = $child.StandardOutput.ReadToEndAsync()
        $stderrTask = $child.StandardError.ReadToEndAsync()
        $LaunchDiagnostics.Value.launch_stage = 'CHILD_EXIT_WAIT'
        $child.WaitForExit()
        [System.IO.File]::WriteAllText($StdoutPath, $stdoutTask.GetAwaiter().GetResult(), $utf8NoBom)
        [System.IO.File]::WriteAllText($StderrPath, $stderrTask.GetAwaiter().GetResult(), $utf8NoBom)
        return $child
    } finally {
        $currentEnvironment = [Environment]::GetEnvironmentVariables($environmentScope)
        $restoreKeys = @($currentEnvironment.Keys | ForEach-Object { [string]$_ } | Where-Object {
            $candidate = $_
            $null -ne ($normalizedKeys | Where-Object { $_ -ieq $candidate } | Select-Object -First 1)
        })
        foreach ($key in $restoreKeys) { [Environment]::SetEnvironmentVariable($key, $null, $environmentScope) }
        foreach ($entry in $parentSnapshot) {
            if ($null -ne $entry.value) { [Environment]::SetEnvironmentVariable([string]$entry.key, [string]$entry.value, $environmentScope) }
        }
    }
}

function New-WorkerLaunchFailureRecord([string]$WorkerId, $ErrorRecord, $LaunchDiagnostics, [int]$ExitCode) {
    $exception = $ErrorRecord.Exception
    $inner = @()
    $cursor = $exception.InnerException
    while ($null -ne $cursor) {
        $inner += [ordered]@{ type = $cursor.GetType().FullName; message = $cursor.Message }
        $cursor = $cursor.InnerException
    }
    return [ordered]@{
        artifact_type = 'WORKER_LAUNCH_FAILED'; schema_version = 1; worker_id = $WorkerId
        launch_stage = [string]$LaunchDiagnostics.launch_stage
        exception_type = $exception.GetType().FullName; exception_message = $exception.Message
        script_stack_trace = [string]$ErrorRecord.ScriptStackTrace
        invocation_position = [string]$ErrorRecord.InvocationInfo.PositionMessage
        inner_exception_chain = @($inner)
        child_created = [bool]$LaunchDiagnostics.child_created
        executable = [string]$LaunchDiagnostics.executable
        working_directory = [string]$LaunchDiagnostics.working_directory
        env_key_count = [int]$LaunchDiagnostics.env_key_count
        case_insensitive_duplicate_count = [int]$LaunchDiagnostics.case_insensitive_duplicate_count
        explicit_exit_code = $ExitCode; occurred_at = (Get-Date -Format o)
    }
}

function Write-ProductionEvent(
    [string]$EventType,
    $Cursor = $null,
    [string]$WorkerId = $null,
    [string]$PolicyId = $null,
    $Attempt = $null,
    $DurationSeconds = $null,
    [string]$Result = $null,
    [string]$Reason = $null
) {
    # Derived telemetry is intentionally non-authoritative.  A failed append
    # must never alter a cursor, lock, worker exit code, or recovery decision.
    $started = Get-Date
    try {
        $record = [ordered]@{
            schema_version = 1; artifact_type = 'PRODUCTION_CAMPAIGN_EVENT'; derived_operational_metadata = $true
            timestamp = $started.ToString('o'); event_type = $EventType; operational_state = $operationalStatus
            batch = if ($Cursor) { $Cursor.active_batch } else { $null }
            checkpoint = if ($Cursor) { $Cursor.next_checkpoint } else { $null }
            completed_count = if ($Cursor) { $Cursor.completed_count } else { $null }
            worker_id = $WorkerId; policy_id = $PolicyId; attempt = $Attempt; duration_seconds = $DurationSeconds
            result = $Result; reason = $Reason
        }
        $stream = [System.IO.File]::Open($eventsPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
        try {
            $eventWriter = [System.IO.StreamWriter]::new($stream, $utf8NoBom)
            try { $eventWriter.WriteLine(($record | ConvertTo-Json -Depth 6 -Compress)); $eventWriter.Flush() } finally { $eventWriter.Dispose() }
        } finally { $stream.Dispose() }
        $script:eventAppendCount++
        $script:eventAppendTotalMilliseconds += ((Get-Date) - $started).TotalMilliseconds
    } catch {
        $script:eventAppendFailureCount++
        Write-Host "TELEMETRY_EVENT_APPEND_FAILED: $($_.Exception.Message)"
    }
}

function Get-TelemetrySnapshot {
    try {
        $state = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json
        $manifest = Get-Content -LiteralPath $state.active_manifest.path -Raw -Encoding utf8 | ConvertFrom-Json
        $batchMatch = [regex]::Match([string]$state.active_batch, 'FULL_BOOK_BATCH_(\d{3})$')
        $batchPath = Join-Path $run ("full_book_batch_{0}" -f $batchMatch.Groups[1].Value)
        $workerLog = if ($latestWorkerLog -and (Test-Path -LiteralPath $latestWorkerLog)) { Get-Item -LiteralPath $latestWorkerLog } else { Get-ChildItem -LiteralPath $logDir -File -Filter 'worker-*.jsonl' | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1 }
        $workerStart = if ($workerLog) { $workerLog.CreationTimeUtc } else { $null }
        $workerEnd = if ($workerLog) { $workerLog.LastWriteTimeUtc } else { $null }
        $events = @()
        $priorSeal = $null
        foreach ($item in @($manifest.checkpoints)) {
            $dir = Join-Path $batchPath $item.id; $request = Join-Path $dir 'translation_request.jsonl'; $evidence = Join-Path $dir 'checkpoint_evidence.json'
            if ((Test-Path -LiteralPath $request) -and (Test-Path -LiteralPath $evidence)) {
                $sealed = (Get-Item -LiteralPath $evidence).LastWriteTimeUtc; $started = if ($priorSeal) { $priorSeal } elseif ($workerStart) { $workerStart } else { (Get-Item -LiteralPath $request).LastWriteTimeUtc }; $priorSeal = $sealed
                $events += [pscustomobject]@{ checkpoint = $item.id; started_at = $started.ToString('o'); sealed_at = $sealed.ToString('o'); duration_seconds = [math]::Round(($sealed - $started).TotalSeconds, 3); units = $item.actual_count }
            }
        }
        $units = [int]$state.completed_count
        $first = if ($workerStart) { $workerStart } elseif ($events.Count) { [datetime]$events[0].started_at } else { $null }; $last = if ($workerEnd) { $workerEnd } elseif ($events.Count) { [datetime]$events[-1].sealed_at } else { $null }
        $rate = if ($first -and $last -and $last -gt $first -and $units -gt 0) { [math]::Round($units / (($last - $first).TotalHours), 3) } else { $null }
        return [pscustomobject]@{ events = $events; batch_started_at = if ($first) { $first.ToString('o') } else { $null }; batch_last_sealed_at = if ($last) { $last.ToString('o') } else { $null }; units_completed = $units; units_per_hour = $rate; current_batch_eta_seconds = if ($rate) { [math]::Round((($state.target_count - $units) / $rate) * 3600, 3) } else { $null }; last_checkpoint_duration_seconds = if ($events.Count) { $events[-1].duration_seconds } else { $null }; worker_startup_duration_seconds = if ($workerStart -and $events.Count) { $events[0].duration_seconds } else { $null }; worker_duration_seconds = if ($workerStart -and $workerEnd) { [math]::Round(($workerEnd - $workerStart).TotalSeconds, 3) } else { $null } }
    } catch { return [pscustomobject]@{ events = @(); batch_started_at = $null; batch_last_sealed_at = $null; units_completed = 0; units_per_hour = $null; current_batch_eta_seconds = $null; last_checkpoint_duration_seconds = $null } }
}

function Write-Metrics([string]$Event) {
    $telemetry = Get-TelemetrySnapshot
    $record = [ordered]@{
        schema_version = 1; artifact_type = "PRODUCTION_CAMPAIGN_METRICS"; derived_operational_metadata = $true
          updated_at = Get-Date -Format o; supervisor_started_at = $startedAt; operational_status = $operationalStatus
          event = $Event; worker_count = $workerCount; auto_recovery_count = $autoRecoveryCount
          auto_recovery_success_count = $autoRecoverySuccessCount; auto_recovery_failure_count = $autoRecoveryFailureCount
          triaged_failure_count = $triagedFailureCount; auto_recovery_eligible_count = $autoRecoveryEligibleCount
          recovery_duration_seconds = [math]::Round($recoveryDurationSeconds, 3)
          auto_recovery_success_rate = if ($autoRecoveryCount) { [math]::Round($autoRecoverySuccessCount / $autoRecoveryCount, 4) } else { $null }
          human_escalation_count = $humanEscalationCount
          recovery_handoff_normalization_count = $recoveryHandoffNormalizationCount
          recovery_handoff_normalization_failure_count = $recoveryHandoffNormalizationFailureCount
          event_append_count = $eventAppendCount; event_append_failure_count = $eventAppendFailureCount
          mean_event_append_latency_ms = if ($eventAppendCount) { [math]::Round($eventAppendTotalMilliseconds / $eventAppendCount, 3) } else { $null }
          cursor_decision_count = $cursorDecisionCount
          mean_cursor_decision_latency_ms = if ($cursorDecisionCount) { [math]::Round($cursorDecisionTotalMilliseconds / $cursorDecisionCount, 3) } else { $null }
          handoff_decision_count = $handoffDecisionCount
          mean_handoff_decision_latency_ms = if ($handoffDecisionCount) { [math]::Round($handoffDecisionTotalMilliseconds / $handoffDecisionCount, 3) } else { $null }
          policy_decision_count = $policyDecisionCount
          mean_policy_decision_latency_ms = if ($policyDecisionCount) { [math]::Round($policyDecisionTotalMilliseconds / $policyDecisionCount, 3) } else { $null }
          control_plane_overhead_ms = [math]::Round(($cursorDecisionTotalMilliseconds + $handoffDecisionTotalMilliseconds + $policyDecisionTotalMilliseconds + $eventAppendTotalMilliseconds), 3)
        productive_duration_seconds = [math]::Round(((Get-Date) - [datetime]$startedAt).TotalSeconds, 3)
        blocked_duration_seconds = 0; last_checkpoint_duration_seconds = $telemetry.last_checkpoint_duration_seconds; units_per_hour = $telemetry.units_per_hour; current_batch_eta_seconds = $telemetry.current_batch_eta_seconds
        batch_started_at = $telemetry.batch_started_at; batch_last_sealed_at = $telemetry.batch_last_sealed_at; units_completed = $telemetry.units_completed; checkpoint_events = @($telemetry.events); worker_startup_duration_seconds = $telemetry.worker_startup_duration_seconds; worker_duration_seconds = $telemetry.worker_duration_seconds
    }
    $temporary = "$metricsPath.$PID.tmp"
    $backup = "$metricsPath.$PID.bak"
    [System.IO.File]::WriteAllText($temporary, (($record | ConvertTo-Json -Depth 5) + "`n"), $utf8NoBom)
    if (Test-Path -LiteralPath $metricsPath) { if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }; [System.IO.File]::Replace($temporary, $metricsPath, $backup); if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force } } else { [System.IO.File]::Move($temporary, $metricsPath) }
}

function Invoke-AuthoritativeCursorDecision($Before, $After, $Evidence) {
    $started = Get-Date
    $decision = Invoke-CursorDecisionAdapter $Before $After $Evidence
    $script:cursorDecisionCount++
    $script:cursorDecisionTotalMilliseconds += ((Get-Date) - $started).TotalMilliseconds
    return $decision
}

function Invoke-AuthoritativeHandoffDecision([string]$WorkerId, $Handoff, $After, $Facts) {
    $started = Get-Date
    $decision = Invoke-HandoffDecisionAdapter $WorkerId $Handoff $After $Facts
    $script:handoffDecisionCount++
    $script:handoffDecisionTotalMilliseconds += ((Get-Date) - $started).TotalMilliseconds
    return $decision
}

function Invoke-CurrentExecutionProjection([string]$Kind, $Facts) {
    # The projector is pure: facts are already-authoritative adapter outputs;
    # stdin is its sole input and it has no provider or persistence role.
    $payload = [ordered]@{ kind = $Kind; facts = $Facts } | ConvertTo-Json -Depth 12 -Compress
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @($payload | & $python $currentExecutionProjectorScript 2>&1)
        # The supervisor is script-scoped; read the native command result from
        # that scope rather than a stale global value inherited from refresh.
        $exit = $script:LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    if ($exit -ne 0) { throw "CURRENT_EXECUTION_PROJECTION_FAILED:${Kind}:$($output -join ' ')" }
    try { $result = (@($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1) | ConvertFrom-Json) } catch { throw "CURRENT_EXECUTION_PROJECTION_INVALID:$Kind" }
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace([string]$result.action)) { throw "CURRENT_EXECUTION_PROJECTION_INVALID:$Kind" }
    return $result
}

function Invoke-ControlPlaneShadow([string]$Adapter, $Payload, [string]$CurrentDecision) {
    # Shadow mode is opt-in (and always enabled for WhatIf).  The adapter gets
    # JSON facts via stdin and has neither filesystem nor process authority.
    if (-not ($WhatIf -or $ShadowControlPlane)) { return }
    $shadowStarted = Get-Date
    $json = $Payload | ConvertTo-Json -Depth 12 -Compress
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @($json | & $python $controlPlaneShadowScript --adapter $Adapter 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    $text = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    try { $actual = $text | ConvertFrom-Json } catch { throw "CONTROL_PLANE_SHADOW_INVALID_OUTPUT:$Adapter" }
    if ($exit -ne 0 -or [string]$actual.decision -ne $CurrentDecision) {
        throw "CONTROL_PLANE_SHADOW_DIFF adapter=$Adapter current=$CurrentDecision actual=$($actual.decision)"
    }
    $latencyMs = [math]::Round(((Get-Date) - $shadowStarted).TotalMilliseconds, 3)
    Write-Log "SHADOW_DECISION adapter=$Adapter decision=$CurrentDecision latency_ms=$latencyMs"
}

function Refresh-State {
    # $LASTEXITCODE can retain BatchReady's nonzero result when this rebuild
    # emits no output.  Use the rebuild process's own exit code and streams.
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo.FileName = $python
    $process.StartInfo.Arguments = ('"{0}" --run "{1}" --source "{2}"' -f $stateScript, $run, $source)
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.CreateNoWindow = $true
    try {
        if (-not $process.Start()) { throw 'CAMPAIGN_STATE_REBUILD_PROCESS_START_FAILED' }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $validationExit = $process.ExitCode
    } finally {
        $process.Dispose()
    }
    if ($validationExit -ne 0) {
        # A failed derived projection is not evidence that immutable artifacts
        # are corrupt; preserve the exact child diagnostics for classification.
        throw "CAMPAIGN_STATE_VALIDATION_FAILED:exit=$validationExit stdout=[$stdout] stderr=[$stderr]"
    }
    return Get-Content -LiteralPath (Join-Path $run "production_campaign_state.json") -Raw -Encoding utf8 | ConvertFrom-Json
}

function Test-RefreshFailureIsAuthoritativeIntegrity([string]$Detail) {
    # These are the rebuild validator's immutable/source/seal/manifest rules.
    # An unknown or empty refresh error remains a derived-state failure until
    # an authoritative validator proves otherwise.
    return $Detail -match '(AUTHORITATIVE_PROGRESS|ACTIVE_MANIFEST|ACTIVE_SELECTION|CHECKPOINT_|SEALED_CHECKPOINT|BATCH_MANIFEST|D050_|SOURCE_SHA|CANONICAL_CLOSE|TERMINAL_BATCH)'
}

function Invoke-BatchReady {
    # This validator is a child-process boundary.  Do not use $LASTEXITCODE:
    # it is ambient PowerShell state and may describe an earlier native call.
    # Capture the PID, streams, and exit code from the same Process instance.
    $process = [System.Diagnostics.Process]::new()
    try {
        # Batch readiness is an immutable-state validator.  D050 proof stays
        # checkpoint-bounded: the existing worker preflight prepares exact
        # evidence only for the frozen slice it is about to process.  Eagerly
        # traversing every future checkpoint here can execute unbounded PDF
        # work before any writer starts and is neither a readiness proof for
        # the current cursor nor an allowed whole-book D050 audit.
        $batchReadyArgs = @('--run', $run, '--source', $source, '--allow-current-partial')
        $process.StartInfo.FileName = $python
        $quotedArguments = @((@($batchReadyScript) + $batchReadyArgs) | ForEach-Object { ConvertTo-ChildCommandLineArgument ([string]$_) })
        $process.StartInfo.Arguments = [string]::Join(' ', [string[]]$quotedArguments)
        $process.StartInfo.WorkingDirectory = $Root
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $process.StartInfo.CreateNoWindow = $true
        if (-not $process.Start()) { throw 'BATCH_READY_PROCESS_START_FAILED' }
        $processId = [int]$process.Id
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exit = [int]$process.ExitCode
    } finally {
        $process.Dispose()
    }
    $reportsPass = $stdout -match '(?m)^BATCH_READY PASS batch='
    if (([bool]($exit -eq 0)) -ne [bool]$reportsPass) {
        throw "BATCH_READY_PROCESS_CONTRADICTION:exit=$exit stdout=[$stdout] stderr=[$stderr]"
    }
    return ,([pscustomobject]@{
        ProcessId = $processId
        ExitCode = $exit
        Passed = [bool]($exit -eq 0)
        Detail = ("$stdout $stderr").Trim()
        Stdout = $stdout
        Stderr = $stderr
    })
}

function ConvertTo-CompactJson($Value) {
    if ($null -eq $Value) { return "null" }
    return ($Value | ConvertTo-Json -Depth 12 -Compress)
}

function Get-CheckpointDirectory($State) {
    if (-not $State.active_batch -or -not $State.next_checkpoint) { return $null }
    $batchMatch = [regex]::Match([string]$State.active_batch, "FULL_BOOK_BATCH_(\d{3})$")
    if (-not $batchMatch.Success) { return $null }
    return Join-Path (Join-Path $run ("full_book_batch_{0}" -f $batchMatch.Groups[1].Value)) $State.next_checkpoint
}

function Test-NoPartialCheckpointArtifact($State) {
    $checkpointDirectory = Get-CheckpointDirectory $State
    if ($checkpointDirectory -and (Test-Path -LiteralPath $checkpointDirectory)) {
        if (Test-CheckpointIsSealed ([string]$State.active_batch) ([string]$State.next_checkpoint)) {
            return [pscustomobject]@{ Safe = $false; Reason = "SEALED_CHECKPOINT_STATE_DRIFT:$checkpointDirectory" }
        }
        return [pscustomobject]@{ Safe = $false; Reason = "PARTIAL_OR_UNSEALED_CHECKPOINT_CONFLICT:$checkpointDirectory" }
    }
    return [pscustomobject]@{ Safe = $true; Reason = $null }
}

function Test-CurrentCanonicalRecoveryResumeEligible($State) {
    # The atomically rebuilt campaign state is the current execution
    # projection.  A handoff and the legacy recovery projector are wake-up
    # evidence only; neither may resurrect a recovery after that projection
    # has consumed it into debt or another terminal.
    if (@($State.recovery_queue).Count -eq 0) {
        return [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_RECOVERY_OBLIGATION' }
    }
    $partial = Get-CheckpointDirectory $State
    if (-not $partial -or -not (Test-Path -LiteralPath $partial) -or (Test-CheckpointIsSealed ([string]$State.active_batch) ([string]$State.next_checkpoint))) { return [pscustomobject]@{ Passed = $false; Detail = 'CANONICAL_RECOVERY_PARTIAL_INVALID' } }
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $python $canonicalRecoveryProjectorScript --run $run --source $source --batch ([string]$State.active_batch) --checkpoint ([string]$State.next_checkpoint) 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    if ($exit -ne 0) { return [pscustomobject]@{ Passed = $false; Detail = "CANONICAL_RECOVERY_PROJECTION_FAILED:$($output -join ' ')" } }
    try { $record = (@($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1) | ConvertFrom-Json).recovery } catch { return [pscustomobject]@{ Passed = $false; Detail = 'CANONICAL_RECOVERY_PROJECTION_INVALID' } }
    if ($null -eq $record) { return [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_CANONICAL_RECOVERY_PROJECTION' } }
    $fingerprint = $record.recovery_fingerprint
    if ([string]$record.production_disposition -ne 'RECOVERY_REQUIRED' -or [string]::IsNullOrWhiteSpace([string]$record.recovery_family) -or $null -eq $fingerprint -or [string]$fingerprint.batch -ne [string]$State.active_batch -or [string]$fingerprint.checkpoint -ne [string]$State.next_checkpoint -or [string]::IsNullOrWhiteSpace([string]$fingerprint.current_head_sha256) -or [string]::IsNullOrWhiteSpace([string]$fingerprint.canonical_reason)) { return [pscustomobject]@{ Passed = $false; Detail = 'CANONICAL_RECOVERY_PROJECTION_CONTRACT_INVALID' } }
    return [pscustomobject]@{ Passed = $true; Detail = "CURRENT_CANONICAL_RECOVERY:$($State.next_checkpoint)"; Projection = $record }
}

function Resume-PreparedCheckpointCommit($State) {
    # A checkpoint evidence/final-QA pair can exist after the worker's
    # prepare/validate phase while the derived manifest publication was
    # interrupted.  Re-run only the deterministic manifest commit; it does
    # not launch a worker or recreate provider/QA/Human evidence.
    $checkpointDirectory = Get-CheckpointDirectory $State
    if (-not $checkpointDirectory) { return [pscustomobject]@{ Attempted = $false; Committed = $false; Detail = $null } }
    $evidence = Join-Path $checkpointDirectory 'checkpoint_evidence.json'
    $finalQa = Join-Path $checkpointDirectory 'translation_final_qa_report.json'
    if (-not (Test-Path -LiteralPath $evidence) -or -not (Test-Path -LiteralPath $finalQa)) {
        return [pscustomobject]@{ Attempted = $false; Committed = $false; Detail = $null }
    }
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $python $stateScript --run $run --source $source --commit-prepared-checkpoint ([string]$State.next_checkpoint) 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    if ($exit -ne 0) { throw "PREPARED_CHECKPOINT_COMMIT_FAILED:$($output -join ' ')" }
    $record = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() -and $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    try { $result = if ($record.Count) { $record[0] | ConvertFrom-Json } else { $null } } catch { throw 'PREPARED_CHECKPOINT_COMMIT_INVALID_OUTPUT' }
    if ($null -eq $result -or $result.prepared_checkpoint_commit -ne [string]$State.next_checkpoint) { throw 'PREPARED_CHECKPOINT_COMMIT_INVALID_OUTPUT' }
    return [pscustomobject]@{ Attempted = $true; Checkpoint = [string]$result.prepared_checkpoint_commit; Committed = [bool]$result.committed; Detail = $result.manifest }
}

function Invoke-BatchBoundaryRelease($State) {
    if ([string]$State.campaign_status -ne 'BATCH_BOUNDARY_READY') { return $null }
    $batch = [string]$State.active_batch
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $python $releaseBoundaryScript --batch $batch --apply --authorize-release-boundary-write 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    if ($exit -ne 0) { throw "RELEASE_BOUNDARY_RECONCILIATION_FAILED:$($output -join ' ')" }
    $json = @($output | ForEach-Object { $_.ToString() }) -join "`n"
    try { $result = $json | ConvertFrom-Json } catch { throw 'RELEASE_BOUNDARY_INVALID_OUTPUT' }
    if ($null -eq $result -or $result.mode -ne 'APPLY' -or $null -eq $result.gate) { throw 'RELEASE_BOUNDARY_INVALID_OUTPUT' }
    if ($result.gate.verdict -eq 'BLOCKED_HUMAN_ADJUDICATION_REQUIRED') {
        return [pscustomobject]@{ Verdict = 'BLOCKED_HUMAN_ADJUDICATION_REQUIRED'; Result = $result }
    }
    if ($result.gate.verdict -ne 'PASS') { throw "RELEASE_BOUNDARY_GATE_INVALID:$($result.gate.verdict)" }

    # An APPLY gate records the release-resolution attempt, but canonical close
    # may consume only the provider-free, current-state-bound FINALIZE_ONLY
    # projection.  In particular, an initial v1 gate has no such binding.
    try {
        $ErrorActionPreference = 'Continue'
        $finalizeOutput = @(& $python $releaseBoundaryScript --batch $batch --finalize-only --authorize-release-boundary-write 2>&1)
        $finalizeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    if ($finalizeExit -ne 0) { throw "RELEASE_BOUNDARY_FINALIZE_FAILED:$($finalizeOutput -join ' ')" }
    $finalizeJson = @($finalizeOutput | ForEach-Object { $_.ToString() }) -join "`n"
    try { $finalized = $finalizeJson | ConvertFrom-Json } catch { throw 'RELEASE_BOUNDARY_FINALIZE_INVALID_OUTPUT' }
    if ($null -eq $finalized -or $finalized.mode -ne 'FINALIZE_ONLY' -or $null -eq $finalized.gate) { throw 'RELEASE_BOUNDARY_FINALIZE_INVALID_OUTPUT' }
    if ($finalized.gate.verdict -eq 'PASS') {
        $number = [regex]::Match($batch, 'FULL_BOOK_BATCH_(\d{3})$').Groups[1].Value
        if (-not $number) { throw 'RELEASE_BOUNDARY_BATCH_ID_INVALID' }
        & $python $terminalClosureScript --batch ([int]$number) 2>&1 | ForEach-Object { Write-Log "CANONICAL_CLOSURE detail=$($_.ToString())" }
        if ($LASTEXITCODE -ne 0) { throw 'CANONICAL_CLOSURE_FAILED' }
        return [pscustomobject]@{ Verdict = 'PASS'; Result = $finalized }
    }
    if ($finalized.gate.verdict -eq 'BLOCKED_HUMAN_ADJUDICATION_REQUIRED') {
        return [pscustomobject]@{ Verdict = 'BLOCKED_HUMAN_ADJUDICATION_REQUIRED'; Result = $finalized }
    }
    throw "RELEASE_BOUNDARY_FINALIZE_GATE_INVALID:$($finalized.gate.verdict)"
}

function Test-ReviewerContractResumeEligible($State) {
    # One exactly-bound reviewer-only retry may resume the preserved RAW
    # partial. A legacy model UID echo mismatch is non-authoritative only when
    # the locally recorded request/candidate/context binding is complete.
    $checkpointDirectory = Get-CheckpointDirectory $State
    if (-not $checkpointDirectory -or -not (Test-Path -LiteralPath $checkpointDirectory)) { return [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_PARTIAL' } }
    $rawPath = Join-Path $checkpointDirectory 'translation_raw_response.jsonl'
    $requestPath = Join-Path $checkpointDirectory 'translation_request.jsonl'
    $evidenceDirectory = Join-Path $checkpointDirectory 'reviewer_contract_failures'
    if (-not (Test-Path -LiteralPath $rawPath) -or -not (Test-Path -LiteralPath $requestPath) -or -not (Test-Path -LiteralPath $evidenceDirectory)) { return [pscustomobject]@{ Passed = $false; Detail = 'REVIEWER_CONTRACT_RESUME_EVIDENCE_MISSING' } }
    $records = @(Get-ChildItem -LiteralPath $evidenceDirectory -File -Filter '*.json' | Sort-Object Name)
    if ($records.Count -ne 1) { return [pscustomobject]@{ Passed = $false; Detail = 'REVIEWER_CONTRACT_RETRY_BUDGET_EXHAUSTED' } }
    try { $record = Get-Content -LiteralPath $records[0].FullName -Raw -Encoding utf8 | ConvertFrom-Json } catch { return [pscustomobject]@{ Passed = $false; Detail = 'REVIEWER_CONTRACT_EVIDENCE_INVALID' } }
    $resumableCodes = @('REVIEWER_CONTRACT_GAP_ACCEPT_WITH_ISSUES', 'REVIEWER_CONTRACT_GAP_UNIT_UID_MISMATCH')
    if ($record.artifact_type -ne 'PRODUCTION_REVIEWER_CONTRACT_FAILURE' -or $record.contract_error_code -notin $resumableCodes -or [int]$record.reviewer_contract_attempt -ne 1) { return [pscustomobject]@{ Passed = $false; Detail = 'REVIEWER_CONTRACT_EVIDENCE_INVALID' } }
    if (-not $record.display_id -or -not $record.unit_uid -or -not $record.request_binding_sha256 -or -not $record.candidate_binding_sha256 -or -not $record.review_request_sha256) { return [pscustomobject]@{ Passed = $false; Detail = 'REVIEWER_CONTRACT_BINDING_MISSING' } }
    return [pscustomobject]@{ Passed = $true; Detail = "REVIEWER_CONTRACT_RESUME:$($record.display_id)"; EvidencePath = $records[0].FullName }
}

function Test-Cp04SemanticRescueEligible($State) {
    # This is intentionally narrower than a general partial-checkpoint bypass:
    # it opens only the known B022/cp04 HUMAN_REQUIRED savepoint and the worker
    # still performs its own exact binding and rescue-budget checks.
    if (-not $EnableCp04SemanticRescue) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_NOT_ENABLED' } }
    if ([string]$State.active_batch -ne 'FULL_BOOK_BATCH_022' -or [string]$State.next_checkpoint -ne 'checkpoint_04' -or [int]$State.completed_count -ne 75 -or [string]$State.next_logical_work_order_id -ne 'body.p.06986') { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_CURSOR_MISMATCH' } }
    $checkpointDirectory = Get-CheckpointDirectory $State
    if (-not $checkpointDirectory -or -not (Test-Path -LiteralPath $checkpointDirectory)) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_PARTIAL_MISSING' } }
    if (Test-Path -LiteralPath (Join-Path $checkpointDirectory 'checkpoint_evidence.json')) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_CHECKPOINT_ALREADY_SEALED' } }
    $rawPath = Join-Path $checkpointDirectory 'translation_raw_response.jsonl'
    $requestPath = Join-Path $checkpointDirectory 'translation_request.jsonl'
    if (-not (Test-Path -LiteralPath $rawPath) -or -not (Test-Path -LiteralPath $requestPath)) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_RAW_OR_REQUEST_MISSING' } }
    $rawLines = @(Get-Content -LiteralPath $rawPath -Encoding utf8 | Where-Object { $_.Trim() })
    if ($rawLines.Count -ne 25) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_RAW_COUNT_MISMATCH' } }
    $expected = @('body.p.06990','body.p.06994','body.p.06996','body.p.06997','body.p.06998','body.p.07005','body.p.07007','body.p.07018')
    $staleRereview = @('body.p.06990','body.p.06996','body.p.06998','body.p.07007','body.p.07018')
    $unitsDirectory = Join-Path $checkpointDirectory 'units'
    $reprocessDirectory = Join-Path $checkpointDirectory 'production_reprocess'
    if (-not (Test-Path -LiteralPath $unitsDirectory) -or -not (Test-Path -LiteralPath $reprocessDirectory)) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_UNIT_EVIDENCE_MISSING' } }
    $actual = @(
        Get-ChildItem -LiteralPath $unitsDirectory -Filter '*.json' -File | ForEach-Object {
            try {
                $unit = Get-Content -LiteralPath $_.FullName -Raw -Encoding utf8 | ConvertFrom-Json
                $reprocessPath = Join-Path $reprocessDirectory $_.Name
                $terminal = [string]$unit.semantic_terminal
                if (Test-Path -LiteralPath $reprocessPath) { $terminal = [string]((Get-Content -LiteralPath $reprocessPath -Raw -Encoding utf8 | ConvertFrom-Json).semantic_terminal) }
                if ($terminal -eq 'HUMAN_REQUIRED') { $_.BaseName }
            } catch { $null }
        } | Where-Object { $_ } | Sort-Object
    )
    if ((@($actual) -join ',') -eq (@($expected | Sort-Object) -join ',')) { return [pscustomobject]@{ Passed = $true; Detail = 'CP04_BOUND_SEMANTIC_RESCUE' } }
    $postRepairExpected = @('body.p.06990','body.p.06996','body.p.06997','body.p.06998','body.p.07007','body.p.07018')
    if ((@($actual) -join ',') -ne (@($postRepairExpected | Sort-Object) -join ',')) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_RESCUE_ELIGIBLE_SET_MISMATCH' } }
    foreach ($id in $staleRereview) {
        if (-not (Test-Path -LiteralPath (Join-Path $checkpointDirectory ("semantic_rescue\\{0}.json" -f $id))) -or (Test-Path -LiteralPath (Join-Path $checkpointDirectory ("semantic_rescue_rereviews\\{0}.json" -f $id)))) { return [pscustomobject]@{ Passed = $false; Detail = 'SEMANTIC_REREVIEW_BUDGET_INVALID' } }
    }
    return [pscustomobject]@{ Passed = $true; Detail = 'CP04_BOUND_STALE_FINAL_REREVIEW' }
}

function Test-SafeTransientRetry($Before, $After) {
    if ($After.campaign_status -ne "CONTINUE") { return [pscustomobject]@{ Safe = $false; Reason = "CAMPAIGN_STATUS:$($After.campaign_status)" } }
    if ($Before.active_batch -ne $After.active_batch) { return [pscustomobject]@{ Safe = $false; Reason = "ACTIVE_BATCH_CHANGED" } }
    if ($Before.next_checkpoint -ne $After.next_checkpoint) { return [pscustomobject]@{ Safe = $false; Reason = "NEXT_CHECKPOINT_CHANGED" } }
    if ($Before.next_logical_work_order_id -ne $After.next_logical_work_order_id) { return [pscustomobject]@{ Safe = $false; Reason = "NEXT_WORK_ORDER_CHANGED" } }
    if ($Before.completed_count -ne $After.completed_count -or (ConvertTo-CompactJson $Before.completed_checkpoints) -ne (ConvertTo-CompactJson $After.completed_checkpoints)) { return [pscustomobject]@{ Safe = $false; Reason = "SEALED_PROGRESS_CHANGED" } }
    if ($Before.latest_authoritative_progress.path -ne $After.latest_authoritative_progress.path -or $Before.latest_authoritative_progress.sha256 -ne $After.latest_authoritative_progress.sha256) { return [pscustomobject]@{ Safe = $false; Reason = "AUTHORITATIVE_PROGRESS_CHANGED" } }
    if ($Before.source_sha256 -ne $After.source_sha256) { return [pscustomobject]@{ Safe = $false; Reason = "SOURCE_SHA_CHANGED" } }
    if ((ConvertTo-CompactJson $Before.recovery_queue) -ne (ConvertTo-CompactJson $After.recovery_queue)) { return [pscustomobject]@{ Safe = $false; Reason = "RECOVERY_QUEUE_CHANGED" } }
    if (-not $script:ownsLock -or -not (Test-Path -LiteralPath $lockPath)) { return [pscustomobject]@{ Safe = $false; Reason = "WRITER_OWNERSHIP_LOST" } }
    return Test-NoPartialCheckpointArtifact $After
}

function Test-WorkerMadeProgress($Before, $After) {
    if ($Before.active_batch -ne $After.active_batch) { return $true }
    if ($Before.completed_count -ne $After.completed_count) { return $true }
    if ($Before.next_checkpoint -ne $After.next_checkpoint) { return $true }
    if ($Before.next_logical_work_order_id -ne $After.next_logical_work_order_id) { return $true }
    if ($Before.latest_authoritative_progress.path -ne $After.latest_authoritative_progress.path -or $Before.latest_authoritative_progress.sha256 -ne $After.latest_authoritative_progress.sha256) { return $true }
    return (ConvertTo-CompactJson $Before.completed_checkpoints) -ne (ConvertTo-CompactJson $After.completed_checkpoints)
}

function Test-ModelRefreshTimeout([string]$WorkerLog, [string]$ExceptionText) {
    $text = $ExceptionText
    if (Test-Path -LiteralPath $WorkerLog) { $text = "$text`n$(Get-Content -LiteralPath $WorkerLog -Raw -Encoding utf8)" }
    return $text -match "(?s)codex_models_manager::manager:\s*failed to refresh available models:\s*timeout waiting for child process to exit"
}

function Test-ProviderTransient([string]$WorkerLog, [string]$ExceptionText) {
    $text = $ExceptionText
    if (Test-Path -LiteralPath $WorkerLog) { $text = "$text`n$(Get-Content -LiteralPath $WorkerLog -Raw -Encoding utf8)" }
    return $text -match "PROVIDER_TRANSIENT"
}

function Rotate-WorkerHandoff([string]$Reason) {
    if (-not (Test-Path -LiteralPath $workerHandoffPath)) { return }
    $archive = Join-Path $logDir ("worker-handoff-{0}-{1}.json" -f (Get-Date -Format "yyyyMMddTHHmmssfff"), $Reason)
    Move-Item -LiteralPath $workerHandoffPath -Destination $archive -ErrorAction Stop
    Write-Log "HANDOFF_ROTATED reason=$Reason archive=$archive"
}

function Get-CheckpointNumber([string]$Checkpoint) {
    $match = [regex]::Match($Checkpoint, "^checkpoint_(\d{2})$")
    if (-not $match.Success) { return $null }
    return [int]$match.Groups[1].Value
}

function Test-CheckpointIsSealed([string]$BatchLabel, [string]$Checkpoint) {
    $batchMatch = [regex]::Match($BatchLabel, "^FULL_BOOK_BATCH_(\d{3})$")
    if (-not $batchMatch.Success) { return $false }
    $batchPath = Join-Path $run ("full_book_batch_{0}" -f $batchMatch.Groups[1].Value)
    $manifestPath = Join-Path $batchPath "batch_manifest.json"
    $checkpointPath = Join-Path $batchPath $Checkpoint
    if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath (Join-Path $checkpointPath "checkpoint_evidence.json")) -or -not (Test-Path -LiteralPath (Join-Path $checkpointPath "translation_final_qa_report.json"))) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
        return @($manifest.checkpoints | Where-Object { [string]$_.id -eq $Checkpoint }).Count -eq 1
    } catch { return $false }
}

function Test-HandoffWrittenDuringWorker($Handoff, [datetime]$WorkerStartedAt) {
    $rawTimestamp = if ($null -ne $Handoff.created_at) { [string]$Handoff.created_at } else { [string]$Handoff.timestamp }
    if ([string]::IsNullOrWhiteSpace($rawTimestamp)) { return $false }
    try { $writtenAt = [datetimeoffset]::Parse($rawTimestamp).UtcDateTime } catch { return $false }
    $earliest = $WorkerStartedAt.ToUniversalTime().AddMinutes(-1)
    $latest = (Get-Date).ToUniversalTime().AddMinutes(1)
    return $writtenAt -ge $earliest -and $writtenAt -le $latest
}

function Normalize-RecoveryHandoff($After) {
    # This is deliberately deterministic and read-only.  It can fill only the
    # documented fields that the unique current partial checkpoint proves; the
    # original worker handoff remains the operational record and is rotated as
    # usual after it is consumed.
    $arguments = @(
        $recoveryHandoffNormalizationScript,
        '--handoff', $workerHandoffPath,
        '--state', (Join-Path $run 'production_campaign_state.json'),
        '--source-sha256', [string]$After.source_sha256,
        '--run', $run
    )
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $python @arguments 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    $text = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    try { $value = $text | ConvertFrom-Json } catch {
        return [pscustomobject]@{ Normalized = $false; Detail = 'RECOVERY_HANDOFF_NORMALIZATION_INVALID_OUTPUT'; Value = $null; DerivedFields = @() }
    }
    if ($exit -ne 0 -or $value.normalized -ne $true) {
        $detail = if ($value.reason) { "RECOVERY_HANDOFF_UNRESOLVABLE:$($value.reason)" } else { 'RECOVERY_HANDOFF_UNRESOLVABLE' }
        return [pscustomobject]@{ Normalized = $false; Detail = $detail; Value = $null; DerivedFields = @() }
    }
    return [pscustomobject]@{ Normalized = $true; Detail = $null; Value = $value.handoff; DerivedFields = @($value.derived_fields) }
}

function Get-RecoveryHandoffMissingFields($Handoff) {
    if ([string]$Handoff.failure_classification -eq 'EVIDENCE_PROVIDER_UNAVAILABLE') {
        $precheckpoint = @('stage_reached', 'failure_classification', 'failing_work_order_ids', 'relevant_qa_artifact_paths', 'raw_exists', 'created_at', 'evidence_requirement')
        return @($precheckpoint | Where-Object {
            $value = $Handoff.$_
            $null -eq $value -or ($value -is [string] -and [string]::IsNullOrWhiteSpace($value))
        })
    }
    $required = @('partial_checkpoint_path', 'stage_reached', 'failure_classification', 'failing_work_order_ids', 'relevant_qa_artifact_paths', 'raw_exists', 'created_at')
    return @($required | Where-Object {
        $value = $Handoff.$_
        $null -eq $value -or ($value -is [string] -and [string]::IsNullOrWhiteSpace($value))
    })
}

function Get-HandoffAdapterFacts($Handoff, [string]$WorkerId, $Before, $After, [datetime]$WorkerStartedAt, [bool]$WorkerMadeProgress) {
    $checkpoint = Get-HandoffAdapterCheckpoint $Handoff
    if ([string]::IsNullOrWhiteSpace([string]$Handoff.checkpoint) -and [string]$Handoff.status -eq 'CONTINUE' -and $checkpoint) {
        $Handoff | Add-Member -NotePropertyName checkpoint -NotePropertyValue $checkpoint -Force
    }
    $checkpointNumber = Get-CheckpointNumber $checkpoint
    $afterCheckpointNumber = Get-CheckpointNumber ([string]$After.next_checkpoint)
    $partialFact = Test-NoPartialCheckpointArtifact $After
    $partialKind = if ($partialFact.Reason -match '^SEALED_CHECKPOINT_STATE_DRIFT') { 'SEALED' } elseif (-not $partialFact.Safe) { 'PARTIAL' } else { 'NONE' }
    $expectedPartial = Get-CheckpointDirectory $After
    $partialPathMatches = $false
    if ($expectedPartial -and -not [string]::IsNullOrWhiteSpace([string]$Handoff.partial_checkpoint_path)) {
        try { $partialPathMatches = [IO.Path]::GetFullPath([string]$Handoff.partial_checkpoint_path) -eq [IO.Path]::GetFullPath($expectedPartial) } catch { $partialPathMatches = $false }
    }
    return [ordered]@{
        source_sha_unchanged = ([string]$Before.source_sha256 -eq [string]$After.source_sha256)
        worker_made_progress = $WorkerMadeProgress; launch_batch = $Before.active_batch
        current_partial = $partialKind
        checkpoint_format_valid = ($null -ne $checkpointNumber)
        checkpoint_is_sealed = if ($checkpoint -and $Handoff.active_batch) { Test-CheckpointIsSealed ([string]$Handoff.active_batch) $checkpoint } else { $false }
        handoff_checkpoint_is_future = ($null -ne $checkpointNumber -and $null -ne $afterCheckpointNumber -and $checkpointNumber -gt $afterCheckpointNumber)
        partial_path_matches = $partialPathMatches
        handoff_written_during_worker = Test-HandoffWrittenDuringWorker $Handoff $WorkerStartedAt
        recovery_fields_complete = ((Get-RecoveryHandoffMissingFields $Handoff).Count -eq 0)
        precheckpoint_evidence_recovery = ([string]$Handoff.failure_classification -eq 'EVIDENCE_PROVIDER_UNAVAILABLE' -and [string]::IsNullOrWhiteSpace([string]$Handoff.partial_checkpoint_path) -and $Handoff.raw_exists -eq $false)
    }
}

function Get-CurrentWorkerHandoff([string]$WorkerId, $Before, $After, [datetime]$WorkerStartedAt, [bool]$WorkerMadeProgress) {
    if (-not (Test-Path -LiteralPath $workerHandoffPath)) {
        return [pscustomobject]@{ Kind = 'MISSING'; Detail = $null; Value = $null; Decision = 'ACCEPT_CONTINUE'; Normalized = $false; DerivedFields = @() }
    }
    try { $handoff = Get-Content -LiteralPath $workerHandoffPath -Raw -Encoding utf8 | ConvertFrom-Json } catch {
        return [pscustomobject]@{ Kind = 'INVALID'; Detail = 'HANDOFF_INVALID_JSON'; Value = $null; Decision = 'BLOCK'; Normalized = $false; DerivedFields = @() }
    }
    $facts = Get-HandoffAdapterFacts $handoff $WorkerId $Before $After $WorkerStartedAt $WorkerMadeProgress
    $decision = Invoke-AuthoritativeHandoffDecision $WorkerId $handoff $After $facts
    $normalized = $false; $derivedFields = @()
    if ($decision.decision -eq 'NORMALIZE') {
        $normalization = Normalize-RecoveryHandoff $After
        if (-not $normalization.Normalized) {
            $script:recoveryHandoffNormalizationFailureCount++
            return [pscustomobject]@{ Kind = 'INVALID'; Detail = $normalization.Detail; Value = $handoff; Decision = 'BLOCK'; Normalized = $false; DerivedFields = @() }
        }
        $handoff = $normalization.Value; $normalized = $true; $derivedFields = @($normalization.DerivedFields); $script:recoveryHandoffNormalizationCount++
        $facts = Get-HandoffAdapterFacts $handoff $WorkerId $Before $After $WorkerStartedAt $WorkerMadeProgress
        $decision = Invoke-AuthoritativeHandoffDecision $WorkerId $handoff $After $facts
        if ($decision.decision -eq 'NORMALIZE') {
            $script:recoveryHandoffNormalizationFailureCount++
            return [pscustomobject]@{ Kind = 'INVALID'; Detail = 'RECOVERY_HANDOFF_UNRESOLVABLE:REQUIRED_FIELDS_STILL_MISSING'; Value = $handoff; Decision = 'BLOCK'; Normalized = $false; DerivedFields = @() }
        }
    }
    $kind = switch ([string]$decision.decision) {
        'ACCEPT_CONTINUE' { 'CURRENT' }
        'TRIAGE' { 'CURRENT' }
        'STALE_ROTATE' { 'STALE' }
        'BLOCK' { if ([string]$handoff.status -eq 'BLOCKED' -and [string]$decision.reason -eq 'CURRENT_WORKER_BLOCKED') { 'CURRENT' } else { 'INVALID' } }
        default { 'INVALID' }
    }
    return [pscustomobject]@{ Kind = $kind; Detail = [string]$decision.reason; Value = $handoff; Decision = [string]$decision.decision; Facts = $facts; Normalized = $normalized; DerivedFields = $derivedFields }
}

function Set-RecoveryInfo($Handoff, [string]$Classification) {
    $script:recoveryInfo = [ordered]@{
        worker_id = $Handoff.worker_id
        batch = $Handoff.active_batch
        checkpoint = $Handoff.checkpoint
        next_logical_work_order_id = $Handoff.next_logical_work_order_id
        partial_checkpoint_path = $Handoff.partial_checkpoint_path
        stage_reached = $Handoff.stage_reached
        failure_classification = $Classification
        failing_work_order_ids = @($Handoff.failing_work_order_ids)
        relevant_qa_artifact_paths = @($Handoff.relevant_qa_artifact_paths)
        raw_exists = $Handoff.raw_exists
        raw_sha256 = $Handoff.raw_sha256
        evidence_requirement = $Handoff.evidence_requirement
        created_at = $Handoff.created_at
    }
}

function Get-CanonicalRecoveryFingerprint($Handoff) {
    # Handoff metadata is operational only.  It carries the already-computed
    # canonical decision and lets the supervisor fail closed if a fresh worker
    # makes no authoritative recovery transition.
    $fingerprint = $Handoff.recovery_fingerprint
    if ($fingerprint -and $fingerprint.batch -and $fingerprint.checkpoint -and $fingerprint.work_order -and $fingerprint.current_head_sha256 -and $fingerprint.recovery_family -and $fingerprint.canonical_reason) {
        return ($fingerprint | ConvertTo-Json -Compress -Depth 4)
    }
    return @(
        'LEGACY', [string]$Handoff.active_batch, [string]$Handoff.checkpoint,
        (@($Handoff.failing_work_order_ids) -join ','), [string]$Handoff.recovery_family,
        [string]$Handoff.disposition_reason
    ) -join '|'
}

function Invoke-FinalQaTriageEvidence($Handoff, $State) {
    $arguments = @(
        $finalQaTriageScript,
        '--checkpoint', [string]$Handoff.partial_checkpoint_path,
        '--expected-raw-sha256', [string]$Handoff.raw_sha256,
        '--expected-source-sha256', [string]$State.source_sha256,
        '--actual-source-sha256', [string]$State.source_sha256
    )
    foreach ($workOrder in @($Handoff.failing_work_order_ids)) { $arguments += @('--failing-work-order-id', [string]$workOrder) }
    $priorEap = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; $output = @(& $python @arguments 2>&1); $exit = $LASTEXITCODE } finally { $ErrorActionPreference = $priorEap }
    $text = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    try { $value = $text | ConvertFrom-Json } catch { return [pscustomobject]@{ Valid = $false; Value = $null; Reason = 'FINAL_QA_TRIAGE_INVALID' } }
    if ($exit -ne 0 -or $value.eligible -ne $true) {
        $reason = if ($value.reason) { [string]$value.reason } else { 'FINAL_QA_TRIAGE_NOT_ELIGIBLE' }
        return [pscustomobject]@{ Valid = $false; Value = $value; Reason = $reason }
    }
    return [pscustomobject]@{ Valid = $true; Value = $value; Reason = $null }
}

function Get-ValidatedRecoveryPolicyEvidence($Handoff, $State) {
    $handoffJson = $Handoff | ConvertTo-Json -Depth 12 -Compress
    $priorEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @($handoffJson | & $python $recoveryEvidenceBuilderScript '--state' (Join-Path $run 'production_campaign_state.json') '--source' $source 2>&1)
        $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorEap }
    $text = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    try { $evidence = $text | ConvertFrom-Json } catch {
        return [pscustomobject]@{ failure_classification = [string]$Handoff.failure_classification; source_sha_valid = $false; raw_required = ($Handoff.raw_exists -eq $true); raw_sha_valid = $false; sparse_local = $false; qa_codes = @(); repair_contract = $null; exact_d050_binding = $false; validation_denial = 'RECOVERY_EVIDENCE_BUILDER_INVALID_OUTPUT' }
    }
    if ($exit -ne 0) { $evidence.validation_denial = 'RECOVERY_EVIDENCE_BUILDER_FAILED' }
    return $evidence
}

function Invoke-RecoveryPolicyRegistry($Evidence) {
    $started = Get-Date
    $json = $Evidence | ConvertTo-Json -Depth 12 -Compress
    $priorEap = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; $output = @($json | & $python $recoveryPolicyDecisionScript 2>&1); $exit = $LASTEXITCODE } finally { $ErrorActionPreference = $priorEap }
    $text = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    try { $decision = $text | ConvertFrom-Json } catch { $invalidLatency = [math]::Round(((Get-Date) - $started).TotalMilliseconds, 3); $script:policyDecisionCount++; $script:policyDecisionTotalMilliseconds += $invalidLatency; return [pscustomobject]@{ Allowed = $false; Reason = 'POLICY_REGISTRY_INVALID_OUTPUT'; Policy = $null; HandlerKind = $null; AttemptBudget = 0; RepairContract = $null; Family = 'UNKNOWN'; LatencyMs = $invalidLatency } }
    $latency = [math]::Round(((Get-Date) - $started).TotalMilliseconds, 3)
    $script:policyDecisionCount++; $script:policyDecisionTotalMilliseconds += $latency
    if ($exit -ne 0 -or $decision.eligible -ne $true) { return [pscustomobject]@{ Allowed = $false; Reason = [string]$decision.denial_reason; Policy = $null; HandlerKind = $null; AttemptBudget = 0; RepairContract = $null; Family = [string]$decision.family; Decision = $decision; LatencyMs = $latency } }
    return [pscustomobject]@{ Allowed = $true; Reason = $null; Policy = [string]$decision.policy_id; HandlerKind = [string]$decision.handler_kind; AttemptBudget = [int]$decision.attempt_budget; RepairContract = $decision.repair_contract; Family = [string]$decision.family; Decision = $decision; LatencyMs = $latency }
}

function Get-AutoRecoveryPolicy($Handoff, $State) {
    $evidence = Get-ValidatedRecoveryPolicyEvidence $Handoff $State
    if ($evidence.canonical_qa_failure) {
        $canonical = $evidence.canonical_qa_failure
        Write-Log "QA_FAILURE_CANONICALIZED stage=$($canonical.stage) qa_codes=$(@($canonical.qa_codes) -join ',') failing_count=$($canonical.failure_count) density=$($canonical.density) valid=$($canonical.valid)"
    }
    Write-Log "TRIAGE_EVIDENCE family_candidate=$($evidence.failure_family_candidate) qa_codes=$(@($evidence.qa_codes) -join ',') failing_count=$($evidence.failure_count) raw_sha_valid=$($evidence.raw_sha_valid) source_sha_valid=$($evidence.source_sha_valid)"
    $registry = Invoke-RecoveryPolicyRegistry $evidence
    Write-Log "REGISTRY_POLICY_DECISION family=$($registry.Family) policy=$($registry.Policy) eligible=$($registry.Allowed) latency_ms=$($registry.LatencyMs)"
    $qaTriage = if (@($evidence.qa_codes).Count -gt 0) { $evidence.repair_contract } else { $null }
    $registry | Add-Member -NotePropertyName Triage -NotePropertyValue $qaTriage -Force
    return $registry
}

  function Get-RuntimePolicyDecision([string]$Classification, $Safety) {
      $configuredBudget = if ($Classification -eq 'NO_PROGRESS_CLEAN_EXIT') { $MaxNoProgressRetries } else { $MaxTransientRuntimeRetries }
      # ConvertTo-Json serializes a one-item PowerShell array as a scalar.  The
      # registry contract deliberately requires a JSON list, including in
      # focused tests that use a zero-second one-item backoff.
      $configuredBackoff = [System.Collections.Generic.List[int]]::new()
      $configuredBackoffSource = if ($Classification -eq 'NO_PROGRESS_CLEAN_EXIT') { @($NoProgressRetryBackoffSeconds) } else { @($TransientRetryBackoffSeconds) }
      foreach ($seconds in $configuredBackoffSource) { $configuredBackoff.Add([int]$seconds) }
    $evidence = [ordered]@{
        failure_classification = $Classification
        source_sha_valid = $true
        raw_required = $false
        raw_sha_valid = $true
        safe_unchanged_cursor = ($Safety.Safe -eq $true)
        validation_denial = if ($Safety.Safe) { $null } else { [string]$Safety.Reason }
        attempt_budget_override = $configuredBudget
        retry_backoff_seconds = $configuredBackoff
      }
      $policy = Invoke-RecoveryPolicyRegistry $evidence
      Write-Log "REGISTRY_POLICY_DECISION family=$($policy.Family) policy=$($policy.Policy) eligible=$($policy.Allowed) latency_ms=$($policy.LatencyMs)"
    if ($WhatIf -or $ShadowControlPlane) {
        if (($policy.Allowed -eq $true) -ne ($Safety.Safe -eq $true)) { throw "RUNTIME_POLICY_SHADOW_DIFF classification=$Classification" }
        Write-Log "SHADOW_POLICY_DECISION eligible=$($policy.Allowed) policy=$($policy.Policy)"
    }
    return $policy
}

function Invoke-SupervisorRetryPolicy($Policy, $State) {
    if ($Policy.HandlerKind -ne 'SUPERVISOR_RETRY' -or $Policy.AttemptBudget -lt 1 -or -not $Policy.RepairContract) {
        return [pscustomobject]@{ Retry = $false; Detail = 'RUNTIME_POLICY_HANDLER_INVALID' }
    }
    $policyId = [string]$Policy.Policy
    $count = if ($script:runtimePolicyAttempts.ContainsKey($policyId)) { [int]$script:runtimePolicyAttempts[$policyId] + 1 } else { 1 }
    $script:runtimePolicyAttempts[$policyId] = $count
    $contract = $Policy.RepairContract
    $script:runtimePolicyLabels[$policyId] = [string]$contract.event_label
    $reason = [string]$contract.retry_reason
    if ($count -ge [int]$Policy.AttemptBudget) {
        Write-Log "RUNTIME_GUARD detail=$($contract.exhaustion_code) attempts=$count/$($Policy.AttemptBudget)"
        return [pscustomobject]@{ Retry = $false; Detail = [string]$contract.exhaustion_code }
    }
    $backoff = @($contract.backoff_seconds)
    if ($backoff.Count -lt 1) { return [pscustomobject]@{ Retry = $false; Detail = 'RUNTIME_POLICY_BACKOFF_INVALID' } }
    $delay = [int]$backoff[[Math]::Min($count - 1, $backoff.Count - 1)]
    $retryInfo = [ordered]@{ attempt = $count; max_attempts = [int]$Policy.AttemptBudget; reason = $reason; next_retry_at = (Get-Date).AddSeconds($delay).ToString('o') }
    $script:retryInfo = $retryInfo
    Write-Log "$($contract.event_label) checkpoint=$($State.next_checkpoint) attempt=$count/$($Policy.AttemptBudget) reason=$reason"
    Write-Log "RETRY_WAIT seconds=$delay"
    $script:operationalStatus = 'RETRYING'; Write-OperationalStatus $script:operationalStatus "Registry retry policy=$policyId reason=$reason"
    return [pscustomobject]@{ Retry = $true; Detail = $null; Delay = $delay }
}

function Invoke-EvidenceProviderRecovery($Policy) {
    # Generic infrastructure handler.  The registry supplied the exact PDF
    # requirement; this function neither detects D050 nor interprets content.
    $contract = $Policy.RepairContract
    if (-not $contract -or -not $contract.source_pdf_path -or -not $contract.pdf_sha256 -or @($contract.page_numbers).Count -lt 1) {
        return [pscustomobject]@{ Pass = $false; Detail = 'EVIDENCE_REQUIREMENT_INVALID' }
    }
    $args = @($pdfEvidenceProbeScript, '--pdf', [string]$contract.source_pdf_path, '--pdf-sha256', [string]$contract.pdf_sha256, '--requirement', [string]$contract.evidence_requirement, '--cache-root', (Join-Path $run 'production_pdf_evidence_cache'))
    foreach ($page in @($contract.page_numbers)) { $args += @('--page', [string]$page) }
    $priorEap = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; $output = @(& $python @args 2>&1); $exit = $LASTEXITCODE } finally { $ErrorActionPreference = $priorEap }
    $detail = @($output | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() }) | Select-Object -Last 1
    if ($exit -eq 0) { return [pscustomobject]@{ Pass = $true; Detail = $detail } }
    return [pscustomobject]@{ Pass = $false; Detail = $detail }
}

function Acknowledge-StopRequest([string]$Phase) {
    if (-not (Test-Path -LiteralPath $stopRequestPath)) { return $false }
    Write-Log "STOP_REQUESTED phase=$Phase"
    Remove-Item -LiteralPath $stopRequestPath -Force
    $script:operationalStatus = "STOPPED"
    $script:finalDetail = "STOP_REQUESTED:$Phase"
    $script:exitCodeForSupervisor = 0
    return $true
}

try {
    try {
        $lock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $ownsLock = $true
    } catch [System.IO.IOException] {
        Write-Host "无法启动：已有 production writer lock。请先用 Watch-Booktrans-Production.ps1 确认现有 Supervisor；不会自动删除锁。"
        $exitCodeForSupervisor = 10
        $operationalStatus = "BLOCKED"
        $finalDetail = "CAMPAIGN_WRITER_LOCK_HELD:$lockPath"
        $lockAcquireFailed = $true
        throw $finalDetail
    }

    $writer = [System.IO.StreamWriter]::new($lock, $utf8NoBom)
    $writer.WriteLine("pid=$PID started=$startedAt")
    $writer.Flush()
    $operationalStatus = "RUNNING"
    $finalDetail = "Supervisor started"
    Write-OperationalStatus $operationalStatus $finalDetail
    Write-Metrics 'SUPERVISOR_START'
    Write-Log "START supervisor pid=$PID"
    Write-ProductionEvent 'SUPERVISOR_STARTED' $null $null $null $null $null 'LOCK_ACQUIRED' $null

    $failures = 0
    $lastFingerprint = $null
    $sameFingerprint = 0
    $lastCanonicalRecoveryFingerprint = $null
    $canonicalRecoveryNoProgressCount = 0
$transientFailureCount = 0
$noProgressCount = 0
$runtimePolicyAttempts = @{}
$runtimePolicyLabels = @{}
    while ($true) {
        # Check the persisted derived state first.  A single rebuild is the
        # only automatic repair permitted for stale operational/index state;
        # immutable contradictions must remain blocked.
        try {
            $before = Refresh-State
        } catch {
            $firstRefreshFailure = $_.Exception.Message
            Write-Log "DERIVED_STATE_REFRESH_FAILED attempt=1 detail=$firstRefreshFailure phase=before_worker"
            if (Test-RefreshFailureIsAuthoritativeIntegrity $firstRefreshFailure) {
                Write-Log "BLOCKER ARTIFACT_CORRUPTION state-refresh=$firstRefreshFailure"
                $operationalStatus = "BLOCKED"; $finalDetail = "ARTIFACT_CORRUPTION"; $lastFailureClassification = "ARTIFACT_CORRUPTION"; $exitCodeForSupervisor = 3
                break
            }
            try {
                $before = Refresh-State
                Write-Log "DERIVED_STATE_REBUILD_RECOVERED attempt=1 phase=before_worker"
            } catch {
                $retryRefreshFailure = $_.Exception.Message
                Write-Log "DERIVED_STATE_REFRESH_FAILED attempt=2 detail=$retryRefreshFailure phase=before_worker"
                if (Test-RefreshFailureIsAuthoritativeIntegrity $retryRefreshFailure) {
                    Write-Log "BLOCKER ARTIFACT_CORRUPTION state-refresh=$retryRefreshFailure"
                    $operationalStatus = "BLOCKED"; $finalDetail = "ARTIFACT_CORRUPTION"; $lastFailureClassification = "ARTIFACT_CORRUPTION"; $exitCodeForSupervisor = 3
                } else {
                    Write-Log "BLOCKER DERIVED_STATE_VALIDATION_FAILED first=[$firstRefreshFailure] retry=[$retryRefreshFailure]"
                    $operationalStatus = "BLOCKED"; $finalDetail = "DERIVED_STATE_VALIDATION_FAILED"; $lastFailureClassification = "DERIVED_STATE_VALIDATION_FAILED"; $exitCodeForSupervisor = 3
                }
                break
            }
        }
        try {
            $preparedCommit = Resume-PreparedCheckpointCommit $before
            if ($preparedCommit.Attempted) {
                $before = Refresh-State
                Write-Log "PREPARED_CHECKPOINT_COMMIT checkpoint=$($preparedCommit.Checkpoint) committed=$($preparedCommit.Committed) manifest=$($preparedCommit.Detail)"
            }
        } catch {
            Write-Log "BLOCKER PREPARED_CHECKPOINT_COMMIT state-refresh=$($_.Exception.Message)"
            $operationalStatus = "BLOCKED"; $finalDetail = "PREPARED_CHECKPOINT_COMMIT_FAILED"; $lastFailureClassification = "ARTIFACT_CORRUPTION"; $exitCodeForSupervisor = 3
            break
        }
        if ($before.campaign_status -eq 'BATCH_BOUNDARY_READY') {
            try {
                $releaseBoundary = Invoke-BatchBoundaryRelease $before
                if ($releaseBoundary.Verdict -eq 'PASS') {
                    Write-Log "RELEASE_BOUNDARY_PASS batch=$($before.active_batch) initial_current_debt=$($releaseBoundary.Result.initial_current_release_debt_total) unresolved_current_debt=$(@($releaseBoundary.Result.gate.unresolved_ids).Count)"
                    $before = Refresh-State
                    continue
                }
                Write-Log "RELEASE_BOUNDARY_HUMAN_REQUIRED batch=$($before.active_batch) initial_current_debt=$($releaseBoundary.Result.initial_current_release_debt_total) unresolved_current_debt=$(@($releaseBoundary.Result.gate.unresolved_ids).Count)"
                $operationalStatus = 'BLOCKED'; $finalDetail = 'BATCH_RELEASE_GATE:BLOCKED_HUMAN_ADJUDICATION_REQUIRED'; $lastFailureClassification = 'HUMAN_REQUIRED'; $exitCodeForSupervisor = 3
                break
            } catch {
                Write-Log "BLOCKER RELEASE_BOUNDARY detail=$($_.Exception.Message)"
                $operationalStatus = 'BLOCKED'; $finalDetail = 'RELEASE_BOUNDARY_FAILED'; $lastFailureClassification = 'RELEASE_BOUNDARY_FAILED'; $exitCodeForSupervisor = 3
                break
            }
        }
        $batchReady = Invoke-BatchReady
        # One scheduling iteration owns one rebuilt snapshot.  Do not compare
        # a readiness probe from before Refresh-State with the post-refresh
        # probe consumed below: that mixes two state views and can make a
        # single iteration log both PASS and BLOCK.
        # CurrentExecutionProjection composes only existing adapter facts.  It
        # is the sole startup interpreter for cursor, partial and resume state.
        $startupPartial = Test-NoPartialCheckpointArtifact $before
        $startupPartialKind = if ($startupPartial.Reason -match '^SEALED_CHECKPOINT_STATE_DRIFT') { 'SEALED' } elseif (-not $startupPartial.Safe) { 'PARTIAL' } else { 'NONE' }
        $reviewerContractResume = if ($startupPartialKind -eq 'PARTIAL') { Test-ReviewerContractResumeEligible $before } else { [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_PARTIAL' } }
        $semanticRescueResume = if ($startupPartialKind -eq 'PARTIAL') { Test-Cp04SemanticRescueEligible $before } else { [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_PARTIAL' } }
        $canonicalRecoveryResume = if ($startupPartialKind -eq 'PARTIAL') { Test-CurrentCanonicalRecoveryResumeEligible $before } else { [pscustomobject]@{ Passed = $false; Detail = 'NO_CURRENT_PARTIAL' } }
        $startupCursor = Invoke-AuthoritativeCursorDecision $before $before ([ordered]@{ source_sha_unchanged = $true; current_partial = $startupPartialKind })
        if ($WhatIf -or $ShadowControlPlane) {
            Invoke-ControlPlaneShadow 'cursor' ([ordered]@{ before = $before; after = $before; evidence = [ordered]@{ source_sha_unchanged = $true; current_partial = $startupPartialKind } }) $startupCursor.decision
        }
        $terminalCheckpointCount = [int][math]::Ceiling(([double]$before.target_count) / 25)
        $terminalCheckpoint = 'checkpoint_{0:D2}' -f $terminalCheckpointCount
        $startupFacts = [ordered]@{
            campaign_status = [string]$before.campaign_status; cursor_decision = [string]$startupCursor.decision
            partial_kind = $startupPartialKind; batch_ready = [bool]$batchReady.Passed
            terminal_checkpoint = ([string]$before.next_checkpoint -eq $terminalCheckpoint)
            reviewer_resume = [bool]$reviewerContractResume.Passed; semantic_rescue = [bool]$semanticRescueResume.Passed
            canonical_recovery = [bool]$canonicalRecoveryResume.Passed
        }
        if ($null -eq $batchReady -or $batchReady.PSObject.Properties.Name -notcontains 'Passed' -or $batchReady.Passed -isnot [bool]) {
            throw 'BATCH_READY_ADAPTER_CONTRACT_INVALID'
        }
        if ([bool]$batchReady.Passed -ne [bool]$startupFacts.batch_ready) {
            throw 'BATCH_READY_ADAPTER_TRANSPORT_CONTRADICTION'
        }
        Write-Log "STARTUP_FACTS adapter_exit=$($batchReady.ExitCode) adapter_passed=$($batchReady.Passed) detail=$($batchReady.Detail) raw=$((ConvertTo-CompactJson $startupFacts))"
        $startupProjection = Invoke-CurrentExecutionProjection 'startup' $startupFacts
        if ($startupProjection.action -eq 'TERMINAL_CLOSURE') {
            $batchNumber = [regex]::Match([string]$before.active_batch, 'FULL_BOOK_BATCH_(\d{3})$').Groups[1].Value
            Write-Log "CLOSURE_RECONCILIATION_START batch=$($before.active_batch) checkpoint=$terminalCheckpoint"
            Write-ProductionEvent 'RECONCILIATION_START' $before $null $null $null $null 'TERMINAL_CLOSURE' $startupPartial.Reason
            & $python $terminalClosureScript --batch ([int]$batchNumber) 2>&1 | ForEach-Object { Write-Log "CLOSURE_RECONCILIATION detail=$($_.ToString())" }
            if ($LASTEXITCODE -ne 0) { Write-Log 'BLOCKER CLOSURE_RECONCILIATION_FAILED'; $operationalStatus='BLOCKED'; $finalDetail='CLOSURE_RECONCILIATION_FAILED'; $exitCodeForSupervisor=3; break }
            $before = Refresh-State; Write-Log "CLOSURE_RECONCILIATION_PASS batch=$($before.active_batch)"; Write-ProductionEvent 'RECONCILIATION_PASS' $before $null $null $null $null 'TERMINAL_CLOSURE' $null; Write-Log 'STATE_REBUILT'; $batchReady = Invoke-BatchReady
            if (-not $batchReady.Passed) { Write-Log "BLOCKER BATCH_READY_AFTER_CLOSURE detail=$($batchReady.Detail)"; $operationalStatus='BLOCKED'; $finalDetail='BATCH_READY_AFTER_CLOSURE'; $exitCodeForSupervisor=3; break }
            continue
        }
        if ($startupProjection.action -eq 'BLOCK_BATCH_READY') {
            Write-Log "BATCH_TRANSITION_REPAIR_REQUIRED detail=$($batchReady.Detail)"
            $operationalStatus = "BLOCKED"; $finalDetail = "BATCH_TRANSITION_REPAIR_REQUIRED"; $lastFailureClassification = "BATCH_TRANSITION_REPAIR_REQUIRED"; $exitCodeForSupervisor = 3
            break
        }
        if ($startupProjection.action -eq 'BLOCK_CAMPAIGN_STATE') {
            Write-Log "BLOCKER state=$($before.campaign_status) reason=$($before.stop_reason)"
            $operationalStatus = "BLOCKED"; $finalDetail = "CAMPAIGN_STATUS:$($before.campaign_status)"; $lastFailureClassification = "PRODUCTION_BLOCKER"; $exitCodeForSupervisor = 2
            break
        }
        if ($startupProjection.action -eq 'BLOCK_CURSOR') {
            Write-Log "BLOCKER cursor_decision=$($startupCursor.decision) reason=$($startupCursor.reason) detail=$($startupPartial.Reason)"
            $operationalStatus = "BLOCKED"; $finalDetail = if ($startupPartial.Reason) { $startupPartial.Reason } else { $startupCursor.reason }; $lastFailureClassification = "PARTIAL_OR_UNSEALED_CHECKPOINT_CONFLICT"; $exitCodeForSupervisor = 3
            break
        }
        switch ([string]$startupProjection.action) {
            'RUN_REVIEWER_RESUME' { Write-Log "REVIEWER_CONTRACT_RESUME $($reviewerContractResume.Detail)" }
            'RUN_SEMANTIC_RESCUE' { Write-Log "SEMANTIC_RESCUE_RESUME $($semanticRescueResume.Detail)" }
            'RUN_CANONICAL_RECOVERY' { Write-Log "CURRENT_CANONICAL_RECOVERY_RESUME $($canonicalRecoveryResume.Detail) family=$($canonicalRecoveryResume.Projection.recovery_family) fingerprint=$((ConvertTo-CompactJson $canonicalRecoveryResume.Projection.recovery_fingerprint))" }
            'RUN_CURRENT_PARTIAL' { Write-Log "CURRENT_PARTIAL_RESUME checkpoint=$($before.next_checkpoint)" }
            'RUN_WORKER' { Write-Log "BATCH_READY $($batchReady.Detail)" }
            default { throw "CURRENT_EXECUTION_PROJECTION_ACTION_INVALID:$($startupProjection.action)" }
        }
        if (Acknowledge-StopRequest "before_worker") { break }
        if ($WhatIf) {
            Write-Log "WHATIF fresh worker would resume $($before.active_batch) $($before.next_checkpoint) $($before.next_logical_work_order_id)"
            $operationalStatus = "STOPPED"; $finalDetail = "WHATIF_COMPLETE"; $lastExitCode = 0; $lastFailureClassification = $null; $retryInfo = $null
            break
        }
        if ($retryInfo) {
            Write-Log "RETRY_START fresh worker checkpoint=$($before.next_checkpoint) attempt=$($retryInfo.attempt)/$($retryInfo.max_attempts)"
            $operationalStatus = "RUNNING"; Write-OperationalStatus $operationalStatus "Retry worker starting"
        }

        Rotate-WorkerHandoff "before_new_worker"
        $timestamp = Get-Date -Format "yyyyMMddTHHmmssfff"
        $workerId = "$PID-$timestamp-$([guid]::NewGuid().ToString('N'))"
        $workerLog = Join-Path $logDir "worker-$timestamp.jsonl"
        $prompt = Get-Content -LiteralPath $promptPath -Raw -Encoding utf8
        $prompt += "`n`nOperational handoff for this fresh worker only:`n- worker_id: $workerId`n- handoff_path: $workerHandoffPath`n- Write schema_version: 1 and this exact worker_id. The Supervisor accepts only a matching, atomically written handoff after this worker exits.`n"
        $latestWorkerLog = $workerLog
        $workerStartedAt = Get-Date
        $currentWorker = [ordered]@{ worker_id = $workerId; started_at = $workerStartedAt.ToString("o"); log = $workerLog; alive = $true }
        $workerAlive = $true; $workerCount++
        Write-OperationalStatus $operationalStatus "Worker running"
        Write-Log "START fresh worker batch=$($before.active_batch) checkpoint=$($before.next_checkpoint) workerId=$workerId"
        Write-ProductionEvent 'WORKER_STARTED' $before $workerId $null $null $null 'PRODUCTION' $null
        $workerExitCode = $null
        $workerInvocationException = $null
        $previousNativeEap = $null
        $previousErrorAction = $ErrorActionPreference
        $previousWorkerIdEnvironment = $env:BOOKTRANS_PRODUCTION_WORKER_ID
        $previousHandoffPathEnvironment = $env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH
        $previousBatchEnvironment = $env:BOOKTRANS_PRODUCTION_BATCH
        $previousCheckpointEnvironment = $env:BOOKTRANS_PRODUCTION_CHECKPOINT
        try {
            $workerWriter = [System.IO.StreamWriter]::new($workerLog, $false, $utf8NoBom)
            $ErrorActionPreference = "Continue"
            $env:BOOKTRANS_PRODUCTION_WORKER_ID = $workerId
            $env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH = $workerHandoffPath
            $env:BOOKTRANS_PRODUCTION_BATCH = [string]$before.active_batch
            $env:BOOKTRANS_PRODUCTION_CHECKPOINT = [string]$before.next_checkpoint
            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) { $previousNativeEap = $PSNativeCommandUseErrorActionPreference; $PSNativeCommandUseErrorActionPreference = $false }
            if ($CodexExecutable -eq "codex") {
                Write-Log "START fresh deterministic worker batch=$($before.active_batch) checkpoint=$($before.next_checkpoint) workerId=$workerId workerKind=deterministic"
                $workerArgs = @()
                if ($semanticRescueResume.Passed -or $canonicalRecoveryResume.Passed) {
                    $workerArgs += '--semantic-rescue'
                    if ($Cp04HumanAdjudicationPath) { $workerArgs += @('--human-adjudication-path', $Cp04HumanAdjudicationPath) }
                    if ($Cp04D051ApprovalPath) { $workerArgs += @('--d051-approval-path', $Cp04D051ApprovalPath) }
                }
                if ($D051ReuseApprovalPath -or $D051ReuseDisplayId) {
                    if (-not $D051ReuseApprovalPath -or -not $D051ReuseDisplayId) { throw 'D051_REUSE_ARGUMENTS_INCOMPLETE' }
                    $workerArgs += @('--d051-reuse-approval-path', $D051ReuseApprovalPath, '--d051-reuse-display-id', $D051ReuseDisplayId)
                }
                # A current-checkpoint human approval is validated by the production
                # worker against its rebuilt SemanticReviewGroup and immutable RAW;
                # it does not rely on the historical cp04-only recovery switch.
                if ($HumanAdjudicationPath) { $workerArgs += @('--human-adjudication-path', $HumanAdjudicationPath) }
                $workerArgumentVector = @(
                    $deterministicWorkerScript, '--root', $Root, '--run-path', $run,
                    '--worker-id', $workerId, '--handoff-path', $workerHandoffPath,
                    '--batch-label', $before.active_batch, '--checkpoint', $before.next_checkpoint,
                    '--mode', 'production', '--authorize-production-checkpoint-write'
                ) + $workerArgs
                $workerStdoutPath = Join-Path $logDir "worker-$timestamp.stdout.log"
                $workerStderrPath = Join-Path $logDir "worker-$timestamp.stderr.log"
                # Capture launch metadata before process creation.  This is the
                # existing worker log, not a second logging subsystem.
                $workerWriter.WriteLine((@{
                    artifact_type = 'PRODUCTION_WORKER_LAUNCH'; schema_version = 1;
                    executable = $python; argument_vector = $workerArgumentVector;
                    working_directory = $Root;
                    environment_overrides = @{ BOOKTRANS_PRODUCTION_WORKER_ID = $workerId; BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH = $workerHandoffPath; BOOKTRANS_PRODUCTION_BATCH = [string]$before.active_batch; BOOKTRANS_PRODUCTION_CHECKPOINT = [string]$before.next_checkpoint };
                    stdout_capture_path = $workerStdoutPath; stderr_capture_path = $workerStderrPath;
                    created_at = (Get-Date -Format o)
                } | ConvertTo-Json -Compress -Depth 5))
                $workerWriter.Flush()
                $child = $null
                $launchDiagnostics = [ordered]@{
                    launch_stage = 'NOT_STARTED'; child_created = $false
                    executable = $python; working_directory = $Root
                    env_key_count = 0; case_insensitive_duplicate_count = 0
                }
                try {
                    $child = Start-DeterministicWorkerProcess $python $workerArgumentVector $Root $workerStdoutPath $workerStderrPath ([ref]$launchDiagnostics)
                } catch {
                    # A launch failure is not a worker exit.  In particular,
                    # never reuse a prior native command's $LASTEXITCODE.
                    $workerInvocationException = "WORKER_LAUNCH_FAILED type=$($_.Exception.GetType().FullName) message=$($_.Exception.Message)"
                    $workerExitCode = 5
                    $workerWriter.WriteLine((New-WorkerLaunchFailureRecord $workerId $_ $launchDiagnostics $workerExitCode | ConvertTo-Json -Compress -Depth 8))
                    $workerWriter.Flush()
                }
                if ($null -ne $child) {
                    $workerExitCode = $child.ExitCode
                    $workerWriter.WriteLine((@{
                        artifact_type = 'PRODUCTION_WORKER_EXIT'; schema_version = 1; child_pid = $child.Id;
                        exit_code = $workerExitCode; stdout_tail = @((Get-Content -LiteralPath $workerStdoutPath -Tail 40 -ErrorAction SilentlyContinue)); stderr_tail = @((Get-Content -LiteralPath $workerStderrPath -Tail 40 -ErrorAction SilentlyContinue));
                        exited_at = (Get-Date -Format o)
                    } | ConvertTo-Json -Compress -Depth 5))
                }
            } else {
                & $CodexExecutable exec --json --skip-git-repo-check --sandbox workspace-write -C $Root $prompt 2>&1 | ForEach-Object { $workerWriter.WriteLine($_.ToString()) }
            }
            if ($null -eq $workerExitCode) { $workerExitCode = $LASTEXITCODE }
        } catch {
            $workerInvocationException = $_.Exception.Message
            if ($workerWriter) { $workerWriter.WriteLine($workerInvocationException); $workerWriter.Flush() }
            $workerExitCode = 5
        } finally {
            $ErrorActionPreference = $previousErrorAction
            if ($null -ne $previousNativeEap) { $PSNativeCommandUseErrorActionPreference = $previousNativeEap }
            if ($null -eq $previousWorkerIdEnvironment) { Remove-Item Env:BOOKTRANS_PRODUCTION_WORKER_ID -ErrorAction SilentlyContinue } else { $env:BOOKTRANS_PRODUCTION_WORKER_ID = $previousWorkerIdEnvironment }
            if ($null -eq $previousHandoffPathEnvironment) { Remove-Item Env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH -ErrorAction SilentlyContinue } else { $env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH = $previousHandoffPathEnvironment }
            if ($null -eq $previousBatchEnvironment) { Remove-Item Env:BOOKTRANS_PRODUCTION_BATCH -ErrorAction SilentlyContinue } else { $env:BOOKTRANS_PRODUCTION_BATCH = $previousBatchEnvironment }
            if ($null -eq $previousCheckpointEnvironment) { Remove-Item Env:BOOKTRANS_PRODUCTION_CHECKPOINT -ErrorAction SilentlyContinue } else { $env:BOOKTRANS_PRODUCTION_CHECKPOINT = $previousCheckpointEnvironment }
            if ($workerWriter) { $workerWriter.Dispose(); $workerWriter = $null }
            $workerAlive = $false
            $currentWorker = $null
        }
        $lastExitCode = $workerExitCode
        try {
            $after = Refresh-State
        } catch {
            $firstRefreshFailure = $_.Exception.Message
            Write-Log "DERIVED_STATE_REFRESH_FAILED attempt=1 detail=$firstRefreshFailure workerExit=$workerExitCode"
            if (Test-RefreshFailureIsAuthoritativeIntegrity $firstRefreshFailure) {
                Write-Log "BLOCKER ARTIFACT_CORRUPTION state-refresh=$firstRefreshFailure workerExit=$workerExitCode"
                $operationalStatus = "BLOCKED"; $finalDetail = "ARTIFACT_CORRUPTION"; $lastFailureClassification = "ARTIFACT_CORRUPTION"; $exitCodeForSupervisor = 3
                break
            }
            # One existing deterministic rebuild is both the authoritative
            # validation and the only legal recovery for derived state.  A
            # successful retry leaves the worker's already-bound handoff in
            # place for normal current-state processing below.
            try {
                $after = Refresh-State
                Write-Log "DERIVED_STATE_REBUILD_RECOVERED attempt=1 current_handoff_preserved=true"
            } catch {
                $retryRefreshFailure = $_.Exception.Message
                Write-Log "DERIVED_STATE_REFRESH_FAILED attempt=2 detail=$retryRefreshFailure workerExit=$workerExitCode"
                if (Test-RefreshFailureIsAuthoritativeIntegrity $retryRefreshFailure) {
                    Write-Log "BLOCKER ARTIFACT_CORRUPTION state-refresh=$retryRefreshFailure workerExit=$workerExitCode"
                    $operationalStatus = "BLOCKED"; $finalDetail = "ARTIFACT_CORRUPTION"; $lastFailureClassification = "ARTIFACT_CORRUPTION"; $exitCodeForSupervisor = 3
                } else {
                    Write-Log "BLOCKER DERIVED_STATE_VALIDATION_FAILED first=[$firstRefreshFailure] retry=[$retryRefreshFailure]"
                    $operationalStatus = "BLOCKED"; $finalDetail = "DERIVED_STATE_VALIDATION_FAILED"; $lastFailureClassification = "DERIVED_STATE_VALIDATION_FAILED"; $exitCodeForSupervisor = 3
                }
                break
            }
        }
        $postWorkerPartial = Test-NoPartialCheckpointArtifact $after
        $postWorkerPartialKind = if ($postWorkerPartial.Reason -match '^SEALED_CHECKPOINT_STATE_DRIFT') { 'SEALED' } elseif (-not $postWorkerPartial.Safe) { 'PARTIAL' } else { 'NONE' }
        $cursorDecision = Invoke-AuthoritativeCursorDecision $before $after ([ordered]@{
            source_sha_unchanged = ([string]$before.source_sha256 -eq [string]$after.source_sha256)
            current_partial = $postWorkerPartialKind
        })
        $workerMadeProgress = $cursorDecision.decision -in @('PROGRESS', 'BATCH_COMPLETE')
        Write-ProductionEvent 'WORKER_EXITED' $after $workerId $null $null $null "exit=$workerExitCode" $workerInvocationException
        $workerHandoff = Get-CurrentWorkerHandoff $workerId $before $after $workerStartedAt $workerMadeProgress
        $handoffProjection = Invoke-CurrentExecutionProjection 'handoff' ([ordered]@{
            handoff_kind = [string]$workerHandoff.Kind; handoff_reason = [string]$workerHandoff.Detail
            handoff_status = if ($workerHandoff.Value) { [string]$workerHandoff.Value.status } else { $null }
            production_disposition = if ($workerHandoff.Value) { [string]$workerHandoff.Value.production_disposition } else { $null }
        })
        if ($WhatIf -or $ShadowControlPlane) {
            Invoke-ControlPlaneShadow 'cursor' ([ordered]@{
                before = $before; after = $after
                evidence = [ordered]@{ source_sha_unchanged = ([string]$before.source_sha256 -eq [string]$after.source_sha256); current_partial = $postWorkerPartialKind }
            }) $cursorDecision.decision
            $handoffValue = if ($workerHandoff.Value) { $workerHandoff.Value } else { $null }
            Invoke-ControlPlaneShadow 'handoff' ([ordered]@{
                current_worker_id = $workerId; handoff = $handoffValue; post_cursor = $after
                facts = if ($workerHandoff.Facts) { $workerHandoff.Facts } else { [ordered]@{ source_sha_unchanged = ([string]$before.source_sha256 -eq [string]$after.source_sha256); worker_made_progress = $workerMadeProgress; launch_batch = $before.active_batch; current_partial = $postWorkerPartialKind } }
            }) $workerHandoff.Decision
        }
        if ($workerHandoff.Kind -eq "INVALID") {
            Write-Log "BLOCKER handoff_protocol=$($workerHandoff.Detail) workerId=$workerId"
            $operationalStatus = "BLOCKED"; $finalDetail = "HANDOFF_PROTOCOL:$($workerHandoff.Detail)"; $lastFailureClassification = "HANDOFF_PROTOCOL_VIOLATION"; $exitCodeForSupervisor = 3
            break
        }
        if ($handoffProjection.action -eq 'ROTATE_STALE_HANDOFF') {
            if (($workerHandoff.Detail -eq 'AUTHORITATIVE_PROGRESS_SUPERSEDES_CONTINUE') -or ($workerMadeProgress -and $workerHandoff.Value -and [string]$workerHandoff.Value.status -eq 'CONTINUE' -and $workerHandoff.Detail -eq 'HANDOFF_CHECKPOINT_ALREADY_SEALED')) { Write-Log "HANDOFF_IGNORED reason=AUTHORITATIVE_STATE_PROGRESS status=CONTINUE workerId=$workerId" }
            elseif ($workerHandoff.Detail -eq 'SUPERSEDED_BY_AUTHORITATIVE_PROGRESS') { Write-Log "HANDOFF_PROTOCOL_RECOVERED reason=SUPERSEDED_BY_AUTHORITATIVE_PROGRESS workerId=$workerId" }
            else { Write-Log "HANDOFF_IGNORED reason=$($workerHandoff.Detail) workerId=$workerId" }
            Rotate-WorkerHandoff "stale_after_worker"
            $workerHandoff = [pscustomobject]@{ Kind = "MISSING"; Detail = $null; Value = $null; Decision = 'ACCEPT_CONTINUE'; Normalized = $false; DerivedFields = @() }
        }
        if ($workerMadeProgress) {
            Write-Log "SEALED state batch=$($after.active_batch) completed=$($after.completed_count)/$($after.target_count) checkpoint=$($after.next_checkpoint) next=$($after.next_logical_work_order_id)"
            Write-ProductionEvent 'CHECKPOINT_SEALED' $after $workerId $null $null $null 'AUTHORITATIVE_PROGRESS' $null
            if (@($runtimePolicyLabels.Values) -contains 'RETRYABLE_RUNTIME') { Write-Log "RETRY_RECOVERED state_progressed=true" }
            if (@($runtimePolicyLabels.Values) -contains 'NO_PROGRESS') { Write-Log "NO_PROGRESS_RECOVERED state_progressed=true" }
            $transientFailureCount = 0; $noProgressCount = 0; $runtimePolicyAttempts = @{}; $runtimePolicyLabels = @{}; $retryInfo = $null; $failures = 0; $sameFingerprint = 0; $lastFingerprint = $null; $lastCanonicalRecoveryFingerprint = $null; $canonicalRecoveryNoProgressCount = 0; $lastFailureClassification = $null
        } else {
            Write-Log "STATE batch=$($after.active_batch) completed=$($after.completed_count)/$($after.target_count) checkpoint=$($after.next_checkpoint)"
            if ($workerExitCode -eq 0 -and @($runtimePolicyLabels.Values) -contains 'RETRYABLE_RUNTIME') {
                Write-Log "RETRY_RECOVERED workerExit=0"
                $runtimePolicyAttempts = @{}; $runtimePolicyLabels = @{}; $transientFailureCount = 0; $retryInfo = $null; $lastFailureClassification = $null
            }
        }
        $lastHandoff = [ordered]@{ at = Get-Date -Format o; worker_id = $workerId; worker_exit = $workerExitCode; worker_status = if ($workerHandoff.Value) { $workerHandoff.Value.status } else { $null }; next_batch = $after.active_batch; next_checkpoint = $after.next_checkpoint; next_work_order = $after.next_logical_work_order_id }
        Write-OperationalStatus $operationalStatus "Worker completed; evaluating handoff"

        if ($handoffProjection.action -in @('CONSUME_CANONICAL_RECOVERY', 'TRIAGE_EXISTING_RECOVERY')) {
            # The worker's current-head boundary may request the existing
            # bounded semantic recovery.  It is deliberately resumed by a
            # fresh ordinary worker (which reads the same RAW) rather than
            # being forced through the deterministic-QA policy registry.
            if ($handoffProjection.action -eq 'CONSUME_CANONICAL_RECOVERY') {
                $canonicalRecoveryFingerprint = Get-CanonicalRecoveryFingerprint $workerHandoff.Value
                if ($canonicalRecoveryFingerprint -eq $lastCanonicalRecoveryFingerprint) {
                    $canonicalRecoveryNoProgressCount++
                } else {
                    $lastCanonicalRecoveryFingerprint = $canonicalRecoveryFingerprint
                    $canonicalRecoveryNoProgressCount = 0
                }
                if ($canonicalRecoveryNoProgressCount -ge 1) {
                    $recoveryInfo = [ordered]@{
                        batch = $workerHandoff.Value.active_batch; checkpoint = $workerHandoff.Value.checkpoint
                        failing_work_order_ids = @($workerHandoff.Value.failing_work_order_ids)
                        recovery_fingerprint = $workerHandoff.Value.recovery_fingerprint
                        repeat_count = $canonicalRecoveryNoProgressCount + 1
                        failure_classification = 'RECOVERY_NO_PROGRESS'
                    }
                    Write-Log "RECOVERY_NO_PROGRESS fingerprint=$canonicalRecoveryFingerprint repeats=$($recoveryInfo.repeat_count)"
                    Write-ProductionEvent 'RECOVERY_NO_PROGRESS' $after $workerId $null $null $null $canonicalRecoveryFingerprint 'OPERATIONAL_FAIL_CLOSED'
                    Rotate-WorkerHandoff 'consumed_recovery_no_progress'
                    $operationalStatus = 'BLOCKED'; $finalDetail = 'RECOVERY_NO_PROGRESS'; $lastFailureClassification = 'RECOVERY_NO_PROGRESS'; $exitCodeForSupervisor = 3
                    break
                }
                Write-Log "CANONICAL_RECOVERY_REQUIRED batch=$($workerHandoff.Value.active_batch) checkpoint=$($workerHandoff.Value.checkpoint) family=$($workerHandoff.Value.recovery_family) reason=$($workerHandoff.Value.disposition_reason)"
                Write-ProductionEvent 'CANONICAL_RECOVERY_REQUIRED' $after $workerId $null $null $null $workerHandoff.Value.disposition_reason $workerHandoff.Value.recovery_family
                Rotate-WorkerHandoff 'consumed_canonical_recovery_required'
                $operationalStatus = 'RUNNING'; $lastFailureClassification = $null
                continue
            }
            if ($workerHandoff.Normalized -eq $true) {
                Write-Log "RECOVERY_HANDOFF_NORMALIZED workerId=$workerId fields=$(@($workerHandoff.DerivedFields) -join ',')"
                Write-Metrics 'RECOVERY_HANDOFF_NORMALIZED'
            }
            Set-RecoveryInfo $workerHandoff.Value $workerHandoff.Value.failure_classification
            Write-Log "RECOVERY_REQUIRED batch=$($recoveryInfo.batch) checkpoint=$($recoveryInfo.checkpoint) stage=$($recoveryInfo.stage_reached) failure=$($recoveryInfo.failure_classification)"
            $operationalStatus = 'TRIAGING'; Write-ProductionEvent 'TRIAGE_STARTED' $after $workerId $null $null $null $recoveryInfo.failure_classification $null
            $policy = Get-AutoRecoveryPolicy $workerHandoff.Value $after
            Write-ProductionEvent 'POLICY_SELECTED' $after $workerId $policy.Policy $null $null $(if ($policy.Allowed) { 'ELIGIBLE' } else { 'DENIED' }) $policy.Reason
            if ($policy.Allowed) {
                if ($policy.HandlerKind -eq 'CURRENT_CHECKPOINT_RESUME') {
                    # The policy has proved an exact current-head Core
                    # failure with an existing deterministic transition.
                    # Consume only the handoff; the next ordinary worker
                    # resumes this partial checkpoint and performs that
                    # transition without retranslation or a second writer.
                    Write-Log "CURRENT_CHECKPOINT_RECOVERY policy=$($policy.Policy) checkpoint=$($recoveryInfo.checkpoint)"
                    Rotate-WorkerHandoff 'current_checkpoint_recovery'
                    $operationalStatus = 'RUNNING'; $lastFailureClassification = $null
                    continue
                }
                if ($policy.HandlerKind -eq 'SUPERVISOR_RETRY') {
                    if ($policy.Family -eq 'EVIDENCE_INFRASTRUCTURE') {
                        Write-Log "EVIDENCE_PROVIDER_FAILURE family=$($policy.Family) policy=$($policy.Policy)"
                        $providerRecovery = Invoke-EvidenceProviderRecovery $policy
                        if ($providerRecovery.Pass) { Write-Log "EVIDENCE_PROVIDER_RECOVERED policy=$($policy.Policy)" }
                        else { Write-Log "EVIDENCE_PROVIDER_RETRY probe=FAILED policy=$($policy.Policy) detail=$($providerRecovery.Detail)" }
                    }
                    $runtimeOutcome = Invoke-SupervisorRetryPolicy $policy $after
                    if (-not $runtimeOutcome.Retry) {
                        Write-Log "BLOCKER policy_retry_exhausted policy=$($policy.Policy) detail=$($runtimeOutcome.Detail)"
                        $operationalStatus = 'BLOCKED'; $finalDetail = $runtimeOutcome.Detail; $lastFailureClassification = $runtimeOutcome.Detail; $exitCodeForSupervisor = 3; break
                    }
                    if ($policy.Family -eq 'EVIDENCE_INFRASTRUCTURE') { Write-Log "EVIDENCE_PROVIDER_RETRY policy=$($policy.Policy)" }
                    Rotate-WorkerHandoff 'provider_retry'
                    Start-Sleep -Seconds $runtimeOutcome.Delay
                    continue
                }
                if ($policy.HandlerKind -notin @('QWEN_NARROW_REPAIR', 'DETERMINISTIC_EVIDENCE') -or $policy.AttemptBudget -lt 1) {
                    Write-Log "BLOCKER registry_handler_invalid policy=$($policy.Policy) handler=$($policy.HandlerKind)"
                    $operationalStatus = 'BLOCKED'; $finalDetail = 'RECOVERY_POLICY_HANDLER_INVALID'; $lastFailureClassification = 'RECOVERY_POLICY_HANDLER_INVALID'; $exitCodeForSupervisor = 3; break
                }
                $attemptBudget = [int]$policy.AttemptBudget
                if ($policy.Triage) {
                    $triagedFailureCount++
                    $autoRecoveryEligibleCount++
                    $recoveryInfo['canonical_failure_classification'] = [string]$policy.Triage.canonical_class
                    $recoveryInfo['repair_policy'] = [string]$policy.Triage.repair_policy
                    Write-Log "RECOVERY_TRIAGED raw_failure=$($workerHandoff.Value.failure_classification) canonical=$($policy.Triage.canonical_class) policy=$($policy.Triage.repair_policy) failures=$($policy.Triage.failure_count) density=$($policy.Triage.failure_density)"
                }
                $recoveryInfo['policy_id'] = $policy.Policy
                $recoveryInfo['repair_contract'] = $policy.RepairContract
                $failingIds = @($recoveryInfo.failing_work_order_ids) -join ','
                $recoveryFingerprint = @($policy.Policy, $recoveryInfo.batch, $recoveryInfo.checkpoint, $failingIds) -join '|'
                $recovered = $false
                for ($attempt = 1; $attempt -le $attemptBudget; $attempt++) {
                    $autoRecoveryCount++
                    $recoveryInfo['auto_recovery_policy'] = $policy.Policy; $recoveryInfo['attempt'] = $attempt; $recoveryInfo['max_attempts'] = $attemptBudget
                    $operationalStatus = 'AUTO_RECOVERING'; Write-OperationalStatus $operationalStatus "Auto recovery $attempt/$attemptBudget"; Write-Metrics 'AUTO_RECOVERY_START'
                    Write-Log "AUTO_RECOVERY_START fingerprint=$recoveryFingerprint attempt=$attempt/$attemptBudget policy=$($policy.Policy) handler=$($policy.HandlerKind)"
                    Write-ProductionEvent 'AUTO_RECOVERY_START' $after $null $policy.Policy $attempt $null $recoveryFingerprint $null
                    $recoveryWorkerId = "$PID-recovery-$(Get-Date -Format 'yyyyMMddTHHmmssfff')-$([guid]::NewGuid().ToString('N'))"
                    $recoveryLog = Join-Path $logDir "recovery-worker-$(Get-Date -Format 'yyyyMMddTHHmmssfff').jsonl"
                    $repairContract = if ($policy.RepairContract) { $policy.RepairContract | ConvertTo-Json -Depth 12 -Compress } else { '' }
                    $currentWorker = [ordered]@{ worker_id = $recoveryWorkerId; started_at = (Get-Date -Format o); log = $recoveryLog; alive = $true; role = 'recovery' }; $workerAlive = $true; $workerCount++; $latestWorkerLog = $recoveryLog
                    $env:BOOKTRANS_PRODUCTION_WORKER_ID = $recoveryWorkerId; $env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH = $workerHandoffPath
                    $recoveryStartedAt = Get-Date
                    try {
                        & $python $allowlistedRecoveryScript '--handler-kind' ([string]$policy.HandlerKind) '--policy-id' ([string]$policy.Policy) '--serialized_repair_contract_json' $repairContract '--handoff-path' $workerHandoffPath '--root' $Root '--run-path' $run '--report-path' $recoveryLog '--forbid-production-write'
                        $recoveryExit = $LASTEXITCODE
                    } catch { $recoveryExit = 5; Add-Content -LiteralPath $recoveryLog -Value $_.Exception.Message -Encoding utf8 } finally { $recoveryDurationSeconds += ((Get-Date) - $recoveryStartedAt).TotalSeconds; Remove-Item Env:BOOKTRANS_PRODUCTION_WORKER_ID -ErrorAction SilentlyContinue; Remove-Item Env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH -ErrorAction SilentlyContinue; $workerAlive = $false; $currentWorker = $null }
                    $afterRecovery = Refresh-State
                    $partialRecovery = Test-NoPartialCheckpointArtifact $afterRecovery
                    # A launcher that never reaches its create-once report
                    # has not entered a content recovery boundary.  Do not
                    # burn the policy's content-attempt budget or imply a
                    # provider dispatch; stop as an operational failure.
                    if (-not (Test-Path -LiteralPath $recoveryLog)) {
                        Write-Log "RECOVERY_HANDLER_EXECUTION_FAILURE policy=$($policy.Policy) attempt=$attempt workerExit=$recoveryExit report=missing"
                        Write-ProductionEvent 'RECOVERY_HANDLER_EXECUTION_FAILURE' $after $recoveryWorkerId $policy.Policy $attempt $null "exit=$recoveryExit" 'REPORT_MISSING'
                        $operationalStatus = 'BLOCKED'; $finalDetail = 'RECOVERY_HANDLER_EXECUTION_FAILURE'; $lastFailureClassification = 'RECOVERY_HANDLER_EXECUTION_FAILURE'; $exitCodeForSupervisor = 3
                        break 2
                    }
                    if ((Test-WorkerMadeProgress $after $afterRecovery) -and $partialRecovery.Safe -and $afterRecovery.campaign_status -eq 'CONTINUE') { $autoRecoverySuccessCount++; Write-Log "AUTO_RECOVERY_PASS fingerprint=$recoveryFingerprint attempt=$attempt/$attemptBudget"; Write-ProductionEvent 'AUTO_RECOVERY_PASS' $afterRecovery $recoveryWorkerId $policy.Policy $attempt $null $recoveryFingerprint $null; Write-Metrics 'AUTO_RECOVERY_PASS'; $recovered = $true; break }
                    $autoRecoveryFailureCount++; Write-Log "AUTO_RECOVERY_RETRY fingerprint=$recoveryFingerprint attempt=$attempt/$attemptBudget workerExit=$recoveryExit"; Write-ProductionEvent 'AUTO_RECOVERY_FAIL' $afterRecovery $recoveryWorkerId $policy.Policy $attempt $null "exit=$recoveryExit" $recoveryFingerprint
                }
                if ($recovered) { Rotate-WorkerHandoff 'auto_recovery_pass'; $recoveryInfo = $null; $operationalStatus = 'RUNNING'; continue }
                Write-Log "AUTO_RECOVERY_EXHAUSTED fingerprint=$recoveryFingerprint attempts=$attemptBudget/$attemptBudget"
                $operationalStatus = 'BLOCKED'; $finalDetail = 'AUTO_RECOVERY_EXHAUSTED'; $lastFailureClassification = 'AUTO_RECOVERY_EXHAUSTED'; $exitCodeForSupervisor = 3; Write-Metrics 'AUTO_RECOVERY_EXHAUSTED'; break
            }
            if ($policy.Triage) { $triagedFailureCount++ }
            $humanEscalationCount++
            Rotate-WorkerHandoff "consumed_recovery_required"
            $operationalStatus = "RECOVERY_REQUIRED"; $finalDetail = "RECOVERY_REQUIRED:$($recoveryInfo.failure_classification)"; $lastFailureClassification = "RECOVERY_REQUIRED"; $exitCodeForSupervisor = 3
            break
        }
        if ($handoffProjection.action -in @('CONSUME_HARD_STOP', 'REPROJECT_CURRENT_HEAD')) {
            # Schema-v1 handoffs written before the canonical boundary have no
            # current-head decision.  A local terminal label cannot stop
            # production: archive it and let one normal worker recompute the
            # same checkpoint from immutable RAW/evidence.  A real stop is
            # emitted only when that replay supplies HARD_STOP.
            if ($handoffProjection.action -eq 'REPROJECT_CURRENT_HEAD') {
                Write-Log "HANDOFF_REQUIRES_CURRENT_HEAD_EVALUATION batch=$($workerHandoff.Value.active_batch) checkpoint=$($workerHandoff.Value.checkpoint) local_failure=$($workerHandoff.Value.failure_classification)"
                Write-ProductionEvent 'CURRENT_HEAD_EVALUATION_REQUIRED' $after $workerId $null $null $null $workerHandoff.Value.failure_classification $null
                Rotate-WorkerHandoff 'requires_current_head_evaluation'
                $operationalStatus = 'RUNNING'; $lastFailureClassification = $null
                continue
            }
            Write-Log "BLOCKER worker_handoff batch=$($workerHandoff.Value.active_batch) checkpoint=$($workerHandoff.Value.checkpoint) failure=$($workerHandoff.Value.failure_classification)"
            Rotate-WorkerHandoff "consumed_blocked"
            $operationalStatus = "BLOCKED"; $finalDetail = "WORKER_HANDOFF_BLOCKED:$($workerHandoff.Value.failure_classification)"; $lastFailureClassification = "WORKER_HANDOFF_BLOCKED"; $exitCodeForSupervisor = 3
            break
        }
        $partialAfter = Test-NoPartialCheckpointArtifact $after
        if (-not $workerMadeProgress -and -not $partialAfter.Safe) {
            $recoveryInfo = [ordered]@{
                worker_id = $workerId; batch = $after.active_batch; checkpoint = $after.next_checkpoint
                next_logical_work_order_id = $after.next_logical_work_order_id; partial_checkpoint_path = (Get-CheckpointDirectory $after)
                stage_reached = "UNKNOWN"; failure_classification = "RECOVERY_PROTOCOL_VIOLATION"
                failing_work_order_ids = @(); relevant_qa_artifact_paths = @(); raw_exists = $null; raw_sha256 = $null; created_at = Get-Date -Format o
            }
            Write-Log "RECOVERY_REQUIRED batch=$($recoveryInfo.batch) checkpoint=$($recoveryInfo.checkpoint) stage=$($recoveryInfo.stage_reached) failure=RECOVERY_PROTOCOL_VIOLATION"
            if ($workerHandoff.Kind -eq "INVALID") { Write-Log "BLOCKER handoff_protocol=$($workerHandoff.Detail)" }
            $operationalStatus = "RECOVERY_REQUIRED"; $finalDetail = "RECOVERY_PROTOCOL_VIOLATION:$($partialAfter.Reason)"; $lastFailureClassification = "RECOVERY_PROTOCOL_VIOLATION"; $exitCodeForSupervisor = 3
            break
        }
        if (Acknowledge-StopRequest "after_worker_refresh") { break }
        if ($after.campaign_status -in @("SYSTEMIC_BLOCKER", "HUMAN_REQUIRED", "ARTIFACT_CORRUPTION")) {
            Write-Log "BLOCKER state=$($after.campaign_status) reason=$($after.stop_reason) workerExit=$workerExitCode"
            $operationalStatus = "BLOCKED"; $finalDetail = "CAMPAIGN_STATUS:$($after.campaign_status)"; $lastFailureClassification = "PRODUCTION_BLOCKER"; $exitCodeForSupervisor = 2
            break
        }
        $runtimeClassification = $null
        if (-not $workerMadeProgress -and $workerExitCode -eq 0) { $runtimeClassification = 'NO_PROGRESS_CLEAN_EXIT' }
        elseif (-not $workerMadeProgress -and $workerExitCode -ne 0 -and (Test-ProviderTransient $workerLog $workerInvocationException)) { $runtimeClassification = 'PROVIDER_TRANSIENT' }
        elseif (-not $workerMadeProgress -and $workerExitCode -ne 0 -and (Test-ModelRefreshTimeout $workerLog $workerInvocationException)) { $runtimeClassification = 'CODEX_MODEL_REFRESH_TIMEOUT' }
        if ($runtimeClassification) {
            $safety = Test-SafeTransientRetry $before $after
            $runtimePolicy = Get-RuntimePolicyDecision $runtimeClassification $safety
            Write-ProductionEvent 'POLICY_SELECTED' $after $workerId $runtimePolicy.Policy $null $null $(if ($runtimePolicy.Allowed) { 'ELIGIBLE' } else { 'DENIED' }) $runtimePolicy.Reason
            if (-not $runtimePolicy.Allowed) {
                Write-Log "BLOCKER runtime_policy=$runtimeClassification reason=$($runtimePolicy.Reason) workerExit=$workerExitCode"
                $operationalStatus = 'BLOCKED'; $finalDetail = $runtimePolicy.Reason; $lastFailureClassification = 'PRODUCTION_BLOCKER'; $exitCodeForSupervisor = 3
                break
            }
            $lastFailureClassification = if ($runtimeClassification -eq 'NO_PROGRESS_CLEAN_EXIT') { 'NO_PROGRESS_CLEAN_EXIT' } elseif ($runtimeClassification -eq 'PROVIDER_TRANSIENT') { 'RETRYABLE_RUNTIME:PROVIDER_TRANSIENT' } else { 'RETRYABLE_RUNTIME:CODEX_MODEL_REFRESH_TIMEOUT' }
            $runtimeOutcome = Invoke-SupervisorRetryPolicy $runtimePolicy $after
            if (-not $runtimeOutcome.Retry) {
                $operationalStatus = 'BLOCKED'; $finalDetail = $runtimeOutcome.Detail; $lastFailureClassification = 'RUNTIME_GUARD'; $exitCodeForSupervisor = 4
                break
            }
            Start-Sleep -Seconds $runtimeOutcome.Delay
            continue
        }
        if ($workerExitCode -ne 0 -and -not $workerMadeProgress) {
            $fingerprint = "{0}::{1}::{2}::{3}::{4}::{5}" -f $workerExitCode, $after.campaign_status, $after.active_batch, $after.next_checkpoint, $after.next_logical_work_order_id, $after.stop_reason
            if ($fingerprint -eq $lastFingerprint) { $sameFingerprint++ } else { $sameFingerprint = 1; $lastFingerprint = $fingerprint }
            $failures++
        } else {
            $sameFingerprint = 0; $lastFingerprint = $null; $failures = 0
        }
        if ($failures -ge $MaxConsecutiveWorkerFailures -or $sameFingerprint -ge $MaxSameFailureFingerprint) {
            Write-Log "BLOCKER RUNTIME_GUARD workerExit=$workerExitCode failures=$failures sameFingerprint=$sameFingerprint fingerprint=$fingerprint"
            $operationalStatus = "BLOCKED"; $finalDetail = "RUNTIME_GUARD:workerExit=$workerExitCode"; $lastFailureClassification = "RUNTIME_GUARD"; $exitCodeForSupervisor = 4
            break
        }
        Write-Log "HANDOFF workerExit=$workerExitCode next=$($after.active_batch)/$($after.next_checkpoint)"
    }
} catch {
    if (-not $lockAcquireFailed) {
        Write-Log "BLOCKER supervisor_error=$($_.Exception.Message)"
        $operationalStatus = "BLOCKED"; $finalDetail = $_.Exception.Message; $lastFailureClassification = "SUPERVISOR_RUNTIME_FAILURE"
        if ($exitCodeForSupervisor -eq 0) { $exitCodeForSupervisor = 5 }
    }
} finally {
    if ($workerWriter) { $workerWriter.Dispose() }
    if ($writer) { $writer.Dispose() }
    if ($lock) { $lock.Dispose() }
    if ($ownsLock) {
        if (Test-Path -LiteralPath $lockPath) { Remove-Item -LiteralPath $lockPath -Force }
        $workerAlive = $false; $currentWorker = $null; $endedAt = Get-Date -Format o
        $terminalCursor = $null
        try { $terminalCursor = Get-Content -LiteralPath (Join-Path $run 'production_campaign_state.json') -Raw -Encoding utf8 | ConvertFrom-Json } catch { }
        $terminalEvent = if ($operationalStatus -eq 'BLOCKED' -or $operationalStatus -eq 'RECOVERY_REQUIRED') { 'BLOCKED' } else { 'STOPPED' }
        Write-ProductionEvent $terminalEvent $terminalCursor $null $null $null $null $finalDetail $lastFailureClassification
        Write-OperationalStatus $operationalStatus $finalDetail
        Write-Metrics 'SUPERVISOR_STOP'
        Write-Log "STOP operational=$operationalStatus detail=$finalDetail exit=$exitCodeForSupervisor"
    }
}

exit $exitCodeForSupervisor
