import tkinter as tk
from tkinter import scrolledtext, messagebox
import requests
import time
import sys
import os
import numpy as np
import threading
import queue
import mlx_whisper
import warnings
import torch
import pyaudio

warnings.filterwarnings("ignore")

# --- 强制屏蔽代理，确保连接本地 Qwen ---
os.environ['no_proxy'] = 'localhost,127.0.0.1'
if "http_proxy" in os.environ: del os.environ["http_proxy"]
if "https_proxy" in os.environ: del os.environ["https_proxy"]

# ================= 全局配置 =================
MODEL_PATH = "mlx-community/whisper-large-v3-mlx"
LLAMA_SERVER_URL = "http://127.0.0.1:8000/v1/chat/completions"
QWEN_MODEL = "Qwen3.5-35B-A3B" # 保持你当前的本地模型名称即可
MEETING_CONTEXT = (
    "This is an IT and automotive software engineering meeting. "
    "Glossary: Jira, AWS, API, Dashboard, CORS, Renault, minor issues, "
    "Grafana, OpenSearch, McLaren, Harley Davidson, ETAS, Bosch, GXI."
)
audio_queue = queue.Queue()
ui_queue = queue.Queue()
running_event = threading.Event()

# ================= VAD 加载 =================
print("🛠 正在加载 VAD 模型...")
vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', trust_repo=True)
(get_speech_timestamps, _, _, _, _) = utils

def check_voice_activity(audio_np):
    """检测一小段音频中是否包含人声"""
    try:
        # Silero VAD 推荐将数值限制在 -1 到 1 之间
        audio_tensor = torch.from_numpy(audio_np)
        speech_timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=16000)
        return len(speech_timestamps) > 0
    except Exception: 
        return False

# ================= Qwen 翻译逻辑 =================
def translate_via_qwen(text_en):
    """重构后的强限制性 Prompt，专治小模型旁白病"""
    system_prompt = (
        "你是一个专业的 IT 领域实时翻译引擎。请将输入的英文直接翻译为流畅的中文。\n"
        "严格遵守以下规则：\n"
        "1. 绝对不要解释、不要概括、不要添加诸如'老板说'、'似乎'、'发言中断'等旁白解说。\n"
        "2. 只输出翻译结果，不要任何多余的字。\n"
        "3. 如果输入是不完整的半句话，请直接字面直译，不要补充脑补。\n"
        "4. 保持专业术语（如 AWS, API, Dashboard, Jira, Cypher queries 等）准确。"
    )
    
    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text_en}
        ],
        "temperature": 0.1,  # 降低温度，减少发散思维，增强翻译的确定性
        "reasoning_effort": "none",  # 关闭思考过程，避免 35B 模型翻译延迟 8-10 秒
        "stream": False 
    }
    
    try:
        response = requests.post(LLAMA_SERVER_URL, json=payload, timeout=30, proxies={'http': None, 'https': None})
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[翻译连接失败: {e}]"
    return ""

# ================= 核心线程 =================

def audio_capture_thread():
    """动态 VAD 音频采集：基于静音断句，而非固定时间"""
    p = pyaudio.PyAudio()
    
    # 自动寻找 BlackHole 或聚合设备
    target_idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "BlackHole" in info['name'] or "Aggregate" in info['name']:
            target_idx = i
            break
    
    if target_idx is None:
        ui_queue.put("❌ 错误: 未找到 BlackHole 16ch 或聚合设备！")
        return

    # 每次仅读取 0.5 秒的数据进行判断
    CHUNK_DURATION = 0.5
    CHUNK_SAMPLES = int(16000 * CHUNK_DURATION)
    
    # 断句参数
    MAX_SILENCE_DURATION = 1.5  # 连续 1.5 秒没有声音，认为一句话结束
    MAX_SPEECH_DURATION = 15.0  # 防止长篇大论不断句，最长 15 秒强制切断
    
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                    input=True, input_device_index=target_idx, frames_per_buffer=CHUNK_SAMPLES)
    
    ui_queue.put(f"🎧 [系统] 已锁定设备: {p.get_device_info_by_index(target_idx)['name']}")
    ui_queue.put("🟢 [系统] 动态 VAD 断句模式已启动，等待语音输入...")

    audio_buffer = []
    silence_duration = 0.0
    speech_duration = 0.0

    while running_event.is_set():
        try:
            data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
            audio_np = np.frombuffer(data, np.int16).flatten().astype(np.float32) / 32768.0
            
            is_speech = check_voice_activity(audio_np)

            if is_speech:
                # 听到声音：追加到缓冲区，重置静音计时
                audio_buffer.append(audio_np)
                silence_duration = 0.0
                speech_duration += CHUNK_DURATION
            else:
                # 没听到声音，但缓冲区里有数据，说明上一句话还没结束完
                if len(audio_buffer) > 0:
                    audio_buffer.append(audio_np)
                    silence_duration += CHUNK_DURATION
                    speech_duration += CHUNK_DURATION

            # 触发打包送审的条件：
            # 1. 缓冲区有数据 且 累计静音超过设定值（一句话说完了）
            # 2. 或者一句话说得太长（超过 MAX_SPEECH_DURATION 强制断句防止延迟卡死）
            if len(audio_buffer) > 0 and (silence_duration >= MAX_SILENCE_DURATION or speech_duration >= MAX_SPEECH_DURATION):
                full_audio = np.concatenate(audio_buffer)
                # 丢给转写线程处理
                audio_queue.put(full_audio)
                
                # 重置状态
                audio_buffer = []
                silence_duration = 0.0
                speech_duration = 0.0

        except Exception as e:
            print(f"音频采集异常: {e}")
            break
            
    stream.stop_stream()
    stream.close()
    p.terminate()


