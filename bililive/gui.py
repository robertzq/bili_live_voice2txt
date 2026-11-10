"""Tkinter 图形界面。"""

from __future__ import annotations

import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from typing import Optional

from .config import (
    CONFIG_DIR,
    RoomConfig,
    ensure_runtime_dirs,
    load_room_config,
    resolve_config_path,
)
from .danmaku import DanmakuEvent
from .pipeline import Pipeline


class SubtitleApp:
    def __init__(
        self,
        root: tk.Tk,
        backend: str = "auto",
        device: str = "auto",
        model: Optional[str] = None,
        danmaku: bool = True,
        autostart: bool = False,
        config_path: Optional[str] = None,
    ) -> None:
        self.root = root
        self.backend = backend
        self.device = device
        self.model = model
        self.danmaku_default = danmaku
        self.autostart = autostart
        self.initial_config = config_path
        self._ui_queue: queue.Queue = queue.Queue()
        self._pipeline: Optional[Pipeline] = None
        self._pipeline_token = 0
        self._closing = False

        self.root.title("Bilibili Live Whisper")
        self.root.geometry("640x720")
        self.root.minsize(520, 480)

        self._build_layout()
        self._load_default_config()
        self.root.after(100, self._drain_ui_queue)
        if self.autostart:
            self.root.after(800, self.start_processing)

    def _build_layout(self) -> None:
        config_frame = tk.Frame(self.root, pady=10)
        config_frame.pack(fill="x")

        tk.Label(config_frame, text="配置文件:").grid(row=0, column=0, padx=5)
        self.entry_config = tk.Entry(config_frame, width=34)
        self.entry_config.grid(row=0, column=1, padx=5)
        self.entry_config.insert(0, self.initial_config or "configs/jiojio.json")
        tk.Button(config_frame, text="选择文件", command=self.load_config_btn).grid(
            row=0, column=2, padx=5
        )

        input_frame = tk.Frame(self.root, pady=5)
        input_frame.pack(fill="x")

        tk.Label(input_frame, text="房间号:").pack(side="left", padx=10)
        self.entry_room = tk.Entry(input_frame, width=18)
        self.entry_room.pack(side="left")

        tk.Label(input_frame, text="主播名:").pack(side="left", padx=10)
        self.entry_name = tk.Entry(input_frame, width=18)
        self.entry_name.pack(side="left")

        self.var_danmaku = tk.BooleanVar(value=self.danmaku_default)
        tk.Checkbutton(
            input_frame,
            text="监听弹幕/礼物",
            variable=self.var_danmaku,
        ).pack(side="left", padx=12)

        button_frame = tk.Frame(self.root, pady=10)
        button_frame.pack(fill="x")

        self.btn_start = tk.Button(
            button_frame,
            text="▶ 启动监听",
            bg="#90EE90",
            command=self.start_processing,
            width=15,
            height=2,
        )
        self.btn_start.pack(side="left", padx=40, expand=True)

        self.btn_stop = tk.Button(
            button_frame,
            text="⏹ 停止",
            bg="#FFCCCB",
            command=self.stop_processing,
            width=15,
            height=2,
            state="disabled",
        )
        self.btn_stop.pack(side="right", padx=40, expand=True)

        font_family = "Menlo" if sys.platform == "darwin" else "Microsoft YaHei"
        self.text_area = scrolledtext.ScrolledText(
            self.root,
            font=(font_family, 13),
            wrap="word",
            state="disabled",
        )
        self.text_area.pack(expand=True, fill="both", padx=10, pady=10)
        self.text_area.tag_config("sys", foreground="gray", font=(font_family, 9))
        self.text_area.tag_config("err", foreground="#c0392b")
        self.text_area.tag_config("gift", foreground="#b8860b")
        self.text_area.tag_config("guard", foreground="#8e44ad")
        self.text_area.tag_config("super_chat", foreground="#d35400")
        self.text_area.tag_config("entry", foreground="#7f8c8d")

    def _load_default_config(self) -> None:
        path = resolve_config_path(self.entry_config.get().strip())
        if not path.is_file():
            return
        try:
            room = load_room_config(path)
        except (OSError, ValueError):
            return
        self.entry_room.delete(0, tk.END)
        self.entry_room.insert(0, room.room_id)
        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, room.streamer_name)

    def load_config_btn(self) -> None:
        path = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=str(CONFIG_DIR),
            filetypes=[("JSON Files", "*.json"), ("All Files", "*")],
        )
        if not path:
            return

        self.entry_config.delete(0, tk.END)
        self.entry_config.insert(0, path)
        try:
            room = load_room_config(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("错误", str(exc))
            return

        self.entry_room.delete(0, tk.END)
        self.entry_room.insert(0, room.room_id)
        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, room.streamer_name)
        self._log(f"✅ 已加载配置: {path}", "sys")

    def start_processing(self) -> None:
        room_id = self.entry_room.get().strip()
        streamer_name = self.entry_name.get().strip()
        if not room_id:
            messagebox.showwarning("提示", "请输入房间号")
            return
        if self._pipeline is not None and self._pipeline.running:
            return

        config_path = resolve_config_path(self.entry_config.get().strip())
        if config_path.is_file():
            try:
                room = load_room_config(config_path)
            except (OSError, ValueError) as exc:
                messagebox.showerror("错误", str(exc))
                return
        else:
            room = None

        if room is None or room.room_id != room_id:
            room = RoomConfig(room_id=room_id, streamer_name=streamer_name)

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log("🚀 引擎启动，首次运行需要加载模型，请稍候...", "sys")

        self._pipeline_token += 1
        token = self._pipeline_token
        self._pipeline = Pipeline(
            room=room,
            backend=self.backend,
            device=self.device,
            model=self.model,
            danmaku=self.var_danmaku.get(),
            on_message=lambda msg, t=token: self._handle_message(msg, t),
            on_transcript=lambda ts, text, cost, t=token: self._handle_transcript(
                ts, text, cost, t
            ),
            on_danmaku=lambda event, t=token: self._handle_danmaku(event, t),
        )
        self._pipeline.start()

    def _handle_message(self, message: str, token: int) -> None:
        print(message, flush=True)
        self._post("err" if "❌" in message else "sys", message, token)

    def _handle_transcript(
        self, timestamp: str, text: str, cost: float, token: int
    ) -> None:
        print(f"[{timestamp}] (⚡️{cost:.2f}s) {text}", flush=True)
        self._post("text", f"[{timestamp}] {text}", token)

    def _handle_danmaku(self, event: DanmakuEvent, token: int) -> None:
        text = event.display_text()
        print(text, flush=True)
        self._post(event.kind, text, token)

    def stop_processing(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def close(self) -> None:
        self._closing = True
        if self._pipeline is not None:
            self._pipeline.stop()
        self.root.destroy()

    def _post(self, kind: str, text: str, token: Optional[int] = None) -> None:
        self._ui_queue.put((kind, text, token))

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                kind, text, token = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            tag = kind if kind in {
                "sys",
                "err",
                "gift",
                "guard",
                "super_chat",
                "entry",
            } else None
            self._log(text, tag)
            if (
                "监听已停止" in text
                and token is not None
                and token == self._pipeline_token
            ):
                self.btn_start.config(state="normal")
                self.btn_stop.config(state="disabled")
        if not self._closing:
            self.root.after(100, self._drain_ui_queue)

    def _log(self, message: str, tag: Optional[str] = None) -> None:
        self.text_area.config(state="normal")
        self.text_area.insert(tk.END, message + "\n", tag)
        self.text_area.see(tk.END)
        self.text_area.config(state="disabled")


def run(
    backend: str = "auto",
    device: str = "auto",
    model: Optional[str] = None,
    danmaku: bool = True,
    autostart: bool = False,
    config_path: Optional[str] = None,
) -> None:
    ensure_runtime_dirs()
    root = tk.Tk()
    app = SubtitleApp(
        root,
        backend=backend,
        device=device,
        model=model,
        danmaku=danmaku,
        autostart=autostart,
        config_path=config_path,
    )
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
