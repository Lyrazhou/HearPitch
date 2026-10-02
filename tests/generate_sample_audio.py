"""Generate a deterministic monophonic WAV fixture for local end-to-end tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 22050
NOTES = [(261.6256, 0.55), (293.6648, 0.45), (329.6276, 0.60), (391.9954, 0.50), (440.0, 0.70)]


def main() -> None:
    chunks: list[np.ndarray] = []
    for frequency, duration in NOTES:
        sample_count = int(SAMPLE_RATE * duration)
        time = np.arange(sample_count) / SAMPLE_RATE
        envelope = np.minimum(1, np.arange(sample_count) / max(1, int(SAMPLE_RATE * .025)))
        envelope *= np.minimum(1, np.arange(sample_count, 0, -1) / max(1, int(SAMPLE_RATE * .04)))
        chunks.append((0.35 * envelope * np.sin(2 * np.pi * frequency * time)).astype(np.float32))
        chunks.append(np.zeros(int(SAMPLE_RATE * .055), dtype=np.float32))
    output = Path(__file__).parent / "sample_scale.wav"
    sf.write(output, np.concatenate(chunks), SAMPLE_RATE)
    print(output)


if __name__ == "__main__":
    main()
