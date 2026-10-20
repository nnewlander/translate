[CmdletBinding()]
param([string]$Root = "D:\translate")

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$run = Join-Path $Root "runs\full-book-20260916T000200Z-decb09"
$lockPath = Join-Path $run "production_campaign_worker.lock"
$statusPath = Join-Path $run "production_campaign_supervisor_status.json"
$requestPath = Join-Path $run "production_campaign_stop.request"

$status = $null
if (Test-Path -LiteralPath $statusPath) {
    try { $status = Get-Content -LiteralPath $statusPath -Raw -Encoding utf8 | ConvertFrom-Json } catch { }
}
$pidAlive = $status -and $status.supervisor_pid -and (Get-Process -Id $status.supervisor_pid -ErrorAction SilentlyContinue)
if (-not (Test-Path -LiteralPath $lockPath) -and -not $pidAlive) {
    Write-Host "当前没有运行中的 Booktrans Supervisor；未创建停止请求。"
    exit 0
}
try {
    $stream = [System.IO.File]::Open($requestPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, $utf8NoBom)
        $writer.WriteLine((([ordered]@{ requested_at = Get-Date -Format o; requested_by_pid = $PID; request = "GRACEFUL_STOP" }) | ConvertTo-Json -Compress))
        $writer.Flush()
    } finally {
        if ($writer) { $writer.Dispose() } elseif ($stream) { $stream.Dispose() }
    }
    Write-Host "已提交安全停止请求。当前 worker 会在完成本次安全事务并刷新 state 后停止；不会被强制终止。"
} catch [System.IO.IOException] {
    Write-Host "安全停止请求已存在；Supervisor 会在下一个安全换班边界处理。"
}
