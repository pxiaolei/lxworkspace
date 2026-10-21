@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo FAIL: lx-haibao venv Python was not found: "%VENV_PY%"
  echo Run first: check_runtime.cmd --install --confirmed
  exit /b 1
)

set "PYTHONNOUSERSITE=1"
"%VENV_PY%" "%SCRIPT_DIR%scripts\run_poster_batch.py" %*
exit /b %ERRORLEVEL%
