@echo off
title BEKAP Sepet Sinyal Sistemi - Durdur
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn app:app --host 127\.0\.0\.1 --port 8766' }; if($p){$p | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}; Write-Output 'BEKAP uygulamasi durduruldu.'} else {Write-Output 'Calisan BEKAP sureci bulunamadi.'}"
pause
