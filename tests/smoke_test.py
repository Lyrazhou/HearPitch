"""Local smoke test for the HearPitch V260924A core without network access."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["HEARPITCH_HOME"] = str((Path(__file__).parent / ".test-data").resolve())

from hearpitch_core.transcription import create_project_from_audio, get_project, update_project_score  # noqa: E402
from hearpitch_core.spectrogram import render_spectrogram  # noqa: E402


def main() -> None:
    audio_path = Path(__file__).parent / "sample_scale.wav"
    result = create_project_from_audio(audio_path, title="Smoke Test Scale", max_seconds=0, prefer_basic_pitch=False)
    assert result["score"]["engine"]["name"] == "pyin"
    assert len(result["score"]["notes"]) >= 4
    for output_path in result["project"]["exports"].values():
        assert Path(output_path).exists(), output_path
    loaded = get_project(result["project"]["id"])
    source_audio = next((Path(loaded["project"]["project_dir"]) / "source").iterdir())
    spectrogram = Path(loaded["project"]["project_dir"]) / "analysis" / "test_spectrogram.png"
    render_spectrogram(source_audio, spectrogram)
    from PIL import Image
    with Image.open(spectrogram) as image:
        assert image.format == "PNG" and image.width >= 240 and image.height == 48 * 12
    loaded["score"]["notes"][0]["midi"] += 1
    loaded["score"]["beat_taps"] = [0.1, 0.7, 1.3]
    loaded["score"]["beat_markers"] = [
        {"time": 0.1, "raw_time": 0.11, "beat": 1, "bar": 1},
        {"time": 0.7, "raw_time": 0.71, "beat": 2, "bar": 1},
        {"time": 1.3, "raw_time": 1.31, "beat": 1, "bar": 2},
    ]
    loaded["score"]["meter_sequence"] = [2, 3]
    loaded["score"]["time_signature"] = {"value": "2/4", "sequence": [2, 3]}
    loaded["score"]["rhythm_quantize"] = 16
    loaded["score"]["pitch_quantize"] = "key_strict"
    loaded["score"]["tempo_mode"] = "tap"
    updated = update_project_score(result["project"]["id"], loaded["score"])
    assert updated["score"]["notes"][0]["user_edited"] is True
    assert updated["score"]["beat_taps"] == [0.1, 0.7, 1.3]
    assert updated["score"]["beat_markers"][0]["raw_time"] == 0.11
    assert updated["score"]["meter_sequence"] == [2, 3]
    assert updated["score"]["rhythm_quantize"] == 16
    assert updated["score"]["pitch_quantize"] == "key_strict"
    assert "<beats>2</beats>" in Path(updated["project"]["exports"]["musicxml"]).read_text(encoding="utf-8")
    invalid = {**updated["score"], "beat_taps": [-1.0]}
    try:
        update_project_score(result["project"]["id"], invalid)
    except Exception as exc:
        assert "打拍记录" in str(exc)
    else:
        raise AssertionError("negative beat tap must be rejected")
    print(json.dumps({"project": updated["project"]["id"], "notes": len(updated["score"]["notes"]), "exports": updated["project"]["exports"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
