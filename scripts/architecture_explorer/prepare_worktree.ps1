[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$createdJunction = $false
$worktreeEnvironment = $null
$sharedEnvironment = $null
$pythonPathWasSet = Test-Path Env:PYTHONPATH
$originalPythonPath = $env:PYTHONPATH

function Get-NormalizedPath {
    param([Parameter(Mandatory)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory)][string]$Repository,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    $output = @(& git -C $Repository @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Arguments -join ' '): $($output -join [Environment]::NewLine)"
    }
    return ($output -join "`n").TrimEnd()
}

try {
    $authorizedRepository = Get-NormalizedPath 'D:\translate'
    $worktree = Get-NormalizedPath (
        Resolve-Path (Join-Path $PSScriptRoot '..\..')
    )
    $gitTopLevel = Get-NormalizedPath (
        Invoke-GitText $worktree @('rev-parse', '--show-toplevel')
    )
    if ($gitTopLevel -ine $worktree) {
        throw "Script location is not the Git worktree root: $worktree"
    }

    $commonGitDirectory = Get-NormalizedPath (
        Invoke-GitText $worktree @(
            'rev-parse',
            '--path-format=absolute',
            '--git-common-dir'
        )
    )
    if ([System.IO.Path]::GetFileName($commonGitDirectory) -ine '.git') {
        throw "Git common directory is not a supported repository layout: $commonGitDirectory"
    }
    $sharedRepository = Get-NormalizedPath (
        [System.IO.Directory]::GetParent($commonGitDirectory).FullName
    )
    if ($sharedRepository -ine $authorizedRepository) {
        throw "Git common directory is not the authorized repository: $commonGitDirectory"
    }
    $allowedWorktreeRoot = Get-NormalizedPath (
        Join-Path $authorizedRepository 'worktrees'
    )
    $relativeWorktree = [System.IO.Path]::GetRelativePath(
        $allowedWorktreeRoot,
        $worktree
    )
    if (
        $relativeWorktree.StartsWith('..') -or
        [System.IO.Path]::IsPathRooted($relativeWorktree) -or
        $relativeWorktree.Contains([System.IO.Path]::DirectorySeparatorChar) -or
        $relativeWorktree.Contains([System.IO.Path]::AltDirectorySeparatorChar) -or
        $relativeWorktree -notlike 'architecture-explorer-*'
    ) {
        throw "Worktree is outside the authorized Architecture Explorer path: $worktree"
    }

    $sharedEnvironment = Get-NormalizedPath (
        Join-Path $authorizedRepository '.venv'
    )
    $sharedPython = Join-Path $sharedEnvironment 'Scripts\python.exe'
    if (
        -not (Test-Path -LiteralPath $sharedEnvironment -PathType Container) -or
        -not (Test-Path -LiteralPath $sharedPython -PathType Leaf)
    ) {
        throw "Shared project environment is unavailable: $sharedEnvironment"
    }
    & $sharedPython --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Shared project interpreter cannot start: $sharedPython"
    }

    & git -C $worktree check-ignore -q -- '.venv/'
    if ($LASTEXITCODE -ne 0) {
        throw 'Worktree Git ignore rules do not ignore .venv/'
    }
    $statusBefore = Invoke-GitText $worktree @(
        'status',
        '--porcelain=v1',
        '--untracked-files=all'
    )

    $worktreeEnvironment = Join-Path $worktree '.venv'
    $environmentItem = Get-Item -LiteralPath $worktreeEnvironment -Force -ErrorAction SilentlyContinue
    $junctionAction = 'reused'
    if ($null -eq $environmentItem) {
        New-Item -ItemType Junction -Path $worktreeEnvironment -Target $sharedEnvironment | Out-Null
        $createdJunction = $true
        $junctionAction = 'created'
        $environmentItem = Get-Item -LiteralPath $worktreeEnvironment -Force
    }
    if ($environmentItem.LinkType -ne 'Junction') {
        throw "Worktree .venv exists but is not a directory junction: $worktreeEnvironment"
    }
    $junctionTarget = Get-NormalizedPath ([string]$environmentItem.Target)
    if ($junctionTarget -ine $sharedEnvironment) {
        throw "Worktree .venv junction has an unexpected target: $junctionTarget"
    }

    $python = Join-Path $worktreeEnvironment 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Worktree project interpreter is unavailable: $python"
    }
    $sourceRoot = Get-NormalizedPath (Join-Path $worktree 'src')
    $env:PYTHONPATH = $sourceRoot
    $probeCode = @'
