"""直播复盘图形界面：多选文件 / 拖拽 / 按直播间日期批量总结。"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

try:
    from tkinterdnd2 import DND_FILES

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - 没有 tkinterdnd2 时降级为普通选择文件
    DND_FILES = None
    DND_AVAILABLE = False


class SummarizeApp:
    def __init__(self, root, core, dnd_enabled: bool = False) -> None:
        self.root = root
        self.core = core
        self.dnd_enabled = dnd_enabled and DND_AVAILABLE
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.config_paths: dict[str, Path] = {}
        self._streaming = False

        self.root.title("直播复盘 · DeepSeek 内容运营分析师")
        self.root.geometry("900x860")
        self.root.minsize(760, 700)

        self._build_ui()
        self._refresh_configs()
        self._update_key_status()
        self.root.after(120, self._drain_queue)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        header = tk.Frame(self.root, padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="直播复盘与增长建议",
            font=("PingFang SC", 18, "bold"),
            anchor="w",
        ).pack(side="left")
        self.key_label = tk.Label(header, text="", anchor="e", fg="#64748b")
        self.key_label.pack(side="right")

        options = tk.Frame(self.root, padx=18)
        options.pack(fill="x")
        tk.Label(options, text="模型:").pack(side="left")
        self.model_var = tk.StringVar(value=self.core.DEFAULT_DEEPSEEK_MODEL)
        model_box = ttk.Combobox(
            options,
            textvariable=self.model_var,
            values=["deepseek-chat", "deepseek-reasoner"],
            width=20,
            state="readonly",
        )
        model_box.pack(side="left", padx=(6, 18))

        self.var_danmaku = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="加入弹幕材料", variable=self.var_danmaku).pack(
            side="left", padx=(0, 12)
        )
        self.var_db = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="统计数据库流水", variable=self.var_db).pack(
            side="left", padx=(0, 12)
        )
        self.var_png = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="渲染完整长图", variable=self.var_png).pack(side="left")
        self.var_brief = tk.BooleanVar(value=True)
        tk.Checkbutton(
            options, text="同时生成一页速览图", variable=self.var_brief
        ).pack(side="left", padx=(8, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=False, padx=18, pady=12)
        self._build_manual_tab()
        self._build_daily_tab()

        action = tk.Frame(self.root, padx=18)
        action.pack(fill="x")

        self.run_button = tk.Button(
            action,
            text="开始生成复盘报告",
            command=self._start,
            bg="#4f46e5",
            fg="white",
            activebackground="#4338ca",
            activeforeground="white",
            font=("PingFang SC", 13, "bold"),
            padx=18,
            pady=8,
            relief="flat",
        )
        self.run_button.pack(side="left")

        self.cancel_button = tk.Button(
            action,
            text="取消",
            command=self._cancel,
            state="disabled",
            padx=14,
            pady=8,
            relief="flat",
        )
        self.cancel_button.pack(side="left", padx=10)

        tk.Button(
            action,
            text="打开输出目录",
            command=self._open_output,
            padx=14,
            pady=8,
            relief="flat",
        ).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(
            self.root, textvariable=self.status_var, anchor="w", fg="#64748b", padx=18
        ).pack(fill="x", pady=(8, 0))

        log_frame = tk.Frame(self.root, padx=18, pady=10)
        log_frame.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(
            log_frame,
            height=16,
            font=("SF Mono", 11),
            wrap="word",
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
            relief="flat",
            padx=12,
            pady=10,
        )
        self.log.pack(fill="both", expand=True)

    def _build_manual_tab(self) -> None:
        tab = tk.Frame(self.notebook, padx=14, pady=12)
        self.notebook.add(tab, text="① 选择文件（可多选/拖拽）")

        hint = (
            "把 .txt 转写或弹幕文件拖到这里"
            if self.dnd_enabled
            else "当前未启用拖拽（pip install tkinterdnd2 后可拖拽），请点击「添加文件」"
        )
        self.drop_hint = tk.Label(
            tab, text=hint, fg="#6366f1", anchor="w", font=("PingFang SC", 12)
        )
        self.drop_hint.pack(fill="x", pady=(0, 8))

        list_frame = tk.Frame(tab)
        list_frame.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")
        self.file_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            yscrollcommand=scroll.set,
            height=9,
            font=("PingFang SC", 12),
        )
        self.file_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.file_list.yview)

        buttons = tk.Frame(tab)
        buttons.pack(fill="x", pady=(8, 0))
        tk.Button(buttons, text="添加文件", command=self._add_files, padx=12).pack(
            side="left"
        )
        tk.Button(buttons, text="移除选中", command=self._remove_selected, padx=12).pack(
            side="left", padx=8
        )
        tk.Button(buttons, text="清空", command=self._clear_files, padx=12).pack(
            side="left"
        )
        tk.Label(
            buttons,
            text="支持同时选择多个文件，会合并成一份报告。",
            fg="#94a3b8",
        ).pack(side="right")

        if self.dnd_enabled:
            for widget in (self.file_list, tab, self.drop_hint):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)

    def _build_daily_tab(self) -> None:
        tab = tk.Frame(self.notebook, padx=14, pady=12)
        self.notebook.add(tab, text="② 按直播间 + 日期总结")

        top = tk.Frame(tab)
        top.pack(fill="x")
        tk.Label(top, text="选择直播间配置（可多选，每个直播间单独生成）：").pack(
            side="left"
        )

        list_frame = tk.Frame(tab)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))
        scroll = tk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")
        self.config_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            yscrollcommand=scroll.set,
            height=7,
            font=("PingFang SC", 12),
        )
        self.config_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.config_list.yview)

        config_buttons = tk.Frame(tab)
        config_buttons.pack(fill="x", pady=(6, 0))
        tk.Button(config_buttons, text="刷新配置", command=self._refresh_configs, padx=12).pack(
            side="left"
        )
        tk.Button(config_buttons, text="全选", command=self._select_all_configs, padx=12).pack(
            side="left", padx=8
        )
        tk.Button(
            config_buttons, text="取消全选", command=self._clear_config_selection, padx=12
        ).pack(side="left")

        date_frame = tk.Frame(tab)
        date_frame.pack(fill="x", pady=(12, 0))
        tk.Label(date_frame, text="日期:").pack(side="left")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        tk.Entry(date_frame, textvariable=self.date_var, width=16).pack(
            side="left", padx=8
        )
        tk.Button(
            date_frame,
            text="今天",
            command=lambda: self.date_var.set(date.today().isoformat()),
            padx=10,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            date_frame,
            text="昨天",
            command=lambda: self.date_var.set(
                (date.today() - timedelta(days=1)).isoformat()
            ),
            padx=10,
        ).pack(side="left")
        tk.Label(
            date_frame,
            text="自动匹配 logs/ 里该直播间的转写与弹幕，并统计当天流水。",
            fg="#94a3b8",
        ).pack(side="left", padx=12)

    # -------------------------------------------------------------- helpers
    def _refresh_configs(self) -> None:
        self.config_list.delete(0, tk.END)
        self.config_paths.clear()
        if not self.core.CONFIG_DIR.is_dir():
            return
        for path in sorted(self.core.CONFIG_DIR.glob("*.json")):
            try:
                config = self.core.load_room_config(path)
                label = f"{path.stem}  ·  {config.streamer_name or '未命名'}  ({config.room_id})"
            except Exception:  # noqa: BLE001
                label = path.stem
            self.config_paths[label] = path
            self.config_list.insert(tk.END, label)

    def _update_key_status(self) -> None:
        key = self.core.load_api_key()
        if key:
            masked = f"{key[:7]}…{key[-4:]}" if len(key) > 12 else "已读取"
            self.key_label.config(text=f"DeepSeek Key: {masked}")
        else:
            self.key_label.config(text="DeepSeek Key: 未找到 tools/.dsSecretKey", fg="#dc2626")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择转写或弹幕日志",
            initialdir=str(self.core.LOG_DIR),
            filetypes=[("文本日志", "*.txt"), ("所有文件", "*.*")],
        )
        self._add_paths(paths)

    def _add_paths(self, paths) -> None:
        existing = set(self.file_list.get(0, tk.END))
        for raw in paths:
            path = str(Path(raw).expanduser())
            if path not in existing:
                self.file_list.insert(tk.END, path)
                existing.add(path)

    def _remove_selected(self) -> None:
        for index in reversed(self.file_list.curselection()):
            self.file_list.delete(index)

    def _clear_files(self) -> None:
        self.file_list.delete(0, tk.END)

    def _select_all_configs(self) -> None:
        self.config_list.selection_set(0, tk.END)

    def _clear_config_selection(self) -> None:
        self.config_list.selection_clear(0, tk.END)

    def _on_drop(self, event) -> None:
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:  # noqa: BLE001
            paths = [event.data]
        self._add_paths(paths)
        self.status_var.set(f"已添加 {len(paths)} 个文件")

    def _open_output(self) -> None:
        path = self.core.REPORT_DIR
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", str(exc))

    # ----------------------------------------------------------------- run
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "当前任务还没结束，请稍候。")
            return

        mode = self.notebook.index(self.notebook.select())
        api_key = self.core.load_api_key()
        if not api_key:
            messagebox.showerror(
                "缺少 API Key",
                "请把 DeepSeek API Key 写入 tools/.dsSecretKey，或设置环境变量 DEEPSEEK_API_KEY。",
            )
            return

        files: list[Path] = []
        configs = []
        day = None

        if mode == 0:
            raw_files = list(self.file_list.get(0, tk.END))
            if not raw_files:
                messagebox.showwarning("没有文件", "请先添加要总结的 .txt 日志。")
                return
            files = [Path(item) for item in raw_files]
        else:
            selected = [self.config_list.get(i) for i in self.config_list.curselection()]
            if not selected:
                messagebox.showwarning("没有选择直播间", "请至少选择一个直播间配置。")
                return
            try:
                day = self.core.parse_day(self.date_var.get())
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("日期格式错误", str(exc))
                return
            for label in selected:
                configs.append(self.core.load_room_config(self.config_paths[label]))

        options = self.core.SummarizeOptions(
            api_key=api_key,
            model=self.model_var.get() or self.core.DEFAULT_DEEPSEEK_MODEL,
            include_danmaku=self.var_danmaku.get(),
            include_db=self.var_db.get(),
            include_brief=self.var_brief.get(),
            cancel_event=self.cancel_event,
            stream_callback=lambda chunk: self.queue.put(("stream", chunk)),
            progress=lambda message: self.queue.put(("progress", message)),
        )

        self.cancel_event.clear()
        self._streaming = False
        self.run_button.config(state="disabled", bg="#94a3b8")
        self.cancel_button.config(state="normal")
        self.status_var.set("正在生成...")
        self._append_log("\n" + "=" * 60 + "\n")

        self.worker = threading.Thread(
            target=self._worker,
            args=(mode, files, configs, day, options, self.var_png.get()),
            daemon=True,
        )
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("正在取消...")
        self._append_log("\n⛔ 收到取消请求，将在当前请求结束后停止。\n")

    def _worker(self, mode, files, configs, day, options, render_png: bool) -> None:
        try:
            if mode == 0:
                report = self.core.summarize_manual(
                    files, day=day, options=options
                )
                streamer = ""
                for path in files:
                    entry = self.core.parse_log_name(Path(path))
                    if entry and entry.streamer:
                        streamer = entry.streamer
                        break
                if not streamer:
                    streamer = "主播"
                day_label = day.isoformat() if day else self._infer_day_label(files)
                self._finish(report, streamer, day_label, options, render_png)
            else:
                for config in configs:
                    if self.cancel_event.is_set():
                        raise self.core.Cancelled("已取消")
                    self.queue.put(
                        (
                            "progress",
                            f"\n===== {config.streamer_name or config.room_id} · {day} =====",
                        )
                    )
                    try:
                        report = self.core.summarize_room_day(config, day, options=options)
                    except self.core.NoLogError as exc:
                        self.queue.put(("progress", f"⚠️ {exc}"))
                        continue
                    streamer = config.streamer_name or config.room_id
                    assert day is not None
                    self._finish(report, streamer, day.isoformat(), options, render_png)
        except self.core.Cancelled:
            self.queue.put(("progress", "\n已取消。"))
        except self.core.SummarizeError as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.queue.put(("done", ""))

    def _infer_day_label(self, files) -> str:
        days = []
        for path in files:
            entry = self.core.parse_log_name(Path(path))
            if entry:
                days.append(entry.day)
        if not days:
            return date.today().isoformat()
        unique = set(days)
        return days[0].isoformat() if len(unique) == 1 else "多日合集"

    def _finish(
        self, report: str, streamer: str, day_label: str, options, render_png: bool
    ) -> None:
        brief_markdown = ""
        if render_png and options.include_brief:
            try:
                brief_markdown = self.core.build_brief_report(
                    report, streamer, day_label, options
                )
            except self.core.SummarizeError as exc:
                self.queue.put(("progress", f"⚠️ 一页速览生成失败：{exc}"))

        saved = self.core.save_report(
            report,
            streamer=streamer,
            day_label=day_label,
            render_png=render_png,
            brief_markdown=brief_markdown,
            progress=lambda message: self.queue.put(("progress", message)),
        )
        self.queue.put(
            (
                "saved",
                (
                    str(saved.markdown),
                    str(saved.image) if saved.image else "",
                    str(saved.brief_image) if saved.brief_image else "",
                ),
            )
        )

    # ---------------------------------------------------------------- queue
    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "stream":
                    if not self._streaming:
                        self._append_log("\n----- 报告生成中 -----\n")
                        self._streaming = True
                    self.log.insert(tk.END, payload)
                    self.log.see(tk.END)
                elif kind == "progress":
                    self._append_log(f"\n{payload}\n")
                    self.status_var.set(str(payload))
                elif kind == "saved":
                    md_path, png_path, brief_path = payload
                    self._append_log(f"\n📄 Markdown: {md_path}\n")
                    if png_path:
                        self._append_log(f"🖼️ 完整长图: {png_path}\n")
                    if brief_path:
                        self._append_log(f"🖼️ 一页速览图: {brief_path}\n")
                    self.status_var.set(f"完成：{brief_path or png_path or md_path}")
                elif kind == "error":
                    self._append_log(f"\n❌ {payload}\n")
                    self.status_var.set("生成失败")
                elif kind == "done":
                    self.run_button.config(state="normal", bg="#4f46e5")
                    self.cancel_button.config(state="disabled")
                    if self.status_var.get() in {"正在生成...", "正在取消..."}:
                        self.status_var.set("已结束")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _append_log(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)


def run_gui(core=None) -> int:
    if core is None:
        try:
            import summarize as core  # type: ignore
        except ImportError:
            from . import summarize as core  # type: ignore

    dnd_enabled = False
    if DND_AVAILABLE:
        try:
            from tkinterdnd2 import TkinterDnD

            root = TkinterDnD.Tk()
            dnd_enabled = True
        except Exception:  # noqa: BLE001
            root = tk.Tk()
    else:
        root = tk.Tk()

    try:
        app = SummarizeApp(root, core, dnd_enabled=dnd_enabled)
        app.root.mainloop()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui())
