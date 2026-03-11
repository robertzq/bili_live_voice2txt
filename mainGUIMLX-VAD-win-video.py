import os
# 解决多个库同时调用 OpenMP 导致的 DLL 冲突闪退问题
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import subprocess
import time
import sys
import json

import numpy as np
import threading
import queue
import warnings
import torch
from faster_whisper import WhisperModel

warnings.filterwarnings("ignore")

# ================= 配置区 =================
# 模型大小
MODEL_SIZE = "large-v3" 
# 过滤词
IGNORE_KEYWORDS = [
    "by bwd6", "字幕by", "Amara.org", "优优独播剧场", "compared compared",
    "YoYo Television", "不吝点赞", "订阅我的频道", "Copyright", "The following content"
]

# ================= 全局变量与队列 =================
audio_queue = queue.Queue()
ui_queue = queue.Queue()       # 子线程给主界面发消息
running_event = threading.Event() # 控制开始/停止

# === 新增：用于切片功能的全局变量 ===
current_record_file = ""
record_start_time = 0.0

# ================= 模型初始化 (启动时加载) =================

print("🛠 正在初始化环境，请稍候...")

# 1. 检查 CUDA
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🖥️ 运行设备: {DEVICE}")
if DEVICE == "cpu":
    print("⚠️ 警告: 未检测到 GPU，运行速度可能会很慢！")

# 2. 加载 VAD 模型
print("🛠 正在加载 VAD 模型...")
try:
    vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                      model='silero_vad',
                                      force_reload=False,
                                      trust_repo=True)
    (get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils
    vad_model.to(DEVICE)
    print("✅ VAD 模型加载完毕")
except Exception as e:
    print(f"❌ VAD 模型加载失败: {e}")
    sys.exit(1)

# 3. 加载 Faster-Whisper
print(f"🚀 正在加载 Faster-Whisper ({MODEL_SIZE})...")
try:
    # compute_type="float16" 是 N 卡甜点精度
    whisper_model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type="int8_float16")
    print("✅ Whisper 模型加载完毕")
except Exception as e:
    print(f"❌ Whisper 模型加载失败: {e}")
    sys.exit(1)


# ================= 核心处理逻辑 =================

def is_hallucination(text):
    for kw in IGNORE_KEYWORDS:
        if kw.lower() in text.lower():
            return True
    return False

def check_voice_activity(audio_np):
    try:
        # numpy -> tensor -> gpu
        audio_tensor = torch.from_numpy(audio_np).to(DEVICE)
        speech_timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=16000)
        if not speech_timestamps:
            return False
        total_speech_time = sum([(i['end'] - i['start']) for i in speech_timestamps]) / 16000
        return total_speech_time > 0.5
    except Exception as e:
        print(f"❌ VAD检测出错: {e}")
        return False

# ================= 线程任务 =================

def run_stream_producer(room_id):
    """ 音频采集与视频录制线程 (FFmpeg) """
    global current_record_file, record_start_time
    
    # 动态生成本次录播的文件名 (MKV格式)
    record_filename = f"live_record_{room_id}_{int(time.time())}.mkv"
    current_record_file = record_filename
    record_start_time = time.time()
    
    streamlink_cmd = ["streamlink", "--twitch-disable-ads", f"https://live.bilibili.com/{room_id}", "best", "--stdout"]
    
    # === FFmpeg 双重输出魔法 ===
    ffmpeg_cmd = [
        "ffmpeg", 
        "-i", "pipe:0", 
        "-c", "copy", record_filename,  # 第一路：录像输出
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-" # 第二路：音频流
    ]
    
    process_streamlink = None
    process_ffmpeg = None
    
    try:
        msg = f"🔗 [系统] 正在连接直播间: {room_id} ..."
        ui_queue.put(msg)
        print(msg) 
        
        msg_rec = f"📼 [系统] 视频后台直录中: {record_filename}"
        ui_queue.put(msg_rec)
        print(msg_rec)
        
        # Windows 下隐藏黑框
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, creationflags=creation_flags)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE, creationflags=creation_flags)
        
        msg_ok = "🎧 [系统] 直播流已接通，录像与监听开始..."
        ui_queue.put(msg_ok)
        print(msg_ok)
        
        chunk_seconds = 8 
        chunk_size = 16000 * 2 * chunk_seconds
        
        while running_event.is_set():
            # 阻塞读取
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes: 
                ui_queue.put("⚠️ [系统] 直播流数据中断")
                print("⚠️ [系统] 直播流数据中断")
                break
            
            if not running_event.is_set(): break

            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            audio_queue.put(audio_data)
            
    except Exception as e:
        err_msg = f"❌ [错误] 采集流异常: {e}"
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

