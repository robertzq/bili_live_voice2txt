# 🎙️ BiliLive-Whisper-GUI
用魔法打败魔法：B站直播间实时语音转文字监控系统 (GUI 版)

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-macOS%20(MLX)%20|%20Windows%20(CUDA)-green.svg)
![License](https://img.shields.io/badge/License-MIT-orange.svg)

## 📖 项目简介
这是一个专为 **Bilibili 直播间** 设计的实时语音转写工具。它利用本地强大的 AI 模型，直接监听直播流并将语音转换为文字，支持 **GUI 图形界面** 操作。

项目分为两个版本，分别针对不同硬件进行了极致优化：
1.  **🍎 macOS 版**：基于 **MLX** 框架，专为 Apple Silicon (M1/M2/M3/M4) 优化，调用神经网络引擎，极低功耗。
2.  **🪟 Windows 版**：基于 **Faster-Whisper** + **CUDA**，利用 NVIDIA 显卡加速，推理速度极快。

## ✨ 核心功能

* **🖥️ 图形化界面 (GUI)**：告别黑框框，通过可视化的 Tkinter 界面管理配置、启动监听、查看字幕。
* **🎵 智能 VAD (语音活动检测)**：集成 Silero VAD，精准识别并过滤直播间的 **BGM 背景音乐** 和 **哼唱**，只转写有效对话，防止歌词幻觉。
* **📄 JSON 配置管理**：通过 JSON 文件快速加载不同主播的房间号和配置，一键切换。
* **👀 双重输出模式**：
    * **GUI 界面**：清爽展示实时字幕，适合阅读。
    * **控制台**：显示硬核监控数据（VAD 过滤状态、推理延迟 ⚡️0.xxs、详细日志）。
* **📹 无头监听**：利用 `streamlink` 直接抓取音频流，无需打开浏览器，节省系统资源。
* **📝 自动归档**：所有转写内容自动保存为带时间戳的 `.txt` 日志，文件名包含主播名与时间，方便回溯。

---

## 🛠️ 环境要求与安装

请根据你的操作系统选择对应的安装步骤。

### 🍎 1. macOS (Apple Silicon)
**硬件要求**：M1/M2/M3/M4 芯片 Mac。

1.  **安装 FFmpeg** (处理音频流必须)：
    ```bash
    brew install ffmpeg
    ```
2.  **安装 Python 依赖**：
    推荐使用 Conda 环境 (Python 3.10+)：
    ```bash
    conda create -n live-whisper python=3.10
    conda activate live-whisper
    
    # 安装 MLX 相关依赖
    pip install mlx-whisper streamlink numpy torch
    # 注意：这里的 torch 仅用于 VAD 模型加载，推理主要靠 MLX
    ```

### 🪟 2. Windows (NVIDIA GPU)
**硬件要求**：NVIDIA 显卡 (建议 RTX 3060 或以上)，已安装显卡驱动。

1.  **安装 FFmpeg (必坑点)**：
    * 下载 FFmpeg (如 gyan.dev)。
    * 解压并将 `bin` 文件夹路径 (例如 `C:\ffmpeg\bin`) 添加到系统 **环境变量 Path** 中。
    * 测试：CMD 输入 `ffmpeg -version` 能看到版本号。

2.  **安装 CUDA 版 PyTorch**：
    * **不要**直接 `pip install torch` (那是 CPU 版)。
    * 请运行以下命令安装 CUDA 12.1 版本 (根据你的驱动调整)：
    ```bash
    pip install torch torchvision torchaudio --index-url [https://download.pytorch.org/whl/cu121](https://download.pytorch.org/whl/cu121)
    ```

3.  **安装核心依赖**：
    ```bash
    pip install faster-whisper streamlink numpy
    ```

---

## ⚙️ 配置文件说明

项目使用 `.json` 文件来管理直播间信息。请在项目根目录下创建一个 json 文件（例如 `ava.json`）：

```json
{
  "room_id": "24692760",
  "streamer_name": "向晚Ava"
}
```
## 🚀 使用指南
启动程序
根据你的系统运行对应的脚本：

macOS 用户:
```
Bash
python mainGUIMLX-VAD.py
```
Windows 用户:
```
Bash
python mainGUIMLX-VAD-win.py
```
## 操作流程
程序启动后，会先在控制台加载 VAD 和 Whisper 模型（需等待几秒）。

GUI 窗口弹出后，点击 "选择文件" 按钮。

选择你创建的 .json 配置文件（如 ava.json）。

点击 "▶ 启动监听"。

## 观察效果：

GUI 文本框会显示实时转写出的文字。

后台控制台会打印详细的延迟数据和 VAD 过滤提示。

## 📝 输出示例
GUI 界面 (清爽版)
控制台/日志文件 (硬核版)
```
Plaintext
🔗 [系统] 正在连接直播间: 24692760...
🎧 [系统] 音频流已建立，开始监听...
🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper... (⚡️0.32s) 兄弟们，今天这把我们要上分了。 (⚡️0.28s) 感谢榜一大哥送来的火箭，老板大气！
🎵 [VAD] 检测到纯音乐/静音，跳过 Whisper... (⚡️0.41s) 刚才那是才艺展示哈。
```
### ⚠️ 常见问题
Q1: macOS 上点击“选择文件”后程序闪退？

原因: macOS 的 Tkinter 对复杂文件后缀支持不佳。

解决: 确保代码中 filedialog 的 filetypes 设置为 *.json 而不是 *room.json。

Q2: Windows 上报错 ffmpeg not found？

原因: 环境变量没配好。

解决: 检查系统 Path 变量，或者重启电脑使环境变量生效。

Q3: 为什么一直是 "🎵 [VAD] 检测到纯音乐..."？

原因: 主播可能没说话，或者背景音乐声音大过人声。

解决: 这是正常现象，VAD 正在帮你过滤无效信息，节省计算资源。

Q4: 出现重复字幕或幻觉 (如 "字幕 by...")？

解决: 代码中已内置 IGNORE_KEYWORDS 列表，你可以自行在代码顶部添加需要过滤的关键词。