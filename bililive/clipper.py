"""关键词触发的直播切片。"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import RECORD_DIR, sanitize_filename

SKIP_WINDOW_SECONDS = 180.0
POST_WINDOW_SECONDS = 5.0


def build_clip_command(
    record_file: str | Path,
    start_seconds: float,
    end_seconds: float,
    output_file: str | Path,
) -> list[str]:
    duration = max(0.1, end_seconds - start_seconds)
    return [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(record_file),
        "-ss",
        f"{max(0.0, start_seconds):.3f}",
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_file),
    ]


def make_clip_filename(
    streamer_name: str,
    start_seconds: float,
    when: Optional[datetime] = None,
) -> str:
    moment = when or datetime.now()
    safe_name = sanitize_filename(streamer_name)
    return (
        f"Clip_{safe_name}_{moment.strftime('%H%M%S')}"
        f"_from_{int(max(0.0, start_seconds))}s.mp4"
    )


class Clipper:
    """在后台用 ffmpeg 从正在录制的文件中截取片段。"""

    def __init__(
        self,
        record_provider: Callable[[], Optional[str]],
        output_dir: str | Path = RECORD_DIR,
        on_message: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.record_provider = record_provider
        self.output_dir = Path(output_dir)
        self.on_message = on_message or (lambda _msg: None)

    def trigger(
        self,
        end_seconds: float,
        streamer_name: str,
        record_file: Optional[str] = None,
    ) -> Optional[str]:
        candidate = record_file or self.record_provider()
        if candidate:
            path = Path(candidate)
            if not path.is_file():
                fallback = path.with_suffix(".mp4")
                candidate = str(fallback) if fallback.is_file() else None
        else:
            candidate = None

        if not candidate:
            self.on_message("⚠️ [切片] 找不到当前录像文件，跳过")
            return None

        start_seconds = max(0.0, end_seconds - SKIP_WINDOW_SECONDS)
        clip_end = end_seconds + POST_WINDOW_SECONDS
        clip_name = make_clip_filename(streamer_name, start_seconds)
        output_file = self.output_dir / clip_name

        self.output_dir.mkdir(parents=True, exist_ok=True)
        command = build_clip_command(candidate, start_seconds, clip_end, output_file)
        self.on_message(f"✂️ [切片] 已触发，正在生成: {clip_name}")

        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            subprocess.Popen(command, **kwargs)
        except Exception as exc:
            self.on_message(f"❌ [切片] 启动 ffmpeg 失败: {exc}")
            return None
        return str(output_file)
