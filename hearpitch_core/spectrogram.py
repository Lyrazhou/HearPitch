"""Local, bounded-memory spectrogram image generation for the score editor."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image

MIN_MIDI = 36
MAX_MIDI = 84
PIXELS_PER_SEMITONE = 12
IMAGE_WIDTH = 1800
FFT_SIZE = 4096


def _colour_map(levels: np.ndarray) -> np.ndarray:
    """Map 0..1 energy values to a high-contrast navy/cyan/yellow palette."""
    stops = np.array([0.0, 0.22, 0.48, 0.72, 1.0])
    colours = np.array([
        [8, 16, 38],
        [23, 64, 112],
        [35, 164, 181],
        [244, 205, 91],
        [255, 250, 224],
    ], dtype=np.float32)
    flat = np.clip(levels, 0, 1).ravel()
    rgb = np.stack([np.interp(flat, stops, colours[:, channel]) for channel in range(3)], axis=1)
    return np.clip(rgb.reshape((*levels.shape, 3)), 0, 255).astype(np.uint8)


def render_spectrogram(audio_path: Path, destination: Path) -> Path:
    """Render a note-oriented log-frequency spectrogram as a local PNG.

    Only IMAGE_WIDTH audio windows are sampled, so long recordings do not
    allocate a full STFT matrix in memory. Vertical coordinates align to
    equal-tempered semitones between MIDI 36 and 84 for editor overlays.
    """
    samples, sample_rate = sf.read(audio_path, always_2d=False, dtype="float32")
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    samples = np.asarray(samples, dtype=np.float32)
    if sample_rate <= 0 or len(samples) < 1:
        raise ValueError("音频文件没有可绘制的采样数据。")

    width = min(IMAGE_WIDTH, max(240, int(len(samples) / sample_rate * 24)))
    centres = np.linspace(0, len(samples) - 1, width).astype(np.int64)
    offsets = np.arange(FFT_SIZE, dtype=np.int64) - FFT_SIZE // 2
    indices = np.clip(centres[:, None] + offsets[None, :], 0, len(samples) - 1)
    frames = samples[indices]
    frames *= np.hanning(FFT_SIZE).astype(np.float32)[None, :]
    spectrum = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    frequencies = np.fft.rfftfreq(FFT_SIZE, 1.0 / sample_rate)

    row_count = (MAX_MIDI - MIN_MIDI) * PIXELS_PER_SEMITONE
    midi_positions = MAX_MIDI - np.arange(row_count, dtype=np.float32) / PIXELS_PER_SEMITONE
    target_hz = 440.0 * np.power(2.0, (midi_positions - 69.0) / 12.0)
    magnitudes = np.empty((row_count, width), dtype=np.float32)
    for column in range(width):
        magnitudes[:, column] = np.interp(target_hz, frequencies, spectrum[column])

    db = 20.0 * np.log10(np.maximum(magnitudes, 1e-7))
    peak = float(np.percentile(db, 99.7)) if db.size else 0.0
    levels = np.clip((db - (peak - 62.0)) / 62.0, 0.0, 1.0)
    rgb = _colour_map(levels)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(destination, format="PNG", optimize=True)
    return destination
