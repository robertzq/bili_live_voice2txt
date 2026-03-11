import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import subprocess
import time
import sys
import json
import os
import numpy as np
import threading
import queue
import mlx_whisper
import warnings
import torch

warnings.filterwarnings("ignore")

# ================= 全局配置与模型 =================
# 模型路径
MODEL_PATH = "mlx-community/whisper-large-v3-mlx"

# 过滤词
IGNORE_KEYWORDS = [
    "by bwd6", "字幕by", "Amara.org", "优优独播剧场", "compared compared",
    "YoYo Television", "不吝点赞", "订阅我的频道", "Copyright"
]

# 全局变量
audio_queue = queue.Queue()
ui_queue = queue.Queue() # 用于子线程给 GUI 发消息
running_event = threading.Event() # 用于控制线程启停

# ================= VAD 与 核心逻辑 =================

print("🛠 正在加载 VAD 模型 (GUI启动中)...")
vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False,
                                  trust_repo=True)
(get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils
print("✅ VAD 模型加载完毕")

def check_voice_activity(audio_np):
    try:
        audio_tensor = torch.from_numpy(audio_np)
        speech_timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=16000)
        if not speech_timestamps:
            return False
        total_speech_time = sum([(i['end'] - i['start']) for i in speech_timestamps]) / 16000
        return total_speech_time > 0.5
    except Exception as e:
        print(f"❌ VAD Error: {e}")
        return False

def is_hallucination(text):
    for kw in IGNORE_KEYWORDS:
        if kw.lower() in text.lower():
            return True
    return False

# ================= 线程工作函数 =================

def run_stream_producer(room_id):
    """音频采集与视频静默录制线程"""
    # 动态生成本次录播的文件名
    record_filename = f"live_record_{room_id}_{int(time.time())}.mkv"
    
    streamlink_cmd = ["streamlink", "--twitch-disable-ads", f"https://live.bilibili.com/{room_id}", "best", "--stdout"]
    
    # === 核心修改区 ===
    # FFmpeg 一石二鸟魔法：
    # 1. -c copy record_filename : 把 streamlink 传来的流直接无损存入 mp4 文件
    # 2. -map 0:a:0 -vn -ac 1 -ar 16000 -f s16le - : 把第一条音频流单独抽出来转成 PCM 发给 stdout
    ffmpeg_cmd = [
        "ffmpeg", 
        "-i", "pipe:0", 
        "-c", "copy", record_filename,  # 录像输出路（零性能损耗）
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-" # 音频 STT 输出路
    ]
    
    process_streamlink = None
    process_ffmpeg = None
    
    try:
        msg_conn = f"🔗 [系统] 正在连接直播间: {room_id}..."
        ui_queue.put(msg_conn)
        print(msg_conn)
        
        # 提示录像文件保存在哪里
        msg_rec = f"📼 [系统] 视频后台直录中: {record_filename}"
        ui_queue.put(msg_rec)
        print(msg_rec)
        
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE)
        
        msg_ok = "🎧 [系统] 视频已开始落盘，音频流监听中..."
        ui_queue.put(msg_ok)
        print(msg_ok)
        
        chunk_seconds = 8
        chunk_size = 16000 * 2 * chunk_seconds
        
        while running_event.is_set():
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes: 
                ui_queue.put("⚠️ [系统] 直播流中断")
                print("⚠️ [系统] 直播流中断")
                break
            
            if not running_event.is_set(): break

            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            audio_queue.put(audio_data)
            
    except Exception as e:
        err_msg = f"❌ [错误] 采集流出错: {e}"
        ui_queue.put(err_msg)
        print(err_msg)
    finally:
        if process_ffmpeg: 
            try: process_ffmpeg.kill() 
            except: pass
        if process_streamlink: 
            try: process_streamlink.kill() 
            except: pass
        
        end_msg = "🛑 [系统] 采集与录制线程已退出"
        ui_queue.put(end_msg)
        print(end_msg)

def run_transcriber(streamer_name, room_id):
    """Whisper 转写线程"""
    last_text = ""
    # 生成日志文件名
    log_filename = f"{streamer_name}_{room_id}_mlx_log_{int(time.time())}.txt"
    
    log_msg = f"📝 [系统] 日志将保存在: {log_filename}"
    ui_queue.put(log_msg)
    print(log_msg)

    while running_event.is_set():
        try:
            # 1秒超时，以便能定期检查 running_event
            audio_data = audio_queue.get(timeout=1) 
        except queue.Empty:
            continue

        # === VAD 检测与终端回显 ===
        if not check_voice_activity(audio_data):
            # 终端打印小点，表示跳过静音
            print(f"🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper...")
            continue
            
        try:
            start_t = time.time()
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=MODEL_PATH,
                language="zh",
                verbose=False,
                no_speech_threshold=0.4, 
                logprob_threshold=-0.8
            )
            text = result["text"].strip()
            
            if len(text) > 1 and text != last_text and not is_hallucination(text):
                cost_time = time.time() - start_t
                timestamp = time.strftime("%H:%M:%S")
                
                # 1. 组装显示文本 (GUI 只看内容)
                display_text = f"[{timestamp}] {text}"
                ui_queue.put(display_text)
                
                # 2. 组装终端/日志文本 (带耗时信息)
                # 先换行，把之前的 VAD 点点断开
                full_log_line = f"[{timestamp}] (⚡️{cost_time:.2f}s) {text}"
                print(full_log_line)
                
                # 3. 写文件
                with open(log_filename, "a", encoding="utf-8") as f:
                    f.write(full_log_line.strip() + "\n")
                
                last_text = text
                
        except Exception as e:
            err_msg = f"❌ [错误] 转写出错: {e}"
            ui_queue.put(err_msg)
            print(err_msg)

