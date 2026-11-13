import tempfile
import unittest
from pathlib import Path

from bililive.bili_auth import parse_cookie_string
from bililive.danmaku import DanmakuEvent, DanmakuRecorder
from bililive.live_profile import LiveUserProfile
import datetime as dt


class CookieTests(unittest.TestCase):
    def test_parse_cookie_header(self):
        cookies = parse_cookie_string(
            "SESSDATA=abc%2C123; bili_jct=def; DedeUserID=42; buvid3=xyz"
        )
        self.assertEqual(cookies["SESSDATA"], "abc%2C123")
        self.assertEqual(cookies["bili_jct"], "def")
        self.assertEqual(cookies["DedeUserID"], "42")

    def test_parse_empty(self):
        self.assertEqual(parse_cookie_string("  "), {})

    def test_parse_json(self):
        cookies = parse_cookie_string('{"SESSDATA": "abc", "bili_jct": "def"}')
        self.assertEqual(cookies["SESSDATA"], "abc")


class FakeStore:
    enabled = True

    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def save(self, event):
        if self.fail:
            raise RuntimeError("db down")
        self.events.append(event)

    def close(self):
        pass


class RecorderTests(unittest.TestCase):
    def test_recorder_writes_log_and_store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        log_path = Path(temp_dir.name) / "danmaku.txt"

        store = FakeStore()
        displayed = []
        messages = []
        recorder = DanmakuRecorder(
            log_path,
            store=store,
            on_display=displayed.append,
            on_message=messages.append,
            enrich_profiles=False,
        )
        event = DanmakuEvent(
            kind="danmaku",
            room_id=1,
            timestamp=dt.datetime(2024, 1, 1, 12, 0, 0),
            uid=42,
            username="alice",
            content="你好",
        )
        recorder.handle(event)
        recorder.close()

        self.assertEqual(store.events, [event])
        self.assertEqual(displayed, [event])
        self.assertIn("你好", log_path.read_text(encoding="utf-8"))
        self.assertEqual(messages, [])

    def test_recorder_enriches_wealth_level(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        log_path = Path(temp_dir.name) / "danmaku.txt"

        class FakeProfileClient:
            def get(self, uid):
                return LiveUserProfile(
                    uid=uid,
                    username="alice",
                    wealth_level=33,
                    guard_text="大航海提督",
                )

        recorder = DanmakuRecorder(log_path, enrich_profiles=True)
        recorder._profile_client = FakeProfileClient()
        event = DanmakuEvent(
            kind="danmaku",
            room_id=1,
            timestamp=dt.datetime(2024, 1, 1, 12, 0, 0),
            uid=42,
            username="alice",
            content="你好",
            medal_name="牌子",
            medal_level=36,
            user_level=21,
        )
        recorder.handle(event)
        recorder.close()

        self.assertEqual(event.wealth_level, 33)
        self.assertEqual(event.guard_level, 2)
        text = log_path.read_text(encoding="utf-8")
        self.assertIn("财富Lv.33", text)
        self.assertIn("直播间Lv.21", text)

    def test_recorder_reports_db_error_once(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = FakeStore(fail=True)
        messages = []
        recorder = DanmakuRecorder(
            Path(temp_dir.name) / "danmaku.txt",
            store=store,
            on_message=messages.append,
            enrich_profiles=False,
        )
        event = DanmakuEvent(
            kind="gift",
            room_id=1,
            timestamp=dt.datetime(2024, 1, 1, 12, 0, 0),
            username="alice",
        )
        recorder.handle(event)
        recorder.handle(event)
        recorder.close()
        self.assertEqual(len(messages), 1)
        self.assertIn("入库失败", messages[0])


if __name__ == "__main__":
    unittest.main()
