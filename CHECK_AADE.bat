@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m myinvoices.local_check
) else (
    py -3 -m myinvoices.local_check
)
set "check_result=%errorlevel%"
pause
exit /b %check_result%
