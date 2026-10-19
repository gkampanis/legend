@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-browser.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto browser_failed
echo Installation complete. Chromium fallback is ready.
echo Open START_MYINVOICES.bat and use Download PDF.
pause
exit /b 0
:browser_failed
echo Libraries installed, but Chromium could not be downloaded.
echo Check your Internet connection and run INSTALL_BROWSER.bat again.
echo Installed Edge, Chrome or Brave can also be used.
pause
exit /b 1
:failed
echo Installation failed. Python 3.11 or later is required.
pause
exit /b 1
