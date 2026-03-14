@echo off
setlocal
cd /d "%~dp0"
set "VIDEOMEMO_HOST=127.0.0.1"
set "VIDEOMEMO_PORT=8765"
set "VIDEOMEMO_URL=http://127.0.0.1:8765/?mode=desktop"
set "VIDEOMEMO_API=http://127.0.0.1:8765/api/config"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" /D "%~dp0" pythonw "%~dp0desktop_app.py" --port %VIDEOMEMO_PORT% --no-open-browser
) else (
  start "" /D "%~dp0" python "%~dp0desktop_app.py" --port %VIDEOMEMO_PORT% --no-open-browser
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$api='%VIDEOMEMO_API%'; $url='%VIDEOMEMO_URL%'; $deadline=(Get-Date).AddSeconds(15); while((Get-Date) -lt $deadline){ try { Invoke-WebRequest -Uri $api -UseBasicParsing | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 500 } } exit 1"
if errorlevel 1 (
  echo VideoMemo failed to open. Check .videomemo-data\launcher.log
  timeout /t 5 >nul
)
endlocal
