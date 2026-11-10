"""直播流采集：streamlink + ffmpeg 同时录像与输出 PCM。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .audio import (
    BLOCK_BYTES,
    BLOCK_SAMPLES,
    SAMPLE_RATE,
    SpeechSegment,
    SpeechSegmenter,
    pcm16_to_float32,
)
from .config import RECORD_DIR


class CaptureError(RuntimeError):
    """无法恢复的采集错误（例如缺少 ffmpeg/streamlink）。"""


class StreamCapture:
    """采集线程。

    ffmpeg 同时做两件事：
    1. 把原始流无损写入 .ts（边录边写，进程被杀也能保留）。
    2. 抽取第一条音轨转成 16k 单声道 PCM 输出到 stdout。
    """

    def __init__(
        self,
        room_id: str,
        segmenter: SpeechSegmenter,
        on_segment: Callable[[SpeechSegment, Optional[str]], None],
        on_message: Callable[[str], None],
        record_dir: str | Path = RECORD_DIR,
        reconnect_delay: float = 3.0,
    ) -> None:
        self.room_id = room_id
        self.segmenter = segmenter
        self.on_segment = on_segment
        self.on_message = on_message
        self.record_dir = Path(record_dir)
        self.reconnect_delay = reconnect_delay

        self._stop_event = threading.Event()
        self._process_lock = threading.Lock()
        self._processes: list[subprocess.Popen] = []
        self._record_path: Optional[str] = None

    @property
    def record_path(self) -> Optional[str]:
        return self._record_path

    def stop(self) -> None:
        self._stop_event.set()
        self._terminate_processes()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except CaptureError as exc:
                self.on_message(f"❌ [采集] {exc}")
                break
            except Exception as exc:
                if not self._stop_event.is_set():
                    self.on_message(f"❌ [采集] {exc}")

            if self._stop_event.is_set():
                break
            self.on_message(f"⚠️ [采集] 直播流已断开，{self.reconnect_delay:.0f} 秒后重连...")
            self._stop_event.wait(self.reconnect_delay)

    def _run_once(self) -> None:
        self._require_binary("streamlink")
        self._require_binary("ffmpeg")

        self.record_dir.mkdir(parents=True, exist_ok=True)
        ts_path = self.record_dir / f"live_record_{self.room_id}_{int(time.time())}.ts"
        self._record_path = str(ts_path)

        streamlink_cmd = [
            "streamlink",
            "--stdout",
            f"https://live.bilibili.com/{self.room_id}",
            "best",
        ]
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if proxy:
            streamlink_cmd.extend(["--http-proxy", proxy])
        ffmpeg_cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-c",
            "copy",
            "-f",
            "mpegts",
            "-flush_packets",
            "1",
            str(ts_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ]

        self.on_message(f"🔗 [采集] 正在连接直播间: {self.room_id} ...")
        self.on_message(f"📼 [采集] 后台录像: {ts_path.name}")

        byte_buffer = bytearray()
        elapsed = 0.0
        try:
            streamlink_proc = self._spawn(streamlink_cmd)
            ffmpeg_proc = self._spawn(ffmpeg_cmd, stdin=streamlink_proc.stdout)
            if streamlink_proc.stdout is not None:
                streamlink_proc.stdout.close()

            self.on_message("🎧 [采集] 直播流已接通，开始监听...")

            while not self._stop_event.is_set():
                chunk = ffmpeg_proc.stdout.read(BLOCK_BYTES)
                if not chunk:
                    if not self._stop_event.is_set():
                        self.on_message("⚠️ [采集] 直播流数据中断")
                    break

                byte_buffer.extend(chunk)
                while len(byte_buffer) >= BLOCK_BYTES:
                    block = bytes(byte_buffer[:BLOCK_BYTES])
                    del byte_buffer[:BLOCK_BYTES]
                    audio = pcm16_to_float32(block)
                    for segment in self.segmenter.push_block(audio, elapsed):
                        self.on_segment(segment, self._record_path)
                    elapsed += BLOCK_SAMPLES / SAMPLE_RATE
        finally:
            self._terminate_processes()
            for segment in self.segmenter.flush():
                self.on_segment(segment, self._record_path)
            try:
                self.segmenter.vad.reset()
            except Exception:
                pass
            self._record_path = self._remux(ts_path)

    def _spawn(self, command: list[str], **kwargs) -> subprocess.Popen:
        kwargs.setdefault("stdout", subprocess.PIPE)
        if sys.platform == "win32":
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        process = subprocess.Popen(command, **kwargs)
        with self._process_lock:
            self._processes.append(process)
        return process

    def _terminate_processes(self) -> None:
        with self._process_lock:
            processes = list(self._processes)
            self._processes.clear()
        for process in reversed(processes):
            if process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _remux(self, ts_path: Path) -> str:
        if not ts_path.is_file():
            return str(ts_path)
        if ts_path.stat().st_size == 0:
            try:
                ts_path.unlink()
            except OSError:
                pass
            return str(ts_path)

        mp4_path = ts_path.with_suffix(".mp4")
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(ts_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(mp4_path),
        ]
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(command, **kwargs)
            if result.returncode == 0 and mp4_path.is_file():
                try:
                    ts_path.unlink()
                except OSError:
                    pass
                self.on_message(f"✅ [录像] 已保存: {mp4_path.name}")
                return str(mp4_path)
        except Exception as exc:
            self.on_message(f"⚠️ [录像] 转封装 mp4 失败，保留 .ts: {exc}")

        self.on_message(f"✅ [录像] 已保存: {ts_path.name}")
        return str(ts_path)

    @staticmethod
    def _require_binary(name: str) -> None:
        if shutil.which(name) is None:
            raise CaptureError(
                f"找不到 {name}，请先安装并确保它在 PATH 中"
            )
