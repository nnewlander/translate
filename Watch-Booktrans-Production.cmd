@echo off
setlocal
set "ROOT=D:\translate"
set "WATCHER=%ROOT%\Watch-Booktrans-Production.ps1"
if not exist "%WATCHER%" (
  echo [BOOKTRANS] Watcher script not found: %WATCHER%
  pause
  exit /b 1
)
start "BOOKTRANS PRODUCTION MONITOR" cmd.exe /k powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '%WATCHER%' -Root '%ROOT%'; $code=$LASTEXITCODE; Write-Host ''; Write-Host ('Watcher finished. Exit code: ' + $code) -ForegroundColor Yellow; exit $code"
if errorlevel 1 (
  echo [BOOKTRANS] Failed to create the persistent watcher host.
  pause
  exit /b 1
)
echo [BOOKTRANS] Persistent watcher host opened. Use Ctrl+C or close the window to stop watching.
exit /b 0
