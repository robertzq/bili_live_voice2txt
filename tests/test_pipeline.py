import tempfile
import unittest
from pathlib import Path

import numpy as np

from bililive.audio import SpeechSegment
from bililive.danmaku import DanmakuEvent
from bililive.config import RoomConfig
from bililive.pipeline import Pipeline, _STOP
from bililive.text import TranscriptFilter


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, _audio):
        return self.text


class FakeClipper:
    def __init__(self):
        self.calls = []

    def trigger(self, end_seconds, streamer_name, record_file=None):
        self.calls.append((end_seconds, streamer_name, record_file))


class PipelineTests(unittest.TestCase):
    def _run_once(self, text):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        log_path = Path(temp_dir.name) / "log.txt"

        pipeline = Pipeline(
            room=RoomConfig(room_id="1", streamer_name="主播"),
            log_dir=temp_dir.name,
            on_transcript=lambda *_args: None,
        )
        pipeline._running.set()
        pipeline._log_path = log_path

        clipper = FakeClipper()
        pipeline._audio_queue.put(
            (SpeechSegment(np.zeros(16, dtype=np.float32), 1.0, 2.0), "rec.ts")
        )
        pipeline._audio_queue.put(_STOP)
        pipeline._transcribe_loop(
            FakeTranscriber(text), TranscriptFilter(), clipper
        )
        return clipper, log_path

    def test_transcript_is_logged_and_clipped(self):
        clipper, log_path = self._run_once("切片飞来 大家好")
        self.assertEqual(clipper.calls, [(2.0, "主播", "rec.ts")])
        self.assertIn("大家好", log_path.read_text(encoding="utf-8"))

    def test_plain_transcript_does_not_clip(self):
        clipper, log_path = self._run_once("今天天气不错")
        self.assertEqual(clipper.calls, [])
        self.assertTrue(log_path.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()


class FakeDanmakuService:
    def __init__(self, room_id, on_event, on_message=None):
        self.on_event = on_event

    def run(self):
        import datetime as dt

        self.on_event(
            DanmakuEvent(
                kind="danmaku",
                room_id=1,
                timestamp=dt.datetime(2024, 1, 1, 12, 0, 0),
                uid=42,
                username="alice",
                content="弹幕内容",
            )
        )

    def stop(self):
        pass


class PipelineDanmakuTests(unittest.TestCase):
    def test_danmaku_event_is_logged(self):
        import os
        from unittest import mock

        from bililive.pipeline import Pipeline

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        displayed = []
        pipeline = Pipeline(
            room=RoomConfig(room_id="1", streamer_name="主播"),
            log_dir=temp_dir.name,
            on_danmaku=displayed.append,
            on_message=lambda _msg: None,
        )
        pipeline._running.set()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "bililive.pipeline.DanmakuService", FakeDanmakuService
        ):
            pipeline._run_danmaku()

        self.assertEqual(len(displayed), 1)
        logs = list(Path(temp_dir.name).glob("*_danmaku_*.txt"))
        self.assertEqual(len(logs), 1)
        self.assertIn("弹幕内容", logs[0].read_text(encoding="utf-8"))
