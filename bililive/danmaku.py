"""B 站直播弹幕/礼物/上舰/进场/SC 监听。"""

from __future__ import annotations

import asyncio
import datetime as dt
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .bili_auth import configure_bilibili_client, load_credential
from .database import DanmakuStore

_STOP = object()

EVENT_COMMANDS = (
    "DANMU_MSG",
    "SEND_GIFT",
    "GUARD_BUY",
    "SUPER_CHAT_MESSAGE",
    "INTERACT_WORD",
    "INTERACT_WORD_V2",
)

GUARD_NAMES = {1: "总督", 2: "提督", 3: "舰长"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_datetime(value: Any, default: Optional[dt.datetime] = None) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return default or dt.datetime.now()
    if timestamp > 1e12:
        timestamp /= 1000.0
    if timestamp <= 0:
        return default or dt.datetime.now()
    try:
        return dt.datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return default or dt.datetime.now()


def _first_int(*values: Any) -> int:
    for value in values:
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _first_optional_int(*values: Any) -> Optional[int]:
    for value in values:
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[0]
        result = _to_optional_int(value)
        if result is not None:
            return result
    return None


def _medal_fields(medal: Any) -> tuple[Optional[str], Optional[int]]:
    if isinstance(medal, dict):
        name = medal.get("medal_name") or medal.get("name")
        level = medal.get("medal_level") or medal.get("level")
        return (str(name) if name else None, _to_optional_int(level))
    if isinstance(medal, (list, tuple)) and len(medal) >= 2:
        return (str(medal[1]) if medal[1] else None, _to_optional_int(medal[0]))
    return (None, None)


@dataclass
class DanmakuEvent:
    kind: str
    room_id: int
    timestamp: dt.datetime
    uid: int = 0
    username: str = ""
    content: str = ""
    medal_name: Optional[str] = None
    medal_level: Optional[int] = None
    user_level: Optional[int] = None
    gift_name: str = ""
    gift_num: int = 0
    price: float = 0.0
    total_price: float = 0.0
    guard_type: int = 0
    receiver_id: Optional[int] = None
    receiver_name: Optional[str] = None
    start_time: Optional[dt.datetime] = None
    end_time: Optional[dt.datetime] = None
    extra: dict = field(default_factory=dict)

    @property
    def medal_text(self) -> str:
        if self.medal_name and self.medal_level is not None:
            return f"[{self.medal_name} Lv.{self.medal_level}] "
        return ""

    @property
    def user_text(self) -> str:
        if self.user_level is not None:
            return f"UL.{self.user_level} {self.username}"
        return self.username

    def display_text(self) -> str:
        moment = self.timestamp.strftime("%H:%M:%S")
        if self.kind == "danmaku":
            return f"[{moment}] [弹幕] {self.medal_text}{self.user_text}: {self.content}"
        if self.kind == "gift":
            return (
                f"[{moment}] [礼物] {self.medal_text}{self.username} 赠送 "
                f"{self.gift_name} x{self.gift_num} (¥{self.total_price:.2f})"
            )
        if self.kind == "guard":
            return (
                f"[{moment}] [上舰] {self.medal_text}{self.username} 购买 "
                f"{self.gift_name} x{self.gift_num} (¥{self.total_price:.2f})"
            )
        if self.kind == "super_chat":
            return (
                f"[{moment}] [SC ¥{self.price:.2f}] {self.medal_text}"
                f"{self.username}: {self.content}"
            )
        if self.kind == "entry":
            return f"[{moment}] [进场] {self.medal_text}{self.username} {self.content}"
        return f"[{moment}] [{self.kind}] {self.username} {self.content}"


def parse_event(cmd: str, payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    if cmd.startswith("DANMU_MSG"):
        return _parse_danmaku(payload, room_id)
    if cmd == "SEND_GIFT":
        return _parse_gift(payload, room_id)
    if cmd == "GUARD_BUY":
        return _parse_guard(payload, room_id)
    if cmd == "SUPER_CHAT_MESSAGE":
        return _parse_super_chat(payload, room_id)
    if cmd in {"INTERACT_WORD", "INTERACT_WORD_V2"}:
        return _parse_entry(payload, room_id)
    return None


def _parse_danmaku(payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    info = payload.get("info")
    if not isinstance(info, list) or len(info) < 3:
        return None

    user = info[2] if isinstance(info[2], (list, tuple)) else []
    uid = _first_int(user[0]) if len(user) > 0 else 0
    username = str(user[1]) if len(user) > 1 else ""
    medal = info[3] if len(info) > 3 else None
    medal_name, medal_level = _medal_fields(medal)
    user_level = _first_optional_int(
        info[4] if len(info) > 4 else None,
        info[-2] if len(info) > 1 else None,
    )
    timestamp = dt.datetime.now()
    if isinstance(info[0], (list, tuple)) and len(info[0]) > 4:
        timestamp = _to_datetime(info[0][4])

    return DanmakuEvent(
        kind="danmaku",
        room_id=room_id,
        timestamp=timestamp,
        uid=uid,
        username=username,
        content=str(info[1]),
        medal_name=medal_name,
        medal_level=medal_level,
        user_level=user_level,
    )


def _parse_gift(payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None

    coin_type = data.get("coin_type") or "gold"
    price = _to_float(data.get("price")) / 1000.0
    num = _to_int(data.get("num"), 1)
    total_coin = data.get("total_coin")
    if total_coin is None:
        total_price = price * num
    else:
        total_price = _to_float(total_coin) / 1000.0
    if coin_type == "silver":
        price = 0.0
        total_price = 0.0

    medal_name, medal_level = _medal_fields(data.get("medal_info"))
    receiver = data.get("receiver_uinfo") or data.get("receiver") or {}

    return DanmakuEvent(
        kind="gift",
        room_id=room_id,
        timestamp=_to_datetime(data.get("timestamp")),
        uid=_to_int(data.get("uid")),
        username=str(data.get("uname") or ""),
        content=str(data.get("giftName") or data.get("gift_name") or ""),
        medal_name=medal_name,
        medal_level=medal_level,
        gift_name=str(data.get("giftName") or data.get("gift_name") or ""),
        gift_num=num,
        price=price,
        total_price=total_price,
        receiver_id=_to_optional_int(receiver.get("uid")),
        receiver_name=receiver.get("uname"),
    )


def _parse_guard(payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None

    guard_type = _to_int(data.get("guard_level") or data.get("guardType"))
    gift_name = (
        data.get("gift_name")
        or data.get("giftName")
        or GUARD_NAMES.get(guard_type, "大航海")
    )
    num = _to_int(data.get("num"), 1)
    price = _to_float(data.get("price")) / 1000.0
    username = data.get("username") or data.get("uname") or ""
    medal_name, medal_level = _medal_fields(data.get("medal_info"))

    return DanmakuEvent(
        kind="guard",
        room_id=room_id,
        timestamp=_to_datetime(data.get("timestamp")),
        uid=_to_int(data.get("uid")),
        username=str(username),
        content=str(gift_name),
        medal_name=medal_name,
        medal_level=medal_level,
        gift_name=str(gift_name),
        gift_num=num,
        price=price,
        total_price=price * num,
        guard_type=guard_type,
        start_time=_to_datetime(data.get("start_time") or data.get("startTime")),
        end_time=_to_datetime(data.get("end_time") or data.get("endTime")),
    )


def _parse_super_chat(payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None

    user_info = data.get("user_info") or {}
    gift = data.get("gift") or {}
    medal_name, medal_level = _medal_fields(data.get("medal_info"))
    price = _to_float(data.get("price"))
    start_time = _to_datetime(data.get("start_time"))
    end_time = _to_datetime(data.get("end_time"), start_time)

    return DanmakuEvent(
        kind="super_chat",
        room_id=room_id,
        timestamp=start_time,
        uid=_to_int(data.get("uid") or user_info.get("uid")),
        username=str(user_info.get("uname") or data.get("uname") or ""),
        content=str(data.get("message") or data.get("content") or ""),
        medal_name=medal_name,
        medal_level=medal_level,
        gift_name=str(gift.get("gift_name") or gift.get("giftName") or "醒目留言"),
        price=price,
        total_price=price,
        start_time=start_time,
        end_time=end_time,
    )


def _parse_entry(payload: dict, room_id: int) -> Optional[DanmakuEvent]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None

    decoded = data.get("pb_decoded") or {}
    source = decoded if decoded else data
    msg_type = _to_int(source.get("msg_type"))
    if msg_type != 1:
        return None

    medal = source.get("fans_medal_info") or source.get("fans_medal") or {}
    medal_name, medal_level = _medal_fields(medal)
    return DanmakuEvent(
        kind="entry",
        room_id=room_id,
        timestamp=_to_datetime(source.get("timestamp")),
        uid=_to_int(source.get("uid")),
        username=str(source.get("uname") or ""),
        content="进入直播间",
        medal_name=medal_name,
        medal_level=medal_level,
    )


class DanmakuService:
    """在独立线程里运行 asyncio 弹幕客户端。

    网络线程只负责收包并放入队列；写文件/数据库由 writer 线程处理。
    """

    def __init__(
        self,
        room_id: str | int,
        on_event: Callable[[DanmakuEvent], None],
        on_message: Optional[Callable[[str], None]] = None,
        retry_delay: float = 3.0,
        idle_timeout: float = 180.0,
    ) -> None:
        self.room_id = int(room_id)
        self.on_event = on_event
        self.on_message = on_message or (lambda _msg: None)
        self.retry_delay = retry_delay
        self.idle_timeout = idle_timeout

        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._last_activity = time.monotonic()
        self._writer_thread: Optional[threading.Thread] = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="danmaku-writer",
            daemon=True,
        )
        self._writer_thread.start()
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.on_message(f"弹幕监听异常: {exc}")
        finally:
            self._queue.put(_STOP)
            if self._writer_thread is not None:
                self._writer_thread.join(timeout=3)

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self.on_event(item)
            except Exception as exc:
                self.on_message(f"弹幕处理失败: {exc}")

    async def _main(self) -> None:
        try:
            from bilibili_api.live import LiveDanmaku
        except ImportError:
            self.on_message(
                "未安装 bilibili-api-python，弹幕监听已禁用 "
                "(pip install bilibili-api-python)"
            )
            return

        configure_bilibili_client()
        credential = load_credential()
        if credential is None:
            self.on_message(
                "未找到 B 站登录凭证，弹幕监听已禁用 "
                "(运行 python run.py --login 扫码登录)"
            )
            return

        try:
            valid = await credential.check_valid()
        except Exception as exc:
            self.on_message(f"无法验证登录状态，继续尝试连接: {exc}")
        else:
            if not valid:
                self.on_message(
                    "B 站登录凭证已失效，请运行 python run.py --login 重新扫码"
                )
                return

        while not self._stop.is_set():
            self.on_message(f"弹幕监听：正在连接直播间 {self.room_id} ...")
            room = LiveDanmaku(self.room_id, credential=credential, max_retry=3)
            self._register_handlers(room)
            self._last_activity = time.monotonic()
            connect_task = asyncio.create_task(room.connect())

            while not self._stop.is_set() and not connect_task.done():
                await asyncio.sleep(0.5)
                if time.monotonic() - self._last_activity > self.idle_timeout:
                    self.on_message("弹幕链路静默超时，准备重连...")
                    break

            if not connect_task.done():
                if room.get_status() == LiveDanmaku.STATUS_ESTABLISHED:
                    try:
                        await room.disconnect()
                    except Exception:
                        pass
                connect_task.cancel()
                await asyncio.gather(connect_task, return_exceptions=True)
            else:
                exception = connect_task.exception()
                if exception is not None and not self._stop.is_set():
                    self.on_message(f"弹幕连接断开: {exception}")

            if self._stop.is_set():
                break
            self.on_message(f"弹幕将在 {self.retry_delay:.0f} 秒后重连...")
            for _ in range(int(self.retry_delay * 2)):
                if self._stop.is_set():
                    break
                await asyncio.sleep(0.5)

    def _register_handlers(self, room) -> None:
        for command in EVENT_COMMANDS:
            room.on(command)(self._make_handler(command))

        @room.on("VERIFICATION_SUCCESSFUL")
        async def _on_connected(_event) -> None:
            self._last_activity = time.monotonic()
            self.on_message("弹幕已连接")

        @room.on("VIEW")
        async def _on_view(_event) -> None:
            # 心跳包约 30 秒一次，用它判断链路仍然存活。
            self._last_activity = time.monotonic()

    def _make_handler(self, command: str):
        async def handler(event: dict) -> None:
            self._last_activity = time.monotonic()
            payload = event.get("data") or {}
            parsed = parse_event(command, payload, self.room_id)
            if parsed is not None:
                self._queue.put(parsed)

        return handler


class DanmakuRecorder:
    """把弹幕事件写入日志、数据库，并回调给 GUI。"""

    def __init__(
        self,
        log_path: str | Path,
        store: Optional[DanmakuStore] = None,
        on_display: Optional[Callable[[DanmakuEvent], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.store = store
        self.on_display = on_display or (lambda _event: None)
        self.on_message = on_message or (lambda _msg: None)
        self._db_error_reported = False
        self._file = None
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.log_path.open("a", encoding="utf-8")
        except OSError as exc:
            self.on_message(f"弹幕日志打开失败: {exc}")

    def handle(self, event: DanmakuEvent) -> None:
        line = event.display_text()
        if self._file is not None:
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except OSError as exc:
                self.on_message(f"弹幕日志写入失败: {exc}")

        if self.store is not None and self.store.enabled:
            try:
                self.store.save(event)
            except Exception as exc:
                if not self._db_error_reported:
                    self._db_error_reported = True
                    self.on_message(f"弹幕入库失败（后续不再提示）: {exc}")

        self.on_display(event)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        if self.store is not None:
            self.store.close()
