"""MySQL 存储：把弹幕、礼物、上舰、进场、SC 写入数据库。"""

from __future__ import annotations

import os
import threading
from typing import Optional

from .config import PROJECT_ROOT


class DatabaseConfig:
    def __init__(
        self,
        host: str = "",
        port: int = 3306,
        user: str = "",
        password: str = "",
        database: str = "",
        auto_create: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.auto_create = auto_create

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        try:
            port = int(os.getenv("DB_PORT", "3306") or 3306)
        except ValueError:
            port = 3306
        return cls(
            host=os.getenv("DB_HOST", "").strip(),
            port=port,
            user=os.getenv("DB_USER", "").strip(),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_DATABASE", "").strip(),
            auto_create=os.getenv("DB_AUTO_CREATE", "1").strip() not in {"0", "false", "False"},
        )

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.database)

    def describe(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


class DanmakuStore:
    """线程安全的 MySQL 写入器。

    数据库未配置时自动禁用；配置后首次写入时建立连接。
    """

    def __init__(self, config: Optional[DatabaseConfig] = None) -> None:
        self.config = config or DatabaseConfig.from_env()
        self._lock = threading.Lock()
        self._conn = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def ensure_schema(self) -> None:
        if not self.enabled:
            return
        try:
            self._execute("SELECT 1")
        except Exception:
            if not self.config.auto_create:
                raise
            self._create_database()
        for statement in self._load_schema():
            self._execute(statement)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def save(self, event) -> None:
        if not self.enabled:
            return
        kind = getattr(event, "kind", "")
        if kind == "danmaku":
            self.save_danmaku(event)
        elif kind == "gift":
            self.save_gift(event)
        elif kind == "guard":
            self.save_guard(event)
        elif kind == "super_chat":
            self.save_super_chat(event)
        elif kind == "entry":
            self.save_entry(event)

    def save_danmaku(self, event) -> None:
        self._execute(
            """
            INSERT INTO danmu_messages
            (room_id, user_id, username, content, medal_level, medal_name, user_level, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.room_id,
                event.uid,
                event.username,
                event.content,
                event.medal_level,
                event.medal_name,
                event.user_level,
                event.timestamp,
            ),
        )

    def save_gift(self, event) -> None:
        self._execute(
            """
            INSERT INTO gift_records
            (room_id, user_id, username, receiver_id, receiver_name,
             gift_name, gift_num, gift_price, total_price, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.room_id,
                event.uid,
                event.username,
                event.receiver_id,
                event.receiver_name,
                event.gift_name,
                event.gift_num,
                event.price,
                event.total_price,
                event.timestamp,
            ),
        )

    def save_guard(self, event) -> None:
        self._execute(
            """
            INSERT INTO guard_records
            (room_id, user_id, username, guard_type, guard_name, guard_num,
             price, total_price, start_time, end_time, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.room_id,
                event.uid,
                event.username,
                event.guard_type,
                event.gift_name,
                event.gift_num,
                event.price,
                event.total_price,
                event.start_time or event.timestamp,
                event.end_time or event.timestamp,
                event.timestamp,
            ),
        )

    def save_super_chat(self, event) -> None:
        self._execute(
            """
            INSERT INTO super_chat_records
            (room_id, user_id, username, content, price, gift_name,
             start_time, end_time, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.room_id,
                event.uid,
                event.username,
                event.content,
                event.price,
                event.gift_name,
                event.start_time or event.timestamp,
                event.end_time or event.timestamp,
                event.timestamp,
            ),
        )

    def save_entry(self, event) -> None:
        self._execute(
            """
            INSERT INTO entry_user_records
            (room_id, user_id, username, entry_content)
            VALUES (%s, %s, %s, %s)
            """,
            (
                event.room_id,
                event.uid,
                event.username,
                event.content or "进入直播间",
            ),
        )

    def _execute(self, sql: str, params: Optional[tuple] = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            for attempt in range(2):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    with self._conn.cursor() as cursor:
                        cursor.execute(sql, params)
                    return
                except Exception:
                    self._close_locked()
                    if attempt == 1:
                        raise

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "未安装 PyMySQL，请运行: pip install PyMySQL"
            ) from exc
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
        )

    def _create_database(self) -> None:
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "未安装 PyMySQL，请运行: pip install PyMySQL"
            ) from exc

        database = self.config.database.replace("`", "``")
        conn = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            conn.close()

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @staticmethod
    def _load_schema() -> list[str]:
        schema_path = PROJECT_ROOT / "sql" / "schema.sql"
        if not schema_path.is_file():
            raise FileNotFoundError(f"找不到建表脚本: {schema_path}")

        lines = []
        for line in schema_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            lines.append(line)
        return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]
