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


def main() -> None:
    audio_path = Path(__file__).parent / "sample_scale.wav"
    result = create_project_from_audio(audio_path, title="Smoke Test Scale", max_seconds=0, prefer_basic_pitch=False)
    assert result["score"]["engine"]["name"] == "pyin"
    assert len(result["score"]["notes"]) >= 4
    for output_path in result["project"]["exports"].values():
        assert Path(output_path).exists(), output_path
    loaded = get_project(result["project"]["id"])
    loaded["score"]["notes"][0]["midi"] += 1
    updated = update_project_score(result["project"]["id"], loaded["score"])
    assert updated["score"]["notes"][0]["user_edited"] is True
    print(json.dumps({"project": updated["project"]["id"], "notes": len(updated["score"]["notes"]), "exports": updated["project"]["exports"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
