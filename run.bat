@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem This file must stay pure ASCII with CRLF line endings:
rem any non-ASCII text makes cmd.exe mis-read the file and fail.

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0main.py"
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start.
  echo  1. Install Python 3.10+ and Chrome
  echo  2. Run:  python -m pip install -r requirements.txt
  echo  See README.md for details.
  echo.
  pause
)
