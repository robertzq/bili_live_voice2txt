"""Silero VAD 封装（重依赖延迟加载）。"""

from __future__ import annotations

from typing import Optional

import numpy as np


class SileroVAD:
    """流式 VAD，使用 Silero 官方的 VADIterator。

    首次 push 时才导入 torch 并下载/加载模型，避免 GUI 启动时卡住。
    """

    def __init__(
        self,
        device: str = "cpu",
        threshold: float = 0.5,
        min_silence_duration_ms: int = 700,
        speech_pad_ms: int = 200,
        sample_rate: int = 16000,
    ) -> None:
        self.device = device
        self.threshold = threshold
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        self.sample_rate = sample_rate
        self._torch = None
        self._model = None
        self._iterator = None

    def load(self) -> None:
        if self._iterator is not None:
            return

        import torch

        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        (_, _, _, vad_iterator_cls, _) = utils

        try:
            model.to(self.device)
        except Exception:
            self.device = "cpu"
            model.to("cpu")

        self._torch = torch
        self._model = model
        self._iterator = vad_iterator_cls(
            model,
            sampling_rate=self.sample_rate,
            threshold=self.threshold,
            min_silence_duration_ms=self.min_silence_duration_ms,
            speech_pad_ms=self.speech_pad_ms,
        )

    def push(self, frame: np.ndarray) -> Optional[str]:
        self.load()
        tensor = self._torch.from_numpy(np.ascontiguousarray(frame)).to(self.device)
        event = self._iterator(tensor)
        if not event:
            return None
        if "start" in event:
            return "start"
        if "end" in event:
            return "end"
        return None

    def reset(self) -> None:
        if self._iterator is not None:
            self._iterator.reset_states()
