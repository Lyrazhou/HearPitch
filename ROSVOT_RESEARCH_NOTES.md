# ROSVOT + RMVPE integration research notes (2026-10-02)

## Verified external sources

- ROSVOT official repo: https://github.com/RickyL-2000/ROSVOT
  - States that it transcribes singing voices into note events/MIDI, including noisy/accompanied voices.
  - Provides a `checkpoints.zip` link; README identifies ROSVOT, RWBD, and RMVPE checkpoints.
  - Published inference setup tested on Python 3.9, PyTorch 2.1.1, CUDA 11.8.
  - Inference example is `python inference/rosvot.py -o OUT -p AUDIO.wav`.
  - README says weights were trained on M4Singer only; model quality should be validated on local material/languages/styles.
  - Source repo license says MIT; do not infer checkpoint redistribution rights from source-code license.
- Pinned ROSVOT main commit discovered 2026-10-02: `3c8332bf43adae35f6e4d64971862f2f6139b310`.
  - API: https://api.github.com/repos/RickyL-2000/ROSVOT/commits/main
  - Source archive: https://github.com/RickyL-2000/ROSVOT/archive/3c8332bf43adae35f6e4d64971862f2f6139b310.zip
- Pinned ROSVOT config: https://raw.githubusercontent.com/RickyL-2000/ROSVOT/3c8332bf43adae35f6e4d64971862f2f6139b310/configs/rosvot.yaml
  - Checkpoint uses 24kHz, 128 hop, 512 FFT, f0 50–900Hz, note-boundary threshold 0.8, min note duration 80 ms.
- Pinned ROSVOT inference source: https://raw.githubusercontent.com/RickyL-2000/ROSVOT/3c8332bf43adae35f6e4d64971862f2f6139b310/inference/rosvot.py
  - CUDA device hard-coded in `run_worker` (`torch.device('cuda:rank')`).
  - `max_frames` default 30,000; at 24kHz/128 hop this can truncate longer input. HearPitch adapter slices 110s chunks with 2s overlap and sets max_frames based on each chunk.
  - Single input produces `<out>/midi/output.mid`.
  - The output is MIDI, no calibrated per-note confidence.
- Official ROSVOT source license: https://raw.githubusercontent.com/RickyL-2000/ROSVOT/3c8332bf43adae35f6e4d64971862f2f6139b310/LICENSE
  - MIT for source code. Checkpoint terms are separately hosted.
- Checkpoint archive URL id from README: `1JNtNT37KiLq9uFQqHk7JFs-3trxd3bRh`.
  - Confirmed using HTTP Range that file is a ZIP, exact total 584,407,709 bytes (about 557 MiB).
  - ZIP central directory contains: `checkpoints/rmvpe/model.pt`, `checkpoints/rosvot/config.yaml`, `checkpoints/rosvot/model.pt`, `checkpoints/rwbd/config.yaml`, `checkpoints/rwbd/model.pt`.
  - Google Drive download needs the large-file `confirm=t` + `uuid` form; install script parses this warning form.
- PyTorch official cu118 Windows wheel index: https://download.pytorch.org/whl/cu118/torch/
  - Contains `torch-2.1.1+cu118-cp39-cp39-win_amd64.whl`.
- PyWORLD PyPI files: https://pypi.org/pypi/pyworld/0.2.12/json and https://pypi.org/pypi/pyworld/0.3.4/json
  - `pyworld==0.2.12` has no CPython 3.9 Windows wheel; `0.3.4` does. HF4 isolated runtime installer substitutes 0.3.4 and this substitution needs Windows regression testing.
- RMVPE paper: https://arxiv.org/abs/2306.15412
  - Pitch estimator directly targets vocal pitch in polyphonic music; it estimates F0, while ROSVOT supplies note-event timing and MIDI.

## Integration design choices

- Keep HearPitch's Python 3.11 environment unchanged.
- Install ROSVOT in `Documents/HearPitchLocal/models/rosvot/.venv` with Python 3.9 and PyTorch 2.1.1+cu118.
- Download source pinned to the commit above, and separately download/extract the model ZIP under `.../models/rosvot/source/checkpoints` because upstream inference resolves `checkpoints/...` relative to its source working directory.
- The installer displays a checkpoint SHA-256 but does not currently have an independently published upstream SHA to compare against; ZIP integrity and required-path checks are used instead.
- Adapter executes ROSVOT via a subprocess and imports resulting MIDI using mido.
- If a model does not expose note confidence, HearPitch stores null and UI shows a dash; do not fabricate confidence from volume.
- User “sensitivity” maps only to ROSVOT note-boundary threshold; it is not confidence.
- GPU acceptance testing cannot be completed on the Linux development sandbox; user's Windows CUDA machine must perform the hardware smoke test.
