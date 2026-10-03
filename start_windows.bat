@echo off
setlocal
cd /d "%~dp0"

if not exist "hearpitch_core\__init__.py" (
  echo [HearPitch ERROR] Hotfix 3 files are incomplete.
  echo Please fully extract the HearPitch package, then run this file again.
  pause
  exit /b 1
)

if exist "hearpitch.py" (
  echo [HearPitch] Legacy hearpitch.py detected. Hotfix 3 will ignore it safely.
  echo [HearPitch] For manual commands, use: python hearpitch_cli.py ...
)

if not exist ".venv\Scripts\python.exe" (
  echo [HearPitch] Creating Python virtual environment...
  py -3.11 -m venv .venv || python -m venv .venv
)

call ".venv\Scripts\activate.bat"
echo [HearPitch] Installing or checking dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo [HearPitch V260924A HF5] Starting local interface at http://127.0.0.1:8765
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"
python hearpitch_cli.py serve --host 127.0.0.1 --port 8765
pause
