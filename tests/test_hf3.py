"""Regression checks for HF3 sensitivity and visible local job behavior."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ["HEARPITCH_HOME"] = str((Path(__file__).parent / ".hf3-data").resolve())

from hearpitch_core.jobs import LocalJobManager  # noqa: E402
from hearpitch_core.transcription import (  # noqa: E402
    SENSITIVITY_PRESETS,
    create_project_from_audio,
    normalize_transcription_settings,
)


def main() -> None:
    audio = Path(__file__).parent / "sample_scale.wav"
    conservative = normalize_transcription_settings({"preset": "conservative", "engine": "pyin"})
    sensitive = normalize_transcription_settings({"preset": "sensitive", "engine": "pyin"})
    custom = normalize_transcription_settings(
        {
            "preset": "custom",
            "engine": "pyin",
            "sensitivity": 54,
            "min_note_duration_ms": 160,
            "min_confidence": 64,
            "merge_gap_ms": 77,
        }
    )
    assert conservative["sensitivity"] == SENSITIVITY_PRESETS["conservative"]["sensitivity"]
    assert sensitive["min_note_duration_ms"] < conservative["min_note_duration_ms"]
    assert custom["label"] == "自定义" and custom["merge_gap_ms"] == 77

    progress: list[tuple[int, str]] = []
    direct = create_project_from_audio(
        audio,
        title="HF3 Conservative",
        prefer_basic_pitch=False,
        transcription_settings=conservative,
        progress_callback=lambda percent, message: progress.append((percent, message)),
    )
    assert direct["score"]["transcription_settings"]["preset"] == "conservative"
    assert direct["score"]["transcription_settings"]["sensitivity"] == 40
    assert progress[-1][0] == 100 and len(progress) >= 5
    assert len(direct["score"]["notes"]) >= 4

    manager = LocalJobManager()
    staged_dir = Path(__file__).parent / ".hf3-staging"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_audio = staged_dir / audio.name
    shutil.copy2(audio, staged_audio)
    job = manager.create(
        audio_path=staged_audio,
        title="HF3 Local Job",
        max_seconds=0,
        transcription_settings=sensitive,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = manager.get(job["id"])
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job
    assert job["progress"] == 100
    assert job["project"] and Path(job["project"]["score_path"]).exists()
    assert any(entry["message"].startswith("候选谱已完成") or entry["message"].startswith("已完成") for entry in job["logs"])
    print({"direct_notes": len(direct["score"]["notes"]), "job_status": job["status"], "log_entries": len(job["logs"])})


if __name__ == "__main__":
    main()
