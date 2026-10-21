@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
call "%~dp0..\..\..\tools\lx.cmd" init %*
exit /b %ERRORLEVEL%
