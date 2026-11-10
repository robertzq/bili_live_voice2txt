"""字幕文本清洗与去重。"""

from __future__ import annotations

import re
from collections import deque
from typing import Iterable

from .config import IGNORE_KEYWORDS

_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_text(text: str) -> str:
    return _NORMALIZE_RE.sub("", text).lower()


def is_hallucination(text: str, keywords: Iterable[str] = IGNORE_KEYWORDS) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


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

        key = normalize_text(text)
        if not key or key in self._recent:
            return False

        self._recent.append(key)
        return True
