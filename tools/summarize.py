#!/usr/bin/env python3
"""直播复盘报告生成器（DeepSeek + 数据驱动）。

功能：
- 用 DeepSeek 对一场或多场直播转写做「内容运营 + 数据分析」复盘
- 自动匹配 configs/ 下的直播间配置、logs/ 转写和 records/ 录像
- 汇总 MySQL 当天的礼物、舰长、SC、弹幕等经营数据
- 输出 Markdown 报告，并渲染成适合发给主播的 PNG 长图（支持 Mermaid）

用法：
    python tools/summarize.py                              # 打开图形界面
    python tools/summarize.py logs/a.txt logs/b.txt         # 多文件合并总结
    python tools/summarize.py --config configs/xiyin.json --date 2026-11-11
    python tools/summarize.py --gui
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time as time_module
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bililive.config import (  # noqa: E402
    CONFIG_DIR,
    LOG_DIR,
    RECORD_DIR,
    RoomConfig,
    load_room_config,
    resolve_config_path,
    sanitize_filename,
)
from bililive.database import DatabaseConfig  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parent
SECRET_PATH = TOOLS_DIR / ".dsSecretKey"
REPORT_DIR = PROJECT_ROOT / "reports"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_OPENAI_URL = "http://127.0.0.1:8000/v1/chat/completions"

BILI_CREDENTIAL_FILE = PROJECT_ROOT / ".bili_credential.json"
GUARD_NAMES = {1: "总督", 2: "提督", 3: "舰长"}
BILI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_GUARD_CACHE: dict[str, list["GuardUser"]] = {}
_ANCHOR_CACHE: dict[str, int] = {}
_LIVE_CARD_CACHE: dict[tuple[str, int], "LiveUserCard"] = {}
_BILI_LEVEL_CACHE: dict[int, int] = {}

MAX_INPUT_CHARS = 60_000
DANMAKU_CHAR_LIMIT = 18_000
PERSONA_SAMPLE_PER_USER = 15
MAX_TOKENS = 8192

LOG_NAME_RE = re.compile(
    r"^(?P<streamer>.+?)_(?P<room>\d+)_(?P<kind>mlx_log|log|danmaku)_(?P<ts>\d+)\.txt$"
)
RECORD_NAME_RE = re.compile(
    r"^live_record_(?P<room>\d+)_(?P<ts>\d+)\.(?:mp4|ts|mkv|flv)$"
)

SYSTEM_PROMPT = (
    "你是一名资深的 B 站直播内容运营总监兼数据增长分析师。"
    "你的读者是主播本人和她的运营团队，报告会直接发给主播看。"
    "你只基于给定材料分析，绝不编造数据、事件或用户行为；材料不足时必须明确说明。"
    "你的语气要委婉、建设性，先肯定做得好的地方，再指出提升空间；对事不对人，"
    "避免尖锐的否定性标签，把问题转化为下一场可以执行的动作。"
    "你的输出必须是结构清晰、可直接复制发布的 Markdown 报告，不要寒暄，不要输出分析过程。"
)


class SummarizeError(RuntimeError):
    pass


class NoLogError(SummarizeError):
    pass


class LLMError(SummarizeError):
    pass


class Cancelled(SummarizeError):
    pass


@dataclass
class LogEntry:
    path: Path
    streamer: str
    room_id: str
    kind: str
    ts: int

    @property
    def day(self) -> date:
        return datetime.fromtimestamp(self.ts).date()

    @property
    def time_label(self) -> str:
        return datetime.fromtimestamp(self.ts).strftime("%H:%M")


@dataclass
class SummarizeOptions:
    api_key: str = ""
    model: str = DEFAULT_DEEPSEEK_MODEL
    url: str = DEEPSEEK_URL
    max_chars: int = MAX_INPUT_CHARS
    include_danmaku: bool = True
    include_db: bool = True
    include_brief: bool = True
    max_tokens: int = MAX_TOKENS
    temperature: float = 0.35
    retries: int = 3
    dry_run: bool = False
    cancel_event: Optional[object] = None
    stream_callback: Optional[Callable[[str], None]] = None
    progress: Optional[Callable[[str], None]] = None


@dataclass
class RecordInfo:
    paths: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    total_seconds: float = 0.0


@dataclass
class SavedReport:
    markdown: Path
    image: Optional[Path] = None
    brief_markdown: Optional[Path] = None
    brief_image: Optional[Path] = None


@dataclass
class GuardUser:
    uid: int
    username: str
    guard_level: int
    medal_level: Optional[int] = None

    @property
    def guard_name(self) -> str:
        return GUARD_NAMES.get(self.guard_level, "大航海")


@dataclass
class LiveUserCard:
    uid: int
    username: str
    wealth_level: Optional[int] = None
    guard_text: str = ""


def _progress(options: SummarizeOptions, message: str) -> None:
    if options.progress:
        options.progress(message)


def _is_cancelled(options: SummarizeOptions) -> bool:
    event = options.cancel_event
    return bool(event is not None and getattr(event, "is_set", lambda: False)())


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bili_cookies() -> dict[str, str]:
    if not BILI_CREDENTIAL_FILE.is_file():
        return {}
    try:
        data = json.loads(BILI_CREDENTIAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        key: str(data[key])
        for key in ("SESSDATA", "bili_jct", "DedeUserID")
        if data.get(key)
    }


def _bili_headers(room_id: str) -> dict[str, str]:
    return {
        "User-Agent": BILI_USER_AGENT,
        "Referer": f"https://live.bilibili.com/{room_id}",
    }


def fetch_anchor_uid(room_id: str, cookies: Optional[dict[str, str]] = None) -> int:
    if room_id in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[room_id]
    cookies = cookies if cookies is not None else _bili_cookies()
    if not cookies:
        return 0
    try:
        response = requests.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info",
            params={"room_id": room_id},
            headers=_bili_headers(room_id),
            cookies=cookies,
            timeout=8,
        )
        payload = response.json()
        anchor_uid = _safe_int((payload.get("data") or {}).get("uid"))
        if payload.get("code") == 0 and anchor_uid:
            _ANCHOR_CACHE[room_id] = anchor_uid
            return anchor_uid
    except (requests.RequestException, ValueError, OSError):
        pass
    return 0


def fetch_guard_users(
    room_id: str, progress: Optional[Callable[[str], None]] = None
) -> list[GuardUser]:
    """获取直播间当前大航海名单（总督/提督/舰长）。

    使用本地 .bili_credential.json 的 Cookie 访问 B 站接口；失败时返回空列表，
    复盘仍然可以基于数据库和日志继续生成。
    """
    if room_id in _GUARD_CACHE:
        return _GUARD_CACHE[room_id]
    cookies = _bili_cookies()
    if not cookies:
        _GUARD_CACHE[room_id] = []
        return []

    if progress:
        progress("⛵ 正在获取 B 站大航海名单...")

    headers = _bili_headers(room_id)
    guards: dict[int, GuardUser] = {}
    try:
        anchor_uid = fetch_anchor_uid(room_id, cookies)
        if not anchor_uid:
            _GUARD_CACHE[room_id] = []
            return []

        total = 0
        for page in range(1, 6):
            response = requests.get(
                "https://api.live.bilibili.com/xlive/app-room/v2/guardTab/topList",
                params={
                    "roomid": room_id,
                    "ruid": anchor_uid,
                    "page": page,
                    "page_size": 50,
                },
                headers=headers,
                cookies=cookies,
                timeout=8,
            )
            payload = response.json()
            if payload.get("code") != 0:
                break
            data = payload.get("data") or {}
            items = list(data.get("top3") or []) + list(data.get("list") or [])
            for item in items:
                uid = _safe_int(item.get("uid"))
                username = str(item.get("username") or "").strip()
                guard_level = _safe_int(item.get("guard_level"))
                if not uid or not username or not guard_level:
                    continue
                medal = item.get("medal_info") or {}
                medal_level = medal.get("medal_level") if isinstance(medal, dict) else None
                guards[uid] = GuardUser(
                    uid=uid,
                    username=username,
                    guard_level=guard_level,
                    medal_level=_safe_int(medal_level) or None,
                )
            info = data.get("info") or {}
            total = _safe_int(info.get("num"), total)
            if not items or (total and len(guards) >= total):
                break
    except (requests.RequestException, ValueError, OSError):
        pass

    _GUARD_CACHE[room_id] = list(guards.values())
    return _GUARD_CACHE[room_id]


def fetch_live_user_cards(
    room_id: str,
    uids: list[int],
    progress: Optional[Callable[[str], None]] = None,
) -> dict[int, LiveUserCard]:
    """查询用户在直播间的财富等级和大航海身份。

    这里的「等级」是 B 站直播间的荣耀/财富等级（例如 33），不是账号等级（0-6），
    也不是大会员。账号等级由 fetch_bili_levels 单独查询。
    """
    cards: dict[int, LiveUserCard] = {}
    pending: list[int] = []
    for uid in uids:
        cached = _LIVE_CARD_CACHE.get((room_id, uid))
        if cached is not None:
            cards[uid] = cached
        elif uid not in pending:
            pending.append(uid)

    pending = pending[:15]
    if not pending:
        return cards
    cookies = _bili_cookies()
    if not cookies:
        return cards
    anchor_uid = fetch_anchor_uid(room_id, cookies)
    if not anchor_uid:
        return cards
    if progress:
        progress("🏅 正在查询核心用户的直播间财富等级...")

    headers = _bili_headers(room_id)
    for uid in pending:
        try:
            response = requests.get(
                "https://api.live.bilibili.com/xlive/app-ucenter/v2/card/user",
                params={"uid": uid, "ruid": anchor_uid},
                headers=headers,
                cookies=cookies,
                timeout=6,
            )
            payload = response.json()
        except (requests.RequestException, ValueError, OSError):
            continue
        if payload.get("code") != 0:
            continue
        data = payload.get("data") or {}
        wealth = data.get("wealth_info") or {}
        card = LiveUserCard(
            uid=uid,
            username=str(data.get("uname") or ""),
            wealth_level=_safe_int(wealth.get("level")) or None,
            guard_text=str(data.get("head_text") or ""),
        )
        _LIVE_CARD_CACHE[(room_id, uid)] = card
        cards[uid] = card
        time_module.sleep(0.1)
    return cards


def fetch_bili_levels(
    uids: list[int], progress: Optional[Callable[[str], None]] = None
) -> dict[int, int]:
    """查询核心用户的 B 站账号等级（不是直播间等级）。"""
    levels: dict[int, int] = {}
    pending: list[int] = []
    for uid in uids:
        if uid in _BILI_LEVEL_CACHE:
            levels[uid] = _BILI_LEVEL_CACHE[uid]
        elif uid not in pending:
            pending.append(uid)

    pending = pending[:15]
    if not pending:
        return levels
    if progress:
        progress("👤 正在查询核心用户的 B 站等级...")

    cookies = _bili_cookies()
    headers = {
        "User-Agent": BILI_USER_AGENT,
        "Referer": "https://live.bilibili.com/",
    }
    for uid in pending:
        try:
            response = requests.get(
                "https://api.bilibili.com/x/web-interface/card",
                params={"mid": uid},
                headers=headers,
                cookies=cookies or None,
                timeout=6,
            )
            payload = response.json()
        except (requests.RequestException, ValueError, OSError):
            continue
        if payload.get("code") == -799:
            break
        if payload.get("code") != 0:
            continue
        card = (payload.get("data") or {}).get("card") or {}
        level = (card.get("level_info") or {}).get("current_level")
        if level is None:
            continue
        level = _safe_int(level, -1)
        if level >= 0:
            _BILI_LEVEL_CACHE[uid] = level
            levels[uid] = level
        time_module.sleep(0.12)
    return levels


def load_api_key(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    if SECRET_PATH.is_file():
        try:
            return SECRET_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def parse_log_name(path: Path) -> Optional[LogEntry]:
    match = LOG_NAME_RE.match(path.name)
    if not match:
        return None
    return LogEntry(
        path=path,
        streamer=match.group("streamer"),
        room_id=match.group("room"),
        kind=match.group("kind"),
        ts=int(match.group("ts")),
    )


def find_daily_sources(config: RoomConfig, day: date) -> tuple[list[LogEntry], list[LogEntry]]:
    transcripts: list[LogEntry] = []
    danmaku: list[LogEntry] = []
    if not LOG_DIR.is_dir():
        return transcripts, danmaku
    for path in LOG_DIR.glob("*.txt"):
        entry = parse_log_name(path)
        if entry is None or entry.room_id != config.room_id or entry.day != day:
            continue
        if entry.kind == "danmaku":
            danmaku.append(entry)
        else:
            transcripts.append(entry)
    transcripts.sort(key=lambda item: item.ts)
    danmaku.sort(key=lambda item: item.ts)
    return transcripts, danmaku


def find_danmaku_for_day(room_id: str, day: date) -> list[LogEntry]:
    found: list[LogEntry] = []
    if not LOG_DIR.is_dir():
        return found
    for path in LOG_DIR.glob("*.txt"):
        entry = parse_log_name(path)
        if (
            entry is not None
            and entry.room_id == room_id
            and entry.day == day
            and entry.kind == "danmaku"
        ):
            found.append(entry)
    found.sort(key=lambda item: item.ts)
    return found


def find_records(room_id: str, day: date) -> RecordInfo:
    info = RecordInfo()
    if not RECORD_DIR.is_dir():
        return info
    for path in RECORD_DIR.glob(f"live_record_{room_id}_*"):
        match = RECORD_NAME_RE.match(path.name)
        if not match:
            continue
        ts = int(match.group("ts"))
        if datetime.fromtimestamp(ts).date() != day:
            continue
        info.paths.append(path)
        try:
            info.total_bytes += path.stat().st_size
        except OSError:
            pass
    info.paths.sort()
    info.total_seconds = _probe_durations(info.paths)
    return info


def _probe_durations(paths: Iterable[Path]) -> float:
    ffprobe = None
    for candidate in ("ffprobe", "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
        from shutil import which

        ffprobe = which(candidate) if not Path(candidate).is_file() else candidate
        if ffprobe:
            break
    if not ffprobe:
        return 0.0

    total = 0.0
    for path in list(paths)[:30]:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            total += float(result.stdout.strip() or 0)
        except (subprocess.SubprocessError, ValueError, OSError):
            continue
    return total


def _human_bytes(num: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _human_duration(seconds: float) -> str:
    if seconds <= 0:
        return "未知"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _money(value: float) -> str:
    return f"¥{float(value):,.2f}"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise SummarizeError(f"无法读取文件 {path}: {exc}") from exc


def _clean_transcript(text: str) -> str:
    text = re.sub(r"\]\s*\(\s*⚡[^)]*\)\s*", "] ", text)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _sample_text(text: str, limit: int) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    total = sum(len(line) + 1 for line in lines)
    if total <= limit:
        return text

    half = max(limit // 2, 1000)
    head: list[str] = []
    size = 0
    index = 0
    while index < len(lines) and size < half:
        head.append(lines[index])
        size += len(lines[index]) + 1
        index += 1

    tail: list[str] = []
    size = 0
    index = len(lines) - 1
    while index >= 0 and size < half:
        tail.append(lines[index])
        size += len(lines[index]) + 1
        index -= 1
    tail.reverse()

    return (
        "\n".join(head)
        + "\n\n……（中间弹幕已省略，完整记录见 logs/ 原始文件）……\n\n"
        + "\n".join(tail)
    )


def _pick_samples(items: list, limit: int) -> list:
    if not items:
        return []
    if len(items) <= limit:
        return list(items)
    step = len(items) / float(limit)
    return [items[min(len(items) - 1, int(index * step))] for index in range(limit)]


def _split_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if current and size + line_size > limit:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


def _connect_db():
    config = DatabaseConfig.from_env()
    if not config.enabled:
        return None, "数据库未配置（请在 .env 填写 DB_HOST / DB_DATABASE）"
    try:
        import pymysql
    except ImportError:
        return None, "未安装 PyMySQL，无法统计流水（pip install PyMySQL）"
    try:
        conn = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=6,
        )
        return conn, ""
    except Exception as exc:  # noqa: BLE001 - 报表要能降级
        return None, f"数据库连接失败：{type(exc).__name__}: {exc}"


def collect_metrics(
    room_ids: list[str], day: date, progress: Optional[Callable[[str], None]] = None
) -> str:
    if not room_ids:
        return "### 经营数据\n\n本期没有可匹配的房间号，未查询数据库。\n"

    conn, error = _connect_db()
    if conn is None:
        return f"### 经营数据\n\n本期未获取到数据库数据：{error}\n"

    if progress:
        progress("📊 正在统计当天流水与互动数据...")

    guard_users: list[GuardUser] = []
    for room_id in room_ids:
        guard_users.extend(fetch_guard_users(room_id, progress))

    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)
    placeholders = ", ".join(["%s"] * len(room_ids))
    room_params = tuple(room_ids)

    def one(sql: str, params: tuple = ()):
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def many(sql: str, params: tuple = ()):
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    try:
        gift_where = (
            f"room_id IN ({placeholders}) AND timestamp >= %s AND timestamp < %s"
        )
        gift_params = room_params + (day_start, day_end)

        gift_count, gift_amount, _ = one(
            f"""
            SELECT COUNT(*), COALESCE(SUM(total_price), 0), COUNT(DISTINCT user_id)
            FROM gift_records WHERE {gift_where}
            """,
            gift_params,
        ) or (0, 0, 0)

        guard_count, guard_amount = one(
            f"""
            SELECT COUNT(*), COALESCE(SUM(total_price), 0)
            FROM guard_records WHERE room_id IN ({placeholders})
              AND timestamp >= %s AND timestamp < %s
            """,
            room_params + (day_start, day_end),
        ) or (0, 0)

        sc_count, sc_amount = one(
            f"""
            SELECT COUNT(*), COALESCE(SUM(price), 0)
            FROM super_chat_records WHERE {gift_where}
            """,
            gift_params,
        ) or (0, 0)

        danmu_count, danmu_users = one(
            f"""
            SELECT COUNT(*), COUNT(DISTINCT user_id)
            FROM danmu_messages WHERE {gift_where}
            """,
            gift_params,
        ) or (0, 0)

        entry_count = one(
            f"""
            SELECT COUNT(*) FROM entry_user_records
            WHERE room_id IN ({placeholders})
              AND created_at >= %s AND created_at < %s
            """,
            room_params + (day_start, day_end),
        )
        entry_count = int((entry_count or (0,))[0])

        paying_sets: list[set] = []
        for table in ("gift_records", "guard_records", "super_chat_records"):
            rows = many(
                f"SELECT DISTINCT user_id FROM {table} WHERE {gift_where}",
                gift_params,
            )
            paying_sets.append({row[0] for row in rows})
        paying_users = len(set().union(*paying_sets)) if paying_sets else 0

        gift_rows = many(
            f"""
            SELECT gift_name, SUM(gift_num), SUM(total_price)
            FROM gift_records WHERE {gift_where}
            GROUP BY gift_name ORDER BY SUM(total_price) DESC LIMIT 8
            """,
            gift_params,
        )
        guard_rows = many(
            f"""
            SELECT guard_name, COUNT(*), COALESCE(SUM(total_price), 0)
            FROM guard_records WHERE room_id IN ({placeholders})
              AND timestamp >= %s AND timestamp < %s
            GROUP BY guard_name ORDER BY 3 DESC
            """,
            room_params + (day_start, day_end),
        )
        top_payers = many(
            f"""
            SELECT username, SUM(total_price), COUNT(*)
            FROM gift_records WHERE {gift_where}
            GROUP BY user_id, username ORDER BY SUM(total_price) DESC LIMIT 8
            """,
            gift_params,
        )
        top_chatters = many(
            f"""
            SELECT username, COUNT(*)
            FROM danmu_messages WHERE {gift_where}
            GROUP BY user_id, username ORDER BY COUNT(*) DESC LIMIT 8
            """,
            gift_params,
        )
        danmu_user_rows = many(
            f"""
            SELECT user_id, username, COUNT(*)
            FROM danmu_messages WHERE {gift_where}
            GROUP BY user_id, username
            """,
            gift_params,
        )
        gift_user_rows = many(
            f"""
            SELECT user_id, username, COALESCE(SUM(total_price), 0)
            FROM gift_records WHERE {gift_where}
            GROUP BY user_id, username
            """,
            gift_params,
        )
        danmu_message_rows = many(
            f"""
            SELECT user_id, username, content, timestamp
            FROM danmu_messages WHERE {gift_where}
            ORDER BY timestamp
            """,
            gift_params,
        )
        top_sc = many(
            f"""
            SELECT username, price, content, timestamp
            FROM super_chat_records WHERE {gift_where}
            ORDER BY price DESC LIMIT 5
            """,
            gift_params,
        )
        gift_hourly = many(
            f"""
            SELECT HOUR(timestamp), COALESCE(SUM(total_price), 0), COUNT(*)
            FROM gift_records WHERE {gift_where}
            GROUP BY HOUR(timestamp)
            """,
            gift_params,
        )
        danmu_hourly = many(
            f"""
            SELECT HOUR(timestamp), COUNT(*)
            FROM danmu_messages WHERE {gift_where}
            GROUP BY HOUR(timestamp)
            """,
            gift_params,
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    gift_amount = float(gift_amount)
    guard_amount = float(guard_amount)
    sc_amount = float(sc_amount)
    total = gift_amount + guard_amount + sc_amount
    arppu = total / paying_users if paying_users else 0.0

    gift_hour_map = {int(row[0]): (float(row[1]), int(row[2])) for row in gift_hourly}
    danmu_hour_map = {int(row[0]): int(row[1]) for row in danmu_hourly}
    peak_gift_hour = max(gift_hour_map, key=lambda h: gift_hour_map[h][0], default=None)
    peak_danmu_hour = max(danmu_hour_map, key=lambda h: danmu_hour_map[h], default=None)

    lines: list[str] = ["### 经营数据总览（数据库口径）", ""]
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 总流水（礼物 + 舰长 + SC） | **{_money(total)}** |")
    lines.append(f"| 礼物流水 | {_money(gift_amount)}（{int(gift_count)} 笔） |")
    lines.append(f"| 大航海流水 | {_money(guard_amount)}（{int(guard_count)} 个） |")
    lines.append(f"| 醒目留言 SC | {_money(sc_amount)}（{int(sc_count)} 条） |")
    lines.append(f"| 付费人数（去重） | {paying_users} 人 |")
    lines.append(f"| ARPPU（总流水 / 付费人数） | {_money(arppu)} |")
    lines.append(f"| 弹幕条数 | {int(danmu_count)} 条 |")
    lines.append(f"| 弹幕活跃人数 | {int(danmu_users)} 人 |")
    lines.append(f"| 进场人次 | {entry_count} 人次 |")
    if peak_gift_hour is not None:
        amount, count = gift_hour_map[peak_gift_hour]
        lines.append(f"| 流水峰值时段 | {peak_gift_hour:02d}:00（{_money(amount)} / {count} 笔） |")
    if peak_danmu_hour is not None:
        lines.append(
            f"| 弹幕峰值时段 | {peak_danmu_hour:02d}:00（{danmu_hour_map[peak_danmu_hour]} 条） |"
        )
    lines.append("")

    if gift_rows:
        lines.extend(["#### 礼物结构 Top", "", "| 礼物 | 数量 | 金额 |", "| --- | ---: | ---: |"])
        for name, num, amount in gift_rows:
            lines.append(f"| {name} | {int(num or 0)} | {_money(amount or 0)} |")
        lines.append("")

    if guard_rows:
        lines.extend(
            ["#### 大航海明细", "", "| 类型 | 数量 | 金额 |", "| --- | ---: | ---: |"]
        )
        for name, num, amount in guard_rows:
            lines.append(f"| {name} | {int(num or 0)} | {_money(amount or 0)} |")
        lines.append("")

    if top_payers:
        lines.extend(
            [
                "#### 礼物贡献用户 Top",
                "",
                "| 用户 | 金额 | 笔数 |",
                "| --- | ---: | ---: |",
            ]
        )
        for name, amount, count in top_payers:
            lines.append(f"| {name} | {_money(amount or 0)} | {int(count or 0)} |")
        lines.append("")

    if top_sc:
        lines.extend(
            [
                "#### 醒目留言 Top",
                "",
                "| 用户 | 金额 | 内容 |",
                "| --- | ---: | --- |",
            ]
        )
        for name, price, content, _ts in top_sc:
            safe = (content or "").replace("|", "｜").replace("\n", " ")
            lines.append(f"| {name} | {_money(price or 0)} | {safe[:120]} |")
        lines.append("")

    if top_chatters:
        lines.extend(
            [
                "#### 弹幕活跃用户 Top",
                "",
                "| 用户 | 弹幕条数 |",
                "| --- | ---: |",
            ]
        )
        for name, count in top_chatters:
            lines.append(f"| {name} | {int(count or 0)} |")
        lines.append("")

    guard_by_uid = {guard.uid: guard for guard in guard_users}
    guard_by_name = {guard.username: guard for guard in guard_users}
    chat_counts = {
        int(row[0]): (str(row[1]), int(row[2])) for row in danmu_user_rows
    }
    gift_amounts = {
        int(row[0]): (str(row[1]), float(row[2])) for row in gift_user_rows
    }
    user_messages: dict[int, list[tuple[datetime, str]]] = {}
    for uid, _name, content, ts in danmu_message_rows:
        user_messages.setdefault(int(uid), []).append((ts, str(content or "")))

    key_uids: set[int] = set()
    for uid, (name, _count) in chat_counts.items():
        if uid in guard_by_uid or name in guard_by_name:
            key_uids.add(uid)
    for uid, (name, amount) in gift_amounts.items():
        if amount > 0 and (uid in guard_by_uid or name in guard_by_name):
            key_uids.add(uid)
    for uid, _value in sorted(
        chat_counts.items(), key=lambda item: item[1][1], reverse=True
    )[:8]:
        key_uids.add(uid)
    for uid, (_name, amount) in sorted(
        gift_amounts.items(), key=lambda item: item[1][1], reverse=True
    )[:8]:
        if amount > 0:
            key_uids.add(uid)

    bili_levels = fetch_bili_levels(sorted(key_uids), progress)
    live_cards = (
        fetch_live_user_cards(room_ids[0], sorted(key_uids), progress)
        if room_ids
        else {}
    )

    key_rows: list[
        tuple[
            int,
            int,
            str,
            str,
            Optional[int],
            Optional[int],
            Optional[int],
            int,
            float,
        ]
    ] = []
    for uid in key_uids:
        name = chat_counts.get(uid, ("", 0))[0] or gift_amounts.get(uid, ("", 0))[0]
        guard = guard_by_uid.get(uid) or guard_by_name.get(name)
        if not name and guard is not None:
            name = guard.username
        if not name:
            continue
        guard_level = guard.guard_level if guard else 0
        guard_name = guard.guard_name if guard else ""
        live_card = live_cards.get(uid)
        if not guard_name and live_card is not None:
            for candidate in ("总督", "提督", "舰长"):
                if candidate in live_card.guard_text:
                    guard_name = candidate
                    break
        if not guard_name:
            guard_name = "—"
        medal_level = guard.medal_level if guard else None
        wealth_level = live_card.wealth_level if live_card is not None else None
        chat_count = chat_counts.get(uid, ("", 0))[1]
        gift_amount = gift_amounts.get(uid, ("", 0.0))[1]
        key_rows.append(
            (
                guard_level,
                uid,
                name,
                guard_name,
                wealth_level,
                bili_levels.get(uid),
                medal_level,
                chat_count,
                gift_amount,
            )
        )
    key_rows.sort(
        key=lambda row: (row[0] if row[0] else 99, -row[8], -row[7])
    )

    if guard_users:
        level_counts: dict[int, int] = {}
        for guard in guard_users:
            level_counts[guard.guard_level] = level_counts.get(guard.guard_level, 0) + 1
        summary = "；".join(
            f"{GUARD_NAMES.get(level, '大航海')} {level_counts[level]} 人"
            for level in (1, 2, 3)
            if level_counts.get(level)
        )
        lines.extend(
            [
                "#### 当前大航海",
                "",
                f"共 {len(guard_users)} 人：{summary}。",
                "",
            ]
        )
        for level in (1, 2, 3):
            names = [guard.username for guard in guard_users if guard.guard_level == level]
            if names:
                suffix = " 等" if len(names) > 20 else ""
                lines.append(f"- {GUARD_NAMES[level]}：{'、'.join(names[:20])}{suffix}")
        lines.append("")
    else:
        lines.extend(
            [
                "#### 当前大航海",
                "",
                "未能获取 B 站当前大航海名单（接口不可用或缺少登录凭证）。",
                "",
            ]
        )

    if key_rows:
        lines.extend(
            [
                "#### 核心用户画像（大航海 / 活跃 / 付费）",
                "",
                "> 「B站等级（财富）」是直播间显示的荣耀/财富等级（例如阿月月为 33）；"
                "「B站账号」是 0-6 的账号等级（例如 6），不是大会员；"
                "「直播间Lv.」是另一个直播间等级。大航海用户是核心资产，"
                "分析时以维护和回访为主，不要判断他们会流失。",
                "",
                "| 用户 | 大航海身份 | B站等级（财富） | B站账号 | 粉丝牌 | 今日弹幕 | 今日礼物 |",
                "| --- | --- | ---: | --- | ---: | ---: | ---: |",
            ]
        )
        for (
            _guard_level,
            _uid,
            name,
            guard_name,
            wealth_level,
            bili_level,
            medal_level,
            chat_count,
            gift_amount,
        ) in key_rows:
            wealth_text = f"{wealth_level}" if wealth_level is not None else "—"
            bili_text = f"Lv.{bili_level}" if bili_level is not None else "—"
            medal_text = f"Lv.{medal_level}" if medal_level is not None else "—"
            lines.append(
                f"| {name} | {guard_name} | {wealth_text} | {bili_text} | "
                f"{medal_text} | {chat_count} | {_money(gift_amount)} |"
            )
        lines.append("")

    persona_blocks: list[str] = []
    for (
        _guard_level,
        uid,
        name,
        guard_name,
        wealth_level,
        _bili_level,
        _medal_level,
        _chat_count,
        _gift_amount,
    ) in key_rows:
        messages = user_messages.get(uid) or []
        if not messages:
            continue
        identity_parts = []
        if guard_name and guard_name != "—":
            identity_parts.append(guard_name)
        if wealth_level is not None:
            identity_parts.append(f"财富{wealth_level}")
        identity_text = f"（{'，'.join(identity_parts)}）" if identity_parts else ""
        block = [f"**{name}{identity_text}**"]
        for ts, content in _pick_samples(messages, PERSONA_SAMPLE_PER_USER):
            time_text = ts.strftime("%H:%M:%S") if isinstance(ts, datetime) else str(ts)
            block.append(f"- {time_text} {content.replace(chr(10), ' ')[:180]}")
        persona_blocks.append("\n".join(block))

    if persona_blocks:
        lines.extend(
            [
                "#### 核心观众弹幕样本（用于沟通风格分析）",
                "",
                "> 以下只用于分析每位观众的沟通风格和互动偏好；不要做人身评价、"
                "心理诊断或负面标签。",
                "",
            ]
        )
        lines.append("\n\n".join(persona_blocks[:6]))
        lines.append("")

    hours = sorted(set(gift_hour_map) | set(danmu_hour_map))
    if hours:
        lines.extend(
            [
                "#### 分时表现",
                "",
                "| 时段 | 流水 | 礼物笔数 | 弹幕数 |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for hour in hours:
            amount, count = gift_hour_map.get(hour, (0.0, 0))
            lines.append(
                f"| {hour:02d}:00-{hour:02d}:59 | {_money(amount)} | {count} | "
                f"{danmu_hour_map.get(hour, 0)} |"
            )
        lines.append("")

    lines.extend(
        [
            "> 数据口径：礼物、舰长、SC 均按数据库 timestamp 统计；总流水不含平台分成与税；"
            "礼物记录可能包含连麦投票等非典型礼物。",
            "",
        ]
    )
    return "\n".join(lines)


def build_records_markdown(info: RecordInfo) -> str:
    if not info.paths:
        return "（未匹配到当天的录像文件）"
    lines = [
        f"- 录像片段：{len(info.paths)} 段",
        f"- 文件大小：{_human_bytes(info.total_bytes)}",
        f"- 录像总时长：{_human_duration(info.total_seconds)}",
    ]
    return "\n".join(lines)


def build_analyst_prompt(
    streamer: str,
    date_label: str,
    records_markdown: str,
    metrics_markdown: str,
    danmaku_text: str,
    transcript_text: str,
) -> str:
    danmaku_block = danmaku_text.strip() or "（本期没有提供弹幕材料）"
    return f"""你是一名资深的 B 站直播内容运营总监 + 数据驱动增长分析师。请基于以下材料，为主播「{streamer}」生成一份可以直接发给主播本人看的复盘报告。这份报告是内部复盘，目的是帮助她下一场做得更好；数据必须真实，但表达要委婉、建设性，先肯定再建议。

