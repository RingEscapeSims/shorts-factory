@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found - installing it now...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. CLOSE this window and double-click install.bat again.
  pause
  exit /b
)
python autopilot.py
pause
