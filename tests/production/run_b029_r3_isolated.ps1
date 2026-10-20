[CmdletBinding()]
param(
    [string]$Root = "D:\translate"
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "PYTHON_MISSING:$python" }

# R3 exercises heavyweight, real immutable checkpoint snapshots.  Execute
# each persisted-state scenario in its own pytest interpreter: this preserves
# the contract under test without coupling correctness to Windows deep-tree
# teardown behavior in a shared pytest process.
$nodes = @(
    'test_b029_current_human_supersession_makes_historical_recovery_inert',
    'test_b029_r2_projection_is_pure_repeatable_and_does_not_mutate_source',
    'test_b029_r2_legacy_and_pure_reconstruction_have_identical_recovery_identity',
    'test_historical_review_hydration_cannot_override_newer_stage_evidence',
    'test_b029_r1_timeslice_projects_corrective_and_legacy_matches_pure'
)
$results = @()
foreach ($node in $nodes) {
    & $python -m pytest ("tests\production\test_b029_canonical_recovery_projection.py::{0}" -f $node) -q -p no:cacheprovider
    $exit = $LASTEXITCODE
    $results += [ordered]@{ node_id = $node; exit_code = $exit; verdict = if ($exit -eq 0) { 'PASS' } else { 'FAIL' } }
    if ($exit -ne 0) {
        [pscustomobject]@{ artifact_type = 'R3_ISOLATED_PYTEST_RESULT'; scenarios = $results; verdict = 'FAIL' } | ConvertTo-Json -Depth 4
        exit $exit
    }
}
[pscustomobject]@{ artifact_type = 'R3_ISOLATED_PYTEST_RESULT'; scenarios = $results; verdict = 'PASS' } | ConvertTo-Json -Depth 4
