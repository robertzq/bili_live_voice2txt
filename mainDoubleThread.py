import subprocess
import time
import sys
import numpy as np
import threading
import queue
import mlx_whisper # 👈 关键：Apple 原生库
import warnings

warnings.filterwarnings("ignore")

# ================= 配置区 =================
ROOM_ID = "24692760" #1950858520
# 这里使用的是 MLX 格式的 Large-v3，精度满血，速度飞快
MODEL_PATH = "mlx-community/whisper-large-v3-mlx"
# =========================================

# 队列（因为 MLX 处理极快，这里几乎永远是空的，不会积压）
audio_queue = queue.Queue()

def stream_producer(room_id):
    """生产者：负责抓取 B站 直播流"""
    print(f"🔗 [生产者] 正在连接直播间: {room_id} ...")

    streamlink_cmd = [
        "streamlink",
        "--twitch-disable-ads",
        f"https://live.bilibili.com/{room_id}",
        "best",
        "--stdout"
    ]
    
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-"
    ]

    try:
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE)
        
        print("🎧 [生产者] 音频流已建立，开始存入队列...")
        
        # 5秒切片
        chunk_seconds = 10
        chunk_size = 16000 * 2 * chunk_seconds
        
        while True:
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes:
                break
            
            # 转换为 float32
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            audio_queue.put(audio_data)

    except Exception as e:
        print(f"生产者出错: {e}")
    finally:
        if 'process_ffmpeg' in locals(): process_ffmpeg.kill()
        if 'process_streamlink' in locals(): process_streamlink.kill()

def main():
    print(f"🚀 [消费者] 正在加载 MLX Large-v3 模型 (第一次需下载)...")
    
    # 预热一下，防止第一次推理卡顿
    # mlx_whisper 没有显式的 load_model，它是即用即载，但在 M 芯片上速度极快
    
    # 2. 启动生产者
    t = threading.Thread(target=stream_producer, args=(ROOM_ID,), daemon=True)
    t.start()
    
    log_file = f"{ROOM_ID}_mlx_log_{int(time.time())}.txt"
    last_text = ""
    
    print("🤖 [消费者] 引擎启动 (Neural Engine 加速中)...")

    while True:
        try:
            audio_data = audio_queue.get()
            start_t = time.time()
            
            # === MLX 核心转写 ===
            # word_timestamps=False 关掉词级时间戳能更快一点
            # language="zh" 强制中文
            result = mlx_whisper.transcribe(
                audio_data, 
                path_or_hf_repo=MODEL_PATH,
                language="zh",
                verbose=False
            )
            
            text = result["text"].strip()
            
            # 输出逻辑
            if len(text) > 1 and text != last_text:
                cost_time = time.time() - start_t
                timestamp = time.strftime("%H:%M:%S")
                
                # 看看这个 cost_time，绝对会让你震惊 (通常 < 0.5s)
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