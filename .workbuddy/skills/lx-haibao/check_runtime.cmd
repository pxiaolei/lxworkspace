@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PY_CMD="

where py >nul 2>nul
if not errorlevel 1 (
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=py -3"
  )
)

if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=python"
  )
)

if not defined PY_CMD (
  where python3 >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=python3"
  )
)

if not defined PY_CMD (
  echo FAIL: Python 3 was not found. Install Python 3, then rerun this command.
  exit /b 127
)

call %PY_CMD% "%SCRIPT_DIR%scripts\check_runtime.py" %*
exit /b %ERRORLEVEL%
