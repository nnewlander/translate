param([ValidateSet('baseline','diagnostic')][string]$Mode='baseline',[Parameter(Mandatory)][string]$RunTag,[int]$Groups=5)
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$ProgressPreference='SilentlyContinue'
$root=(Get-Location).Path
$artifacts=Join-Path $root 'output/retro-r3'
$session=Join-Path $artifacts "$Mode-$RunTag"
if (Test-Path -LiteralPath $session) {throw 'Existing measurement session'}
New-Item -ItemType Directory -Path $session | Out-Null
$commit=(& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -ne '50bff1145d1533db28610ada5fe9c89eb4a2c7ff') {throw 'Unexpected fixed baseline'}
$status=(& git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $status) {throw 'Unexpected worktree changes'}
$env:PYTHONDONTWRITEBYTECODE='1'
& .\scripts\architecture_explorer\prepare_worktree.ps1 *> (Join-Path $session 'environment.log')
if ($LASTEXITCODE) {throw 'PROJECT_VENV_UNAVAILABLE'}
if ($env:PYTHONPATH -ne (Join-Path $root 'src')) {throw 'PROJECT_VENV_UNAVAILABLE: missing process source binding'}
$python=(Resolve-Path '.venv\Scripts\python.exe').Path
& $python -c "import pathlib,sys,booktrans,pytest; expected=pathlib.Path('.venv').resolve(); assert pathlib.Path(sys.prefix).resolve() == expected; assert pathlib.Path(booktrans.__file__).resolve().is_relative_to(pathlib.Path('src').resolve()); print(sys.executable); print(sys.prefix); print(sys.version); print(pytest.__version__); print(booktrans.__file__)" *> (Join-Path $session 'interpreter.log')
if ($LASTEXITCODE) {throw 'PROJECT_VENV_UNAVAILABLE'}
$tempRoot="D:\translate\output\r3-$RunTag"
if (Test-Path -LiteralPath $tempRoot) {throw 'Existing temporary root'}
New-Item -ItemType Directory -Path $tempRoot | Out-Null
if ($Mode -eq 'diagnostic') { $env:PYTHONPATH += ";$artifacts" }
$order=0
for ($group=1; $group -le $Groups; $group++) {
    $variants = if ($group % 2) { @('approved','rejected') } else { @('rejected','approved') }
    foreach ($variant in $variants) {
        $order++
        $sample = '{0:d2}-{1}' -f $order,$variant
        $temp=Join-Path $tempRoot ('{0:d2}' -f $order)
        if (Test-Path -LiteralPath $temp) {throw 'Expected a fresh basetemp'}
        $node="tests/architecture_explorer/test_t8_security_repairs.py::test_github_import_flows_through_offline_build_to_generated_site[$variant]"
        $arguments=@('-m','pytest','-q','--durations=0','--durations-min=0','--basetemp',$temp,$node)
        if ($Mode -eq 'diagnostic') {
            $env:R3_DIAG_OUT=Join-Path $session "$sample-trace.json"
            $arguments+=@('-p','r3_trace')
        }
        $out=Join-Path $session "$sample-stdout.txt"
        $err=Join-Path $session "$sample-stderr.txt"
        $started=Get-Date -Format o
        Write-Output "$started $Mode $sample started"
        $watch=[Diagnostics.Stopwatch]::StartNew()
        & $python @arguments > $out 2> $err
        $code=$LASTEXITCODE
        $watch.Stop()
        [ordered]@{mode=$Mode; commit=$commit; group=$group; order=$order; variant=$variant; started=$started; ended=(Get-Date -Format o); seconds=$watch.Elapsed.TotalSeconds; exitCode=$code; node=$node; arguments=$arguments; interpreter=$python; basetemp=$temp; stdout=(Split-Path $out -Leaf); stderr=(Split-Path $err -Leaf)} | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $session 'runs.jsonl') -Encoding utf8
        Write-Output "$sample exit=$code wall=$($watch.Elapsed.TotalSeconds)"
        if ($code -ne 0) {throw "pytest sample failed: $sample"}
        if ($Mode -eq 'diagnostic' -and !(Test-Path -LiteralPath $env:R3_DIAG_OUT)) {throw 'Missing diagnostic trace'}
    }
}
git status --porcelain=v1 --untracked-files=all | Set-Content -LiteralPath (Join-Path $session 'git-status-after.txt')



