"""Local paths and OpenAI-compatible profile management.

Profiles intentionally contain no API key. On Windows keyring stores secrets in
Windows Credential Manager; the JSON file contains non-secret connection metadata.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_VERSION = "V260924A"
KEYRING_SERVICE = "HearPitchLocal.V260924A"
PROFILE_SCHEMA_VERSION = 1


class ConfigurationError(ValueError):
    """Raised when a local profile cannot be used safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_data_root() -> Path:
    """Return the local, user-owned application data directory.

    HEARPITCH_HOME is intentionally supported for portable testing.  On Windows
    the normal target is Documents\\HearPitchLocal; Unix-like environments use
    ~/Documents/HearPitchLocal for development parity.
    """
    configured = os.environ.get("HEARPITCH_HOME")
    if configured:
        root = Path(configured).expanduser().resolve()
    elif sys.platform.startswith("win"):
        root = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "HearPitchLocal"
    else:
        root = Path.home() / "Documents" / "HearPitchLocal"
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)
    (root / "temp").mkdir(exist_ok=True)
    return root


def projects_root() -> Path:
    path = get_data_root() / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_path() -> Path:
    return get_data_root() / "profiles.json"


def _default_profile_document() -> dict[str, Any]:
    return {"schema_version": PROFILE_SCHEMA_VERSION, "profiles": []}


def _read_profile_document() -> dict[str, Any]:
    path = profiles_path()
    if not path.exists():
        return _default_profile_document()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"无法读取 profiles.json：{exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("profiles"), list):
        raise ConfigurationError("profiles.json 格式无效。")
    return document


def _write_profile_document(document: dict[str, Any]) -> None:
    path = profiles_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _clean_text(value: Any, label: str, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConfigurationError(f"{label}不能为空。")
    if len(text) > maximum:
        raise ConfigurationError(f"{label}过长。")
    return text


def _normalise_base_url(value: Any) -> str:
    url = _clean_text(value, "Base URL", 500).rstrip("/")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ConfigurationError("Base URL 必须以 http:// 或 https:// 开头。")
    return url


def list_profiles() -> list[dict[str, Any]]:
    """List non-secret OpenAI-compatible profile metadata."""
    profiles = _read_profile_document()["profiles"]
    result: list[dict[str, Any]] = []
    for item in profiles:
        profile = dict(item)
        profile["credential"] = credential_status(profile["id"])
        result.append(profile)
    return result


def create_or_update_profile(
    *,
    profile_id: str | None,
    name: str,
    base_url: str,
    model: str,
    temperature: float = 0.2,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Save profile metadata and optionally save its key in the OS credential store."""
    clean_name = _clean_text(name, "配置名称")
    clean_model = _clean_text(model, "模型名称")
    clean_url = _normalise_base_url(base_url)
    try:
        clean_temperature = float(temperature)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("temperature 必须是数值。") from exc
    if not 0 <= clean_temperature <= 2:
        raise ConfigurationError("temperature 必须在 0 到 2 之间。")

    document = _read_profile_document()
    existing = next((item for item in document["profiles"] if item["id"] == profile_id), None)
    now = _utc_now()
    profile = {
        "id": profile_id or uuid.uuid4().hex,
        "name": clean_name,
        "provider": "openai-compatible",
        "base_url": clean_url,
        "model": clean_model,
        "temperature": clean_temperature,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
    }
    if api_key and api_key.strip():
        set_api_key(profile["id"], api_key.strip())
    if existing:
        document["profiles"] = [profile if item["id"] == profile_id else item for item in document["profiles"]]
    else:
        document["profiles"].append(profile)
    _write_profile_document(document)
    profile["credential"] = credential_status(profile["id"])
    return profile


def delete_profile(profile_id: str) -> None:
    document = _read_profile_document()
    if not any(item["id"] == profile_id for item in document["profiles"]):
        raise ConfigurationError("未找到该配置。")
    document["profiles"] = [item for item in document["profiles"] if item["id"] != profile_id]
    _write_profile_document(document)
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, profile_id)
    except Exception:
        # Metadata deletion must not fail merely because a key was never supplied
        # or the credential store is temporarily unavailable.
        pass


def get_profile(profile_id: str) -> dict[str, Any]:
    profile = next((item for item in list_profiles() if item["id"] == profile_id), None)
    if not profile:
        raise ConfigurationError("未找到所选 AI 配置。")
    return profile


def set_api_key(profile_id: str, api_key: str) -> None:
    if not api_key.strip():
        raise ConfigurationError("API Key 不能为空。")
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, profile_id, api_key.strip())
    except Exception as exc:
        raise ConfigurationError(
            "无法写入系统凭据管理器。Windows 请确认 Credential Manager 服务可用；"
            "API Key 不会降级保存到 profiles.json。可先保存不含 Key 的配置，再排查凭据管理器。"
        ) from exc


def credential_status(profile_id: str) -> dict[str, str]:
    """Return non-secret credential state for rendering in the local UI."""
    try:
        import keyring
        api_key = keyring.get_password(KEYRING_SERVICE, profile_id)
    except Exception as exc:
        return {
            "status": "unavailable",
            "message": "无法访问系统凭据管理器；请检查 Windows Credential Manager 服务。",
        }
    if api_key:
        return {"status": "stored", "message": "API Key 已安全保存至系统凭据管理器。"}
    return {"status": "missing", "message": "尚未保存 API Key；基础听谱仍可使用。"}


def get_api_key(profile_id: str) -> str:
    try:
        import keyring
        api_key = keyring.get_password(KEYRING_SERVICE, profile_id)
    except Exception as exc:
        raise ConfigurationError("无法读取系统凭据管理器中的 API Key。") from exc
    if not api_key:
        raise ConfigurationError("所选 AI 配置尚未保存 API Key。请在设置中填写后保存。")
    return api_key