# 本期信息
- 主播：{streamer}
- 日期：{date_label}
- 录像档案：
{records_markdown}

# 今日经营数据（来自 MySQL）
{metrics_markdown}

# 弹幕与互动材料
{danmaku_block}

# 直播语音转写
{transcript_text}

# 分析要求
请严格按照下面的 Markdown 结构输出，标题不要省略：

# 【{streamer}】{date_label} 直播复盘与增长建议

> 本报告是内部复盘，目的是帮助主播下一场做得更好。数据真实，表达尽量委婉、对事不对人。

## 一、本场亮点（先说做得好的）
先写 2-4 条本场做得好的地方，每条尽量带数据或原话。即使整体数据不理想，也要找出真实存在的亮点，不要为了安慰而编造。

## 二、核心结论与提升空间
给出 3-5 条最重要的结论。每条包含：
1. **观察**：今天发生了什么；
2. **证据**：数据或原话；
3. **提升空间**：下一场可以怎么调整，预期带来什么变化。
最后用一句话温和地概括这场直播（例如：陪伴感很强，内容产品化和付费场景还有很大提升空间）。

## 三、数据看板与经营诊断
先用一个表格汇总关键指标（总流水、礼物/舰长/SC 结构、付费人数、ARPPU、弹幕人数、峰值时段），然后逐项解读：
- 目前的流水结构更偏哪一类？有哪些可以优化的空间？
- 收入峰值时段对应什么内容事件？当时观众为什么愿意付费？
- 弹幕热度与付费转化是否同步？哪些时段互动很好、哪些时段还有提升空间？
- 付费用户、活跃用户、路人各自的行为特征。
- 如果没有历史基线，明确写“本期缺少基线，以下为单场结构判断”，不要虚构同比环比。
- 数据不理想时，用“目前更偏…”“还有提升空间”“下一场可以尝试…”来表达，不要用否定性标签。

