[CmdletBinding()]
param(
    [string]$CandidateHead,
    [string]$CandidateRecordPath,
    [string]$TargetContract = 'architecture-explorer/v3',
    [string]$ManifestPath = 'architecture-explorer/manifests/v3-ticket-06.v1.json'
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repository '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Output 'ARCHITECTURE_EXPLORER_VALIDATION v1'
    Write-Output "environment: BLOCKED - project Python is unavailable at $python"
    Write-Output 'static: NOT_RUN - environment blocked'
    Write-Output 'architecture-explorer: NOT_RUN - environment blocked'
    Write-Output 'firefox: NOT_RUN - Owner manual acceptance only'
    Write-Output 'edge: NOT_RUN - supplemental browser not requested'
    Write-Output 'non-explorer: NOT_RUN - translation full-repository tests are outside this check'
    Write-Output "FIRST_REASON: project Python is unavailable at $python"
    Write-Output 'RESULT: BLOCKED (exit 2)'
    exit 2
}

$env:PYTHONPATH = (Resolve-Path (Join-Path $repository 'src')).Path
Push-Location $repository
try {
    $validationArguments = @(
        '-m',
        'booktrans.architecture_explorer.validation',
        '--repository',
        $repository
    )
    if ($CandidateHead -or $CandidateRecordPath) {
        if (-not $CandidateHead -or -not $CandidateRecordPath) {
            throw 'CandidateHead and CandidateRecordPath must be provided together.'
        }
        $recordPath = if ([System.IO.Path]::IsPathRooted($CandidateRecordPath)) {
            $CandidateRecordPath
        }
        else {
            Join-Path $repository $CandidateRecordPath
        }
        $validationArguments += @(
            '--candidate-head',
            $CandidateHead,
            '--target-contract',
            $TargetContract,
            '--manifest-path',
            $ManifestPath,
            '--candidate-record',
            $recordPath
        )
    }
    & $python @validationArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
