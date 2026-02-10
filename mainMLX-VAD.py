import subprocess
import time
import sys
import numpy as np
import threading
import queue
import mlx_whisper # 👈 关键：Apple 原生库
import warnings
import torch
warnings.filterwarnings("ignore")

# ================= 配置区 =================
ROOM_ID = "24692760" #1950858520 jiojio. 24692760 1946526637 1749934708 1879296633. 6838597. 32673043 1884963175 1755260650 24692760
# 这里使用的是 MLX 格式的 Large-v3，精度满血，速度飞快
MODEL_PATH = "mlx-community/whisper-large-v3-mlx"
# =========================================

# 队列（因为 MLX 处理极快，这里几乎永远是空的，不会积压）
audio_queue = queue.Queue()
IGNORE_KEYWORDS = [
    "by bwd6", "字幕by", "Amara.org", "优优独播剧场", "compared compared",
    "YoYo Television", "不吝点赞", "订阅我的频道", "Copyright"
]
# === 🎧 初始化 VAD 模型 ===
print("🛠 正在加载 VAD 模型...")
# 加载 silero VAD，非常轻量，几秒钟就好
vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False,
                                  trust_repo=True)
(get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils
print("✅ VAD 模型加载完毕")
def stream_producer(room_id):
    """生产者：负责抓取 B站 直播流"""
    print(f"🔗 [生产者] 正在连接直播间: {room_id} ...")
    streamlink_cmd = ["streamlink", "--twitch-disable-ads", f"https://live.bilibili.com/{room_id}", "best", "--stdout"]
    ffmpeg_cmd = ["ffmpeg", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-"]
    
    try:
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE)
        print("🎧 [生产者] 音频流已建立，开始存入队列...")
        
        # 💡 建议：把切片改小一点，比如 5-6秒。
        # 10秒太长，万一前5秒唱歌，后5秒说话，VAD可能会因为有人声而把整段放过去
        chunk_seconds = 8 
        chunk_size = 16000 * 2 * chunk_seconds
        
        while True:
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes: break
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            audio_queue.put(audio_data)
    except Exception as e:
        print(f"生产者出错: {e}")
    finally:
        if 'process_ffmpeg' in locals(): process_ffmpeg.kill()
        if 'process_streamlink' in locals(): process_streamlink.kill()

def is_hallucination(text):
    for kw in IGNORE_KEYWORDS:
        if kw.lower() in text.lower():
            return True
    return False

def check_voice_activity(audio_np, model):
    # Silero 需要 Tensor 格式
    audio_tensor = torch.from_numpy(audio_np)
    # 获取语音时间戳
    speech_timestamps = get_speech_timestamps(audio_tensor, model, sampling_rate=16000)
    
    # 如果检测到的语音片段总时长太短（比如少于 0.5秒），就认为是噪音或误触
    if not speech_timestamps:
        return False
    
    total_speech_time = sum([(i['end'] - i['start']) for i in speech_timestamps]) / 16000
    # 阈值：至少要有 0.5 秒的人声才算数
    return total_speech_time > 0.5

def main():
    print(f"🚀 [消费者] 正在加载 MLX Large-v3 模型...")
    t = threading.Thread(target=stream_producer, args=(ROOM_ID,), daemon=True)
    t.start()
    
    log_file = f"{ROOM_ID}_mlx_log_{int(time.time())}.txt"
    last_text = ""
    
    print("🤖 [消费者] 引擎启动...")

    while True:
        try:
            audio_data = audio_queue.get()
            
            # === 🛑 第一道关卡：VAD 检测 ===
            # 如果这一段音频里没有有效人声，直接跳过！
            if not check_voice_activity(audio_data, vad_model):
                print(f"🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper...")
                continue # 直接进下一次循环，不跑 Whisper
            
           
            # === ⚡️ 第二道关卡：Whisper ===
            start_t = time.time()
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=MODEL_PATH,
                language="zh",
                verbose=False,
                
                # 稍微调高一点无声阈值
                no_speech_threshold=0.4, 
                logprob_threshold=-0.8
            )
            
            text = result["text"].strip()
            
            if len(text) > 1 and text != last_text and not is_hallucination(text):
                cost_time = time.time() - start_t
                timestamp = time.strftime("%H:%M:%S")
                line = f"[{timestamp}] (⚡️{cost_time:.2f}s) {text}"
                print(line)
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                last_text = text
                        
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()