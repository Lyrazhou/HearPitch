"""Local-first monophonic transcription engine for HearPitch V260924A.

Primary engine: Spotify Basic Pitch when installed.
Fallback engine: librosa pYIN, designed for clean monophonic recordings.

The fallback produces a *candidate score*. It deliberately preserves confidence,
raw timing, and audit metadata so that the user can correct the result instead of
being presented with false certainty.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import APP_VERSION, projects_root

TARGET_SAMPLE_RATE = 22050
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
SUPPORTED_INPUT_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
ProgressCallback = Callable[[int, str], None]

# Higher sensitivity intentionally lowers the pYIN voiced-frame threshold. It
# finds weak and short notes more readily, but it can also turn breath, vibrato,
# room noise, or accompaniment leakage into candidate notes. The numeric values
# below are persisted with a project, so a result can always be reproduced.
SENSITIVITY_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "label": "保守",
        "sensitivity": 40,
        "min_note_duration_ms": 180,
        "min_confidence": 70,
        "merge_gap_ms": 90,
    },
    "balanced": {
        "label": "平衡",
        "sensitivity": 65,
        "min_note_duration_ms": 140,
        "min_confidence": 60,
        "merge_gap_ms": 80,
    },
    "sensitive": {
        "label": "敏感",
        "sensitivity": 82,
        "min_note_duration_ms": 80,
        "min_confidence": 45,
        "merge_gap_ms": 55,
    },
}
DEFAULT_TRANSCRIPTION_SETTINGS = {"preset": "conservative", "engine": "auto", **SENSITIVITY_PRESETS["conservative"]}


class TranscriptionError(RuntimeError):
    """Raised for a recoverable transcription or input failure."""


def _report(progress_callback: ProgressCallback | None, percent: int, message: str) -> None:
    if progress_callback:
        progress_callback(int(np.clip(percent, 0, 100)), message)


def normalize_transcription_settings(incoming: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and freeze user-facing recognition controls for a job."""
    incoming = incoming or {}
    preset = str(incoming.get("preset") or DEFAULT_TRANSCRIPTION_SETTINGS["preset"])
    base = dict(SENSITIVITY_PRESETS.get(preset, SENSITIVITY_PRESETS["conservative"]))
    base["preset"] = preset if preset in SENSITIVITY_PRESETS else "custom"
    engine = str(incoming.get("engine") or "auto").lower()
    if engine not in {"auto", "basic-pitch", "pyin", "rosvot-rmvpe"}:
        raise TranscriptionError("未知转录引擎。请选择自动、Basic Pitch、pYIN 或 ROSVOT+RMVPE。")
    base["engine"] = engine

    limits = {
        "sensitivity": (0, 100, int),
        "min_note_duration_ms": (50, 500, int),
        "min_confidence": (0, 100, int),
        "merge_gap_ms": (0, 300, int),
    }
    for field, (minimum, maximum, converter) in limits.items():
        raw_value = incoming.get(field, base[field])
        try:
            value = converter(float(raw_value))
        except (TypeError, ValueError) as exc:
            raise TranscriptionError(f"{field} 必须是数值。") from exc
        if not minimum <= value <= maximum:
            raise TranscriptionError(f"{field} 必须在 {minimum} 到 {maximum} 之间。")
        base[field] = value
    base["label"] = "自定义" if base["preset"] == "custom" else SENSITIVITY_PRESETS[base["preset"]]["label"]
    return base


