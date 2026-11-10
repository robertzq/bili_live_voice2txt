import io
import unittest
from pathlib import Path

import numpy as np

from bililive.audio import FRAME_SAMPLES, SpeechSegmenter
from bililive.capture import StreamCapture


class FakeVAD:
    def __init__(self, events):
        self.events = events
        self.index = 0

    def push(self, frame):
        self.index += 1
        return self.events.get(self.index)

    def reset(self):
        pass


class FakeProcess:
    def __init__(self, stdout):
        self.stdout = stdout
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class CaptureWiringTests(unittest.TestCase):
    def test_run_once_emits_segments_with_record_path(self):
        frame_count = 25
        pcm = np.zeros(frame_count * FRAME_SAMPLES, dtype=np.int16).tobytes()
        stream_process = FakeProcess(io.BytesIO(b""))
        ffmpeg_process = FakeProcess(io.BytesIO(pcm))
        processes = [stream_process, ffmpeg_process]

        vad = FakeVAD({2: "start", 20: "end"})
        captured = []
        capture = StreamCapture(
            room_id="123",
            segmenter=SpeechSegmenter(vad, min_speech_seconds=0.1),
            on_segment=lambda segment, record: captured.append((segment, record)),
            on_message=lambda _msg: None,
            record_dir=Path("/tmp"),
        )
        capture._require_binary = lambda _name: None

        def fake_spawn(_command, **_kwargs):
            process = processes.pop(0)
            capture._processes.append(process)
            return process

        capture._spawn = fake_spawn
        capture._remux = lambda path: str(path)

        capture._run_once()

        self.assertEqual(len(captured), 1)
        segment, record = captured[0]
        self.assertGreater(segment.duration, 0.1)
        self.assertTrue(record.endswith(".ts"))
        self.assertTrue(stream_process.terminated)
        self.assertTrue(ffmpeg_process.terminated)


if __name__ == "__main__":
    unittest.main()
