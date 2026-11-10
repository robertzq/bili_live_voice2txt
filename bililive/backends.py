"""转写后端：macOS 使用 MLX Whisper，其他平台使用 Faster-Whisper。"""

from __future__ import annotations

import importlib.util
import sys
from typing import Optional, Protocol

import numpy as np

from .config import DEFAULT_FASTER_WHISPER_MODEL, DEFAULT_MLX_MODEL


class Transcriber(Protocol):
    label: str

    def load(self) -> None:
        ...

    def transcribe(self, audio: np.ndarray) -> str:
        ...


def resolve_device(preferred: str = "auto") -> str:
    if preferred and preferred != "auto":
        return preferred
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def choose_compute_type(device: str, requested: Optional[str] = None) -> str:
    """Faster-Whisper 在 CPU 上不支持 int8_float16，这里做自适应。"""
    if requested:
        return requested
    return "float16" if device == "cuda" else "int8"


class MLXTranscriber:
    def __init__(
        self,
        model: str = DEFAULT_MLX_MODEL,
        language: str = "zh",
    ) -> None:
        self.model = model
        self.language = language
        self.label = f"MLX Whisper ({model})"
        self._mlx_whisper = None

    def load(self) -> None:
        if self._mlx_whisper is not None:
            return
        try:
            import mlx_whisper
        except ImportError as exc:
            raise RuntimeError(
                "未安装 mlx-whisper，请运行: pip install mlx-whisper"
            ) from exc
        self._mlx_whisper = mlx_whisper

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        result = self._mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model,
            language=self.language,
            verbose=False,
            no_speech_threshold=0.4,
            logprob_threshold=-0.8,
            condition_on_previous_text=False,
        )
        return str(result.get("text", "")).strip()


class FasterWhisperTranscriber:
    def __init__(
        self,
        model: str = DEFAULT_FASTER_WHISPER_MODEL,
        device: str = "auto",
        compute_type: Optional[str] = None,
        language: str = "zh",
    ) -> None:
        self.model = model
        self.requested_device = device
        self.requested_compute_type = compute_type
        self.language = language
        self.device = "cpu"
        self.compute_type = ""
        self.label = f"Faster-Whisper ({model})"
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "未安装 faster-whisper，请运行: pip install faster-whisper"
            ) from exc

        self.device = resolve_device(self.requested_device)
        self.compute_type = choose_compute_type(
            self.device, self.requested_compute_type
        )
        self.label = (
            f"Faster-Whisper ({self.model}, {self.device}, {self.compute_type})"
        )
        self._model = WhisperModel(
            self.model,
            device=self.device,
            compute_type=self.compute_type,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        segments, _ = self._model.transcribe(
            audio,
            beam_size=5,
            language=self.language,
            vad_filter=False,
            no_speech_threshold=0.4,
            log_prob_threshold=-0.8,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments).strip()


def create_transcriber(
    backend: str = "auto",
    model: Optional[str] = None,
    device: str = "auto",
    compute_type: Optional[str] = None,
    language: str = "zh",
) -> Transcriber:
    if backend == "auto":
        if sys.platform == "darwin" and importlib.util.find_spec("mlx_whisper"):
            backend = "mlx"
        else:
            backend = "faster-whisper"

    if backend == "mlx":
        return MLXTranscriber(model=model or DEFAULT_MLX_MODEL, language=language)
    if backend in {"faster-whisper", "faster"}:
        return FasterWhisperTranscriber(
            model=model or DEFAULT_FASTER_WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
            language=language,
        )
    raise ValueError(f"未知转写后端: {backend}")
