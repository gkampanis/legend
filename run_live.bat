@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m myinvoices --live
) else (
    py -3 -m myinvoices --live
)
if errorlevel 1 pause
