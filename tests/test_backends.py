import unittest

from bililive.backends import (
    FasterWhisperTranscriber,
    MLXTranscriber,
    choose_compute_type,
    create_transcriber,
    resolve_device,
)


class BackendTests(unittest.TestCase):
    def test_compute_type_adapts_to_device(self):
        self.assertEqual(choose_compute_type("cuda"), "float16")
        self.assertEqual(choose_compute_type("cpu"), "int8")
        self.assertEqual(choose_compute_type("cpu", "int8_float32"), "int8_float32")

    def test_explicit_device(self):
        self.assertEqual(resolve_device("cpu"), "cpu")
        self.assertEqual(resolve_device("cuda"), "cuda")

    def test_create_mlx_backend_without_loading(self):
        backend = create_transcriber("mlx", model="fake/model")
        self.assertIsInstance(backend, MLXTranscriber)
        self.assertEqual(backend.model, "fake/model")

    def test_create_faster_backend_without_loading(self):
        backend = create_transcriber(
            "faster-whisper", model="tiny", device="cpu"
        )
        self.assertIsInstance(backend, FasterWhisperTranscriber)
        self.assertEqual(backend.model, "tiny")

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            create_transcriber("nope")


if __name__ == "__main__":
    unittest.main()
