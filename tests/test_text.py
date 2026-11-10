import unittest

from bililive.text import TranscriptFilter, is_hallucination, normalize_text


class TextFilterTests(unittest.TestCase):
    def test_normalize_removes_punctuation_and_spaces(self):
        self.assertEqual(normalize_text("你好， 世界！"), "你好世界")

    def test_hallucination_keywords(self):
        self.assertTrue(is_hallucination("字幕by 某某"))
        self.assertTrue(is_hallucination("请订阅我的频道"))
        self.assertFalse(is_hallucination("今天直播内容不错"))

    def test_filter_rejects_short_and_empty(self):
        transcript_filter = TranscriptFilter(min_length=2)
        self.assertFalse(transcript_filter.accept(""))
        self.assertFalse(transcript_filter.accept("嗯"))

    def test_filter_deduplicates_recent_text(self):
        transcript_filter = TranscriptFilter(dedup_size=3)
        self.assertTrue(transcript_filter.accept("大家好"))
        self.assertFalse(transcript_filter.accept("大家好！"))
        self.assertTrue(transcript_filter.accept("今天天气不错"))

    def test_filter_allows_repeat_after_window(self):
        transcript_filter = TranscriptFilter(dedup_size=1)
        self.assertTrue(transcript_filter.accept("大家好"))
        self.assertTrue(transcript_filter.accept("换一句"))
        self.assertTrue(transcript_filter.accept("大家好"))


if __name__ == "__main__":
    unittest.main()
