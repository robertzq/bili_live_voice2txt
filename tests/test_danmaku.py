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


class MultiConnGiftTrackerTests(unittest.TestCase):
    def _payload(self, version, price, session="s1", cmd="UNIVERSAL_EVENT_GIFT"):
        if cmd == "UNIVERSAL_EVENT_GIFT_V2":
            return {
                "cmd": cmd,
                "data": {
                    "biz_session_id": session,
                    "system_time_unix": 1700000000,
                    "version": version,
                    "members": [
                        {
                            "uid": 1,
                            "uname": "主播A",
                            "biz_extra_data": {"multi_conn": {"price": price}},
                        }
                    ],
                },
            }
        return {
            "cmd": cmd,
            "data": {
                "biz_session_id": session,
                "system_time_unix": 1700000000,
                "info": {
                    "version": version,
                    "members": [{"uid": 1, "uname": "主播A"}],
                    "multi_conn_info": {"scores": [{"uid": 1, "price": price}]},
                },
            },
        }

    def test_first_snapshot_is_baseline(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        self.assertEqual(tracker.process(self._payload(100, 1000), 123), [])

    def test_score_increase_creates_gift(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        tracker.process(self._payload(100, 1000), 123)
        events = tracker.process(self._payload(101, 1500), 123)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, "gift")
        self.assertEqual(event.gift_name, "连麦投票")
        self.assertEqual(event.gift_num, 5)
        self.assertAlmostEqual(event.total_price, 0.5)
        self.assertEqual(event.receiver_name, "主播A")

    def test_duplicate_and_older_versions_are_ignored(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        tracker.process(self._payload(100, 1000), 123)
        tracker.process(self._payload(101, 1500), 123)
        self.assertEqual(tracker.process(self._payload(101, 1500), 123), [])
        self.assertEqual(tracker.process(self._payload(100, 1500), 123), [])

    def test_v2_payload_is_supported(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        tracker.process(self._payload(100, 1000), 123)
        events = tracker.process(
            self._payload(102, 2000, cmd="UNIVERSAL_EVENT_GIFT_V2"), 123
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].gift_num, 10)
        self.assertAlmostEqual(events[0].total_price, 1.0)

    def test_new_session_resets_baseline(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        tracker.process(self._payload(100, 1000, session="s1"), 123)
        self.assertEqual(
            tracker.process(self._payload(100, 5000, session="s2"), 123), []
        )
        events = tracker.process(self._payload(101, 6000, session="s2"), 123)
        self.assertEqual(len(events), 1)

    def test_out_of_order_snapshot_does_not_double_count(self):
        from bililive.danmaku import MultiConnGiftTracker

        tracker = MultiConnGiftTracker()
        tracker.process(self._payload(100, 1000), 123)
        first = tracker.process(self._payload(101, 1500), 123)
        self.assertEqual(first[0].gift_num, 5)
        self.assertEqual(tracker.process(self._payload(100, 1000), 123), [])
        second = tracker.process(self._payload(102, 1600), 123)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].gift_num, 1)

    def test_service_handler_queues_universal_gift(self):
        import asyncio
        from bililive.danmaku import DanmakuService

        service = DanmakuService(123, on_event=lambda _event: None)
        handler = service._make_handler("UNIVERSAL_EVENT_GIFT")
        asyncio.run(handler({"data": self._payload(100, 1000)}))
        asyncio.run(handler({"data": self._payload(101, 1500)}))
        event = service._queue.get_nowait()
        self.assertEqual(event.gift_name, "连麦投票")
        self.assertEqual(event.gift_num, 5)


class SendGiftV2Tests(unittest.TestCase):
    @staticmethod
    def _varint(value):
        out = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                return bytes(out)

    @classmethod
    def _field(cls, number, wire, payload):
        key = cls._varint((number << 3) | wire)
        if wire == 0:
            return key + cls._varint(payload)
        if wire == 2:
            return key + cls._varint(len(payload)) + payload
        raise ValueError(wire)

    @classmethod
    def _gift_message(cls, num=1, coin_type="gold", gift_name="粉丝团灯牌"):
        price = 0 if coin_type == "silver" else 100
        receiver = cls._field(1, 2, "测试主播".encode()) + cls._field(2, 0, 30003)
        return b"".join(
            [
                cls._field(1, 0, 31164),
                cls._field(2, 2, gift_name.encode()),
                cls._field(3, 0, num),
                cls._field(5, 0, price),
                cls._field(6, 0, price),
                cls._field(7, 0, price * num),
                cls._field(8, 2, coin_type.encode()),
                cls._field(10, 0, 1789041024),
                cls._field(29, 2, receiver),
            ]
        )

    @classmethod
    def _payload(cls, gifts=None, coin_type="gold", medal_target_id=30003):
        medal = (
            cls._field(1, 0, medal_target_id)
            + cls._field(5, 0, 30)
            + cls._field(6, 2, "测试牌子".encode())
        )
        gifts = gifts or [cls._gift_message(coin_type=coin_type)]
        body = (
            cls._field(1, 0, 10001)
            + cls._field(2, 2, "测试观众甲".encode())
            + cls._field(8, 2, medal)
            + b"".join(cls._field(10, 2, gift) for gift in gifts)
        )
        import base64

        return {"data": {"pb": base64.b64encode(body).decode()}}

    def test_parses_sender_gift_receiver_and_medal(self):
        from bililive.danmaku import parse_event

        event = parse_event("SEND_GIFT_V2", self._payload(), 123)
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "gift")
        self.assertEqual(event.uid, 10001)
        self.assertEqual(event.username, "测试观众甲")
        self.assertEqual(event.medal_name, "测试牌子")
        self.assertEqual(event.medal_level, 30)
        self.assertEqual(event.gift_name, "粉丝团灯牌")
        self.assertEqual(event.gift_num, 1)
        self.assertAlmostEqual(event.price, 0.1)
        self.assertAlmostEqual(event.total_price, 0.1)
        self.assertEqual(event.receiver_id, 30003)
        self.assertEqual(event.receiver_name, "测试主播")

    def test_multiple_gift_items_are_all_returned(self):
        from bililive.danmaku import parse_events

        events = parse_events(
            "SEND_GIFT_V2",
            self._payload(
                [
                    self._gift_message(gift_name="礼物A"),
                    self._gift_message(gift_name="礼物B"),
                ]
            ),
            123,
        )
        self.assertEqual([event.gift_name for event in events], ["礼物A", "礼物B"])

    def test_silver_gift_is_free(self):
        from bililive.danmaku import parse_event

        event = parse_event(
            "SEND_GIFT_V2", self._payload(coin_type="silver"), 123
        )
        self.assertEqual(event.price, 0.0)
        self.assertEqual(event.total_price, 0.0)

    def test_medal_from_other_room_is_cleared(self):
        from bililive.danmaku import parse_event

        event = parse_event(
            "SEND_GIFT_V2", self._payload(medal_target_id=99999), 123
        )
        self.assertIsNone(event.medal_name)
        self.assertIsNone(event.medal_level)
        self.assertTrue(event.extra["medal_mismatch"])

    def test_medal_from_current_room_is_kept(self):
        from bililive.danmaku import parse_event

        event = parse_event(
            "SEND_GIFT_V2", self._payload(medal_target_id=30003), 123
        )
        self.assertEqual(event.medal_name, "测试牌子")
        self.assertEqual(event.medal_level, 30)

    def test_service_uses_cached_current_room_medal_for_gift(self):
        import datetime as dt
        from bililive.danmaku import DanmakuEvent, DanmakuService

        service = DanmakuService(123, on_event=lambda _event: None)
        service._apply_medal(
            DanmakuEvent(
                kind="danmaku",
                room_id=123,
                timestamp=dt.datetime.now(),
                uid=10001,
                medal_name="恋汐生",
                medal_level=39,
            )
        )
        gift = DanmakuEvent(
            kind="gift",
            room_id=123,
            timestamp=dt.datetime.now(),
            uid=10001,
            medal_name="弃車人",
            medal_level=31,
        )
        service._apply_medal(gift)
        self.assertEqual(gift.medal_name, "恋汐生")
        self.assertEqual(gift.medal_level, 39)

    def test_mismatched_gift_medal_is_cleared_without_cache(self):
        import datetime as dt
        from bililive.danmaku import DanmakuEvent, DanmakuService

        service = DanmakuService(123, on_event=lambda _event: None)
        gift = DanmakuEvent(
            kind="gift",
            room_id=123,
            timestamp=dt.datetime.now(),
            uid=2,
            medal_name="乌拉宝",
            medal_level=42,
            extra={"medal_mismatch": True},
        )
        service._apply_medal(gift)
        self.assertIsNone(gift.medal_name)
        self.assertIsNone(gift.medal_level)

    def test_service_handler_queues_gift_v2(self):
        import asyncio
        from bililive.danmaku import DanmakuService

        service = DanmakuService(123, on_event=lambda _event: None)
        handler = service._make_handler("SEND_GIFT_V2")
        asyncio.run(handler({"data": {"data": self._payload()["data"]}}))
        event = service._queue.get_nowait()
        self.assertEqual(event.gift_name, "粉丝团灯牌")
