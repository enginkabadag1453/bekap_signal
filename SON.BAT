@echo off
setlocal
title BEKAP Sepet Sinyal Sistemi
set "PROJECT_DIR=%~dp0"
call "%PROJECT_DIR%start.bat"
exit /b %errorlevel%
