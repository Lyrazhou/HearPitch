#!/usr/bin/env python3
"""HearPitch Local V261003A command-line entry point.

Examples:
  python hearpitch_cli.py transcribe C:\\recordings\\melody.wav
  python hearpitch_cli.py transcribe melody.wav --max-seconds 0 --title "练习旋律"
  python hearpitch_cli.py serve
  python hearpitch_cli.py profile add --name OpenAI --base-url https://api.openai.com/v1 --model gpt-5-mini
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

# The core deliberately avoids the historic ``hearpitch`` package name.  That
# prevents a stale root-level hearpitch.py from masking the installed package
# when a hotfix is extracted over an older Windows folder.
from hearpitch_core import __version__
from hearpitch_core.config import ConfigurationError, create_or_update_profile, delete_profile, list_profiles
from hearpitch_core.transcription import TranscriptionError, create_project_from_audio


def _set_data_dir(value: str | None) -> None:
    if value:
        os.environ["HEARPITCH_HOME"] = str(Path(value).expanduser().resolve())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hearpitch_cli.py",
        description=f"HearPitch Local {__version__} — 本机单旋律候选转谱。",
    )
    parser.add_argument("--data-dir", help="本地数据目录。默认：Documents/HearPitchLocal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe = subparsers.add_parser("transcribe", help="将本地音频转为候选谱。")
    transcribe.add_argument("audio", type=Path, help="MP3/WAV/FLAC/OGG/M4A/AAC 本地音频文件")
    transcribe.add_argument("--title", help="项目和导出文件标题")
    transcribe.add_argument("--max-seconds", type=float, default=0, help="最长处理秒数；0 = 不限（默认）")
    transcribe.add_argument("--no-basic-pitch", action="store_true", help="跳过可选 Basic Pitch，引擎固定使用 pYIN")
    transcribe.add_argument("--engine", choices=["auto", "basic-pitch", "pyin", "rosvot-rmvpe"], default="auto", help="转录引擎；ROSVOT+RMVPE 需单独安装 CUDA 模型")
    transcribe.add_argument("--preset", choices=["conservative", "balanced", "sensitive"], default="conservative", help="识别预设；默认保守")
    transcribe.add_argument("--sensitivity", type=int, help="识别灵敏度 0–100；指定后覆盖预设")
    transcribe.add_argument("--min-note-duration-ms", type=int, help="最短音符时长 50–500 ms")
    transcribe.add_argument("--min-confidence", type=int, help="最低音高置信度 0–100")
    transcribe.add_argument("--merge-gap-ms", type=int, help="同音合并容差 0–300 ms")
    transcribe.add_argument("--json", action="store_true", help="以 JSON 形式输出项目与导出路径")

    serve = subparsers.add_parser("serve", help="启动仅本机访问的网页界面。")
    serve.add_argument("--host", default="127.0.0.1", help="默认仅本机：127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    profile = subparsers.add_parser("profile", help="管理可选 AI 校对配置。")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="列出非秘密 AI 配置。")
    add = profile_sub.add_parser("add", help="新增或更新 OpenAI 兼容接口配置。")
    add.add_argument("--id", help="更新已有配置时填写 ID")
    add.add_argument("--name", required=True)
    add.add_argument("--base-url", required=True, help="例如 https://api.openai.com/v1")
    add.add_argument("--model", required=True)
    add.add_argument("--temperature", type=float, default=0.2)
    add.add_argument("--api-key", help="不建议写在命令行历史中；省略后将安全地提示输入。")
    remove = profile_sub.add_parser("remove", help="删除 AI 配置及其系统凭据。")
    remove.add_argument("id")
    return parser


def _handle_transcribe(args: argparse.Namespace) -> int:
    settings = {"preset": args.preset, "engine": "pyin" if args.no_basic_pitch else args.engine}
    for source, destination in [
        ("sensitivity", "sensitivity"),
        ("min_note_duration_ms", "min_note_duration_ms"),
        ("min_confidence", "min_confidence"),
        ("merge_gap_ms", "merge_gap_ms"),
    ]:
        value = getattr(args, source)
        if value is not None:
            settings[destination] = value
    result = create_project_from_audio(
        args.audio.expanduser().resolve(),
        title=args.title,
        max_seconds=args.max_seconds,
        prefer_basic_pitch=not args.no_basic_pitch,
        transcription_settings=settings,
    )
    if args.json:
        print(json.dumps(result["project"], ensure_ascii=False, indent=2))
    else:
        project = result["project"]
        score = result["score"]
        print(f"完成：{project['title']}")
        print(f"引擎：{score['engine']['name']}（可编辑候选谱）")
        print(f"音符数：{len(score['notes'])}")
        print(f"项目目录：{project['project_dir']}")
        print("导出：")
        for kind, path in project["exports"].items():
            print(f"  {kind}: {path}")
    return 0


def _handle_serve(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("警告：当前服务将不只对本机开放。私人使用建议保持 127.0.0.1。", file=sys.stderr)
    import uvicorn
    from hearpitch_core.app import create_app
    print(f"HearPitch Local {__version__} 正在运行： http://{args.host}:{args.port}")
    print("音频默认只在本机处理；按 Ctrl+C 停止。")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def _handle_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "list":
        profiles = list_profiles()
        if not profiles:
            print("尚无 AI 配置。基础转谱不需要 API Key。")
        for item in profiles:
            print(f"{item['id']}\t{item['name']}\t{item['base_url']}\t{item['model']}")
        return 0
    if args.profile_command == "add":
        api_key = args.api_key or getpass.getpass("API Key（将保存到系统凭据管理器，不写入 JSON）：")
        profile = create_or_update_profile(
            profile_id=args.id,
            name=args.name,
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            api_key=api_key,
        )
        print(f"已保存配置：{profile['name']}（{profile['id']}）")
        return 0
    if args.profile_command == "remove":
        delete_profile(args.id)
        print("已删除配置及其系统凭据。")
        return 0
    return 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _set_data_dir(args.data_dir)
    try:
        if args.command == "transcribe":
            return _handle_transcribe(args)
        if args.command == "serve":
            return _handle_serve(args)
        if args.command == "profile":
            return _handle_profile(args)
        return 1
    except (TranscriptionError, ConfigurationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
