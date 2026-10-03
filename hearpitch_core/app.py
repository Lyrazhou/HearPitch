"""Local web interface for HearPitch Local V260924A."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import APP_VERSION, ConfigurationError, get_data_root, list_profiles, create_or_update_profile, delete_profile
from .llm import LLMReviewError, review_score
from .jobs import JobNotFoundError, LocalJobManager
from .rosvot_engine import rosvot_model_status
from .transcription import (
    MAX_UPLOAD_BYTES,
    TranscriptionError,
    get_project,
    list_projects,
    normalize_transcription_settings,
    update_project_score,
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PACKAGE_ROOT / "static"


class ScoreUpdate(BaseModel):
    score: dict[str, Any]


class ProfileInput(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.2, ge=0, le=2)
    api_key: str | None = Field(default=None, max_length=1000)


class ReviewInput(BaseModel):
    profile_id: str
    instruction: str = Field(default="", max_length=2000)


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def create_app() -> FastAPI:
    app = FastAPI(title="HearPitch Local", version=APP_VERSION, docs_url=None, redoc_url=None)
    jobs = LocalJobManager()
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": APP_VERSION,
            "mode": "local-only",
            "data_root": str(get_data_root()),
            "audio_uploads_leave_device": False,
            "engines": {"rosvot-rmvpe": rosvot_model_status()},
        }

    @app.get("/api/engines/rosvot-rmvpe")
    async def rosvot_status() -> dict[str, Any]:
        return rosvot_model_status()

    @app.get("/api/projects")
    async def projects() -> dict[str, Any]:
        return {"projects": list_projects()}

    @app.get("/api/projects/{project_id}")
    async def project_detail(project_id: str) -> dict[str, Any]:
        try:
            return get_project(project_id)
        except TranscriptionError as exc:
            raise _http_error(exc) from exc

    @app.get("/api/projects/{project_id}/audio")
    async def project_audio(project_id: str) -> FileResponse:
        try:
            project = get_project(project_id)["project"]
            project_dir = Path(project["project_dir"]).resolve()
            source_dir = (project_dir / "source").resolve()
            candidates = [item for item in source_dir.iterdir() if item.is_file()]
            if not candidates:
                raise TranscriptionError("项目中没有可回听的原始音频。")
            source = candidates[0].resolve()
            if not source.is_relative_to(source_dir):
                raise TranscriptionError("项目音频路径无效。")
        except (TranscriptionError, OSError, KeyError) as exc:
            raise _http_error(exc) from exc
        return FileResponse(source, filename=source.name)

    @app.get("/api/projects/{project_id}/spectrogram")
    async def project_spectrogram(project_id: str) -> FileResponse:
        try:
            project = get_project(project_id)["project"]
            project_dir = Path(project["project_dir"]).resolve()
            analysis_dir = (project_dir / "analysis").resolve()
            source = (analysis_dir / "analysis_mono_22050.wav").resolve()
            if not source.is_relative_to(analysis_dir) or not source.is_file():
                raise TranscriptionError("项目中没有可绘制的本机分析音频。")
            destination = project_dir / "analysis" / "spectrogram.png"
            if not destination.is_file() or destination.stat().st_mtime < source.stat().st_mtime:
                from .spectrogram import render_spectrogram
                await run_in_threadpool(render_spectrogram, source, destination)
        except (TranscriptionError, OSError, KeyError, ValueError) as exc:
            raise _http_error(exc) from exc
        return FileResponse(destination, media_type="image/png", filename="spectrogram.png")

    @app.post("/api/projects")
    async def create_project(
        audio: UploadFile = File(...),
        title: str = Form(default=""),
        long_audio_mode: bool = Form(default=False),
        preset: str = Form(default="conservative"),
        engine: str = Form(default="auto"),
        sensitivity: int = Form(default=40),
        min_note_duration_ms: int = Form(default=180),
        min_confidence: int = Form(default=70),
        merge_gap_ms: int = Form(default=90),
    ) -> dict[str, Any]:
        safe_upload_name = Path(audio.filename or "audio.wav").name
        suffix = Path(safe_upload_name).suffix.lower()
        temp_dir = get_data_root() / "temp" / uuid.uuid4().hex
        temp_dir.mkdir(parents=True, exist_ok=False)
        temp_path = temp_dir / (safe_upload_name if suffix else "audio.wav")
        size = 0
        try:
            settings = normalize_transcription_settings(
                {
                    "preset": preset,
                    "engine": engine,
                    "sensitivity": sensitivity,
                    "min_note_duration_ms": min_note_duration_ms,
                    "min_confidence": min_confidence,
                    "merge_gap_ms": merge_gap_ms,
                }
            )
            with temp_path.open("wb") as handle:
                while chunk := await audio.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="文件超过 500 MB，已拒绝处理。")
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="上传文件为空。")
            job = jobs.create(
                audio_path=temp_path,
                title=title.strip() or None,
                max_seconds=0 if long_audio_mode else 300,
                transcription_settings=settings,
            )
            temp_path = Path()  # Job owns and cleans the temporary upload directory.
            return {"job": job}
        except TranscriptionError as exc:
            raise _http_error(exc) from exc
        finally:
            await audio.close()
            if temp_path != Path():
                shutil.rmtree(temp_dir, ignore_errors=True)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        try:
            return {"job": jobs.get(job_id)}
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="本机任务不存在或已过期。") from exc

    @app.put("/api/projects/{project_id}")
    async def save_project(project_id: str, incoming: ScoreUpdate) -> dict[str, Any]:
        try:
            return update_project_score(project_id, incoming.score)
        except TranscriptionError as exc:
            raise _http_error(exc) from exc

    @app.get("/api/projects/{project_id}/exports/{kind}")
    async def download_export(project_id: str, kind: str) -> FileResponse:
        if kind not in {"json", "midi", "musicxml"}:
            raise HTTPException(status_code=404, detail="未知导出格式。")
        try:
            project = get_project(project_id)["project"]
            path = Path(project["exports"][kind])
            project_dir = Path(project["project_dir"]).resolve()
            if not path.resolve().is_relative_to(project_dir) or not path.exists():
                raise TranscriptionError("导出文件不存在。")
        except (TranscriptionError, KeyError) as exc:
            raise _http_error(exc) from exc
        media_type = {
            "json": "application/json",
            "midi": "audio/midi",
            "musicxml": "application/vnd.recordare.musicxml+xml",
        }[kind]
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/profiles")
    async def profiles() -> dict[str, Any]:
        try:
            return {"profiles": list_profiles()}
        except ConfigurationError as exc:
            raise _http_error(exc) from exc

    @app.post("/api/profiles")
    async def save_profile(incoming: ProfileInput) -> dict[str, Any]:
        try:
            profile = create_or_update_profile(
                profile_id=incoming.id,
                name=incoming.name,
                base_url=incoming.base_url,
                model=incoming.model,
                temperature=incoming.temperature,
                api_key=incoming.api_key,
            )
            return {"profile": profile}
        except ConfigurationError as exc:
            raise _http_error(exc) from exc

    @app.delete("/api/profiles/{profile_id}")
    async def remove_profile(profile_id: str) -> dict[str, bool]:
        try:
            delete_profile(profile_id)
            return {"ok": True}
        except ConfigurationError as exc:
            raise _http_error(exc) from exc

    @app.post("/api/projects/{project_id}/review")
    async def review(project_id: str, incoming: ReviewInput) -> dict[str, Any]:
        try:
            score = get_project(project_id)["score"]
            result = await run_in_threadpool(review_score, score, incoming.profile_id, incoming.instruction)
            return {"review": result}
        except (TranscriptionError, ConfigurationError, LLMReviewError) as exc:
            raise _http_error(exc) from exc

    return app
