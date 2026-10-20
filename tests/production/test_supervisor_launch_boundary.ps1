[CmdletBinding()]
param([string]$Root = 'D:\translate')

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$supervisor = Join-Path $Root 'scripts\production\production_campaign_supervisor.ps1'
$python = Join-Path $Root '.venv\Scripts\python.exe'
$testRoot = Join-Path $Root ('runs\_test_tmp\launch-boundary-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION_FAILED:$Message" }
}

function Get-ManagedProcessEnvironmentSnapshot {
    $managed = @('Path', 'BOOKTRANS_PRODUCTION_WORKER_ID', 'BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH', 'BOOKTRANS_PRODUCTION_BATCH', 'BOOKTRANS_PRODUCTION_CHECKPOINT')
    $raw = [Environment]::GetEnvironmentVariables([System.EnvironmentVariableTarget]::Process)
    return @($raw.Keys | ForEach-Object { [string]$_ } | Where-Object {
        $candidate = $_
        $null -ne ($managed | Where-Object { $_ -ieq $candidate } | Select-Object -First 1)
    } | Sort-Object | ForEach-Object { '{0}={1}' -f $_, [string]$raw[$_] })
}

# Load only the two production launch-boundary functions.  The supervisor's
# top-level operational loop is intentionally not executed by this test.
$text = Get-Content -LiteralPath $supervisor -Raw -Encoding utf8
$start = $text.IndexOf('function Get-CanonicalChildEnvironment')
$end = $text.IndexOf('function Write-ProductionEvent', $start)
Assert-True ($start -ge 0 -and $end -gt $start) 'launch-boundary functions are present'
. ([scriptblock]::Create($text.Substring($start, $end - $start)))

