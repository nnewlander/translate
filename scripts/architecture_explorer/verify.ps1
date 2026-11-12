[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
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
    & $python -m booktrans.architecture_explorer.validation --repository $repository
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
