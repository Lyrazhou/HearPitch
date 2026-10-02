"""OpenAI-compatible, opt-in score review.

The audio never leaves the local transcription pipeline. This module sends only
structured score data and optional user instructions to the selected API endpoint.
It returns suggestions; it never mutates a project automatically.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import ConfigurationError, get_api_key, get_profile


class LLMReviewError(RuntimeError):
    """Raised when an optional score review cannot be completed."""


def _extract_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMReviewError("AI 未返回有效 JSON，未对谱面作任何修改。") from exc
    if not isinstance(data, dict):
        raise LLMReviewError("AI 返回格式不正确，未对谱面作任何修改。")
    return data


def review_score(score: dict[str, Any], profile_id: str, instruction: str = "") -> dict[str, Any]:
    """Ask an OpenAI-compatible model to suggest review actions for a score."""
    profile = get_profile(profile_id)
    api_key = get_api_key(profile_id)

    notes = score.get("notes", [])
    # A review of a 5-minute monophonic recording may carry thousands of frames,
    # but the document has note events. Cap the payload and state this explicitly.
    safe_score = {
        "version": score.get("version"),
        "tempo_bpm": score.get("tempo_bpm"),
        "time_signature": score.get("time_signature"),
        "key": score.get("key"),
        "duration_seconds": score.get("duration_seconds"),
        "notes": notes[:1200],
        "truncated": len(notes) > 1200,
    }
    schema_hint = {
        "summary": "一句话总结",
        "findings": [
            {
                "note_ids": ["n001"],
                "severity": "low|medium|high",
                "reason": "为何可疑",
                "suggested_action": "keep|raise_semitone|lower_semitone|merge_with_next|split|adjust_duration|manual_check",
                "proposed_change": "人类可读的建议，不得直接假装已修改",
            }
        ],
        "global_suggestions": ["可选建议"],
        "limitations": ["不能从事件数据确认的事实"],
    }
    system = (
        "你是谨慎的单旋律乐谱校对助手。你只能依据给定的结构化候选谱做建议，"
        "不得声称听到了音频，不得编造小节、歌词、和声或音符。低置信度、极短音符、"
        "异常跳进和不合理量化可作为人工复听提示，不是自动错误。"
        "所有修改都必须由用户确认后才会执行。只输出 JSON，结构为："
        + json.dumps(schema_hint, ensure_ascii=False)
    )
    user = {
        "task": "检查候选谱并提出需要人工确认的校对建议。",
        "user_instruction": instruction.strip(),
        "score": safe_score,
    }
    endpoint = profile["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": profile["model"],
        "temperature": profile.get("temperature", 0.2),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    }
    try:
        with httpx.Client(timeout=75.0) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise LLMReviewError(f"AI 校对请求失败：{exc}") from exc
    except ValueError as exc:
        raise LLMReviewError("AI 服务返回了无法解析的响应。") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMReviewError("AI 服务响应中没有可用内容。") from exc
    if not isinstance(content, str):
        raise LLMReviewError("AI 服务返回了空内容。")
    result = _extract_json(content)
    result["profile_name"] = profile["name"]
    result["note_count_sent"] = min(len(notes), 1200)
    result["audio_uploaded"] = False
    return result
