@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo HearPitch Local - Optional ROSVOT + RMVPE CUDA setup
echo This creates an isolated Python 3.9 environment and downloads
echo model weights separately. It does not modify HearPitch .venv.
echo The checkpoint archive is approximately 557 MiB.
echo ============================================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Please run start_windows.bat once first.
  pause
  exit /b 1
)

choice /C YN /N /M "Continue with ROSVOT CUDA and model installation? [Y/N] "
if errorlevel 2 exit /b 0

call ".venv\Scripts\activate.bat"
python scripts\install_rosvot.py
if errorlevel 1 (
  echo.
  echo ROSVOT installation failed. The existing HearPitch environment is unchanged.
  pause
  exit /b 1
)
echo.
echo Close this window and restart HearPitch to use ROSVOT + RMVPE.
pause
