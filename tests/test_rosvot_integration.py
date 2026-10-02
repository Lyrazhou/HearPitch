"""CPU-only contract tests for ROSVOT MIDI normalization and optional engine status."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HEARPITCH_HOME"] = str(Path(__file__).parent / ".rosvot-test-data")

import mido
from hearpitch_core.rosvot_engine import midi_to_score_notes, rosvot_model_status
from hearpitch_core import transcription
from hearpitch_core.transcription import (
    TranscriptionError,
    _merge_nearby_same_pitch_notes,
    estimate_key,
    normalize_transcription_settings,
    rule_findings,
)


def make_midi(path: Path):
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.Message("note_on", note=62, velocity=80, time=0))
    track.append(mido.Message("note_off", note=62, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.save(path)


def midi_with_tempo_changes(path: Path):
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("set_tempo", tempo=1000000, time=0))
    track.append(mido.Message("note_on", note=62, velocity=80, time=0))
    track.append(mido.Message("note_off", note=62, velocity=0, time=480))
    midi.save(path)


def main():
    with tempfile.TemporaryDirectory() as folder:
        midi_path = Path(folder) / "output.mid"
        make_midi(midi_path)
        notes = midi_to_score_notes(midi_path, min_duration_ms=100, merge_gap_ms=50)
        assert [n["midi"] for n in notes] == [60, 62]
        assert [round(n["onset"], 2) for n in notes] == [0.0, 0.5]
        assert notes[0]["confidence"] is None
        assert notes[0]["name"] == "C4"
        assert not any("置信度" in item["reason"] for item in rule_findings(notes))
        assert estimate_key(notes)["label"]
        tempo_path = Path(folder) / "tempo.mid"
        midi_with_tempo_changes(tempo_path)
        tempo_notes = midi_to_score_notes(tempo_path, min_duration_ms=100, merge_gap_ms=0)
        assert round(tempo_notes[1]["onset"], 2) == 0.5
        assert round(tempo_notes[1]["offset"], 2) == 1.5
        merged = _merge_nearby_same_pitch_notes([notes[0], {**notes[0], "onset": .55, "offset": 1.2, "duration": .65}], .1)
        assert len(merged) == 1 and merged[0]["confidence"] is None

        # Exercise the same project-building/export path without requiring the
        # user's Windows CUDA device; only the model subprocess is substituted.
        import numpy as np
        import soundfile as sf
        audio = Path(folder) / "solo.wav"
        sf.write(audio, np.zeros(24_000, dtype=np.float32), 24_000)
        from hearpitch_core import rosvot_engine
        original = rosvot_engine.transcribe_with_rosvot
        rosvot_engine.transcribe_with_rosvot = lambda *args, **kwargs: (notes, "rosvot-rmvpe")
        try:
            project = transcription.create_project_from_audio(
                audio,
                title="ROSVOT integration",
                transcription_settings=normalize_transcription_settings({"engine": "rosvot-rmvpe"}),
            )
            assert project["score"]["engine"]["name"] == "rosvot-rmvpe"
            assert project["score"]["notes"][0]["confidence"] is None
            assert all(Path(path).is_file() for path in project["project"]["exports"].values())
        finally:
            rosvot_engine.transcribe_with_rosvot = original

    try:
        normalize_transcription_settings({"engine": "invalid"})
    except TranscriptionError:
        pass
    else:
        raise AssertionError("invalid model selection should fail clearly")
    status = rosvot_model_status()
    assert status["installed"] is False
    print({"midi_notes": len(notes), "confidence": notes[0]["confidence"], "optional_model_installed": status["installed"]})


if __name__ == "__main__":
    main()