@dataclass
class DecodedAudio:
    path: Path
    samples: np.ndarray
    sample_rate: int
    duration_seconds: float
    original_duration_seconds: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_supported_audio(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise TranscriptionError("找不到音频文件。")
    if path.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
        supported = "、".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise TranscriptionError(f"不支持的文件格式。支持：{supported}")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise TranscriptionError("音频文件超过 500 MB，本地 MVP 为保护内存不处理该文件。")


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def decode_audio(input_path: Path, temp_dir: Path) -> DecodedAudio:
    """Decode common audio to a mono 22.05 kHz WAV for analysis."""
    require_supported_audio(input_path)
    temp_dir.mkdir(parents=True, exist_ok=True)
    normalised = temp_dir / "analysis_mono_22050.wav"
    ffmpeg = _ffmpeg_path()
    if ffmpeg:
        command = [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(normalised),
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            message = process.stderr.strip() or "FFmpeg 无法解码此音频。"
            raise TranscriptionError(f"音频解码失败：{message[:500]}")
    elif input_path.suffix.lower() == ".wav":
        shutil.copy2(input_path, normalised)
    else:
        raise TranscriptionError("处理 MP3 等格式需要安装并加入 PATH 的 FFmpeg。")

    try:
        import soundfile as sf
        samples, sample_rate = sf.read(normalised, always_2d=False, dtype="float32")
    except Exception as exc:
        raise TranscriptionError("无法读取解码后的 WAV 文件。") from exc

    if samples.ndim > 1:
        samples = np.mean(samples, axis=1)
    samples = np.asarray(samples, dtype=np.float32)
    if sample_rate != TARGET_SAMPLE_RATE:
        # This branch is normally unreachable after FFmpeg, but keeps WAV-only
        # fallback deterministic if a decoder preserves a differing sample rate.
        try:
            import librosa
            samples = librosa.resample(samples, orig_sr=sample_rate, target_sr=TARGET_SAMPLE_RATE)
            sample_rate = TARGET_SAMPLE_RATE
        except Exception as exc:
            raise TranscriptionError("无法将音频重采样到分析采样率。") from exc
    duration = len(samples) / float(sample_rate) if sample_rate else 0.0
    if duration < 0.15:
        raise TranscriptionError("音频过短，至少需要 0.15 秒。")
    return DecodedAudio(normalised, samples, sample_rate, duration, duration)


def audio_quality_report(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """Provide intentionally simple, local recording diagnostics."""
    rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    clipping_ratio = float(np.mean(np.abs(samples) >= 0.995)) if len(samples) else 0.0
    frame = max(1, int(sample_rate * 0.05))
    frames = samples[: len(samples) // frame * frame].reshape(-1, frame) if len(samples) >= frame else np.array([])
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1)) if getattr(frames, "size", 0) else np.array([])
    silence_ratio = float(np.mean(frame_rms < 0.008)) if len(frame_rms) else 0.0
    notices: list[dict[str, str]] = []
    if rms < 0.012:
        notices.append({"level": "warning", "text": "整体音量偏低，建议靠近麦克风重新录制。"})
    if peak > 0.99 or clipping_ratio > 0.002:
        notices.append({"level": "warning", "text": "检测到削波或过强输入，音高和起音可能不稳定。"})
    if silence_ratio > 0.75:
        notices.append({"level": "warning", "text": "静音占比较高，请裁剪空白段或确认录音是否正确。"})
    if not notices:
        notices.append({"level": "info", "text": "基础音量检查通过；仍建议使用无伴奏、单声源录音。"})
    return {
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "clipping_ratio": round(clipping_ratio, 6),
        "silence_ratio": round(silence_ratio, 6),
        "notices": notices,
    }


def midi_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[int(midi) % 12]}{int(midi) // 12 - 1}"


def _new_note(index: int, onset: float, offset: float, midi: int, confidence: float | None, source: str) -> dict[str, Any]:
    onset = max(0.0, round(float(onset), 4))
    offset = max(onset + 0.03, round(float(offset), 4))
    midi = int(np.clip(round(float(midi)), 21, 108))
    return {
        "id": f"n{index:04d}",
        "onset": onset,
        "offset": offset,
        "raw_onset": onset,
        "raw_offset": offset,
        "duration": round(offset - onset, 4),
        "midi": midi,
        "name": midi_name(midi),
        "velocity": 88,
        "confidence": round(float(np.clip(confidence, 0.0, 1.0)), 3) if confidence is not None else None,
        "source": source,
        "user_edited": False,
    }


def _basic_pitch_events(
    audio_path: Path,
    settings: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], str] | None:
    """Use Basic Pitch only when deliberately installed by the user."""
    try:
        from basic_pitch.inference import predict
    except Exception:
        return None
    try:
        _report(progress_callback, 35, "正在使用 Basic Pitch 推理音符…")
        _model_output, _midi_data, note_events = predict(str(audio_path))
    except Exception as exc:
        raise TranscriptionError(f"Basic Pitch 转录失败：{exc}") from exc

    notes: list[dict[str, Any]] = []
    for index, event in enumerate(note_events, start=1):
        try:
            # Official Basic Pitch examples expose at least start, end, MIDI pitch,
            # and amplitude. Extra pitch-bend data is intentionally not required.
            onset, offset, pitch = float(event[0]), float(event[1]), int(round(float(event[2])))
            amplitude = float(event[3]) if len(event) > 3 else 0.7
        except (TypeError, ValueError, IndexError):
            continue
        if offset - onset < settings["min_note_duration_ms"] / 1000:
            continue
        confidence = max(0.25, min(0.98, amplitude if 0 <= amplitude <= 1 else 0.7))
        if confidence < settings["min_confidence"] / 100:
            continue
        notes.append(_new_note(index, onset, offset, pitch, confidence, "basic-pitch"))
    notes = _merge_nearby_same_pitch_notes(notes, settings["merge_gap_ms"] / 1000)
    if not notes:
        raise TranscriptionError("Basic Pitch 未检测到可用音符。请确认音频为单声源且音量足够。")
    return notes, "basic-pitch"


def _pyin_events(
    samples: np.ndarray,
    sample_rate: int,
    settings: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Fallback conversion from pYIN frames to conservative monophonic notes."""
    try:
        import librosa
    except Exception as exc:
        raise TranscriptionError("未安装 librosa，无法运行本地 pYIN 转录。") from exc
    try:
        _report(progress_callback, 35, "正在进行 pYIN 音高分析…")
        threshold = float(np.clip(0.76 - settings["sensitivity"] / 500, 0.52, 0.76))
        f0, voiced, voiced_prob = librosa.pyin(
            samples,
            fmin=librosa.note_to_hz("A0"),
            fmax=librosa.note_to_hz("C8"),
            sr=sample_rate,
            frame_length=2048,
            hop_length=256,
            fill_na=np.nan,
            no_trough_prob=threshold,
        )
    except Exception as exc:
        raise TranscriptionError(f"pYIN 音高估计失败：{exc}") from exc
    if f0 is None or not np.any(np.isfinite(f0)):
        raise TranscriptionError("没有检测到稳定音高。请使用清唱或单旋律独奏，并避免伴奏。")

    hop = 256
    midi_values = np.full(len(f0), np.nan)
    valid = np.isfinite(f0)
    midi_values[valid] = librosa.hz_to_midi(f0[valid])
    quantized = np.where(valid, np.rint(midi_values), np.nan)
    frame_times = librosa.frames_to_time(np.arange(len(f0)), sr=sample_rate, hop_length=hop)

    notes: list[dict[str, Any]] = []
    current_pitch: int | None = None
    start_index: int | None = None
    confidence_values: list[float] = []
    previous_index: int | None = None

    def flush(end_index: int) -> None:
        nonlocal current_pitch, start_index, confidence_values
        if current_pitch is None or start_index is None:
            return
        onset = float(frame_times[start_index])
        offset = float(frame_times[min(end_index, len(frame_times) - 1)] + hop / sample_rate)
        min_duration = settings["min_note_duration_ms"] / 1000
        min_confidence = settings["min_confidence"] / 100
        if offset - onset >= min_duration:
            confidence = float(np.mean(confidence_values)) if confidence_values else 0.35
            if confidence >= min_confidence:
                notes.append(_new_note(len(notes) + 1, onset, offset, current_pitch, confidence, "pyin"))
        current_pitch = None
        start_index = None
        confidence_values = []

    for index, value in enumerate(quantized):
        is_voiced = np.isfinite(value) and bool(voiced[index])
        pitch = int(value) if is_voiced else None
        probability = float(voiced_prob[index]) if is_voiced and np.isfinite(voiced_prob[index]) else 0.0
        discontinuity = previous_index is not None and index - previous_index > 2
        if pitch is None:
            if current_pitch is not None:
                flush(index - 1)
            previous_index = None
            continue
        if current_pitch is None:
            current_pitch, start_index, confidence_values = pitch, index, [probability]
        elif pitch != current_pitch or discontinuity:
            flush(index - 1)
            current_pitch, start_index, confidence_values = pitch, index, [probability]
        else:
            confidence_values.append(probability)
        previous_index = index
    if current_pitch is not None:
        flush(len(quantized) - 1)

    if not notes:
        raise TranscriptionError("pYIN 未生成可用音符。请尝试更清晰的单旋律录音。")

    merged = _merge_nearby_same_pitch_notes(notes, settings["merge_gap_ms"] / 1000)
    return merged, "pyin"


def _merge_nearby_same_pitch_notes(notes: list[dict[str, Any]], merge_gap_seconds: float) -> list[dict[str, Any]]:
    """Merge same-pitch fragments separated by a tiny unvoiced gap."""
    merged: list[dict[str, Any]] = []
    for note in sorted(notes, key=lambda item: item["onset"]):
        if merged and merged[-1]["midi"] == note["midi"] and note["onset"] - merged[-1]["offset"] <= merge_gap_seconds:
            merged[-1]["offset"] = note["offset"]
            merged[-1]["raw_offset"] = note.get("raw_offset", note["offset"])
            merged[-1]["duration"] = round(note["offset"] - merged[-1]["onset"], 4)
            left_confidence = merged[-1].get("confidence")
            right_confidence = note.get("confidence")
            if left_confidence is None or right_confidence is None:
                merged[-1]["confidence"] = None
            else:
                merged[-1]["confidence"] = round(max(float(left_confidence), float(right_confidence)), 3)
        else:
            merged.append(dict(note))
    for index, note in enumerate(merged, start=1):
        note["id"] = f"n{index:04d}"
    return merged


def estimate_tempo(samples: np.ndarray, sample_rate: int) -> float | None:
    try:
        import librosa
        tempo, _beats = librosa.beat.beat_track(y=samples, sr=sample_rate, hop_length=512)
        tempo_value = float(np.asarray(tempo).reshape(-1)[0])
        if 35 <= tempo_value <= 240:
            return round(tempo_value, 2)
    except Exception:
        pass
    return None


def estimate_key(notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate a key candidate from pitch-class duration weights.

    This is a transparent heuristic, not an asserted musical fact. The UI labels
    it as a suggestion so the user can change it before exporting notation.
    """
    if not notes:
        return {"tonic": "C", "mode": "major", "confidence": 0.0, "label": "C major"}
    histogram = np.zeros(12)
    for note in notes:
        confidence = note.get("confidence")
        # Some event models (including the released ROSVOT MIDI interface) do
        # not expose calibrated per-note confidence. Keep the event usable for
        # key estimation without fabricating a confidence score.
        weight = float(confidence) if confidence is not None else 0.5
        histogram[int(note["midi"]) % 12] += max(0.05, float(note["duration"])) * weight
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    scores: list[tuple[float, int, str]] = []
    for tonic in range(12):
        scores.append((float(np.dot(histogram, np.roll(major_profile, tonic))), tonic, "major"))
        scores.append((float(np.dot(histogram, np.roll(minor_profile, tonic))), tonic, "minor"))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1] if len(scores) > 1 else (0.0, 0, "major")
    labels = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    separation = (best[0] - second[0]) / max(best[0], 1e-6)
    return {
        "tonic": labels[best[1]],
        "mode": best[2],
        "confidence": round(float(np.clip(separation, 0.0, 0.95)), 3),
        "label": f"{labels[best[1]]} {best[2]}",
    }


def quantize_notes(notes: list[dict[str, Any]], tempo_bpm: float | None) -> None:
    """Add reversible 16th-note timing suggestions to note events."""
    if not tempo_bpm or tempo_bpm <= 0:
        for note in notes:
            note["quantized_onset"] = note["onset"]
            note["quantized_offset"] = note["offset"]
            note["quantization_grid"] = None
        return
    grid = 60.0 / float(tempo_bpm) / 4.0
    previous_offset = 0.0
    for note in notes:
        onset = max(previous_offset, round(note["onset"] / grid) * grid)
        offset = max(onset + grid, round(note["offset"] / grid) * grid)
        note["quantized_onset"] = round(onset, 4)
        note["quantized_offset"] = round(offset, 4)
        note["quantization_grid"] = "1/16"
        previous_offset = onset


def rule_findings(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, note in enumerate(notes):
        if note["duration"] < 0.11:
            findings.append({"note_ids": [note["id"]], "severity": "medium", "reason": "音符极短，可能是颤音、滑音或错切。", "suggested_action": "manual_check"})
        confidence = note.get("confidence")
        if confidence is not None and confidence < 0.45:
            findings.append({"note_ids": [note["id"]], "severity": "medium", "reason": "音高置信度偏低，建议回听确认。", "suggested_action": "manual_check"})
        if index > 0:
            interval = abs(note["midi"] - notes[index - 1]["midi"])
            confidences = [value for value in (note.get("confidence"), notes[index - 1].get("confidence")) if value is not None]
            if interval >= 12 and confidences and min(confidences) < 0.7:
                findings.append({"note_ids": [notes[index - 1]["id"], note["id"]], "severity": "low", "reason": "检测到大音程跳进且至少一个音符置信度一般。", "suggested_action": "manual_check"})
    return findings[:300]


def build_score(
    *,
    title: str,
    input_path: Path,
    decoded: DecodedAudio,
    notes: list[dict[str, Any]],
    engine: str,
    quality: dict[str, Any],
    transcription_settings: dict[str, Any],
) -> dict[str, Any]:
    tempo = estimate_tempo(decoded.samples, decoded.sample_rate)
    quantize_notes(notes, tempo)
    key = estimate_key(notes)
    return {
        "schema_version": 1,
        "version": APP_VERSION,
        "title": title,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "input": {
            "original_filename": input_path.name,
            "duration_seconds": round(decoded.duration_seconds, 3),
            "analysis_sample_rate": decoded.sample_rate,
            "channel_mode": "mono",
        },
        "engine": {"name": engine, "mode": "candidate-score", "audio_sent_to_cloud": False},
        "transcription_settings": transcription_settings,
        "tempo_bpm": tempo,
        "time_signature": {"value": "4/4", "confidence": 0.25, "status": "default_candidate"},
        "key": key,
        "quality": quality,
        "notes": notes,
        "rule_findings": rule_findings(notes),
        "user_notes": "",
        "disclaimer": "该结果是本机生成的可编辑候选谱。请通过原音回听和人工校正确认音高、时值、调性与拍号。",
    }


def _safe_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ " else "_" for char in value).strip()
    return cleaned[:80] or "untitled"


def _score_path(project_dir: Path) -> Path:
    return project_dir / "score.json"


def write_score(project_dir: Path, score: dict[str, Any]) -> Path:
    score["updated_at"] = utc_now()
    path = _score_path(project_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def read_score(project_dir: Path) -> dict[str, Any]:
    try:
        return json.loads(_score_path(project_dir).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TranscriptionError("项目中缺少 score.json。") from exc
    except json.JSONDecodeError as exc:
        raise TranscriptionError("score.json 已损坏，无法读取。") from exc


def _midi_to_xml_pitch(midi: int) -> tuple[str, int, int]:
    names = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0), ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
    step, alter = names[midi % 12]
    octave = midi // 12 - 1
    return step, alter, octave


def export_midi(score: dict[str, Any], destination: Path) -> Path:
    try:
        import mido
    except Exception as exc:
        raise TranscriptionError("未安装 mido，无法导出 MIDI。") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    tempo = float(score.get("tempo_bpm") or 100.0)
    ticks_per_beat = 480
    midi_file = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi_file.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=score.get("title", "HearPitch"), time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0))
    signature = score.get("time_signature") or {}
    signature_value = signature.get("value", "4/4") if isinstance(signature, dict) else str(signature)
    try:
        numerator_text, denominator_text = str(signature_value).split("/", 1)
        numerator, denominator = int(numerator_text), int(denominator_text)
        if not (1 <= numerator <= 32 and denominator in {1, 2, 4, 8, 16, 32}):
            raise ValueError
    except (TypeError, ValueError):
        numerator, denominator = 4, 4
    track.append(mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator, time=0))
    seconds_per_tick = (60.0 / tempo) / ticks_per_beat
    previous_tick = 0
    for note in sorted(score.get("notes", []), key=lambda item: float(item["onset"])):
        start_tick = max(previous_tick, int(round(float(note["onset"]) / seconds_per_tick)))
        end_tick = max(start_tick + 1, int(round(float(note["offset"]) / seconds_per_tick)))
        track.append(mido.Message("note_on", note=int(note["midi"]), velocity=int(note.get("velocity", 88)), time=start_tick - previous_tick))
        track.append(mido.Message("note_off", note=int(note["midi"]), velocity=0, time=end_tick - start_tick))
        previous_tick = end_tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi_file.save(destination)
    return destination


