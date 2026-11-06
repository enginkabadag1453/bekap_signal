@echo off
title BEKAP Sepet Sinyal Sistemi
cd /d "%~dp0"
if exist provider.env (
  for /f "usebackq tokens=1,* delims==" %%A in ("provider.env") do if not "%%A"=="" set "%%A=%%B"
)
 powershell -NoProfile -Command "if (Test-NetConnection 127.0.0.1 -Port 8766 -InformationLevel Quiet) { exit 0 } else { exit 1 }"
if %errorlevel%==0 goto already_running
start http://127.0.0.1:8766
python -m uvicorn app:app --host 127.0.0.1 --port 8766
pause
goto end
:already_running
echo BEKAP Sepet uygulamasi zaten calisiyor: http://127.0.0.1:8766
start http://127.0.0.1:8766
:end
