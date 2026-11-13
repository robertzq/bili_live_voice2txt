"""查询直播间用户的财富等级与大航海身份。

弹幕原始消息里没有稳定的财富等级字段，这里用 B 站直播用户卡片接口补齐，
供弹幕 UI / 日志展示使用。查询结果按 UID 缓存，接口失败时静默降级。
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .bili_auth import CREDENTIAL_FILE

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
GUARD_NAMES = {1: "总督", 2: "提督", 3: "舰长"}


@dataclass
class LiveUserProfile:
    uid: int
    username: str = ""
    wealth_level: Optional[int] = None
    guard_text: str = ""

    @property
    def guard_level(self) -> int:
        for level, name in GUARD_NAMES.items():
            if name in self.guard_text:
                return level
        return 0


class LiveProfileClient:
    """线程安全的用户资料查询器，只使用标准库，避免额外依赖。"""

    def __init__(self, room_id: int | str, timeout: float = 3.0) -> None:
        self.room_id = str(room_id)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._anchor_uid = 0
        self._cache: dict[int, Optional[LiveUserProfile]] = {}
        self._cookies = self._load_cookies()

    @staticmethod
    def _load_cookies() -> str:
        if not CREDENTIAL_FILE.is_file():
            return ""
        try:
            data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return "; ".join(
            f"{key}={data[key]}"
            for key in ("SESSDATA", "bili_jct", "DedeUserID")
            if data.get(key)
        )

    def _get_json(self, url: str, params: dict) -> dict:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": f"https://live.bilibili.com/{self.room_id}",
            "Accept-Encoding": "identity",
        }
        if self._cookies:
            headers["Cookie"] = self._cookies
        request = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))

    def _get_anchor_uid(self) -> int:
        if self._anchor_uid:
            return self._anchor_uid
        try:
            payload = self._get_json(
                "https://api.live.bilibili.com/room/v1/Room/get_info",
                {"room_id": self.room_id},
            )
            if payload.get("code") == 0:
                uid = (payload.get("data") or {}).get("uid")
                self._anchor_uid = int(uid or 0)
        except Exception:  # noqa: BLE001 - 资料查询失败不能影响弹幕
            pass
        return self._anchor_uid

    def get(self, uid: int) -> Optional[LiveUserProfile]:
        if uid <= 0:
            return None
        with self._lock:
            if uid in self._cache:
                return self._cache[uid]

        anchor_uid = self._get_anchor_uid()
        if not anchor_uid:
            return None

        profile: Optional[LiveUserProfile] = None
        try:
            payload = self._get_json(
                "https://api.live.bilibili.com/xlive/app-ucenter/v2/card/user",
                {"uid": uid, "ruid": anchor_uid},
            )
            if payload.get("code") == 0:
                data = payload.get("data") or {}
                wealth = (data.get("wealth_info") or {}).get("level")
                profile = LiveUserProfile(
                    uid=uid,
                    username=str(data.get("uname") or ""),
                    wealth_level=int(wealth) if wealth is not None else None,
                    guard_text=str(data.get("head_text") or ""),
                )
        except Exception:  # noqa: BLE001 - 接口失败时只显示已有信息
            profile = None

        with self._lock:
            self._cache[uid] = profile
        return profile
