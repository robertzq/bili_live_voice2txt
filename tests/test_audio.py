import unittest

import numpy as np

from bililive.audio import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    SpeechSegmenter,
    pcm16_to_float32,
)


class FakeVAD:
    def __init__(self, events):
        self.events = events
        self.index = 0
        self.resets = 0

    def push(self, frame):
        self.index += 1
        return self.events.get(self.index)

    def reset(self):
        self.resets += 1


def make_block(frame_count):
    return np.zeros(FRAME_SAMPLES * frame_count, dtype=np.float32)


class AudioTests(unittest.TestCase):
    def test_pcm16_conversion(self):
        audio = pcm16_to_float32(b"\x00\x00\xff\x7f")
        self.assertEqual(audio.dtype, np.float32)
        self.assertAlmostEqual(float(audio[0]), 0.0, places=4)
        self.assertAlmostEqual(float(audio[1]), 0.9999, places=3)

    def test_segment_emitted_on_vad_end(self):
        vad = FakeVAD({2: "start", 20: "end"})
        segmenter = SpeechSegmenter(vad, min_speech_seconds=0.1)
        segments = segmenter.push_block(make_block(25), block_start=0.0)
        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertGreater(segment.duration, 0.1)
        self.assertGreater(len(segment.audio), 0)
        self.assertEqual(len(segment.audio) % FRAME_SAMPLES, 0)

    def test_short_segment_is_dropped(self):
        vad = FakeVAD({2: "start", 3: "end"})
        segmenter = SpeechSegmenter(vad, min_speech_seconds=0.2)
        self.assertEqual(segmenter.push_block(make_block(10), 0.0), [])

    def test_pre_roll_is_included(self):
        vad = FakeVAD({5: "start", 20: "end"})
        segmenter = SpeechSegmenter(vad, min_speech_seconds=0.1, pre_roll_seconds=0.2)
        segments = segmenter.push_block(make_block(25), 0.0)
        self.assertEqual(len(segments), 1)
        self.assertLess(segments[0].start, (5 - 1) * FRAME_SAMPLES / SAMPLE_RATE)

    def test_long_speech_is_force_cut(self):
        vad = FakeVAD({1: "start"})
        segmenter = SpeechSegmenter(vad, min_speech_seconds=0.0, max_speech_seconds=0.5)
        segments = segmenter.push_block(make_block(80), 0.0)
        self.assertGreaterEqual(len(segments), 2)
        for segment in segments[:-1]:
            self.assertLessEqual(segment.duration, 0.6)

    def test_short_segments_are_merged_after_silence(self):
        vad = FakeVAD({1: "start", 10: "end", 20: "start", 30: "end"})
        segmenter = SpeechSegmenter(
            vad,
            min_speech_seconds=0.0,
            min_emit_seconds=1.0,
            merge_silence_seconds=0.5,
        )
        segments = segmenter.push_block(make_block(70), 0.0)
        self.assertEqual(len(segments), 1)
        self.assertGreater(segments[0].duration, 1.0)

    def test_long_gap_still_splits(self):
        vad = FakeVAD({1: "start", 10: "end", 40: "start", 50: "end"})
        segmenter = SpeechSegmenter(
            vad,
            min_speech_seconds=0.0,
            min_emit_seconds=1.0,
            merge_silence_seconds=0.5,
        )
        segments = segmenter.push_block(make_block(80), 0.0)
        self.assertEqual(len(segments), 2)

    def test_flush_emits_active_segment(self):
        vad = FakeVAD({1: "start"})
        segmenter = SpeechSegmenter(vad, min_speech_seconds=0.0)
        segmenter.push_block(make_block(10), 0.0)
        flushed = segmenter.flush()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(segmenter.flush(), [])


if __name__ == "__main__":
    unittest.main()
