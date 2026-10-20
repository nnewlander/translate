[CmdletBinding()]
param([string]$Root = 'D:\translate')

$ErrorActionPreference = 'Stop'
try {
    Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
    if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) { throw 'GET_FILE_HASH_UNAVAILABLE_AFTER_UTILITY_IMPORT' }
    & (Join-Path $PSScriptRoot 'test_production_campaign_supervisor_control.ps1') -Root $Root
    'SUPERVISOR_CONTROL_HOST_PASS'
    exit 0
} catch {
    "SUPERVISOR_CONTROL_HOST_FAIL:$($_.Exception.Message)"
    exit 1
}