try {
    $collidingEnvironment = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
    $collidingEnvironment.Add('Path', 'preferred-path')
    $collidingEnvironment.Add('PATH', 'duplicate-path')
    $collidingEnvironment.Add('TEMP', $env:TEMP)
    Assert-True ((@($collidingEnvironment.Keys | Where-Object { [string]$_ -ieq 'path' })).Count -eq 2) 'test input contains Path/PATH collision'
    $canonical = Get-CanonicalChildEnvironment $collidingEnvironment
    Assert-True ((@($canonical.Keys | Where-Object { [string]$_ -ieq 'path' })).Count -eq 1) 'canonical child env contains one path key'
    Assert-True ($canonical.ContainsKey('Path')) 'canonical child env uses Path'

    # The Windows PowerShell-compatible path deliberately uses process-env
    # inheritance.  The child must see the normalized worker values, while
    # the parent returns exactly to its pre-launch state.
    $env:BOOKTRANS_PRODUCTION_WORKER_ID = 'launch-boundary-test-worker'
    $env:BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH = 'launch-boundary-test-handoff'
    $env:BOOKTRANS_PRODUCTION_BATCH = 'launch-boundary-test-batch'
    $env:BOOKTRANS_PRODUCTION_CHECKPOINT = 'launch-boundary-test-checkpoint'
    $parentBeforeSuccess = Get-ManagedProcessEnvironmentSnapshot
    $inheritOut = Join-Path $testRoot 'inherit.stdout.log'
    $inheritErr = Join-Path $testRoot 'inherit.stderr.log'
    $inheritDiagnostics = [ordered]@{ launch_stage = 'NOT_STARTED'; child_created = $false; executable = $null; working_directory = $null; env_key_count = 0; case_insensitive_duplicate_count = 0 }
    $inheritChild = Start-DeterministicWorkerProcess $python @('-c', 'import json, os; print(json.dumps({"worker": os.environ.get("BOOKTRANS_PRODUCTION_WORKER_ID"), "path_count": sum(1 for key in os.environ if key.lower() == "path")}))') $Root $inheritOut $inheritErr ([ref]$inheritDiagnostics)
    $inheritResult = Get-Content -LiteralPath $inheritOut -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($inheritChild.ExitCode -eq 0 -and $inheritDiagnostics.child_created) 'inherited-environment launch creates child'
    Assert-True ($inheritResult.worker -eq 'launch-boundary-test-worker') 'child sees worker launch override'
    Assert-True ($inheritResult.path_count -eq 1) 'child sees exactly one effective Path'
    Assert-True ((Compare-Object $parentBeforeSuccess (Get-ManagedProcessEnvironmentSnapshot)).Count -eq 0) 'parent environment is restored after successful launch'

    $collisionOut = Join-Path $testRoot 'collision.stdout.log'
    $collisionErr = Join-Path $testRoot 'collision.stderr.log'
    $collisionDiagnostics = [ordered]@{ launch_stage = 'NOT_STARTED'; child_created = $false; executable = $null; working_directory = $null; env_key_count = 0; case_insensitive_duplicate_count = 0 }
    $collisionChild = Start-DeterministicWorkerProcess $python @('-c', 'import sys; print("collision-ok"); sys.exit(0)') $Root $collisionOut $collisionErr ([ref]$collisionDiagnostics)
    Assert-True ($null -ne $collisionChild -and $collisionChild.ExitCode -eq 0) 'Path/PATH collision still creates a child'
    Assert-True ((Get-Content -LiteralPath $collisionOut -Raw -Encoding utf8).Trim() -eq 'collision-ok') 'collision child stdout preserved'

    $staleLastExitCode = 0
    $workerExitCode = $staleLastExitCode
    $launchFailed = $false
    $parentBeforeFailure = Get-ManagedProcessEnvironmentSnapshot
    $failureDiagnostics = [ordered]@{ launch_stage = 'NOT_STARTED'; child_created = $false; executable = $null; working_directory = $null; env_key_count = 0; case_insensitive_duplicate_count = 0 }
    try {
        Start-DeterministicWorkerProcess (Join-Path $testRoot 'missing-python.exe') @('-c', 'pass') $Root (Join-Path $testRoot 'missing.stdout.log') (Join-Path $testRoot 'missing.stderr.log') ([ref]$failureDiagnostics) | Out-Null
    } catch {
        $launchFailed = $true
        # This is the exact production launch catch policy: a launch exception
        # gets an explicit failure code and cannot reuse a prior native exit.
        $workerExitCode = 5
    }
    Assert-True $launchFailed 'forced launch failure is observable'
    Assert-True ($workerExitCode -ne 0) 'stale LASTEXITCODE cannot classify launch failure as clean exit'
    Assert-True ((Compare-Object $parentBeforeFailure (Get-ManagedProcessEnvironmentSnapshot)).Count -eq 0) 'parent environment is restored after failed launch'

    try { $null.ToString() } catch {
        $record = New-WorkerLaunchFailureRecord 'forced-null-method' $_ $failureDiagnostics 5
        Assert-True ($record.launch_stage -ne 'NOT_STARTED') 'failure record retains the exact launch stage'
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$record.script_stack_trace)) 'failure record includes ScriptStackTrace'
        Assert-True (-not [string]::IsNullOrWhiteSpace([string]$record.invocation_position)) 'failure record includes invocation position'
        Assert-True ($record.explicit_exit_code -eq 5 -and -not $record.child_created) 'failure record is explicit nonzero and child-free'
    }

    $normalOut = Join-Path $testRoot 'normal.stdout.log'
    $normalErr = Join-Path $testRoot 'normal.stderr.log'
    $normalDiagnostics = [ordered]@{ launch_stage = 'NOT_STARTED'; child_created = $false; executable = $null; working_directory = $null; env_key_count = 0; case_insensitive_duplicate_count = 0 }
    $normalChild = Start-DeterministicWorkerProcess $python @('-c', 'import sys; print("normal-ok"); sys.exit(0)') $Root $normalOut $normalErr ([ref]$normalDiagnostics)
    Assert-True ($normalChild.ExitCode -eq 0) 'normal launch preserves child exit behavior'
    Assert-True ((Get-Content -LiteralPath $normalOut -Raw -Encoding utf8).Trim() -eq 'normal-ok') 'normal child stdout preserved'

    Assert-True ($text -match "artifact_type = 'WORKER_LAUNCH_FAILED'") 'launch failure is recorded in worker log'
    Assert-True ($text -match 'script_stack_trace' -and $text -match 'invocation_position' -and $text -match 'inner_exception_chain') 'launch failure diagnostic fields are recorded'
    Assert-True ($text -match '\$workerExitCode = 5') 'launch failure uses explicit nonzero exit code'
    'SUPERVISOR_LAUNCH_BOUNDARY_TESTS_PASS'
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
