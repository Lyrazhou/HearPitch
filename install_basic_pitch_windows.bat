@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Please run start_windows.bat once first to create the local Python environment.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
echo [HearPitch] Installing optional Basic Pitch engine...
python -m pip install basic-pitch
if errorlevel 1 (
  echo.
  echo Basic Pitch installation failed. HearPitch can still use the built-in pYIN fallback.
  echo See README.md for compatible Python and TensorFlow guidance.
)
pause
