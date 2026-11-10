"""音频分帧与基于 VAD 的动态断句。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # Silero VAD 在 16k 采样率下要求 512 样本一帧
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
BYTES_PER_SAMPLE = 2
BLOCK_FRAMES = 5
BLOCK_SAMPLES = FRAME_SAMPLES * BLOCK_FRAMES
BLOCK_BYTES = BLOCK_SAMPLES * BYTES_PER_SAMPLE


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """把 16-bit 单声道 PCM 转成 [-1, 1] 的 float32。"""
    if not data:
        return np.empty(0, dtype=np.float32)
    samples = np.frombuffer(data, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


@dataclass(frozen=True)
class SpeechSegment:
    audio: np.ndarray
    start: float  # 相对本次录像起点，单位秒
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class StreamingVAD(Protocol):
    def push(self, frame: np.ndarray) -> Optional[str]:
        """返回 'start'、'end' 或 None。"""

    def reset(self) -> None:
        ...


class SpeechSegmenter:
    """把连续的 512 样本帧拼接成一句完整的话。

    与旧版固定 8 秒切块不同，这里以 VAD 检测到的静音作为断句点，
    并支持最长语音时长强制切分，避免一句话被从中间截断。
    """

    def __init__(
        self,
        vad: StreamingVAD,
        sample_rate: int = SAMPLE_RATE,
        frame_samples: int = FRAME_SAMPLES,
        min_speech_seconds: float = 0.35,
        max_speech_seconds: float = 15.0,
        pre_roll_seconds: float = 0.2,
    ) -> None:
        self.vad = vad
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.frame_seconds = frame_samples / sample_rate
        self.min_speech_seconds = min_speech_seconds
        self.max_speech_seconds = max_speech_seconds
        pre_roll_frames = max(1, round(pre_roll_seconds / self.frame_seconds))
        self._pre_roll: deque[tuple[np.ndarray, float]] = deque(maxlen=pre_roll_frames)
        self._frames: list[tuple[np.ndarray, float]] = []
        self._start: Optional[float] = None
        self._last_time: float = 0.0
        self._active = False

    def push_block(self, audio: np.ndarray, block_start: float) -> list[SpeechSegment]:
        results: list[SpeechSegment] = []
        frame_count = len(audio) // self.frame_samples
        for index in range(frame_count):
            offset = index * self.frame_samples
            frame = audio[offset : offset + self.frame_samples]
            frame_start = block_start + offset / self.sample_rate
            results.extend(self._push_frame(frame, frame_start))
        return results

    def _push_frame(self, frame: np.ndarray, frame_start: float) -> list[SpeechSegment]:
        results: list[SpeechSegment] = []
        event = self.vad.push(frame)
        frame_end = frame_start + self.frame_seconds

        if event == "start":
            self._begin(frame_start)

        if self._active:
            self._frames.append((frame, frame_start))
            self._last_time = frame_end
            if event == "end":
                segment = self._finish()
                if segment is not None:
                    results.append(segment)
        else:
            self._pre_roll.append((frame, frame_start))

        if self._active and (self._last_time - (self._start or 0.0)) >= self.max_speech_seconds:
            segment = self._finish()
            if segment is not None:
                results.append(segment)
            # 强制切分后继续接收后续语音，不等待下一次 VAD start。
            self._start = frame_end
            self._last_time = frame_end
            self._active = True

        return results

    def _begin(self, frame_start: float) -> None:
        if self._active:
            return
        if self._pre_roll:
            self._frames = list(self._pre_roll)
            self._start = self._pre_roll[0][1]
            self._pre_roll.clear()
        else:
            self._frames = []
            self._start = frame_start
        self._active = True
        self._last_time = frame_start

    def _finish(self) -> Optional[SpeechSegment]:
        if not self._active:
            return None
        frames = self._frames
        start = self._start or 0.0
        end = self._last_time
        self._frames = []
        self._active = False
        self._start = None
        self._pre_roll.clear()

        if not frames:
            return None
        audio = np.concatenate([frame for frame, _ in frames])
        segment = SpeechSegment(audio=audio, start=start, end=end)
        return segment if segment.duration >= self.min_speech_seconds else None

    def flush(self) -> list[SpeechSegment]:
        segment = self._finish()
        return [segment] if segment is not None else []
