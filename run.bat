@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem This file must stay pure ASCII with CRLF line endings:
rem any non-ASCII text makes cmd.exe mis-read the file and fail.
rem Launch with pythonw.exe (no console window) and detach with start,
rem so this cmd window closes right away and closing it never kills the app.

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%PYW%" goto launch

for %%I in (pythonw.exe) do set "PYW=%%~$PATH:I"
if exist "%PYW%" goto launch

echo.
echo [ERROR] pythonw.exe not found.
echo  1. Install Python 3.10+ (tick "Add python.exe to PATH") and Chrome
echo  2. Run:  python -m pip install -r requirements.txt
echo  See README.md for details.
echo.
pause
exit /b 1

:launch
start "" "%PYW%" "%~dp0main.py"
exit /b 0