## 四、内容复盘
### 4.1 时间线还原
按时间顺序梳理 6-10 个关键节点，带时间戳，写清「发生了什么 + 观众反应 + 数据变化（如有）」。

### 4.2 名场面与梗
提取本场最有传播价值的梗、金句、名场面。每条注明：原话、出现时间、为什么有传播点、可以怎么二创。

### 4.3 话题结构
把内容拆成：主线内容 / 支线话题 / 闲聊陪伴 / 整活互动 / 情绪输出。估算大致占比，并评价哪些部分留人效果好、哪些部分还可以更紧凑。

### 4.4 关键人物
主播本人、核心粉丝、金主、房管、连麦对象等。写清他们的行为、发言和影响，不要只列名字；对观众只做运营分析，不做人身评价。

## 五、核心观众性格与沟通建议
根据「核心用户画像」和「核心观众弹幕样本」，挑选 3-6 位最重要的核心观众（提督/舰长优先，其次高活跃、高付费），每人写：
- **表达风格**：从用词、语气、互动方式分析（例如：直接表达、爱吐槽、理性分析、情绪型、捧哏型、陪伴型）。用“从弹幕看，更偏…”的推测语气。
- **互动动机**：她/他在直播间想获得什么（被看见、被认可、找人聊天、听歌、参与话题、被尊重等）。
- **沟通建议**：2-3 条具体做法，附 1-2 句可以直接念的话术。
- **避雷**：哪些话或做法可能让对方不舒服。
每位控制在 150-250 字。只基于弹幕中的公开表达做沟通风格分析，不做人身评价、心理诊断或负面标签；不要猜测现实身份。

