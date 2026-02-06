import subprocess
import time
import sys
import numpy as np
from faster_whisper import WhisperModel

# ================= 配置区 =================
ROOM_ID = "1950858520" # 你的直播间 ID。22334596
MODEL_SIZE = "small"   # M芯片推荐 small
# =========================================

def main():
    # 1. 加载模型
    print(f"🚀 正在加载 Whisper 模型 ({MODEL_SIZE})...")
    # M芯片 Mac 使用 int8 效率最高
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"🔗 正在连接直播间: {ROOM_ID} ...")

    # 2. 启动 Streamlink (生产者)
    # --stdout 参数让它把视频流直接吐到标准输出，而不是写文件
    # streamlink 会自动处理 B站的 Header 和 Cookie 验证
    streamlink_cmd = [
        "streamlink",
        "--twitch-disable-ads", # 习惯性加上，虽然是B站
        f"https://live.bilibili.com/{ROOM_ID}",
        "best",
        "--stdout"
    ]
    
    # 3. 启动 FFmpeg (消费者 1)
    # -i pipe:0 表示从标准输入读取数据
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", "pipe:0",     # 关键修改：从管道读取
        "-vn",              # 不要视频
        "-ac", "1",         # 单声道
        "-ar", "16000",     # 采样率
        "-f", "s16le",      # 格式
        "-loglevel", "quiet", 
        "-"                 # 输出到标准输出
    ]

    try:
        # 核心逻辑：用 Python 把两个进程串起来
        # Popen 1: 启动 streamlink
        process_streamlink = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE)
        
        # Popen 2: 启动 ffmpeg，它的 stdin 连着 streamlink 的 stdout
        process_ffmpeg = subprocess.Popen(
            ffmpeg_cmd, 
            stdin=process_streamlink.stdout, 
            stdout=subprocess.PIPE
        )
        
        # 允许 streamlink 进程虽然把输出给了 ffmpeg，但如果 streamlink 挂了我们要知道
        # (可选：关闭 streamlink 的 stdout 句柄，避免资源泄漏，Python GC通常会处理)
        
        print("🎧 直播流已接通！开始转写... (按 Ctrl+C 停止)")
        
        # 4. 循环读取 (消费者 2)
        chunk_seconds = 5
        chunk_size = 16000 * 2 * chunk_seconds 
        log_file = f"{ROOM_ID}_live_log_{int(time.time())}.txt"

        while True:
            # 从 ffmpeg 读取处理好的音频
            in_bytes = process_ffmpeg.stdout.read(chunk_size)
            
            if not in_bytes:
                # 如果读不到数据，说明流断了
                if process_streamlink.poll() is not None:
                    print("⚠️ Streamlink 进程已退出，可能是直播结束或房间号错误。")
                    # 打印一下错误信息以便调试
                    # print(process_streamlink.stderr.read()) 
                break
            
            # 正常处理逻辑
            audio_data = np.frombuffer(in_bytes, np.int16).flatten().astype(np.float32) / 32768.0
            segments, info = model.transcribe(audio_data, beam_size=5, language="zh")
            
            for segment in segments:
                text = segment.text.strip()
                if len(text) > 1: # 过滤杂音
                    timestamp = time.strftime("%H:%M:%S")
                    line = f"[{timestamp}] {text}"
                    print(line)
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(line + "\n")

    except KeyboardInterrupt:
        print(f"\n🛑 停止。")
    finally:
        # 清理战场
        if 'process_ffmpeg' in locals(): process_ffmpeg.kill()
        if 'process_streamlink' in locals(): process_streamlink.kill()

if __name__ == "__main__":
    main()