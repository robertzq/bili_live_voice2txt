import subprocess
import time
import sys
import numpy as np
import threading
import queue
import warnings
import torch
from faster_whisper import WhisperModel  # 👈 替换了 mlx_whisper

warnings.filterwarnings("ignore")

# ================= 配置区 =================
ROOM_ID = "24692760" 
# Windows 上模型会自动下载到 C:\Users\你的用户名\.cache\huggingface...
MODEL_SIZE = "large-v3" 
# =========================================

audio_queue = queue.Queue()
IGNORE_KEYWORDS = [
    "by bwd6", "字幕by", "Amara.org", "优优独播剧场", "compared compared",
    "YoYo Television", "不吝点赞", "订阅我的频道", "Copyright", "The following content"
]

# === 🎧 初始化 VAD 模型 (GPU 加速) ===
print("🛠 正在加载 VAD 模型...")
# 检查是否有 NVIDIA 显卡
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🖥️ 运行设备: {DEVICE} (RTX 3060 Ti 应该显示 cuda)")

vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False,
                                  trust_repo=True)
(get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils
vad_model.to(DEVICE) # 把 VAD 模型也丢到显卡上
print("✅ VAD 模型加载完毕")


# === 🚀 初始化 Whisper 模型 (Faster-Whisper) ===
print(f"🚀 正在加载 Faster-Whisper ({MODEL_SIZE})...")
# compute_type="float16" 是 3060Ti 的甜点精度，速度快且精度不损失
whisper_model = WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")
print("✅ Whisper 模型加载完毕")


def stream_producer(room_id):
    """生产者：负责抓取 B站 直播流"""
    print(f"🔗 [生产者] 正在连接直播间: {room_id} ...")
    
    # Windows 下 subprocess 调用命令，有时候需要 shell=True 或者完整的 exe 路径
    # 如果报错找不到命令，请确保 streamlink 和 ffmpeg 在环境变量里
    streamlink_cmd = ["streamlink", "--twitch-disable-ads", f"https://live.bilibili.com/{room_id}", "best", "--stdout"]
    ffmpeg_cmd = ["ffmpeg", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-"]
    
    try:
        # Windows 上可能需要 shell=True 来寻找命令，但通常不建议。如果跑不通，尝试改为 True
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE)
        print("🎧 [生产者] 音频流已建立，开始存入队列...")
        
        # 切片时间
        chunk_seconds = 8 
        chunk_size = 16000 * 2 * chunk_seconds
        
        while True:
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes: break
            
            # 转为 float32
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            audio_queue.put(audio_data)
            
    except Exception as e:
        print(f"生产者出错: {e}")
        print("⚠️ 提示：如果在 Windows 上报错找不到文件，请检查 FFmpeg 是否添加到了环境变量 Path 中")
    finally:
        if 'process_ffmpeg' in locals(): process_ffmpeg.kill()
        if 'process_streamlink' in locals(): process_streamlink.kill()

def is_hallucination(text):
    for kw in IGNORE_KEYWORDS:
        if kw.lower() in text.lower():
            return True
    return False

def check_voice_activity(audio_np, model):
    # numpy -> tensor -> gpu
    audio_tensor = torch.from_numpy(audio_np).to(DEVICE)
    
    # 获取语音时间戳
    speech_timestamps = get_speech_timestamps(audio_tensor, model, sampling_rate=16000)
    
    if not speech_timestamps:
        return False
    
    total_speech_time = sum([(i['end'] - i['start']) for i in speech_timestamps]) / 16000
    return total_speech_time > 0.5

def main():
    t = threading.Thread(target=stream_producer, args=(ROOM_ID,), daemon=True)
    t.start()
    
    log_file = f"{ROOM_ID}_win_log_{int(time.time())}.txt"
    last_text = ""
    
    print("🤖 [消费者] 引擎启动 (CUDA 加速中)...")

    while True:
        try:
            audio_data = audio_queue.get()
            
            # === 🛑 VAD 检测 ===
            if not check_voice_activity(audio_data, vad_model):
                print(f"🎵 [VAD] 静音/纯音乐，跳过...")
                continue 
            
            # === ⚡️ Whisper 转写 (CUDA) ===
            start_t = time.time()
            
            # faster-whisper 的调用方式略有不同
            # beam_size=5 是标准精度，如果想要更快可以设为 1
            segments, info = whisper_model.transcribe(
                audio_data, 
                beam_size=5, 
                language="zh",
                vad_filter=False, # 我们自己做了 VAD，所以这里关掉内置的
                no_speech_threshold=0.4,
                log_prob_threshold=-0.8
            )
            
            # faster-whisper 返回的是生成器，需要遍历出来
            text = "".join([segment.text for segment in segments]).strip()
            
            if len(text) > 1 and text != last_text and not is_hallucination(text):
                cost_time = time.time() - start_t
                timestamp = time.strftime("%H:%M:%S")
                line = f"[{timestamp}] (🚀{cost_time:.2f}s) {text}"
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