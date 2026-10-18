@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-browser.txt
if errorlevel 1 goto failed
echo Installation complete. Edge or Chrome must be installed.
echo Open START_MYINVOICES.bat and use the browser PDF download button.
pause
exit /b 0
:failed
echo Installation failed. Python 3.11 or later is required.
pause
exit /b 1