def transcriber_thread():
    """接收完整句子进行转写和翻译"""
    log_filename = f"meeting_log_{int(time.time())}.txt"
    ui_queue.put(f"📝 日志: {log_filename}")

    # 将 Whisper 的初始化提示词设空，后续可考虑用它传递上文
    while running_event.is_set():
        try:
            # 等待从队列获取完整的音频段
            audio_data = audio_queue.get(timeout=1)
        except queue.Empty: 
            continue

        try:
            # 1. Whisper 转写 (英文)
            # 此时的 audio_data 是经过 VAD 裁切的完整语义段落
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=MODEL_PATH,
                language="en", 
                initial_prompt=MEETING_CONTEXT,
                verbose=False
            )
            text_en = result["text"].strip()

            # 过滤掉杂音导致的极短无意义文本
            if len(text_en) > 3:
                # 2. 调用 Qwen 翻译
                text_zh = translate_via_qwen(text_en)
                
                timestamp = time.strftime("%H:%M:%S")
                display_msg = f"[{timestamp}]\nENG: {text_en}\n中文: {text_zh}\n"
                ui_queue.put(display_msg)

                # 3. 记录日志
                with open(log_filename, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}]\nENG: {text_en}\n中文: {text_zh}\n{'-'*40}\n")
        except Exception as e:
            print(f"转写出错: {e}")

# ================= GUI 类 =================

class MeetingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Teams Meeting Translator (Dynamic VAD + Qwen)")
        self.root.geometry("800x800")
        
        # 控制区
        btn_frame = tk.Frame(root, pady=10)
        btn_frame.pack(fill="x")
        
        self.btn_start = tk.Button(btn_frame, text="▶ 开始实时翻译", bg="#90EE90", command=self.start, width=20, height=2)
        self.btn_start.pack(side="left", padx=50)
        
        self.btn_stop = tk.Button(btn_frame, text="⏹ 停止", bg="#FFCCCB", command=self.stop, width=20, height=2, state="disabled")
        self.btn_stop.pack(side="right", padx=50)
        
        # 显示区
        self.text_area = scrolledtext.ScrolledText(root, font=("Menlo", 13), wrap="word", state="disabled")
        self.text_area.pack(expand=True, fill="both", padx=10, pady=10)
        
        # 消息监听
        self.root.after(100, self.update_ui)

    def start(self):
        running_event.set()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        # 每次启动前清空旧列队数据
        while not audio_queue.empty(): audio_queue.get_nowait()
        
        threading.Thread(target=audio_capture_thread, daemon=True).start()
        threading.Thread(target=transcriber_thread, daemon=True).start()

    def stop(self):
        running_event.clear()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        ui_queue.put("🛑 [系统] 翻译已停止。")

    def update_ui(self):
        while not ui_queue.empty():
            msg = ui_queue.get()
            self.text_area.config(state="normal")
            self.text_area.insert(tk.END, msg + "\n" + "-"*40 + "\n")
            self.text_area.see(tk.END)
            self.text_area.config(state="disabled")
        self.root.after(100, self.update_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = MeetingApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (running_event.clear(), sys.exit(0)))
    root.mainloop()