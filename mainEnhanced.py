import subprocess
import time
import sys
import numpy as np
from faster_whisper import WhisperModel
import warnings

warnings.filterwarnings("ignore")

# ================= 配置区 =================
ROOM_ID = "1950858520"
# 建议：如果 M1/M2/M3 芯片，坚持用 medium，它懂的词多。
# 如果觉得慢，可以改回 small。
MODEL_SIZE = "medium" 
# =========================================

# 硬编码过滤表：如果包含这些，绝对是幻觉，直接杀掉
BLACKLIST = [
    "订阅", "频道", "点赞", "转发", "打赏", "谢谢观看", 
    "Amara.org", "字幕", "Copyright", "请忽略"
]

def main():
    print(f"🚀 正在加载 Whisper 模型 ({MODEL_SIZE})...")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"🔗 正在连接直播间: {ROOM_ID} ...")

    streamlink_cmd = [
        "streamlink",
        "--twitch-disable-ads",
        f"https://live.bilibili.com/{ROOM_ID}",
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
        
        print(f"🎧 接通成功！已开启[暴力防复读]模式...")
        
        # 回归 5秒 切片，保持灵敏度
        chunk_seconds = 5
        chunk_size = 16000 * 2 * chunk_seconds 
        log_file = f"{ROOM_ID}_live_log_{int(time.time())}.txt"
        
        last_text = ""

        while True:
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            if not in_bytes:
                break
            
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            
            # 核心参数调整：
            segments, info = model.transcribe(
                audio_data, 
                beam_size=5, 
                language="zh",
                
                # 1. 关掉上下文，每句话独立识别，防止死循环
                condition_on_previous_text=False,
                
                # 2. 【关键】重复惩罚：数值越大，越不敢说重复的话 (默认是1.0)
                repetition_penalty=1.2,
                
                # 3. 【关键】禁止 N-gram 重复：禁止连续出现3个相同的词组
                no_repeat_ngram_size=3,
                
                # 4. 温度回退：如果它卡住了，允许它尝试更“随机”的结果，而不是一直复读
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8],
                
                # 5. 不要提示词了，防止泄露
                initial_prompt=None 
            )
            
            for segment in segments:
                text = segment.text.strip()
                
                # === 过滤逻辑 ===
                
                # 1. 幻觉检测：如果模型觉得这句话只有 BGM (no_speech_prob 高)，丢弃
                if segment.no_speech_prob > 0.6:
                    continue

                # 2. 垃圾话硬过滤
                if any(word in text for word in BLACKLIST):
                    continue
                
                # 3. 长度和去重
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