## 六、情绪与节奏曲线
按时间段分析主播的情绪状态、直播间氛围、节奏变化。指出哪些节奏值得保留、哪些节奏下一场可以调整得更紧凑。

## 七、风险与合规
指出可能引发节奏、违规、舆情或粉丝矛盾的点，按风险等级排序，并给出可直接使用的话术或处理方案。措辞保持客观、建设性。

## 八、下一场行动清单（最重要）
按优先级给出 5-8 条具体动作，每条都必须可执行、可检查：
- 开场 10 分钟怎么留人；
- 内容选题 / 节目单 / 连麦安排；
- 互动话术与弹幕引导，给 2-3 句可以直接念的话术；
- 付费转化设计：礼物节点、舰长维护、SC 引导、榜单运营；
- 下一场建议盯的 2-3 个数据指标和目标值；
- 需要避免的坑。
每条都注明「为什么」和「预期效果」。

## 九、思维导图
输出一个 Mermaid mindmap 代码块，概括本场直播的结构与运营重点。节点文字不要包含括号、引号、冒号等特殊符号，必要时用空格代替。

语气与表达要求（必须遵守）：
- 先肯定做得好的地方，再谈提升空间；让主播看完有方向、有动力，而不是被否定。
- 禁止使用“假热闹”“不构成经营”“聊天室式”“失败”“毫无价值”等尖锐否定词；改用“目前更偏…”“还有提升空间”“下一场可以尝试…”等建设性表达。
- 数据必须真实，不能为了委婉而美化或编造；可以用“目前”“本期”“单场来看”等限定词。
- 对观众和粉丝只做运营层面的分析，不评价人品、不贴负面标签。
- 性格分析只用弹幕中的表达方式和互动风格，不猜测现实身份、不做心理诊断、不使用负面标签。
- 数据口径：用户等级有三种，不要混用——①「B站等级（财富）」是直播间显示的荣耀/财富等级（如阿月月 33）；②「B站账号」是 0-6 的账号等级（如 6），不是大会员；③「直播间Lv.」是另一个直播间等级。大会员是独立身份，不是等级。
- 大航海用户（总督/提督/舰长）是直播间的核心资产，不能判断他们会“流失”或“离开”；应重点分析如何维护、回访和加深关系。
- 用主播能听懂的话写，但保持专业，不要空话套话。
- 每个结论尽量有证据：数据或原话。
- 不要写“回应了近期争议”这类空泛表述，必须写清具体起因、主播态度、处理方式和结果。
- 引用原话用「」标注，并尽量带时间戳。
- 必须把数据与对话内容结合起来解释：数据变化背后的内容原因是什么。
- 如果某项数据缺失，直接写“本期无数据”，不要编造。
"""


def build_brief_prompt(
    full_report: str, streamer: str, date_label: str
) -> str:
    return f"""请把下面这份完整的直播复盘报告，压缩成一张给主播本人看的「一页速览」。

