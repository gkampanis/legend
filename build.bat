@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" tools\build_windows.py
if errorlevel 1 goto failed
echo Built dist\myInvoices\myInvoices.exe - keep the entire directory together.
exit /b 0
:failed
echo Build failed. No verified Windows artifact has been produced.
exit /b 1
