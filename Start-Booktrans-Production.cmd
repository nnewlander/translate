@echo off
setlocal
set "ROOT=D:\translate"
set "RUN=%ROOT%\runs\full-book-20260916T000200Z-decb09"
set "SUPERVISOR=%ROOT%\scripts\production\production_campaign_supervisor.ps1"
if not exist "%SUPERVISOR%" (
  echo [BOOKTRANS] Supervisor script not found: %SUPERVISOR%
  pause
  exit /b 1
)
if exist "%RUN%\production_campaign_worker.lock" (
  echo [BOOKTRANS] Writer lock is present. A second writer was NOT started.
  echo Open Watch-Booktrans-Production.cmd to inspect the existing supervisor.
  pause
  exit /b 10
)
start "BOOKTRANS PRODUCTION SUPERVISOR" cmd.exe /k powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '%SUPERVISOR%' -Root '%ROOT%'; $code=$LASTEXITCODE; Write-Host ''; Write-Host ('Supervisor finished. Exit code: ' + $code) -ForegroundColor Yellow; exit $code"
if errorlevel 1 (
  echo [BOOKTRANS] Failed to create the persistent supervisor host.
  pause
  exit /b 1
)
echo [BOOKTRANS] Persistent supervisor host opened.
exit /b 0