要求：
- 总字数控制在 500-800 字，只保留最重要的信息。
- 先写本场亮点，再写提升空间，语气委婉、建设性，让主播看完有方向、有动力。
- 禁止使用“假热闹”“不构成经营”“聊天室式”“失败”“毫无价值”等尖锐否定词。
- 提督/舰长/总督是核心用户，不要写他们会流失；B站等级（财富，如 33）、B站账号（0-6）、直播间Lv. 是三种不同等级，不要混用；Lv.6 是账号等级，不是大会员。
- 数据必须与完整报告一致，不要新增或编造数据。
- 不要输出 Mermaid，不要输出分析过程。
- 严格按以下 Markdown 结构输出：

# 【{streamer}】{date_label} 直播复盘速览

> 一句话总结本场直播（温和、客观，先肯定再指出方向）。

## 本场亮点
- 2-3 条，尽量带数据或原话。

## 核心结论
- 3 条，每条一句话，指出目前的状态和下一场的提升方向。

## 关键数据
| 指标 | 数值 |
| --- | --- |
| 总流水 | ... |
| 付费人数 | ... |
| 弹幕条数 / 活跃人数 | ... |
| 进场人次 | ... |
| 流水峰值时段 | ... |

## 核心观众沟通建议
- 挑选 2-3 位核心观众，每人一句话：从弹幕看她的沟通风格 + 一条可执行的互动建议或话术。
- 不做人身评价、心理诊断或负面标签。

