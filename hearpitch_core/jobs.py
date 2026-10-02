"""In-process local transcription jobs with bounded, user-visible progress.

This is deliberately an in-process manager for a single-user localhost app. No
internet queue, database, or background service is required. Jobs are lost if the
user closes the command window, while completed projects remain on disk.
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .transcription import TranscriptionError, create_project_from_audio


class JobNotFoundError(KeyError):
    """Raised when a client asks for an expired or nonexistent job."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "id",
        "status",
        "progress",
        "message",
        "created_at",
        "started_at",
        "finished_at",
        "error",
        "logs",
        "project",
        "settings",
    }
    return {key: job[key] for key in keys if key in job}


class LocalJobManager:
    """Thread-safe status registry for a small number of local CPU jobs."""

    def __init__(self, max_retained: int = 30) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._max_retained = max_retained

    def create(
        self,
        *,
        audio_path: Path,
        title: str | None,
        max_seconds: float,
        transcription_settings: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "任务已加入本机处理队列。",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "logs": [],
            "project": None,
            "settings": transcription_settings,
        }
        self._append_log(job, "任务已创建，等待本机分析。")
        with self._lock:
            self._jobs[job_id] = job
            self._trim_finished_jobs()
        thread = threading.Thread(
            target=self._run,
            args=(job_id, audio_path, title, max_seconds, transcription_settings),
            daemon=True,
            name=f"HearPitch-{job_id[:8]}",
        )
        thread.start()
        return _public_job(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobNotFoundError(job_id)
            return _public_job(job)

    def _append_log(self, job: dict[str, Any], message: str) -> None:
        logs: list[dict[str, Any]] = job["logs"]
        if logs and logs[-1]["message"] == message:
            return
        logs.append({"at": _now(), "message": message})
        del logs[:-25]

    def _update(self, job_id: str, progress: int, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] not in {"queued", "running"}:
                return
            job["progress"] = max(int(job["progress"]), min(100, int(progress)))
            job["message"] = message
            self._append_log(job, message)

    def _run(
        self,
        job_id: str,
        audio_path: Path,
        title: str | None,
        max_seconds: float,
        transcription_settings: dict[str, Any],
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = _now()
            self._append_log(job, "本机转谱已开始。")
        try:
            result = create_project_from_audio(
                audio_path,
                title=title,
                max_seconds=max_seconds,
                prefer_basic_pitch=True,
                transcription_settings=transcription_settings,
                progress_callback=lambda percent, message: self._update(job_id, percent, message),
            )
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["progress"] = 100
                job["message"] = "候选谱已完成。"
                job["project"] = result["project"]
                job["finished_at"] = _now()
                self._append_log(job, "已完成并生成 MIDI、MusicXML 和 JSON。")
        except TranscriptionError as exc:
            self._fail(job_id, str(exc))
        except Exception as exc:  # pragma: no cover - unexpected local failure details still surface to user
            self._fail(job_id, f"转谱任务出现未预期错误：{exc}")
        finally:
            shutil.rmtree(audio_path.parent, ignore_errors=True)

    def _fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = message
            job["message"] = "转谱未完成。"
            job["finished_at"] = _now()
            self._append_log(job, f"失败：{message}")

    def _trim_finished_jobs(self) -> None:
        if len(self._jobs) <= self._max_retained:
            return
        finished = [
            job_id
            for job_id, job in self._jobs.items()
            if job["status"] in {"completed", "failed"}
        ]
        for job_id in finished[: max(0, len(self._jobs) - self._max_retained)]:
            self._jobs.pop(job_id, None)
