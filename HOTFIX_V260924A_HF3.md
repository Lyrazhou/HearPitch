# HearPitch Local V260924A — Hotfix 3

This is a **complete upgrade package**, not a differential patch. It includes the collision-resistant `hearpitch_core` package introduced in Hotfix 2, plus the fixes and features below.

## Fixed

### AI settings dialog

- “关闭” is now a real close-only button. It does not submit or duplicate a profile.
- “安全保存配置” is the only save action.
- A failed API Key save now shows the specific reason instead of silently appearing to save.
- API Key status is displayed as saved / missing / credential manager unavailable.
- API Key is never written to `profiles.json`.

> On Windows, API Key storage uses **Windows Credential Manager**. If saving a key still fails, confirm that the Windows **Credential Manager** service is running. You can still save an endpoint/model profile without a key and return later.

### Visible local transcription jobs

After clicking “开始本机转谱”, the page now displays:

- current stage and percentage;
- elapsed time;
- recent activity log messages;
- explicit failures, such as choosing Basic Pitch before installing it.

The command window must stay open while a job is running. Finished projects remain stored in `Documents\HearPitchLocal`.

## New recognition controls

The upload card now has:

- **保守 / 平衡 / 敏感** presets;
- numeric **识别灵敏度** from 0–100;
- advanced controls for minimum note duration, minimum pitch confidence, and same-pitch merge gap;
- **自动 / Basic Pitch / pYIN** engine choice.

The default is **保守** to reduce false short notes. Higher sensitivity means more note candidates, not guaranteed higher accuracy.

See `ACCURACY_AND_MODEL_GUIDE.md` for practical settings and model tradeoffs.

## Upgrade steps

1. Close any running HearPitch command window.
2. Download and fully extract this HF3 archive into a **new empty folder**.
3. Run `start_windows.bat` from the new folder.
4. Wait for dependency installation on the first run, then open `http://127.0.0.1:8765`.

Your existing local projects in `Documents\HearPitchLocal` are retained.

## Command-line example

```powershell
python hearpitch_cli.py transcribe "C:\Music\my_melody.wav" --preset conservative --engine pyin
python hearpitch_cli.py transcribe "C:\Music\my_melody.wav" --preset sensitive --sensitivity 78 --min-note-duration-ms 90 --min-confidence 50
```

Do not execute the obsolete `hearpitch.py` launcher. Use `start_windows.bat` or `hearpitch_cli.py`.