## 下场就做这 3 件事
1. ...
2. ...
3. ...
每条写清「做什么」和「预期效果」。

## 需要留意
- 1-2 条风险或注意事项，措辞客观、建设性。

完整报告如下：
{full_report}
"""


def build_brief_report(
    full_report: str,
    streamer: str,
    date_label: str,
    options: SummarizeOptions,
) -> str:
    if not options.include_brief or options.dry_run:
        return ""
    _progress(options, "🧾 正在生成一页速览摘要...")
    prompt = build_brief_prompt(full_report, streamer, date_label)
    return call_chat(prompt, replace(options, stream_callback=None))


def _compress_chunks(chunks: list[str], options: SummarizeOptions) -> str:
    partials: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        if _is_cancelled(options):
            raise Cancelled("已取消")
        _progress(options, f"🧩 文本较长，正在压缩第 {index}/{len(chunks)} 段...")
        prompt = f"""你是一名直播转写整理员。下面是第 {index}/{len(chunks)} 段直播转写。
请把它压缩成事实型要点，供后续复盘使用：
- 保留时间戳、事件因果、关键原话、梗、人物、情绪变化；
- 删除口水话和重复内容；
- 不要输出建议，不要编造；
- 输出 500-1200 字的结构化要点。

转写内容：
{chunk}
"""
        partials.append(call_chat(prompt, replace(options, stream_callback=None)))
    _progress(options, "🧩 分段压缩完成，正在汇总...")
    return "\n\n---\n\n".join(
        f"【第 {i} 段压缩稿】\n{text}" for i, text in enumerate(partials, 1)
    )


def call_chat(prompt: str, options: SummarizeOptions) -> str:
    headers = {"Content-Type": "application/json"}
    if options.api_key:
        headers["Authorization"] = f"Bearer {options.api_key}"

    payload = {
        "model": options.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": options.max_tokens,
    }
    if "reasoner" not in options.model.lower():
        payload["temperature"] = options.temperature

    local_url = options.url.startswith("http://127.0.0.1") or options.url.startswith(
        "http://localhost"
    )
    proxies = {"http": None, "https": None} if local_url else None

    last_error = ""
    for attempt in range(1, options.retries + 1):
        if _is_cancelled(options):
            raise Cancelled("已取消")
        try:
            response = requests.post(
                options.url,
                json=payload,
                headers=headers,
                stream=True,
                timeout=(30, 600),
                proxies=proxies,
            )
        except requests.RequestException as exc:
            last_error = f"网络请求失败：{exc}"
            if attempt < options.retries:
                _progress(options, f"⚠️ {last_error}，正在重试 ({attempt}/{options.retries})...")
                import time as _time

                _time.sleep(2 * attempt)
                continue
            raise LLMError(last_error) from exc

        if response.status_code == 401:
            raise LLMError(
                "DeepSeek API Key 无效或已过期，请检查 tools/.dsSecretKey（或 DEEPSEEK_API_KEY）。"
            )
        if response.status_code == 402:
            raise LLMError("DeepSeek 账户余额不足，请充值后再试。")
        if response.status_code in (429, 500, 502, 503, 504):
            last_error = f"服务繁忙（HTTP {response.status_code}）"
            if attempt < options.retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 * attempt
                _progress(options, f"⚠️ {last_error}，{delay:.0f}s 后重试...")
                import time as _time

                _time.sleep(delay)
                continue
            raise LLMError(last_error)
        if response.status_code != 200:
            detail = response.text[:500]
            raise LLMError(f"服务返回错误 {response.status_code}：{detail}")

        parts: list[str] = []
        try:
            for raw_line in response.iter_lines():
                if _is_cancelled(options):
                    response.close()
                    raise Cancelled("已取消")
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"]))
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    parts.append(content)
                    if options.stream_callback:
                        options.stream_callback(content)
        except requests.RequestException as exc:
            raise LLMError(f"读取流式响应失败：{exc}") from exc
        finally:
            response.close()

        text = "".join(parts).strip()
        if not text:
            raise LLMError("模型没有返回内容，请稍后重试。")
        return text

    raise LLMError(last_error or "请求失败")


def _collect_danmaku_text(
    danmaku_entries: list[LogEntry], options: SummarizeOptions
) -> str:
    if not options.include_danmaku or not danmaku_entries:
        return ""
    parts: list[str] = []
    for entry in danmaku_entries:
        text = _read_text(entry.path)
        if len(danmaku_entries) > 1:
            parts.append(f"【{entry.path.name}】\n{text}")
        else:
            parts.append(text)
    combined = "\n\n".join(parts)
    return _sample_text(combined, DANMAKU_CHAR_LIMIT)


def _combine_transcripts(entries: list[LogEntry]) -> str:
    parts: list[str] = []
    for entry in sorted(entries, key=lambda item: item.ts):
        text = _clean_transcript(_read_text(entry.path))
        if len(entries) > 1:
            label = datetime.fromtimestamp(entry.ts).strftime("%Y-%m-%d %H:%M")
            parts.append(f"### 录播片段 {label}（{entry.path.name}）\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def _generate(
    transcripts: list[LogEntry],
    danmaku_entries: list[LogEntry],
    streamer: str,
    day: Optional[date],
    room_ids: list[str],
    options: SummarizeOptions,
) -> str:
    if not transcripts:
        raise NoLogError("没有找到可总结的语音转写日志。")

    _progress(options, f"📖 正在读取 {len(transcripts)} 个转写文件...")
    transcript_text = _combine_transcripts(transcripts)
    if not transcript_text.strip():
        raise NoLogError("转写日志内容为空。")

    original_chars = len(transcript_text)
    if original_chars > options.max_chars:
        _progress(
            options,
            f"📦 转写共 {original_chars:,} 字，超过单次上下文，将分段压缩后再汇总。",
        )
        chunks = _split_text(transcript_text, options.max_chars)
        transcript_text = _compress_chunks(chunks, options)
    else:
        _progress(options, f"📖 转写共 {original_chars:,} 字，长度正常。")

    danmaku_text = _collect_danmaku_text(danmaku_entries, options)
    if danmaku_text:
        _progress(options, f"💬 已加入 {len(danmaku_entries)} 个弹幕文件。")

    if options.include_db and day is not None:
        metrics_markdown = collect_metrics(room_ids, day, progress=options.progress)
    elif not options.include_db:
        metrics_markdown = "### 经营数据\n\n（本次已关闭数据库统计）\n"
    else:
        metrics_markdown = "### 经营数据\n\n本期无法确定日期，未查询数据库。\n"

    if room_ids and day:
        records_info = RecordInfo()
        for room_id in room_ids:
            info = find_records(room_id, day)
            records_info.paths.extend(info.paths)
            records_info.total_bytes += info.total_bytes
            records_info.total_seconds += info.total_seconds
        records_markdown = build_records_markdown(records_info)
    else:
        records_markdown = "（未匹配到录像文件）"

    date_label = day.isoformat() if day else "多日合集"
    prompt = build_analyst_prompt(
        streamer=streamer or "主播",
        date_label=date_label,
        records_markdown=records_markdown,
        metrics_markdown=metrics_markdown,
        danmaku_text=danmaku_text,
        transcript_text=transcript_text,
    )

    if options.dry_run:
        _progress(options, "🧪 dry-run：只生成 Prompt，不调用 DeepSeek。")
        return prompt

    _progress(options, f"🚀 正在请求 DeepSeek（{options.model}）...")
    report = call_chat(prompt, options)
    _progress(options, "✅ 报告生成完成。")
    return report


def summarize_manual(
    files: list[Path],
    streamer: str = "",
    day: Optional[date] = None,
    options: Optional[SummarizeOptions] = None,
) -> str:
    options = options or SummarizeOptions()
    entries: list[LogEntry] = []
    raw_files: list[Path] = []
    for path in files:
        path = Path(path)
        entry = parse_log_name(path)
        if entry is not None:
            entries.append(entry)
        else:
            raw_files.append(path)

    transcripts = [entry for entry in entries if entry.kind in {"log", "mlx_log"}]
    danmaku_entries = [entry for entry in entries if entry.kind == "danmaku"]

    inferred_streamers = [entry.streamer for entry in entries if entry.streamer]
    if not streamer and inferred_streamers:
        streamer = max(set(inferred_streamers), key=inferred_streamers.count)

    inferred_days = [entry.day for entry in entries]
    if day is None and inferred_days:
        day = max(set(inferred_days), key=inferred_days.count)

    for path in raw_files:
        text = _clean_transcript(_read_text(path))
        if not text.strip():
            continue
        ts = int(path.stat().st_mtime)
        entries.append(
            LogEntry(
                path=path,
                streamer=streamer or "主播",
                room_id="",
                kind="log",
                ts=ts,
            )
        )
        transcripts.append(entries[-1])
        if day is None:
            day = datetime.fromtimestamp(ts).date()

    if options.include_danmaku and day is not None:
        room_ids = sorted({entry.room_id for entry in entries if entry.room_id})
        known = {entry.path.resolve() for entry in danmaku_entries}
        for room_id in room_ids:
            for extra in find_danmaku_for_day(room_id, day):
                if extra.path.resolve() not in known:
                    danmaku_entries.append(extra)
                    known.add(extra.path.resolve())
        danmaku_entries.sort(key=lambda item: item.ts)

    room_ids = sorted({entry.room_id for entry in entries if entry.room_id})
    return _generate(transcripts, danmaku_entries, streamer, day, room_ids, options)


def summarize_room_day(
    config: RoomConfig,
    day: date,
    options: Optional[SummarizeOptions] = None,
) -> str:
    options = options or SummarizeOptions()
    transcripts, danmaku_entries = find_daily_sources(config, day)
    if not transcripts:
        raise NoLogError(
            f"{config.streamer_name or config.room_id} 在 {day.isoformat()} 没有转写日志。"
        )
    if not options.include_danmaku:
        danmaku_entries = []
    return _generate(
        transcripts=transcripts,
        danmaku_entries=danmaku_entries,
        streamer=config.streamer_name or config.room_id,
        day=day,
        room_ids=[config.room_id],
        options=options,
    )


def save_report(
    markdown_text: str,
    streamer: str,
    day_label: str,
    out_dir: Optional[Path] = None,
    md_path: Optional[Path] = None,
    render_png: bool = True,
    brief_markdown: str = "",
    full_layout: str = "newspaper",
    full_columns: int = 4,
    progress: Optional[Callable[[str], None]] = None,
) -> SavedReport:
    if md_path:
        md_path = Path(md_path)
        report_dir = md_path.parent
        base = md_path.stem
    else:
        report_dir = Path(out_dir) if out_dir else REPORT_DIR
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = sanitize_filename(f"{streamer}_{day_label}_{stamp}")
        md_path = report_dir / f"{base}.md"
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown_text, encoding="utf-8")
    saved = SavedReport(markdown=md_path)

    if brief_markdown.strip():
        saved.brief_markdown = report_dir / f"{base}_摘要.md"
        saved.brief_markdown.write_text(brief_markdown, encoding="utf-8")

    if not render_png:
        return saved

    try:
        from report_render import RenderError, render_markdown_to_png
    except ImportError:
        try:
            from .report_render import RenderError, render_markdown_to_png  # type: ignore
        except ImportError as exc:
            if progress:
                progress(f"⚠️ 跳过图片渲染：{exc}")
            return saved

    if progress:
        progress("🖼️ 正在渲染完整长图（含 Mermaid）...")
    try:
        saved.image = render_markdown_to_png(
            markdown_text,
            report_dir / f"{base}.png",
            title=f"{streamer} {day_label} 直播复盘",
            layout=full_layout,
            columns=full_columns,
        )
    except RenderError as exc:
        if progress:
            progress(f"⚠️ 完整长图渲染失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        if progress:
            progress(f"⚠️ 完整长图渲染异常：{type(exc).__name__}: {exc}")

    if saved.brief_markdown is not None:
        if progress:
            progress("🖼️ 正在渲染一页速览图...")
        try:
            saved.brief_image = render_markdown_to_png(
                brief_markdown,
                report_dir / f"{base}_摘要.png",
                title=f"{streamer} {day_label} 直播复盘速览",
            )
        except RenderError as exc:
            if progress:
                progress(f"⚠️ 摘要图渲染失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            if progress:
                progress(f"⚠️ 摘要图渲染异常：{type(exc).__name__}: {exc}")

    return saved


def parse_day(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"无法识别日期: {value}（示例：2026-11-12）")


def _load_configs(paths: list[str]) -> list[RoomConfig]:
    configs: list[RoomConfig] = []
    for raw in paths:
        configs.append(load_room_config(resolve_config_path(raw)))
    return configs


def run_cli(args: argparse.Namespace) -> int:
    api_key = load_api_key(args.api_key)
    if args.backend == "deepseek" and not api_key and not args.dry_run:
        print(
            "❌ 找不到 DeepSeek API Key。请把 Key 写入 tools/.dsSecretKey，"
            "或设置 DEEPSEEK_API_KEY。",
            file=sys.stderr,
        )
        return 1

    url = args.url or (DEEPSEEK_URL if args.backend == "deepseek" else DEFAULT_OPENAI_URL)
    model = args.model or (
        DEFAULT_DEEPSEEK_MODEL if args.backend == "deepseek" else "local-model"
    )
    options = SummarizeOptions(
        api_key=api_key,
        model=model,
        url=url,
        max_chars=args.max_chars,
        include_danmaku=not args.no_danmaku,
        include_db=not args.no_db,
        dry_run=args.dry_run,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )

    configs = _load_configs(args.config) if args.config else []
    tasks: list[tuple[str, str, Callable[[], str]]] = []

    if args.files:
        files = [Path(item) for item in args.files]
        inferred = ""
        for path in files:
            entry = parse_log_name(path)
            if entry and entry.streamer:
                inferred = entry.streamer
                break
        streamer = args.streamer or inferred or "主播"
        day = args.date
        if day is None:
            days = [
                entry.day
                for entry in (parse_log_name(path) for path in files)
                if entry is not None
            ]
            day = max(set(days), key=days.count) if days else None
        day_label = day.isoformat() if day else "多日合集"
        tasks.append(
            (
                streamer,
                day_label,
                lambda files=files, streamer=streamer, day=day: summarize_manual(
                    files, streamer=streamer, day=day, options=options
                ),
            )
        )
    elif configs:
        for config in configs:
            day = args.date or date.today()
            tasks.append(
                (
                    config.streamer_name or config.room_id,
                    day.isoformat(),
                    lambda config=config, day=day: summarize_room_day(
                        config, day, options=options
                    ),
                )
            )
    else:
        print("请提供要总结的日志文件，或使用 --config 指定直播间配置。", file=sys.stderr)
        return 2

    failures = 0
    for streamer, day_label, builder in tasks:
        print(f"\n===== {streamer} · {day_label} =====", file=sys.stderr)
        try:
            report = builder()
        except NoLogError as exc:
            print(f"⚠️ {exc}", file=sys.stderr)
            failures += 1
            continue
        except Cancelled:
            print("已取消。", file=sys.stderr)
            return 130
        except SummarizeError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            failures += 1
            continue

        if args.dry_run:
            stamp = datetime.now().strftime("%H%M%S")
            out = args.out or (
                REPORT_DIR
                / f"prompt_{sanitize_filename(streamer)}_{day_label}_{stamp}.md"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report, encoding="utf-8")
            print(f"🧪 Prompt 已保存: {out}", file=sys.stderr)
            continue

        if args.print_report:
            print(report)

        brief_markdown = ""
        if not args.no_png and not args.no_brief:
            try:
                brief_markdown = build_brief_report(
                    report, streamer, day_label, options
                )
            except SummarizeError as exc:
                print(f"⚠️ 一页速览生成失败：{exc}", file=sys.stderr)

        saved = save_report(
            report,
            streamer=streamer,
            day_label=day_label,
            md_path=Path(args.out) if args.out else None,
            render_png=not args.no_png,
            brief_markdown=brief_markdown,
            full_layout=args.full_layout,
            full_columns=args.columns,
            progress=options.progress,
        )
        print(f"📄 Markdown: {saved.markdown}", file=sys.stderr)
        if saved.image:
            print(f"🖼️ 完整长图: {saved.image}", file=sys.stderr)
        if saved.brief_image:
            print(f"🖼️ 一页速览图: {saved.brief_image}", file=sys.stderr)

    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用 DeepSeek 生成直播复盘报告，并渲染成图片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例：
  python tools/summarize.py                              # 打开图形界面
  python tools/summarize.py logs/a.txt logs/b.txt         # 多文件合并总结
  python tools/summarize.py --config configs/xiyin.json --date 2026-11-11
  python tools/summarize.py logs/a.txt --no-png --no-db  # 只生成 Markdown
""",
    )
    parser.add_argument("files", nargs="*", type=Path, help="要总结的转写/弹幕日志")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="直播间配置（可重复），例如 configs/xiyin.json",
    )
    parser.add_argument("--date", type=parse_day, default=None, help="按天总结的日期，默认今天")
    parser.add_argument("--streamer", default="", help="主播名（手动选文件时可选）")
    parser.add_argument(
        "--backend",
        choices=["deepseek", "openai"],
        default="deepseek",
        help="模型后端，默认 deepseek",
    )
    parser.add_argument("--model", default=None, help=f"默认 {DEFAULT_DEEPSEEK_MODEL}")
    parser.add_argument("--api-key", default=None, help="覆盖 tools/.dsSecretKey")
    parser.add_argument("--url", default=None, help="自定义 OpenAI 兼容接口地址")
    parser.add_argument("--max-chars", type=int, default=MAX_INPUT_CHARS, help="单次上下文上限")
    parser.add_argument("--out", type=Path, default=None, help="Markdown 输出路径")
    parser.add_argument("--no-png", action="store_true", help="不渲染图片")
    parser.add_argument("--no-brief", action="store_true", help="不生成一页速览摘要图")
    parser.add_argument(
        "--full-layout",
        choices=["newspaper", "single"],
        default="newspaper",
        help="完整长图版式：newspaper=四栏横向（默认），single=单栏长图",
    )
    parser.add_argument("--columns", type=int, default=4, help="四栏版式的栏数")
    parser.add_argument("--no-db", action="store_true", help="不查询数据库流水")
    parser.add_argument("--no-danmaku", action="store_true", help="不加入弹幕")
    parser.add_argument("--dry-run", action="store_true", help="只生成 Prompt，不调用模型")
    parser.add_argument("--print", dest="print_report", action="store_true", help="同时打印 Markdown")
    parser.add_argument("--gui", action="store_true", help="打开图形界面")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gui or (not args.files and not args.config):
        try:
            try:
                from summarize_gui import run_gui
            except ImportError:
                from .summarize_gui import run_gui  # type: ignore
        except ImportError as exc:
            print(f"无法加载图形界面: {exc}", file=sys.stderr)
            return 1
        return run_gui(core=sys.modules[__name__])
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
