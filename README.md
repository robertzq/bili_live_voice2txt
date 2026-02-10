# 🎙️ BiliLive-Whisper-MLX
用魔法打败魔法：基于 Apple Silicon (MLX) 的 B站直播间实时语音转文字监控系统

## 📖 项目简介
这是一个专为 Apple Silicon (M1/M2/M3/M4) 芯片优化的 Bilibili 直播间实时语音转写工具。

利用 Apple 最新的 mlx-whisper 框架，直接调用 Mac 的神经网络引擎 (Neural Engine)，实现毫秒级的语音转文字。同时集成了 Silero VAD (语音活动检测)，能够智能过滤直播间的背景音乐（BGM）和哼唱，只抓取有效对话。

## 核心功能：

⚡️ 极致性能：基于 MLX 框架，推理速度极快，几乎不占用 CPU/GPU 资源。

🎵 智能 VAD：自动识别并过滤纯音乐/唱歌片段，防止歌词干扰（VAD版）。

📹 实时流监听：直接通过 streamlink 抓取直播间音频流，无需开浏览器。

📝 自动日志：所有转写内容自动保存为带时间戳的 .txt 日志，方便后续数据分析。

## 🛠️ 环境要求
硬件：Mac (Apple Silicon 芯片推荐)

系统：macOS 13.0+

Python：3.10+

依赖工具：需要安装 ffmpeg

1. 安装 FFmpeg (必须)
程序需要使用 ffmpeg 处理音频流：

Bash
brew install ffmpeg
2. 安装 Python 依赖
推荐创建一个新的 Conda 环境：

Bash
conda create -n live-whisper python=3.10
conda activate live-whisper

# 安装核心依赖
pip install mlx-whisper streamlink numpy torch
(注：torch 仅用于 VAD 版本加载检测模型，MLX 版本本身不依赖 torch 推理)

# 🚀 使用指南
本项目提供了两个版本的脚本，适用于不同的场景。请根据需求选择运行。

修改目标直播间
打开脚本文件，修改顶部的 ROOM_ID 变量：

Python
ROOM_ID = "24692760"  # 替换为你想要监听的直播间 ID
🎯 模式 A：智能降噪版 (推荐)
文件名： main_vad.py (对应您提供的 VAD 代码)

特点： 集成了 Silero VAD 模型。
适用场景： 主播喜欢放 BGM、唱歌，或者环境嘈杂。VAD 会先判断“是不是人在说话”，如果是纯音乐直接跳过，极大减少乱码和幻觉。

Bash
python main_vad.py
⚡️ 模式 B：极速直连版
文件名： main_fast.py (对应您提供的原始代码)

特点： 没有任何前置过滤，所有音频直接送入 Whisper。
适用场景： 纯聊天直播间，背景安静。此模式延迟最低，但如果主播唱歌，会尝试强行翻译歌词。

Bash
python main_fast.py
# ⚙️ 参数调优 (进阶)
在代码中你可以调整以下参数来获得更好的效果：

chunk_seconds (切片时间):

默认为 6 秒 (VAD版) 或 10 秒 (极速版)。

调小：延迟更低，但长句容易被截断。

调大：语义更完整，但实时性降低。

MODEL_PATH:

默认为 mlx-community/whisper-large-v3-mlx (精度最高)。

如果需要极致速度，可换成 mlx-community/whisper-base-mlx。

# 📝 输出示例
程序运行时，控制台和生成的日志文件将显示如下格式：

Plaintext
[22:04:15] (⚡️0.32s) 兄弟们，今天这把我们要上分了。
[22:04:22] (⚡️0.28s) 感谢榜一大哥送来的火箭，老板大气！
[22:04:30] 🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper...
[22:04:36] 🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper...
[22:04:45] (⚡️0.41s) 刚才那是才艺展示哈，只要我不尴尬尴尬的就是你们。
# ⚠️ 常见问题
报错 NameError: name 'torch' is not defined

请确保在 main_vad.py 头部添加了 import torch。

报错 ffmpeg not found

请确保终端能直接运行 ffmpeg 命令，或检查环境变量。

转写出现重复或幻觉 (如 "字幕 by...")

已内置 IGNORE_KEYWORDS 列表过滤常见字幕组水印，可在代码中自行添加关键词。

Created with ❤️ for exploring the boundaries of Real-time AI.


## 🛠️ Windows 环境准备 (必读)
在运行代码前，你需要在 Windows 上配置好环境：

安装 FFmpeg (Windows 必坑点)

下载 FFmpeg (gyan.dev 等源)。

解压，将 bin 文件夹的路径（例如 C:\ffmpeg\bin）添加到系统的 环境变量 Path 中。

测试：打开 CMD 输入 ffmpeg -version，能看到版本号才算成功。

安装 CUDA 版 PyTorch (关键)
不要直接 pip install torch（那样会装成 CPU 版）。去 PyTorch 官网 复制安装命令，或者直接用下面这个（适配 CUDA 11.8/12.x）：

Bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
安装核心库

Bash
pip install faster-whisper streamlink numpy