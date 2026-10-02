# HF4 Windows CUDA acceptance checklist

This checklist is for the first run on the user's own NVIDIA Windows PC. The Linux build sandbox cannot certify the CUDA portion.

## Before installation

- Run `nvidia-smi` and confirm the NVIDIA GPU and driver are shown.
- Run `py -3.9-64 -c "import sys; print(sys.version); print(sys.executable)"` and confirm Python 3.9 x64.
- Close the HearPitch service before installing the optional model.
- Ensure several GB of free space; model ZIP itself is 584,407,709 bytes.

## Install and verify

- Run `install_rosvot_cuda_windows.bat` from the extracted HF4 project folder.
- Confirm the installer prints `cuda True` and the GPU model name.
- Restart HearPitch and check `/api/engines/rosvot-rmvpe` reports `installed: true` and `cuda_available: true`.
- In the UI, select ROSVOT + RMVPE and transcribe a 20–60 second clear solo vocal.
- Confirm a new project appears; notes, source audio playback, MIDI, MusicXML, and JSON exports work.
- Inspect note boundaries, pitch, phrase gaps, and especially transitions at approximately 45-second segment joins on a longer file.

## Report-back diagnostics

If the check fails, preserve the exact final error and record:

```powershell
nvidia-smi
py -0p
Documents\HearPitchLocal\models\rosvot\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO_CUDA')"
```

Model logs are temporary and cleaned after each task; the task error includes the final part of the ROSVOT log. Do not paste any API keys or secrets into diagnostics.
