import json
import tempfile
import unittest
from pathlib import Path

from bililive.config import load_room_config, sanitize_filename


class ConfigTests(unittest.TestCase):
    def _write(self, data):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "room.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_load_valid_config(self):
        room = load_room_config(self._write({"room_id": 123, "streamer_name": "测试"}))
        self.assertEqual(room.room_id, "123")
        self.assertEqual(room.streamer_name, "测试")

    def test_missing_room_id(self):
        with self.assertRaises(ValueError):
            load_room_config(self._write({"streamer_name": "测试"}))

    def test_invalid_json(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_room_config(path)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_room_config("/definitely/not/here.json")

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('a\\b/c:d*e?f"g<h>i|j'), "abcdefghij")
        self.assertEqual(sanitize_filename("   "), "streamer")


if __name__ == "__main__":
    unittest.main()
