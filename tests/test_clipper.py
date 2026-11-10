import unittest
from datetime import datetime

from bililive.clipper import build_clip_command, make_clip_filename


class ClipperTests(unittest.TestCase):
    def test_build_command_uses_seek_and_duration(self):
        command = build_clip_command("rec.ts", 12.5, 30.0, "out.mp4")
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-ss", command)
        self.assertEqual(command[command.index("-ss") + 1], "12.500")
        self.assertEqual(command[command.index("-t") + 1], "17.500")
        self.assertEqual(command[-1], "out.mp4")
        self.assertIn("+faststart", command)

    def test_build_command_clamps_negative_start(self):
        command = build_clip_command("rec.ts", -5, 2, "out.mp4")
        self.assertEqual(command[command.index("-ss") + 1], "0.000")
        self.assertEqual(command[command.index("-t") + 1], "7.000")

    def test_filename_is_sanitized(self):
        name = make_clip_filename(
            "a/b:c*d",
            start_seconds=65.9,
            when=datetime(2024, 1, 2, 3, 4, 5),
        )
        self.assertEqual(name, "Clip_abcd_030405_from_65s.mp4")

    def test_empty_streamer_falls_back(self):
        name = make_clip_filename("", 0, when=datetime(2024, 1, 1, 0, 0, 0))
        self.assertTrue(name.startswith("Clip_streamer_"))


if __name__ == "__main__":
    unittest.main()
