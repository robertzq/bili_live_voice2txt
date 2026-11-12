from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import summarize  # noqa: E402


class ParseLogNameTests(unittest.TestCase):
    def test_parses_standard_log(self) -> None:
        entry = summarize.parse_log_name(
            Path("logs/苏狸狸_1924024821_log_1794374263.txt")
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.streamer, "苏狸狸")
        self.assertEqual(entry.room_id, "1924024821")
        self.assertEqual(entry.kind, "log")
        self.assertEqual(entry.day, datetime.fromtimestamp(1794374263).date())

    def test_parses_mlx_log_and_danmaku(self) -> None:
        mlx = summarize.parse_log_name(
            Path("logs/jiojio_1950858520_mlx_log_1775531570.txt")
        )
        danmaku = summarize.parse_log_name(
            Path("logs/青荼_1899306331_danmaku_1794490417.txt")
        )
        self.assertEqual(mlx.kind if mlx else None, "mlx_log")
        self.assertEqual(danmaku.kind if danmaku else None, "danmaku")

    def test_rejects_unknown_name(self) -> None:
        self.assertIsNone(summarize.parse_log_name(Path("meeting_log_1.txt")))


class TextHelpersTests(unittest.TestCase):
    def test_clean_transcript_removes_latency(self) -> None:
        raw = "[13:17:49] (⚡0.53s) 应用在哪儿\n[13:17:50] (⚡0.55s) 你咋现在的就"
        cleaned = summarize._clean_transcript(raw)
        self.assertEqual(cleaned, "[13:17:49] 应用在哪儿\n[13:17:50] 你咋现在的就")

    def test_split_text_respects_limit(self) -> None:
        text = "\n".join(f"第 {i} 行" * 10 for i in range(50))
        chunks = summarize._split_text(text, 200)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 220)

    def test_sample_text_keeps_head_and_tail(self) -> None:
        text = "\n".join(f"line-{i:04d}" for i in range(1000))
        sampled = summarize._sample_text(text, 500)
        self.assertIn("line-0000", sampled)
        self.assertIn("line-0999", sampled)
        self.assertIn("已省略", sampled)

    def test_parse_day_accepts_common_formats(self) -> None:
        self.assertEqual(summarize.parse_day("2026-11-12"), date(2026, 11, 12))
        self.assertEqual(summarize.parse_day("2026/11/12"), date(2026, 11, 12))
        self.assertEqual(summarize.parse_day("20261112"), date(2026, 11, 12))


class PromptTests(unittest.TestCase):
    def test_prompt_contains_required_sections(self) -> None:
        prompt = summarize.build_analyst_prompt(
            streamer="测试主播",
            date_label="2026-11-12",
            records_markdown="- 录像片段：1 段",
            metrics_markdown="总流水 ¥100",
            danmaku_text="观众：加油",
            transcript_text="主播：今天聊点不一样的",
        )
        for expected in (
            "本场亮点",
            "核心结论",
            "提升空间",
            "数据看板与经营诊断",
            "内容复盘",
            "核心观众性格与沟通建议",
            "情绪与节奏曲线",
            "风险与合规",
            "下一场行动清单",
            "Mermaid",
            "测试主播",
            "语气与表达要求",
        ):
            self.assertIn(expected, prompt)
        self.assertIn("观众：加油", prompt)
        self.assertIn("今天聊点不一样的", prompt)

    def test_prompt_uses_gentle_tone(self) -> None:
        prompt = summarize.build_analyst_prompt(
            streamer="测试主播",
            date_label="2026-11-12",
            records_markdown="",
            metrics_markdown="",
            danmaku_text="",
            transcript_text="",
        )
        self.assertIn("先肯定", prompt)
        self.assertIn("假热闹", prompt)
        self.assertIn("还有提升空间", prompt)

    def test_brief_prompt_structure(self) -> None:
        prompt = summarize.build_brief_prompt(
            full_report="# 完整报告\n这里是完整内容",
            streamer="测试主播",
            date_label="2026-11-12",
        )
        for expected in (
            "一页速览",
            "本场亮点",
            "核心结论",
            "关键数据",
            "核心观众沟通建议",
            "下场就做这 3 件事",
            "需要留意",
            "完整报告",
            "测试主播",
        ):
            self.assertIn(expected, prompt)
        self.assertIn("这里是完整内容", prompt)

    def test_brief_is_enabled_by_default(self) -> None:
        self.assertTrue(summarize.SummarizeOptions().include_brief)

    def test_pick_samples_spreads_across_messages(self) -> None:
        items = list(range(100))
        samples = summarize._pick_samples(items, 10)
        self.assertEqual(len(samples), 10)
        self.assertEqual(samples[0], 0)
        self.assertGreater(samples[-1], 80)

    def test_system_prompt_forbids_fabrication(self) -> None:
        self.assertIn("绝不编造", summarize.SYSTEM_PROMPT)
        self.assertIn("委婉", summarize.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