def make_clip(trigger_time, streamer_name):
    """执行后台切片的独立函数 (Windows 防黑框版)"""
    global current_record_file, record_start_time
    
    if not current_record_file or not os.path.exists(current_record_file):
        return
        
    # 1. 计算相对时间
    current_video_duration = trigger_time - record_start_time
    start_sec = max(0, current_video_duration - 180)
    end_sec = current_video_duration + 5 
    
    clip_name = f"Clip_{streamer_name}_{time.strftime('%H%M%S')}_from_{int(start_sec)}s.mkv"
    
    # 2. 构造命令
    cmd = [
        "ffmpeg", "-y", "-v", "error", 
        "-i", current_record_file,
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-c", "copy",
        clip_name
    ]
    
    msg = f"✂️ [切片触发] 已截取过去3分钟画面 -> {clip_name}"
    ui_queue.put(msg)
    print(msg)
    
    # 3. Windows 下隐藏 FFmpeg 执行时的黑框
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW
        
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation_flags)

def run_transcriber(streamer_name, room_id):
    """ Whisper 转写线程 """
    last_text = ""
    log_file = f"{streamer_name}_{room_id}_win_cuda_log_{int(time.time())}.txt"
    
    log_msg = f"📝 [系统] 日志将写入: {log_file}"
    ui_queue.put(log_msg)
    print(log_msg)

    while running_event.is_set():
        try:
            # 1秒超时
            audio_data = audio_queue.get(timeout=1)
        except queue.Empty:
            continue
            
        # === VAD 检测与控制台输出 ===
        if not check_voice_activity(audio_data):
            print(f"🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper...")
            continue
            
        try:
            start_t = time.time()
            
            # Faster-Whisper 推理
            segments, info = whisper_model.transcribe(
                audio_data, 
                beam_size=5, 
                language="zh",
                vad_filter=False, 
                no_speech_threshold=0.4,
                log_prob_threshold=-0.8
            )
            
            text = "".join([segment.text for segment in segments]).strip()
            
            if len(text) > 1 and text != last_text and not is_hallucination(text):
                cost_time = time.time() - start_t
                timestamp = time.strftime("%H:%M:%S")
                
                # === 新增：关键词触发器 ===
                trigger_keywords = ["切片飞来", "切片飞莱", "贴片飞来"]
                if any(kw in text for kw in trigger_keywords):
                    make_clip(time.time(), streamer_name)
                
                # 1. 发送给 UI
                display_msg = f"[{timestamp}] {text}"
                ui_queue.put(display_msg)
                
                # 2. 发送给 控制台
                console_msg = f"[{timestamp}] (🚀{cost_time:.2f}s) {text}"
                print(console_msg)
                
                # 3. 写入文件
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(console_msg.strip() + "\n")
                
                last_text = text
                
        except Exception as e:
            err_msg = f"❌ [错误] 转写异常: {e}"
            ui_queue.put(err_msg)
            print(err_msg)

