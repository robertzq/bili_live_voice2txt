"""字幕文本清洗与去重。"""

from __future__ import annotations

import re
from collections import deque
from typing import Iterable

from .config import IGNORE_KEYWORDS

_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]*\|>")
_converter = None


def _simplifier():
    global _converter
    if _converter is None:
        try:
            from opencc import OpenCC

            _converter = OpenCC("t2s")
        except Exception:
            _converter = False
    return _converter


def to_simplified(text: str) -> str:
    converter = _simplifier()
    if not converter:
        return text
    try:
        return converter.convert(text)
    except Exception:
        return text


def clean_transcript(text: str) -> str:
    """去掉 Whisper 特殊标记，并尽量转成简体中文。"""
    text = _SPECIAL_TOKEN_RE.sub("", text)
    text = text.replace("\u200b", "").strip()
    text = to_simplified(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_text(text: str) -> str:
    return _NORMALIZE_RE.sub("", text).lower()


def is_hallucination(text: str, keywords: Iterable[str] = IGNORE_KEYWORDS) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def is_repetition(text: str, min_repeats: int = 6) -> bool:
    """识别 Whisper 在音乐/噪声上常见的整段重复幻觉。"""
    tokens = text.split()
    if len(tokens) >= min_repeats and len(set(tokens)) == 1:
        return True

    compact = re.sub(r"\s+", "", text)
    if len(compact) < min_repeats * 2:
        return False
    for size in range(1, len(compact) // min_repeats + 1):
        if len(compact) % size == 0:
            unit = compact[:size]
            if unit * (len(compact) // size) == compact:
                return True
    return False


class TranscriptFilter:
    """过滤空文本、已知幻觉，并对近期重复字幕去重。"""

    def __init__(
        self,
        keywords: Iterable[str] = IGNORE_KEYWORDS,
        min_length: int = 2,
        dedup_size: int = 8,
    ) -> None:
        self.keywords = list(keywords)
        self.min_length = min_length
        self._recent: deque[str] = deque(maxlen=dedup_size)

    def accept(self, text: str) -> bool:
        text = text.strip()
        if len(text) < self.min_length:
            return False
        if is_hallucination(text, self.keywords):
            return False
        if is_repetition(text):
            return False

        key = normalize_text(text)
        if not key or key in self._recent:
            return False

        self._recent.append(key)
        return True
