"""项目配置、路径与常量。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "configs"
RECORD_DIR = PROJECT_ROOT / "records"
LOG_DIR = PROJECT_ROOT / "logs"

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-mlx"
DEFAULT_FASTER_WHISPER_MODEL = "large-v3"

IGNORE_KEYWORDS = [
    "by bwd6",
    "字幕by",
    "Amara.org",
    "优优独播剧场",
    "compared compared",
    "YoYo Television",
    "不吝点赞",
    "订阅我的频道",
    "Copyright",
    "The following content",
    "中文字幕志愿者",
]

TRIGGER_KEYWORDS = [
    "切片飞来",
    "切片飞莱",
    "贴片飞来",
    "切片飛來",
    "切片飛来",
]


@dataclass(frozen=True)
class RoomConfig:
    room_id: str
    streamer_name: str = ""

    @property
    def safe_name(self) -> str:
        return sanitize_filename(self.streamer_name or self.room_id)


def sanitize_filename(name: str) -> str:
    """去掉文件名里的非法字符，避免切片/日志路径出错。"""
    cleaned = "".join(ch for ch in name if ch not in '\\/:*?"<>|\n\r\t')
    return cleaned.strip() or "streamer"


def load_room_config(path: str | os.PathLike[str]) -> RoomConfig:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"找不到配置文件: {file_path}")

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是合法 JSON: {file_path} ({exc})") from exc

    if not isinstance(data, dict):
        raise ValueError(f"配置文件内容必须是对象: {file_path}")

    room_id = str(data.get("room_id", "")).strip()
    if not room_id:
        raise ValueError(f"配置文件缺少 room_id: {file_path}")

    return RoomConfig(
        room_id=room_id,
        streamer_name=str(data.get("streamer_name", "")).strip(),
    )


def resolve_config_path(name: str) -> Path:
    """支持绝对路径、相对路径以及 configs/ 下的短名称。"""
    if not name:
        return CONFIG_DIR / "jiojio.json"

    path = Path(name).expanduser()
    if path.is_file():
        return path

    if not path.is_absolute():
        for base in (CONFIG_DIR, PROJECT_ROOT):
            candidate = base / path
            if candidate.is_file():
                return candidate
            if not candidate.suffix:
                candidate = candidate.with_suffix(".json")
                if candidate.is_file():
                    return candidate

    return path


def ensure_runtime_dirs() -> None:
    for directory in (CONFIG_DIR, RECORD_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
