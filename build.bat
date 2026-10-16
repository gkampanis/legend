@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 exit /b 1
call .venv\Scripts\activate.bat
python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
set FILE_ORGANIZER_REQUIRE_GUI_TESTS=1
python -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name FileOrganizer main.py
if errorlevel 1 exit /b 1
echo Ready: dist\FileOrganizer.exe
