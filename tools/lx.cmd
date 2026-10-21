@echo off
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%~dp0run_skill.py" %*
) else (
  python "%~dp0run_skill.py" %*
)
