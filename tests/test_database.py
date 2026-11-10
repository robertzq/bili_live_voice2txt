import os
import unittest
from unittest import mock

from bililive.database import DatabaseConfig, DanmakuStore
from bililive.danmaku import DanmakuEvent
import datetime as dt


def make_event(kind):
    return DanmakuEvent(
        kind=kind,
        room_id=1,
        timestamp=dt.datetime(2024, 1, 1, 12, 0, 0),
        uid=42,
        username="alice",
        content="hello",
        gift_name="礼物",
        gift_num=2,
        price=1.5,
        total_price=3.0,
        guard_type=3,
        start_time=dt.datetime(2024, 1, 1, 12, 0, 0),
        end_time=dt.datetime(2024, 2, 1, 12, 0, 0),
    )


class DatabaseTests(unittest.TestCase):
    def test_config_from_env(self):
        env = {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "3307",
            "DB_USER": "u",
            "DB_PASSWORD": "p",
            "DB_DATABASE": "d",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = DatabaseConfig.from_env()
        self.assertTrue(config.enabled)
        self.assertEqual(config.port, 3307)
        self.assertNotIn("p", config.describe())

    def test_config_disabled_without_database(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = DatabaseConfig.from_env()
        self.assertFalse(config.enabled)

    def test_save_dispatches_to_correct_table(self):
        store = DanmakuStore(DatabaseConfig(host="h", database="d"))
        calls = []
        store._execute = lambda sql, params=None: calls.append((sql, params))

        expected = {
            "danmaku": "danmu_messages",
            "gift": "gift_records",
            "guard": "guard_records",
            "super_chat": "super_chat_records",
            "entry": "entry_user_records",
        }
        for kind, table in expected.items():
            store.save(make_event(kind))
            self.assertIn(table, calls[-1][0])

    def test_disabled_store_does_nothing(self):
        store = DanmakuStore(DatabaseConfig())
        calls = []
        store._execute = lambda sql, params=None: calls.append((sql, params))
        store.save(make_event("danmaku"))
        self.assertEqual(calls, [])

    def test_schema_file_is_loaded(self):
        statements = DanmakuStore._load_schema()
        self.assertGreaterEqual(len(statements), 5)
        self.assertTrue(any("danmu_messages" in sql for sql in statements))
        self.assertTrue(any("super_chat_records" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
