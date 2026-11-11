"""采集、断句、转写、切片的主流水线。"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .audio import SpeechSegment, SpeechSegmenter
from .backends import create_transcriber, resolve_device
from .capture import StreamCapture
from .clipper import Clipper
from .config import (
    LOG_DIR,
    RECORD_DIR,
    TRIGGER_KEYWORDS,
    RoomConfig,
)
from .danmaku import DanmakuEvent, DanmakuRecorder, DanmakuService
from .database import DatabaseConfig, DanmakuStore
from .text import TranscriptFilter, clean_transcript
from .vad import SileroVAD

MessageCallback = Callable[[str], None]
TranscriptCallback = Callable[[str, str, float], None]
DanmakuCallback = Callable[[DanmakuEvent], None]

_STOP = object()


class Pipeline:
    """把各个线程和队列封装起来，GUI 只负责 start/stop 和显示消息。"""

    def __init__(
        self,
        room: RoomConfig,
        backend: str = "auto",
        device: str = "auto",
        model: Optional[str] = None,
        compute_type: Optional[str] = None,
        language: str = "zh",
        danmaku: bool = True,
        on_message: Optional[MessageCallback] = None,
        on_transcript: Optional[TranscriptCallback] = None,
        on_danmaku: Optional[DanmakuCallback] = None,
        record_dir: str | Path = RECORD_DIR,
        log_dir: str | Path = LOG_DIR,
    ) -> None:
        self.room = room
        self.backend = backend
        self.device = device
        self.model = model
        self.compute_type = compute_type
        self.language = language
        self.danmaku_enabled = danmaku
        self.on_message = on_message or (lambda _msg: None)
        self.on_transcript = on_transcript or (
            lambda timestamp, text, cost: print(f"[{timestamp}] (⚡️{cost:.2f}s) {text}")
        )
        self.on_danmaku = on_danmaku or (lambda _event: None)
        self.record_dir = Path(record_dir)
        self.log_dir = Path(log_dir)

        self._running = threading.Event()
        self._audio_queue: queue.Queue = queue.Queue()
        self._capture: Optional[StreamCapture] = None
        self._clipper: Optional[Clipper] = None
        self._bootstrap_thread: Optional[threading.Thread] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._danmaku_thread: Optional[threading.Thread] = None
        self._danmaku_service: Optional[DanmakuService] = None
        self._danmaku_recorder: Optional[DanmakuRecorder] = None
        self._log_path: Optional[Path] = None

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def log_path(self) -> Optional[Path]:
        return self._log_path

    def start(self) -> None:
        if self._running.is_set():
            return
        self._drain_audio_queue()
        self._capture = None
        self._clipper = None
        self._log_path = None
        self._danmaku_service = None
        self._danmaku_recorder = None
        self._running.set()
        self._bootstrap_thread = threading.Thread(
            target=self._bootstrap,
            name="pipeline-bootstrap",
            daemon=True,
        )
        self._bootstrap_thread.start()

    def stop(self) -> None:
        if not self._running.is_set() and (
            self._bootstrap_thread is None or not self._bootstrap_thread.is_alive()
        ):
            return
        self._running.clear()
        if self._capture is not None:
            self._capture.stop()
        if self._danmaku_service is not None:
            self._danmaku_service.stop()
        self._drain_audio_queue()
        self._audio_queue.put(_STOP)
        self.on_message("⏳ [系统] 正在停止...")

    def _bootstrap(self) -> None:
        try:
            self.on_message("🛠 [模型] 正在加载 VAD...")
            vad = SileroVAD(device=resolve_device(self.device))
            vad.load()
            if not self._running.is_set():
                return

            self.on_message("🚀 [模型] 正在加载转写模型...")
            transcriber = create_transcriber(
                backend=self.backend,
                model=self.model,
                device=self.device,
                compute_type=self.compute_type,
                language=self.language,
            )
            transcriber.load()
            if not self._running.is_set():
                return
            self.on_message(f"✅ [模型] 就绪: {transcriber.label}")

            self.record_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_name = (
                f"{self.room.safe_name}_{self.room.room_id}_log_"
                f"{int(time.time())}.txt"
            )
            self._log_path = self.log_dir / log_name
            self.on_message(f"📝 [日志] {self._log_path}")

            transcript_filter = TranscriptFilter()
            segmenter = SpeechSegmenter(
                vad,
                min_emit_seconds=2.5,
                merge_silence_seconds=0.6,
                max_speech_seconds=10.0,
            )
            clipper = Clipper(
                record_provider=lambda: (
                    self._capture.record_path if self._capture is not None else None
                ),
                output_dir=self.record_dir,
                on_message=self.on_message,
            )
            self._clipper = clipper
            capture = StreamCapture(
                room_id=self.room.room_id,
                segmenter=segmenter,
                on_segment=self._enqueue_segment,
                on_message=self.on_message,
                record_dir=self.record_dir,
            )
            self._capture = capture

            if not self._running.is_set():
                capture.stop()
                return

            self._transcribe_thread = threading.Thread(
                target=self._transcribe_loop,
                args=(transcriber, transcript_filter, clipper),
                name="pipeline-transcriber",
                daemon=True,
            )
            self._transcribe_thread.start()

            self._start_danmaku()

            if not self._running.is_set():
                capture.stop()
                return
            capture.run()
        except Exception as exc:
            self.on_message(f"❌ [系统] 启动失败: {exc}")
        finally:
            self._running.clear()
            if self._danmaku_service is not None:
                self._danmaku_service.stop()
            if self._danmaku_thread is not None:
                self._danmaku_thread.join(timeout=3)
            if self._danmaku_recorder is not None:
                self._danmaku_recorder.close()
            self._audio_queue.put(_STOP)
            self.on_message("🛑 [系统] 监听已停止")

    def _start_danmaku(self) -> None:
        if not self.danmaku_enabled:
            self.on_message("弹幕监听未启用")
            return
        self._danmaku_thread = threading.Thread(
            target=self._run_danmaku,
            name="pipeline-danmaku",
            daemon=True,
        )
        self._danmaku_thread.start()

    def _run_danmaku(self) -> None:
        try:
            store = self._create_store()
            log_name = (
                f"{self.room.safe_name}_{self.room.room_id}_danmaku_"
                f"{int(time.time())}.txt"
            )
            log_path = self.log_dir / log_name
            self._danmaku_recorder = DanmakuRecorder(
                log_path=log_path,
                store=store,
                on_display=self.on_danmaku,
                on_message=self.on_message,
            )
            self.on_message(f"📝 [弹幕] 日志: {log_path}")

            if not self._running.is_set():
                self._danmaku_recorder.close()
                return

            self._danmaku_service = DanmakuService(
                self.room.room_id,
                on_event=self._danmaku_recorder.handle,
                on_message=self.on_message,
            )
            if not self._running.is_set():
                self._danmaku_recorder.close()
                return
            self._danmaku_service.run()
        except Exception as exc:
            self.on_message(f"❌ [弹幕] 启动失败: {exc}")
        finally:
            if self._danmaku_recorder is not None:
                self._danmaku_recorder.close()

    def _create_store(self) -> Optional[DanmakuStore]:
        config = DatabaseConfig.from_env()
        if not config.enabled:
            self.on_message("弹幕数据库未配置，弹幕仅写入本地日志")
            return None

        store = DanmakuStore(config)
        try:
            store.ensure_schema()
        except Exception as exc:
            store.close()
            self.on_message(f"弹幕数据库不可用，改为仅写日志: {exc}")
            return None

        self.on_message(f"弹幕数据库已连接: {config.describe()}")
        return store

    def _enqueue_segment(
        self, segment: SpeechSegment, record_file: Optional[str]
    ) -> None:
        self._audio_queue.put((segment, record_file))

    def _transcribe_loop(
        self,
        transcriber,
        transcript_filter: TranscriptFilter,
        clipper: Clipper,
    ) -> None:
        while True:
            item = self._audio_queue.get()
            if item is _STOP:
                break
            if not self._running.is_set():
                continue

            segment, record_file = item
            started = time.time()
            try:
                text = clean_transcript(transcriber.transcribe(segment.audio))
            except Exception as exc:
                self.on_message(f"❌ [转写] {exc}")
                continue
            cost = time.time() - started

            if not transcript_filter.accept(text):
                continue

            timestamp = time.strftime("%H:%M:%S")
            if any(keyword in text for keyword in TRIGGER_KEYWORDS):
                clipper.trigger(
                    segment.end,
                    self.room.streamer_name,
                    record_file=record_file,
                )

            line = f"[{timestamp}] (⚡️{cost:.2f}s) {text}"
            self._append_log(line)
            self.on_transcript(timestamp, text, cost)

    def _append_log(self, line: str) -> None:
        if self._log_path is None:
            return
        try:
            with self._log_path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError as exc:
            self.on_message(f"❌ [日志] 写入失败: {exc}")

    def _drain_audio_queue(self) -> None:
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                return
