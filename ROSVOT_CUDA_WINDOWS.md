# HearPitch Local — ROSVOT + RMVPE (CUDA) Windows Setup

## What this optional engine does

HearPitch uses the ROSVOT singing-transcription model. ROSVOT predicts note events and MIDI and uses RMVPE internally for vocal pitch. It is not merely a pitch tracker. Its project reports evaluation on noisy/accompanied singing, but those reports do not guarantee accuracy on an arbitrary user's recording.

The model checkpoint is trained with M4Singer. Expect domain differences between singing styles, microphones, languages, and heavily mixed commercial songs. Model output does not include calibrated per-note confidence, so HearPitch deliberately displays `—` rather than inventing a confidence value.

## Requirements

- Windows 10/11, 64-bit.
- NVIDIA GPU, installed NVIDIA driver, and `nvidia-smi` working.
- Existing HearPitch HF3 package, installed and run once.
- Python **3.9, 3.10 or 3.11 x64** installed. The installer prefers 3.9, then 3.10, then 3.11; the main HearPitch Python environment is not changed.
- Internet access for the first installation.
- About 5 GB free disk space is recommended. The official checkpoint archive is about 557 MiB; PyTorch CUDA wheels and its separate environment require additional space.

The installer uses PyTorch 2.1.1 + CUDA 11.8 wheels. You generally need a compatible NVIDIA **driver**, not a separately installed CUDA toolkit. An old driver may not load this runtime.

## Install

1. Start HearPitch with `start_windows.bat` once, then close its command window.
2. Confirm a compatible Python is installed. Python 3.9 is preferred, but 3.10/3.11 also work:

   ```powershell
   py -0p
   py -3.11 -c "import sys; print(sys.version); print(sys.executable)"
   nvidia-smi
   ```

3. Double-click `install_rosvot_cuda_windows.bat` and confirm the optional setup. The installer:
   - creates a separate environment under `Documents\HearPitchLocal\models\rosvot\.venv`;
   - installs pinned PyTorch CUDA 11.8 and ROSVOT inference dependencies there;
   - downloads upstream ROSVOT source at revision `3c8332bf43adae35f6e4d64971862f2f6139b310`;
   - downloads the model checkpoint archive from the link currently provided in the ROSVOT repository;
   - verifies ZIP integrity and required model files;
   - checks `torch.cuda.is_available()` and prints the detected GPU.
4. Restart HearPitch.
5. Select **ROSVOT + RMVPE（歌声转谱）**. The page displays whether the local CUDA model is ready.

The model archive is downloaded separately and is **not bundled in the HearPitch source/upgrade ZIP**. Checkpoint redistribution permissions are not assumed from the ROSVOT source-code MIT license; this installer downloads directly to your own computer.

## Recommended first test

Use a 20–60 second clip first. A clean solo vocal is the clearest baseline; then test a vocal mixed with accompaniment. Compare the MIDI against what you hear and keep a few representative examples for later regression checks.

The ROSVOT adapter processes long audio in approximately 45-second chunks with 2 seconds of overlap. Longer files take longer and may cause CUDA out-of-memory errors depending on GPU memory. If that happens, start with shorter excerpts. The overlap merge is pragmatic boundary handling; inspect notes near chunk joins.

The HearPitch sensitivity control changes ROSVOT's note-boundary threshold. Higher sensitivity lowers the threshold and tends to create more note candidates; it is not a model-confidence score. The minimum note duration and same-pitch merge settings still apply. The pYIN “minimum confidence” filter is not treated as ROSVOT confidence.

## Command-line use

```powershell
python hearpitch_cli.py transcribe "C:\Music\vocal.wav" --engine rosvot-rmvpe --preset conservative
```

## Troubleshooting

- **No CUDA / `torch.cuda.is_available()` is false:** update NVIDIA drivers, restart Windows, and re-run the installer. Do not install a different Torch into HearPitch's main `.venv`.
- **No compatible Python found:** install Python 3.9, 3.10 or 3.11 x64 and Python Launcher. The installer automatically chooses the first available version in that order; the main application environment is never replaced.
- **Out of memory:** test a shorter audio file and close other GPU-intensive applications.
- **Missing model/config file:** run the installer again; inspect `Documents\HearPitchLocal\models\rosvot\source\checkpoints`.
- **Accuracy is still not good:** compare clean vocal vs accompanied input and inspect mistakes near note transitions. This is a candidate transcription model, not guaranteed sheet music.
- **Want to revert:** choose Basic Pitch or pYIN. The optional environment can remain installed; it does not affect those engines.

For the first run on the NVIDIA PC, follow the [HF4 Windows CUDA acceptance checklist](HF4_WINDOWS_ACCEPTANCE.md). The development sandbox does not have the user's GPU and cannot certify that step.

## Privacy and permissions

Audio is processed by ROSVOT locally. The installer fetches code and pretrained checkpoints from upstream links. It does not upload the user's recordings. ROSVOT code declares MIT; pretrained-weight license/redistribution terms are a separate question. Keep upstream copyright/license notices with the installed source and do not redistribute the model archive unless its terms have been confirmed.

## Sources

- ROSVOT upstream repository and checkpoint link: <https://github.com/RickyL-2000/ROSVOT>
- RMVPE paper: <https://arxiv.org/abs/2306.15412>
- PyTorch CUDA 11.8 wheel index: <https://download.pytorch.org/whl/cu118/torch/>
- Pinned ROSVOT source revision: <https://github.com/RickyL-2000/ROSVOT/tree/3c8332bf43adae35f6e4d64971862f2f6139b310>

---

**Important:** Current implementation calls upstream ROSVOT's CUDA inference entry point through an isolated subprocess. GPU inference cannot be fully validated in this Linux development sandbox; after installation, the first Windows run is the hardware acceptance test.

**Python dependency note:** ROSVOT's upstream Linux instructions specify Python 3.9, PyTorch 2.1.1 and CUDA 11.8. This installer prefers Python 3.9 but also supports 3.10/3.11 because many Windows installations do not include 3.9. It uses `pyworld` 0.3.4 because 0.2.12 does not publish a CPython 3.9 Windows wheel. This compatibility substitution must be included in local regression testing.

**ROSVOT result confidence:** The upstream inference output is MIDI/note events, not calibrated note confidence. HearPitch keeps confidence unset for this engine.

**Long audio:** HearPitch divides longer inputs into overlapping segments to avoid the upstream default frame limit silently truncating them. Segmented inference may still alter boundary notes; inspect joins and retry shorter clips if needed.