def export_musicxml(score: dict[str, Any], destination: Path) -> Path:
    """Export a deliberately simple, monophonic MusicXML candidate score."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    divisions = 480
    tempo = float(score.get("tempo_bpm") or 100.0)
    seconds_per_quarter = 60.0 / tempo
    root = ET.Element("score-partwise", version="4.0")
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = score.get("title", "HearPitch Candidate Score")
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = "Melody"
    part = ET.SubElement(root, "part", id="P1")
    measure = ET.SubElement(part, "measure", number="1")
    attributes = ET.SubElement(measure, "attributes")
    ET.SubElement(attributes, "divisions").text = str(divisions)
    signature = score.get("time_signature") or {}
    signature_value = signature.get("value", "4/4") if isinstance(signature, dict) else str(signature)
    try:
        beats_text, beat_type_text = str(signature_value).split("/", 1)
        beats_value, beat_type = int(beats_text), int(beat_type_text)
        if not (1 <= beats_value <= 32 and beat_type in {1, 2, 4, 8, 16, 32}):
            raise ValueError
    except (TypeError, ValueError):
        beats_value, beat_type = 4, 4
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = str(beats_value)
    ET.SubElement(time, "beat-type").text = str(beat_type)
    clef = ET.SubElement(attributes, "clef")
    ET.SubElement(clef, "sign").text = "G"
    ET.SubElement(clef, "line").text = "2"
    direction = ET.SubElement(measure, "direction", placement="above")
    sound = ET.SubElement(direction, "sound")
    sound.set("tempo", str(round(tempo, 2)))

    measure_number = 1
    ticks_in_measure = 0
    denominator_capacity = divisions * 4 // beat_type
    meter_sequence = score.get("meter_sequence")
    if not isinstance(meter_sequence, list) or not meter_sequence:
        meter_sequence = [beats_value]
    capacity_index = 0
    measure_capacity = denominator_capacity * int(meter_sequence[capacity_index % len(meter_sequence)])
    for note in score.get("notes", []):
        duration = max(1, int(round(float(note["duration"]) / seconds_per_quarter * divisions)))
        if ticks_in_measure and ticks_in_measure + duration > measure_capacity:
            measure_number += 1
            measure = ET.SubElement(part, "measure", number=str(measure_number))
            ticks_in_measure = 0
            capacity_index += 1
            measure_capacity = denominator_capacity * int(meter_sequence[capacity_index % len(meter_sequence)])
            measure_attributes = ET.SubElement(measure, "attributes")
            measure_time = ET.SubElement(measure_attributes, "time")
            ET.SubElement(measure_time, "beats").text = str(meter_sequence[capacity_index % len(meter_sequence)])
            ET.SubElement(measure_time, "beat-type").text = str(beat_type)
        xml_note = ET.SubElement(measure, "note")
        pitch = ET.SubElement(xml_note, "pitch")
        step, alter, octave = _midi_to_xml_pitch(int(note["midi"]))
        ET.SubElement(pitch, "step").text = step
        if alter:
            ET.SubElement(pitch, "alter").text = str(alter)
        ET.SubElement(pitch, "octave").text = str(octave)
        ET.SubElement(xml_note, "duration").text = str(duration)
        ET.SubElement(xml_note, "voice").text = "1"
        ET.SubElement(xml_note, "type").text = "quarter"
        ticks_in_measure += duration
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def export_all(project_dir: Path, score: dict[str, Any]) -> dict[str, str]:
    exports = project_dir / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(score.get("title", "hearpitch"))
    json_path = exports / f"{stem}.json"
    json_path.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    midi_path = export_midi(score, exports / f"{stem}.mid")
    xml_path = export_musicxml(score, exports / f"{stem}.musicxml")
    return {"json": str(json_path), "midi": str(midi_path), "musicxml": str(xml_path)}


def create_project_from_audio(
    input_path: Path,
    *,
    title: str | None = None,
    max_seconds: float = 0,
    prefer_basic_pitch: bool = True,
    transcription_settings: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Create a complete local project and exports from a local audio file."""
    settings = normalize_transcription_settings(transcription_settings)
    _report(progress_callback, 3, "正在创建本机项目…")
    require_supported_audio(input_path)
    project_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    project_dir = projects_root() / project_id
    project_dir.mkdir(parents=True, exist_ok=False)
    source_dir = project_dir / "source"
    source_dir.mkdir()
    copied_source = source_dir / input_path.name
    shutil.copy2(input_path, copied_source)
    _report(progress_callback, 10, "正在复制音频并准备解码…")
    decoded = decode_audio(copied_source, project_dir / "analysis")
    if max_seconds and decoded.duration_seconds > max_seconds:
        raise TranscriptionError(
            f"音频时长为 {decoded.duration_seconds:.1f} 秒，超过当前限制 {max_seconds:.0f} 秒。"
            "命令行可用 --max-seconds 0 取消限制；网页可开启长音频模式。"
        )
    _report(progress_callback, 22, "正在检查音频质量…")
    quality = audio_quality_report(decoded.samples, decoded.sample_rate)
    if settings["engine"] == "rosvot-rmvpe":
        from .rosvot_engine import transcribe_with_rosvot

        result = transcribe_with_rosvot(
            decoded.path,
            min_note_duration_ms=settings["min_note_duration_ms"],
            merge_gap_ms=settings["merge_gap_ms"],
            sensitivity=settings["sensitivity"],
            progress_callback=progress_callback,
        )
    elif settings["engine"] == "pyin":
        result = None
    elif settings["engine"] == "basic-pitch":
        result = _basic_pitch_events(decoded.path, settings, progress_callback)
        if result is None:
            raise TranscriptionError("已选择 Basic Pitch，但该引擎未安装。请运行 install_basic_pitch_windows.bat，或选择自动 / pYIN。")
    else:
        result = _basic_pitch_events(decoded.path, settings, progress_callback) if prefer_basic_pitch else None
    if result is None:
        _report(progress_callback, 33, "Basic Pitch 未安装，正在使用本机 pYIN 回退引擎…")
        notes, engine = _pyin_events(decoded.samples, decoded.sample_rate, settings, progress_callback)
    else:
        notes, engine = result
    _report(progress_callback, 76, "正在估计速度、调性并生成候选谱…")
    score = build_score(
        title=(title or input_path.stem),
        input_path=input_path,
        decoded=decoded,
        notes=notes,
        engine=engine,
        quality=quality,
        transcription_settings=settings,
    )
    _report(progress_callback, 88, "正在生成 MIDI、MusicXML 和 JSON 导出…")
    write_score(project_dir, score)
    exports = export_all(project_dir, score)
    metadata = {
        "id": project_id,
        "title": score["title"],
        "created_at": score["created_at"],
        "project_dir": str(project_dir),
        "score_path": str(_score_path(project_dir)),
        "exports": exports,
    }
    (project_dir / "project.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    _report(progress_callback, 100, "候选谱已完成，可开始回听与编辑。")
    return {"project": metadata, "score": score}


def list_projects() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for project_dir in sorted(projects_root().iterdir(), reverse=True):
        metadata = project_dir / "project.json"
        if not metadata.exists():
            continue
        try:
            items.append(json.loads(metadata.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return items


def get_project(project_id: str) -> dict[str, Any]:
    if not project_id or "/" in project_id or "\\" in project_id or ".." in project_id:
        raise TranscriptionError("无效项目编号。")
    project_dir = projects_root() / project_id
    metadata_path = project_dir / "project.json"
    if not metadata_path.exists():
        raise TranscriptionError("项目不存在。")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {"project": metadata, "score": read_score(project_dir)}


def update_project_score(project_id: str, incoming_score: dict[str, Any]) -> dict[str, Any]:
    current = get_project(project_id)
    project_dir = Path(current["project"]["project_dir"])
    allowed_top_level = {"title", "tempo_bpm", "time_signature", "key", "notes", "user_notes", "beat_taps", "beat_markers", "tempo_mode", "meter_sequence", "rhythm_quantize", "pitch_quantize"}
    score = current["score"]
    for key in allowed_top_level:
        if key in incoming_score:
            score[key] = incoming_score[key]
    if not isinstance(score.get("notes"), list) or not score["notes"]:
        raise TranscriptionError("谱面至少需要保留一个音符。")
    if "beat_taps" in score:
        taps = score["beat_taps"]
        if not isinstance(taps, list) or any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in taps):
            raise TranscriptionError("打拍记录格式无效；时间必须是非负数字。")
        score["beat_taps"] = [round(float(value), 4) for value in taps]
    if "beat_markers" in score:
        markers = score["beat_markers"]
        if not isinstance(markers, list) or len(markers) > 20000:
            raise TranscriptionError("拍点列表格式无效或数量过多。")
        cleaned_markers = []
        for marker in markers:
            if not isinstance(marker, dict):
                raise TranscriptionError("拍点标记格式无效。")
            try:
                time_value = float(marker["time"])
                beat_value = int(marker["beat"])
                bar_value = int(marker["bar"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TranscriptionError("拍点标记缺少有效时间、拍号或小节号。") from exc
            if not math.isfinite(time_value) or time_value < 0 or beat_value < 1 or bar_value < 1:
                raise TranscriptionError("拍点标记必须使用非负时间及正整数拍号/小节号。")
            cleaned = {"time": round(time_value, 4), "beat": beat_value, "bar": bar_value}
            if "raw_time" in marker:
                try:
                    raw_time = float(marker["raw_time"])
                except (TypeError, ValueError) as exc:
                    raise TranscriptionError("拍点原始时间格式无效。") from exc
                if not math.isfinite(raw_time) or raw_time < 0:
                    raise TranscriptionError("拍点原始时间必须是非负数字。")
                cleaned["raw_time"] = round(raw_time, 4)
            cleaned_markers.append(cleaned)
        score["beat_markers"] = sorted(cleaned_markers, key=lambda item: item["time"])
    if "meter_sequence" in score:
        sequence = score["meter_sequence"]
        if not isinstance(sequence, list) or not sequence or len(sequence) > 32 or any(not isinstance(value, int) or not 1 <= value <= 32 for value in sequence):
            raise TranscriptionError("拍号序列无效；每小节拍数应为 1 到 32 的整数。")
    if score.get("rhythm_quantize") not in (None, "off", 8, 16, 32):
        raise TranscriptionError("节奏量化设置无效。")
    if score.get("pitch_quantize") not in (None, "semitone", "key_suggest", "key_strict"):
        raise TranscriptionError("音高量化设置无效。")
    if score.get("tempo_mode") not in (None, "estimated", "tap", "manual"):
        raise TranscriptionError("速度模式无效。")
    for index, note in enumerate(score["notes"], start=1):
        try:
            midi = int(note["midi"])
            onset = float(note["onset"])
            offset = float(note["offset"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TranscriptionError(f"第 {index} 个音符格式错误。") from exc
        if not 0 <= midi <= 127 or offset <= onset:
            raise TranscriptionError(f"第 {index} 个音符的音高或时值无效。")
        note["id"] = note.get("id") or f"n{index:04d}"
        note["midi"] = midi
        note["name"] = midi_name(midi)
        note["onset"] = round(onset, 4)
        note["offset"] = round(offset, 4)
        note["duration"] = round(offset - onset, 4)
        note["user_edited"] = True
    score["notes"].sort(key=lambda item: item["onset"])
    score["rule_findings"] = rule_findings(score["notes"])
    write_score(project_dir, score)
    exports = export_all(project_dir, score)
    metadata = current["project"]
    metadata["title"] = score["title"]
    metadata["exports"] = exports
    (project_dir / "project.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"project": metadata, "score": score}
