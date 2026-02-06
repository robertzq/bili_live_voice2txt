import subprocess
import time
import sys
import numpy as np
from faster_whisper import WhisperModel
import warnings

warnings.filterwarnings("ignore")

# ================= 配置区 =================
ROOM_ID = "1950858520"
# 【核武器】直接上 Large-v3
# 它比 medium 慢一点，但在 M1/M2/M3 Pro/Max 上完全能跑实时
MODEL_SIZE = "large-v3" 
# =========================================

def main():
    print(f"🚀 正在加载 Whisper 核武器 ({MODEL_SIZE})... (这得花点时间下载)")
    
    # 尝试使用 float16 (精度更高)，如果报错或者太慢，再改回 int8
    # device="cpu" 在 Mac 上其实是调用了 Accelerate 框架，速度还可以
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"🔗 正在连接直播间: {ROOM_ID} ...")

    streamlink_cmd = [
        "streamlink",
        "--twitch-disable-ads",
        f"https://live.bilibili.com/{ROOM_ID}",
        "best",
        "--stdout"
    ]
    
    # 回归最原始的 FFmpeg，不要任何滤镜，原汁原味给模型听
    # 往往最高级的模型，只需要最朴素的食材
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-loglevel", "quiet", "-"
    ]

    try:
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        process_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=process_streamlink.stdout, stdout=subprocess.PIPE)
        
        print(f"🎧 接通成功！Large-v3 启动中...")
        
        # 5秒切片，保持灵敏
        chunk_seconds = 5
        chunk_size = 16000 * 2 * chunk_seconds 
        log_file = f"{ROOM_ID}_live_log_{int(time.time())}.txt"
        
        last_text = ""

        while True:
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes:
                break
            
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            
            # 极简参数
            segments, info = model.transcribe(
                audio_data, 
                beam_size=5, 
                language="zh",
                # 依然关掉上下文，防止BGM导致的死循环
                condition_on_previous_text=False,
                # 依然保留一点重复惩罚
                repetition_penalty=1.1,
                # 【关键】不再给 initial_prompt，防止它背课文
                initial_prompt=None
            )
            
            for segment in segments:
                text = segment.text.strip()
                
                # 严格过滤：如果大概率不是人话（比如是BGM），直接扔
                if segment.no_speech_prob > 0.4: continue 
                
                if len(text) > 1 and text != last_text:
                    timestamp = time.strftime("%H:%M:%S")
                    line = f"[{timestamp}] {text}"
                    print(line)
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                    last_text = text

    except KeyboardInterrupt:
        print(f"\n🛑 停止。")
    finally:
        if 'process_ffmpeg' in locals(): process_ffmpeg.kill()
        if 'process_streamlink' in locals(): process_streamlink.kill()

if __name__ == "__main__":
    main()