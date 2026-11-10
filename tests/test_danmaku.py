import unittest

from bililive.danmaku import DanmakuEvent, parse_event


class DanmakuParseTests(unittest.TestCase):
    def test_parse_danmaku(self):
        payload = {
            "info": [
                [0, 1, 25, 16777215, 1700000000, 0, 0, ""],
                "你好",
                [42, "alice", 0, 0, 0, 1000, 1, ""],
                [21, "牌子", "anchor", 99, 0, 0, 0, 1],
                [33, "", 0, 0, 0, 0],
            ]
        }
        event = parse_event("DANMU_MSG", payload, 123)
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "danmaku")
        self.assertEqual(event.uid, 42)
        self.assertEqual(event.username, "alice")
        self.assertEqual(event.content, "你好")
        self.assertEqual(event.medal_name, "牌子")
        self.assertEqual(event.medal_level, 21)
        self.assertEqual(event.user_level, 33)
        self.assertEqual(event.timestamp.year, 2023)

    def test_parse_gift(self):
        payload = {
            "data": {
                "uid": 42,
                "uname": "alice",
                "giftName": "辣条",
                "num": 2,
                "price": 100,
                "total_coin": 200,
                "coin_type": "gold",
                "timestamp": 1700000000,
                "medal_info": {"medal_name": "牌子", "medal_level": 10},
                "receiver_uinfo": {"uid": 9, "uname": "主播"},
            }
        }
        event = parse_event("SEND_GIFT", payload, 123)
        self.assertEqual(event.kind, "gift")
        self.assertEqual(event.gift_name, "辣条")
        self.assertEqual(event.gift_num, 2)
        self.assertAlmostEqual(event.price, 0.1)
        self.assertAlmostEqual(event.total_price, 0.2)
        self.assertEqual(event.receiver_name, "主播")

    def test_silver_gift_has_no_price(self):
        payload = {
            "data": {
                "uid": 42,
                "uname": "alice",
                "giftName": "小心心",
                "num": 5,
                "price": 0,
                "total_coin": 0,
                "coin_type": "silver",
                "timestamp": 1700000000,
            }
        }
        event = parse_event("SEND_GIFT", payload, 123)
        self.assertEqual(event.total_price, 0.0)

    def test_parse_guard(self):
        payload = {
            "data": {
                "uid": 42,
                "username": "alice",
                "guard_level": 3,
                "num": 1,
                "price": 198000,
                "gift_name": "舰长",
                "timestamp": 1700000000,
                "start_time": 1700000000,
                "end_time": 1702592000,
            }
        }
        event = parse_event("GUARD_BUY", payload, 123)
        self.assertEqual(event.kind, "guard")
        self.assertEqual(event.guard_type, 3)
        self.assertEqual(event.gift_name, "舰长")
        self.assertAlmostEqual(event.price, 198.0)
        self.assertAlmostEqual(event.total_price, 198.0)
        self.assertIsNotNone(event.start_time)

    def test_parse_super_chat(self):
        payload = {
            "data": {
                "uid": 42,
                "user_info": {"uname": "alice"},
                "message": "加油",
                "price": 30,
                "start_time": 1700000000,
                "end_time": 1700000060,
                "gift": {"gift_name": "醒目留言"},
                "medal_info": {"medal_name": "牌子", "medal_level": 5},
            }
        }
        event = parse_event("SUPER_CHAT_MESSAGE", payload, 123)
        self.assertEqual(event.kind, "super_chat")
        self.assertEqual(event.username, "alice")
        self.assertEqual(event.content, "加油")
        self.assertAlmostEqual(event.price, 30.0)
        self.assertEqual(event.gift_name, "醒目留言")

    def test_parse_entry_v2(self):
        payload = {
            "data": {
                "pb_decoded": {
                    "uid": 42,
                    "uname": "alice",
                    "msg_type": 1,
                    "timestamp": 1700000000,
                    "fans_medal_info": {"medal_name": "牌子", "medal_level": 5},
                }
            }
        }
        event = parse_event("INTERACT_WORD_V2", payload, 123)
        self.assertEqual(event.kind, "entry")
        self.assertEqual(event.username, "alice")
        self.assertEqual(event.content, "进入直播间")

    def test_non_entry_interaction_is_ignored(self):
        payload = {"data": {"uid": 42, "uname": "alice", "msg_type": 2}}
        self.assertIsNone(parse_event("INTERACT_WORD", payload, 123))

    def test_unknown_command_is_ignored(self):
        self.assertIsNone(parse_event("SOMETHING_ELSE", {}, 123))

    def test_display_text_contains_kind_and_content(self):
        event = DanmakuEvent(
            kind="danmaku",
            room_id=1,
            timestamp=__import__("datetime").datetime(2024, 1, 1, 12, 0, 0),
            uid=42,
            username="alice",
            content="你好",
        )
        self.assertIn("[弹幕]", event.display_text())
        self.assertIn("alice", event.display_text())


if __name__ == "__main__":
    unittest.main()