# ================= GUI 界面类 =================
# ... 后面的 WinSubtitleApp 类代码保持原样，没有任何修改，无需改动 ...
class WinSubtitleApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Bilibili Live Whisper (Win CUDA版) - {MODEL_SIZE}")
        self.root.geometry("640x720")
        
        # --- 配置区域 ---
        frame_top = tk.Frame(root, pady=10)
        frame_top.pack(fill="x")
        
        tk.Label(frame_top, text="配置文件:").grid(row=0, column=0, padx=5)
        self.entry_config = tk.Entry(frame_top, width=30)
        self.entry_config.grid(row=0, column=1, padx=5)
        self.entry_config.insert(0, "ava.json") 
        
        tk.Button(frame_top, text="选择文件", command=self.load_config_btn).grid(row=0, column=2, padx=5)
        
        frame_inputs = tk.Frame(root, pady=5)
        frame_inputs.pack(fill="x")
        
        tk.Label(frame_inputs, text="房间号:").pack(side="left", padx=10)
        self.entry_room = tk.Entry(frame_inputs, width=15)
        self.entry_room.pack(side="left")
        
        tk.Label(frame_inputs, text="主播名:").pack(side="left", padx=10)
        self.entry_name = tk.Entry(frame_inputs, width=15)
        self.entry_name.pack(side="left")
        
        # --- 按钮区域 ---
        frame_btns = tk.Frame(root, pady=10)
        frame_btns.pack(fill="x")
        
        self.btn_start = tk.Button(frame_btns, text="▶ 启动字幕", bg="#98FB98", command=self.start_processing, width=15, height=2, font=("微软雅黑", 10, "bold"))
        self.btn_start.pack(side="left", padx=40, expand=True)
        
        self.btn_stop = tk.Button(frame_btns, text="⏹ 停止连接", bg="#FFB6C1", command=self.stop_processing, width=15, height=2, font=("微软雅黑", 10, "bold"), state="disabled")
        self.btn_stop.pack(side="right", padx=40, expand=True)
        
        # --- 文本显示区域 ---
        self.text_area = scrolledtext.ScrolledText(root, font=("Microsoft YaHei", 12), wrap="word", state="disabled")
        self.text_area.pack(expand=True, fill="both", padx=10, pady=10)
        
        self.text_area.tag_config("sys", foreground="gray", font=("Microsoft YaHei", 9))
        self.text_area.tag_config("err", foreground="red")
        
        self.root.after(100, self.process_ui_queue)

    def log_to_ui(self, message, tag=None):
        self.text_area.config(state="normal")
        self.text_area.insert(tk.END, message + "\n", tag)
        self.text_area.see(tk.END) # 自动滚动到底部
        self.text_area.config(state="disabled")

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

    def log(self, msg, tag=None):
        self.text_area.config(state="normal")
        self.text_area.insert(tk.END, msg + "\n", tag)
        self.text_area.see(tk.END)
        self.text_area.config(state="disabled")

    def process_ui_queue(self):
        while not ui_queue.empty():
            msg = ui_queue.get()
            if "❌" in msg:
                self.log(msg, "err")
            elif "🔗" in msg or "🎧" in msg or "🛑" in msg or "📝" in msg or "✅" in msg or "⚠️" in msg:
                self.log(msg, "sys")
            else:
                self.log(msg) 
        
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
        self.log("🚀 引擎启动中...", "sys")
        print("🚀 [GUI] 用户点击了启动按钮")
        
        t1 = threading.Thread(target=run_stream_producer, args=(room_id,), daemon=True)
        t1.start()
        
        t2 = threading.Thread(target=run_transcriber, args=(name, room_id), daemon=True)
        t2.start()

    def stop_processing(self):
        if not running_event.is_set():
            return
        
        self.log("⏳ 正在断开连接...", "sys")
        print("⏳ [GUI] 用户点击了停止按钮")
        running_event.clear()
        
        with audio_queue.mutex:
            audio_queue.queue.clear()
            
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

if __name__ == "__main__":
    root = tk.Tk()
    app = WinSubtitleApp(root)
    def on_closing():
        running_event.clear()
        root.destroy()
        sys.exit(0)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    root.mainloop()