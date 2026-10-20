@echo off
setlocal
set "ROOT=D:\translate"
set "STOPPER=%ROOT%\Stop-Booktrans-Production.ps1"
if not exist "%STOPPER%" (
  echo [BOOKTRANS] Stop script not found: %STOPPER%
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& '%STOPPER%' -Root '%ROOT%'; $code=$LASTEXITCODE; Write-Host ''; Write-Host ('Stop command finished. Exit code: ' + $code) -ForegroundColor Yellow; exit $code"
set "STOP_EXIT=%ERRORLEVEL%"
echo [BOOKTRANS] Stop command returned. Review the message above.
pause
exit /b %STOP_EXIT%