import json
import pathlib
import sys

expected_prefix = pathlib.Path(sys.argv[1]).resolve()
source_root = pathlib.Path(sys.argv[2]).resolve()
actual_prefix = pathlib.Path(sys.prefix).resolve()
if actual_prefix != expected_prefix:
    raise SystemExit(f"unexpected sys.prefix: {actual_prefix} != {expected_prefix}")
import booktrans
booktrans_file = pathlib.Path(booktrans.__file__).resolve()
if not booktrans_file.is_relative_to(source_root):
    raise SystemExit(
        f"booktrans import escaped worktree source: {booktrans_file} not under {source_root}"
    )
print(json.dumps({"prefix": str(actual_prefix), "booktrans": str(booktrans_file)}))
'@
    $probeOutput = @(& $python -c $probeCode $worktreeEnvironment $sourceRoot 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Project interpreter validation failed: $($probeOutput -join [Environment]::NewLine)"
    }
    $probe = ($probeOutput -join "`n") | ConvertFrom-Json

    $pipCheck = @(& $python -m pip check 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "pip check failed: $($pipCheck -join [Environment]::NewLine)"
    }

    $statusAfter = Invoke-GitText $worktree @(
        'status',
        '--porcelain=v1',
        '--untracked-files=all'
    )
    if ($statusAfter -cne $statusBefore) {
        throw 'Worktree preparation changed Git status'
    }

    Write-Output 'PROJECT_VENV_READY'
    Write-Output "junction=$junctionAction"
    Write-Output "worktree=$worktree"
    Write-Output "shared_venv=$sharedEnvironment"
    Write-Output "python=$python"
    Write-Output "python_prefix=$($probe.prefix)"
    Write-Output "booktrans=$($probe.booktrans)"
    Write-Output "PYTHONPATH=$sourceRoot"
    Write-Output 'PYTHONPATH_SCOPE=process; rerun in each new PowerShell process that needs this binding'
    exit 0
}
catch {
    $originalFailure = $_.Exception.Message
    $cleanupFailures = [System.Collections.Generic.List[string]]::new()
    try {
        if ($pythonPathWasSet) {
            $env:PYTHONPATH = $originalPythonPath
        }
        else {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
    }
    catch {
        $cleanupFailures.Add("Could not restore PYTHONPATH: $($_.Exception.Message)")
    }
    if ($createdJunction) {
        try {
            $createdItem = Get-Item -LiteralPath $worktreeEnvironment -Force -ErrorAction SilentlyContinue
            if ($null -ne $createdItem) {
                if ($createdItem.LinkType -ne 'Junction') {
                    throw "Refusing to remove a non-Junction path: $worktreeEnvironment"
                }
                $createdTarget = Get-NormalizedPath ([string]$createdItem.Target)
                if ($createdTarget -ine $sharedEnvironment) {
                    throw "Refusing to remove a Junction with an unexpected target: $createdTarget"
                }
                [System.IO.Directory]::Delete($worktreeEnvironment, $false)
            }
            if ($null -ne (Get-Item -LiteralPath $worktreeEnvironment -Force -ErrorAction SilentlyContinue)) {
                throw "Created Junction remains after rollback: $worktreeEnvironment"
            }
            if (-not (Test-Path -LiteralPath $sharedEnvironment -PathType Container)) {
                throw "Shared environment is unavailable after rollback: $sharedEnvironment"
            }
        }
        catch {
            $cleanupFailures.Add($_.Exception.Message)
        }
    }
    Write-Output 'PROJECT_VENV_UNAVAILABLE'
    [Console]::Error.WriteLine($originalFailure)
    if ($cleanupFailures.Count -gt 0) {
        Write-Output 'PROJECT_VENV_ROLLBACK_INCOMPLETE'
        foreach ($cleanupFailure in $cleanupFailures) {
            [Console]::Error.WriteLine("rollback: $cleanupFailure")
        }
    }
    exit 2
}
