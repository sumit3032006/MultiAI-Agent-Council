@echo off
setlocal
cd /d "%~dp0"
title Multi-AI Agent Council

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during installation. Then run START.bat again.
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.11 or newer is required. Your version:
  python --version
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment ...
  python -m venv .venv
  if errorlevel 1 ( echo [ERROR] Could not create the virtual environment. & pause & exit /b 1 )
)

if not exist ".venv\.deps_ok" (
  echo Installing dependencies - this can take a few minutes the first time ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo [ERROR] Dependency installation failed. Check your internet connection. & pause & exit /b 1 )
  echo ok> ".venv\.deps_ok"
)

if not exist ".env" (
  echo.
  echo [NOTICE] No .env file found - no API keys are configured yet.
  echo   1. Rename  .env.example  to  .env
  echo   2. Open .env in Notepad and paste your free API keys
  echo   3. Save the file and run START.bat again
  echo See SETUP_GUIDE.md, section 6 and 7. Opening the dashboard anyway so you can look around.
  echo.
)

echo Starting the dashboard. Open this address if the browser does not open by itself:
echo     http://127.0.0.1:7860/?__theme=dark
echo Press Ctrl+C in this window to stop the application.
echo.
".venv\Scripts\python.exe" app.py
pause
