"""Optional isolated ROSVOT + RMVPE CUDA inference bridge for Windows."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .config import get_data_root
from .transcription import TranscriptionError, _merge_nearby_same_pitch_notes, _new_note

ProgressCallback = Callable[[int, str], None]
ROSVOT_REVISION = "3c8332bf43adae35f6e4d64971862f2f6139b310"
CHECKPOINT_ARCHIVE_ID = "1JNtNT37KiLq9uFQqHk7JFs-3trxd3bRh"
DEFAULT_SEGMENT_SECONDS = 45
SEGMENT_OVERLAP_SECONDS = 2.0


def rosvot_install_root() -> Path:
    """Return the user data directory used by install_rosvot_cuda_windows.bat."""
    return get_data_root() / "models" / "rosvot"


def rosvot_source_root() -> Path:
    return rosvot_install_root() / "source"


def rosvot_python() -> Path:
    return rosvot_install_root() / ".venv" / "Scripts" / "python.exe"


def rosvot_model_status(*, probe_cuda: bool = True) -> dict[str, Any]:
    root = rosvot_install_root()
    source = root / "source"
    python = rosvot_python()
    required = [
        source / "inference" / "rosvot.py",
        source / "checkpoints" / "rosvot" / "model.pt",
        source / "checkpoints" / "rosvot" / "config.yaml",
        source / "checkpoints" / "rwbd" / "model.pt",
        source / "checkpoints" / "rwbd" / "config.yaml",
        source / "checkpoints" / "rmvpe" / "model.pt",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    installed = python.is_file() and not missing
    cuda_available = False
    cuda_error = None
    if installed and probe_cuda:
        try:
            probe = subprocess.run(
                [str(python), "-c", "import torch; print(torch.cuda.is_available())"],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            cuda_available = probe.returncode == 0 and probe.stdout.strip().endswith("True")
            if not cuda_available:
                cuda_error = (probe.stderr or probe.stdout or "PyTorch 没有检测到 CUDA").strip()[-500:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            cuda_error = str(exc)
    return {
        "installed": installed,
        "cuda_available": cuda_available,
        "cuda_error": cuda_error,
        "python": str(python),
        "source": str(source),
        "checkpoints": str(source / "checkpoints"),
        "missing": missing,
        "message": "ROSVOT 模型和 CUDA 环境已就绪。" if installed and cuda_available else (
            "ROSVOT 模型已安装，但 CUDA 当前不可用；请检查 NVIDIA 驱动并运行安装器自检。" if installed else
            "尚未安装 ROSVOT CUDA 环境或模型文件。请关闭 HearPitch 后运行 install_rosvot_cuda_windows.bat。"
        ),
    }


def _report(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback:
        callback(percent, message)


def midi_to_score_notes(midi_path: Path, *, min_duration_ms: int, merge_gap_ms: int) -> list[dict[str, Any]]:
    """Convert a ROSVOT MIDI result to HearPitch events; confidence is unavailable."""
    try:
        import mido
    except Exception as exc:  # pragma: no cover - base install includes mido
        raise TranscriptionError("HearPitch 缺少 mido，无法读取 ROSVOT 的 MIDI 输出。") from exc
    if not midi_path.is_file():
        raise TranscriptionError("ROSVOT 推理结束，但没有生成 MIDI 文件。请查看本机任务日志。")

    try:
        midi_file = mido.MidiFile(midi_path)
    except Exception as exc:
        raise TranscriptionError(f"无法读取 ROSVOT MIDI 输出：{exc}") from exc

    events: list[dict[str, Any]] = []
    active: dict[tuple[int, int], list[tuple[float, int]]] = {}
    absolute_tick = 0
    seconds = 0.0
    tempo = 500000
    for message in mido.merge_tracks(midi_file.tracks):
        absolute_tick += int(message.time)
        delta_ticks = int(message.time)
        seconds += mido.tick2second(delta_ticks, midi_file.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "note_on" and message.velocity > 0:
            active.setdefault((message.channel, message.note), []).append((seconds, int(message.velocity)))
        elif message.type in {"note_off", "note_on"}:
            key = (message.channel, message.note)
            starts = active.get(key, [])
            if not starts:
                continue
            start, velocity = starts.pop(0)
            if seconds - start < min_duration_ms / 1000:
                continue
            events.append({"pitch": int(message.note), "onset": start, "offset": seconds, "velocity": velocity})
    if not events:
        raise TranscriptionError("ROSVOT 没有识别到可用音符；请尝试清晰的单人歌声或较短片段。")

    notes = []
    for index, event in enumerate(sorted(events, key=lambda item: (item["onset"], item["pitch"])), start=1):
        note = _new_note(index, event["onset"], event["offset"], event["pitch"], 0.5, "rosvot-rmvpe")
        note["confidence"] = None
        note["velocity"] = int(event.get("velocity", 88))
        note["confidence_kind"] = "not_provided_by_model"
        notes.append(note)
    return _merge_nearby_same_pitch_notes(notes, merge_gap_ms / 1000)


def transcribe_with_rosvot(
    audio_path: Path,
    *,
    min_note_duration_ms: int,
    merge_gap_ms: int,
    sensitivity: int,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Run pinned upstream ROSVOT in its own CUDA-enabled Python environment."""
    status = rosvot_model_status()
    if not status["installed"]:
        raise TranscriptionError(
            "ROSVOT+RMVPE 尚未安装。请关闭 HearPitch，运行项目目录中的 "
            "install_rosvot_cuda_windows.bat；安装后重新启动软件。"
        )
    if not status["cuda_available"]:
        raise TranscriptionError(
            "ROSVOT 模型文件已安装，但 CUDA 未就绪。请检查 NVIDIA 驱动和 ROSVOT 专用 PyTorch CUDA 环境；"
            f"状态：{status.get('cuda_error') or '不可用'}"
        )
    python = Path(status["python"])
    source = Path(status["source"])
    if sys.platform == "win32" and not python.is_file():
        raise TranscriptionError("找不到 ROSVOT 专用 Python 环境。请重新运行 CUDA 安装脚本。")

    _report(progress_callback, 34, "正在准备 ROSVOT 输入音频与 CUDA 推理…")
    (get_data_root() / "temp").mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="hearpitch-rosvot-", dir=get_data_root() / "temp"))
    try:
        # ROSVOT's published M4Singer checkpoint is configured for 24 kHz.
        import soundfile as sf
        samples, sample_rate = sf.read(audio_path, always_2d=False, dtype="float32")
        if getattr(samples, "ndim", 1) > 1:
            samples = samples.mean(axis=1)
        if sample_rate != 24000:
            from scipy.signal import resample_poly
            from math import gcd
            divisor = gcd(int(sample_rate), 24000)
            samples = resample_poly(samples, 24000 // divisor, int(sample_rate) // divisor).astype("float32")
        duration = len(samples) / 24000
        # Upstream default is 0.8. Sensitivity spans 0.92 (conservative) to
        # 0.72 (more boundary candidates); it is not a confidence probability.
        threshold = float(0.92 - 0.20 * max(0, min(100, sensitivity)) / 100)
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = "0"
        env["PYTHONUNBUFFERED"] = "1"
        notes: list[dict[str, Any]] = []
        segment_frames = DEFAULT_SEGMENT_SECONDS * 24000
        overlap_frames = int(SEGMENT_OVERLAP_SECONDS * 24000)
        starts = list(range(0, len(samples), max(1, segment_frames - overlap_frames)))
        if len(samples) <= segment_frames:
            starts = [0]
        starts = [start for start in starts if start < len(samples)]
        for segment_index, start_frame in enumerate(starts):
            end_frame = min(len(samples), start_frame + segment_frames)
            if end_frame - start_frame < int(24000 * 0.15):
                if notes:
                    break
            segment_audio = work / f"input_{segment_index:03d}.wav"
            sf.write(segment_audio, samples[start_frame:end_frame], 24000, subtype="PCM_16")
            segment_duration = (end_frame - start_frame) / 24000
            max_frames = max(30000, int(math.ceil(segment_duration * 24000 / 128 / 16) * 16 + 256))
            out_dir = work / f"output_{segment_index:03d}"
            command = [
                str(python), "-u", "inference/rosvot.py",
                "--save_dir", str(out_dir),
                "--wav_fn", str(segment_audio),
                "--max_frames", str(max_frames),
                "--thr", f"{threshold:.3f}",
                "--ds_workers", "0",
                "--no_save_every_npy",
                "--no_save_final_npy",
            ]
            percent = 39 + int(35 * segment_index / max(1, len(starts)))
            _report(progress_callback, percent, f"正在使用 CUDA 推理歌声事件（片段 {segment_index + 1}/{len(starts)}）…")
            log_path = work / f"rosvot_{segment_index:03d}.log"
            started = time.monotonic()
            timeout_seconds = max(300, int(segment_duration * 30))
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                process = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
                while process.poll() is None:
                    elapsed = int(time.monotonic() - started)
                    if elapsed > timeout_seconds:
                        process.kill()
                        process.wait()
                        raise TranscriptionError("ROSVOT 推理超时；请缩短音频片段后重试。")
                    time.sleep(4)
                    if process.poll() is None:
                        pulse = min(7, elapsed // 20)
                        _report(
                            progress_callback,
                            min(72, percent + pulse),
                            f"ROSVOT + RMVPE 正在 CUDA 上推理（片段 {segment_index + 1}/{len(starts)}，已运行 {elapsed} 秒）…",
                        )
                return_code = process.returncode
            if return_code != 0:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:]
                lowered = tail.lower()
                if "out of memory" in lowered or "cuda out of memory" in lowered:
                    raise TranscriptionError("ROSVOT CUDA 显存不足。请尝试缩短音频或关闭其他 GPU 程序。")
                if "no cuda" in lowered or "cuda is not available" in lowered or "cuda driver" in lowered:
                    raise TranscriptionError("ROSVOT 专用环境无法访问 CUDA。请检查 NVIDIA 驱动和 CUDA 版 PyTorch 安装。")
                raise TranscriptionError(f"ROSVOT 推理失败（代码 {completed.returncode}）：{tail}")

            midi_path = out_dir / "midi" / "output.mid"
            segment_notes = midi_to_score_notes(
                midi_path,
                min_duration_ms=min_note_duration_ms,
                merge_gap_ms=merge_gap_ms,
            )
            segment_start = start_frame / 24000
            previous_start = starts[segment_index - 1] / 24000 if segment_index > 0 else None
            next_start = starts[segment_index + 1] / 24000 if segment_index + 1 < len(starts) else None
            # Partition at midpoints between segment starts. This remains
            # gap-free even when the final segment is shorter than the nominal
            # overlap window.
            owner_start = 0.0 if previous_start is None else (previous_start + segment_start) / 2
            owner_end = duration if next_start is None else (segment_start + next_start) / 2
            for note in segment_notes:
                midpoint = (note["onset"] + note["offset"]) / 2
                if owner_start <= midpoint < owner_end:
                    note["onset"] = round(note["onset"] + segment_start, 4)
                    note["offset"] = round(note["offset"] + segment_start, 4)
                    note["raw_onset"] = note["onset"]
                    note["raw_offset"] = note["offset"]
                    note["duration"] = round(note["offset"] - note["onset"], 4)
                    notes.append(note)
        if not notes:
            raise TranscriptionError("ROSVOT 没有识别到可用音符；请尝试清晰的单人歌声或较短片段。")
        notes.sort(key=lambda item: item["onset"])
        for index, note in enumerate(notes, start=1):
            note["id"] = f"n{index:04d}"
        notes = _merge_nearby_same_pitch_notes(notes, merge_gap_ms / 1000)
        _report(progress_callback, 78, f"已导入 {len(notes)} 个 ROSVOT 歌声音符候选。")
        return notes, "rosvot-rmvpe"
    finally:
        shutil.rmtree(work, ignore_errors=True)