# ================= GUI 主类 =================

class SubtitleApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bilibili Live Whisper (MLX版)")
        self.root.geometry("600x700")
        
        # --- 顶部配置区 ---
        config_frame = tk.Frame(root, pady=10)
        config_frame.pack(fill="x")
        
        tk.Label(config_frame, text="配置文件:").grid(row=0, column=0, padx=5)
        self.entry_config = tk.Entry(config_frame, width=30)
        self.entry_config.grid(row=0, column=1, padx=5)
        self.entry_config.insert(0, "ava.json") # 默认值
        
        tk.Button(config_frame, text="选择文件", command=self.load_config_btn).grid(row=0, column=2, padx=5)
        
        tk.Label(config_frame, text="房间号:").grid(row=1, column=0, padx=5, pady=5)
        self.entry_room = tk.Entry(config_frame, width=20)
        self.entry_room.grid(row=1, column=1, sticky="w", padx=5)
        
        tk.Label(config_frame, text="主播名:").grid(row=1, column=1, sticky="e", padx=5) # 放在同一格稍微挤一点
        self.entry_name = tk.Entry(config_frame, width=10)
        self.entry_name.grid(row=1, column=2, padx=5)
        
        # --- 控制按钮区 ---
        btn_frame = tk.Frame(root, pady=5)
        btn_frame.pack(fill="x")
        
        self.btn_start = tk.Button(btn_frame, text="▶ 启动监听", bg="#90EE90", command=self.start_processing, width=15, height=2)
        self.btn_start.pack(side="left", padx=20, expand=True)
        
        self.btn_stop = tk.Button(btn_frame, text="⏹ 停止", bg="#FFCCCB", command=self.stop_processing, width=15, height=2, state="disabled")
        self.btn_stop.pack(side="right", padx=20, expand=True)
        
        # --- 字幕显示区 ---
        self.text_area = scrolledtext.ScrolledText(root, font=("Menlo", 14), wrap="word", state="disabled")
        self.text_area.pack(expand=True, fill="both", padx=10, pady=10)
        
        # 配置 Tag 样式
        self.text_area.tag_config("sys", foreground="gray", font=("Arial", 10))
        self.text_area.tag_config("err", foreground="red")
        
        # --- 定时器 ---
        self.root.after(100, self.process_ui_queue)

    def load_config_btn(self):
        # 1. 弹出文件选择框
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*")]
        )
        
        # 2. 如果用户取消了选择，直接返回
        if not path:
            return

        # 3. 把选中的路径填入输入框（方便你查看）
        self.entry_config.delete(0, tk.END)
        self.entry_config.insert(0, path)

        # 4. 开始读取逻辑 (这部分和原来一样)
        if not os.path.exists(path):
            messagebox.showerror("错误", f"找不到文件: {path}")
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.entry_room.delete(0, tk.END)
                self.entry_room.insert(0, str(data.get("room_id", "")))
                self.entry_name.delete(0, tk.END)
                self.entry_name.insert(0, data.get("streamer_name", ""))
                
                msg = f"✅ 已加载配置文件: {path}"
                self.log_to_ui(msg, "sys")
                print(msg)
        except Exception as e:
            messagebox.showerror("错误", f"解析失败: {e}")

    def log_to_ui(self, message, tag=None):
        self.text_area.config(state="normal")
        self.text_area.insert(tk.END, message + "\n", tag)
        self.text_area.see(tk.END) # 自动滚动到底部
        self.text_area.config(state="disabled")

    def process_ui_queue(self):
        """定期检查队列并更新 UI"""
        while not ui_queue.empty():
            msg = ui_queue.get()
            if "❌" in msg:
                self.log_to_ui(msg, "err")
            elif "🔗" in msg or "🎧" in msg or "🛑" in msg or "📝" in msg or "✅" in msg or "⚠️" in msg:
                self.log_to_ui(msg, "sys")
            else:
                self.log_to_ui(msg) # 普通字幕
        
        self.root.after(100, self.process_ui_queue)

    def start_processing(self):
        room_id = self.entry_room.get().strip()
        name = self.entry_name.get().strip()
        
        if not room_id:
            messagebox.showwarning("提示", "请输入房间号")
            return
        
        if running_event.is_set():
            return

        running_event.set()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.log_to_ui("🚀 引擎启动...", "sys")
        print("🚀 [GUI] 用户点击了启动")
        
        # 启动生产者线程
        t_prod = threading.Thread(target=run_stream_producer, args=(room_id,), daemon=True)
        t_prod.start()
        
        # 启动消费者（转写）线程
        t_trans = threading.Thread(target=run_transcriber, args=(name, room_id), daemon=True)
        t_trans.start()

    def stop_processing(self):
        if not running_event.is_set():
            return
            
        self.log_to_ui("⏳ 正在停止...", "sys")
        print("⏳ [GUI] 用户点击了停止")
        running_event.clear() # 通知所有线程停止
        
        # 清空音频队列，防止阻塞
        with audio_queue.mutex:
            audio_queue.queue.clear()
            
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

if __name__ == "__main__":
    root = tk.Tk()
    app = SubtitleApp(root)
    # 捕获关闭窗口事件，强制退出
    def on_closing():
        running_event.clear()
        root.destroy()
        sys.exit(0)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    root.mainloop()