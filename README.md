# 🎙️ BiliLive-Whisper

B 站直播间实时语音转文字监控：本地 Whisper 转写、VAD 过滤 BGM、后台录像、关键词自动切片，并可同步监听弹幕/礼物/上舰/SC 写入 MySQL。

- **macOS**：MLX Whisper，Apple Silicon 优化
- **Windows / Linux**：Faster-Whisper + CUDA（无 GPU 自动回退 CPU）

## 功能

- 无头抓流：`streamlink` + `ffmpeg`，不需要打开浏览器
- 智能 VAD：Silero VAD 按静音断句，过滤背景音乐与静音
- 实时字幕 GUI + 带时间戳的日志归档
- 后台无损录像（`.ts` 实时写入，结束后自动封装为 `.mp4`）
- 断线自动重连
- 说出关键词（默认「切片飞来」）自动截取前 3 分钟画面
- 弹幕监听：弹幕、礼物、上舰、醒目留言、进场，显示到 GUI 并单独写日志
- MySQL 入库：表结构与 `bili_live_monitor` 兼容，便于后续 SQL 分析
- 可选：本地大模型直播复盘、会议实时英译中

## 目录结构

```
bili-whisper/
├── run.py                 # 统一入口（GUI）
├── bililive/
│   ├── config.py          # 路径、常量、房间配置
│   ├── text.py            # 幻觉过滤与字幕去重
│   ├── audio.py           # PCM 转换、VAD 动态断句
│   ├── vad.py             # Silero VAD 封装（延迟加载）
│   ├── backends.py        # MLX / Faster-Whisper 后端
│   ├── capture.py         # streamlink + ffmpeg 采集与录像
│   ├── clipper.py         # 关键词切片
│   ├── bili_auth.py       # B 站凭证 / 扫码登录
│   ├── danmaku.py         # 弹幕协议、事件解析、日志
│   ├── database.py        # MySQL 连接与入库
│   ├── pipeline.py        # 音频 + 弹幕流水线
│   └── gui.py             # Tkinter 界面
├── configs/               # 直播间配置
├── records/               # 录像与切片
├── logs/                  # 转写日志与弹幕日志
├── reports/               # 复盘报告（Markdown + PNG，自动生成）
├── sql/schema.sql         # MySQL 建表脚本
├── .env.example           # 数据库/凭证配置模板
├── tools/
│   ├── meeting_translate.py  # 会议实时英译中（原 catchTranslate.py）
│   ├── summarize.py          # 直播复盘 CLI / 入口
│   ├── summarize_gui.py      # 复盘图形界面（多选 / 拖拽 / 按天）
│   └── report_render.py      # Markdown + Mermaid 渲染成 PNG
└── tests/                 # 单元测试
```

## 安装

### 🍎 macOS (Apple Silicon)

```bash
brew install ffmpeg streamlink
pip install -r requirements-macos.txt
```

### 🪟 Windows

1. 安装 FFmpeg 并把 `bin` 目录加入 `PATH`（`ffmpeg -version` 能运行）。
2. 安装 CUDA 版 PyTorch（按你的驱动选择版本）：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

3. 安装其余依赖：

```bash
pip install -r requirements-windows.txt
pip install streamlink
```

> 注意：不要在 Windows 上直接 `pip install torch`（会装成 CPU 版）。
> 没有 NVIDIA GPU 时会自动回退 CPU + int8，速度较慢但能运行。

### 弹幕登录（必须）

B 站现在要求登录状态才能收到完整弹幕包。任选一种方式：

```bash
# 方式一：终端扫码，凭证保存到 .bili_credential.json
python run.py --login

# 方式二：把浏览器请求头里的 cookie 原文保存为项目根目录的 bilicookie.txt
#         （文件已被 gitignore）
```

凭证也可以直接写在 `.env` 里（`BILI_SESSDATA`、`BILI_JCT`、`BILI_DEDEUSERID`、`BILI_BUVID3`、`BILI_BUVID4`）。

### 公司代理

如果 `HTTPS_PROXY` 已设置，程序会自动：

- 让 `bilibili-api` 的 HTTP 与 WebSocket 都走 `HTTPS_PROXY`（给 aiohttp 的 `ws_connect` 显式注入代理）
- 给 `streamlink` 追加 `--http-proxy`

登录凭证失效时，弹幕会自动禁用并提示重新执行 `python run.py --login`，不影响语音转写。

### MySQL（可选）

不配置数据库时，弹幕仍会显示在 GUI 并写入 `logs/`，只是不入库。

```bash
cp .env.example .env
# 编辑 .env 填写 DB_HOST / DB_USER / DB_PASSWORD / DB_DATABASE

mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS bili_live_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p bili_live_db < sql/schema.sql
```

程序启动时也会尝试自动建库/建表（`DB_AUTO_CREATE=1`），数据库不可用时会自动降级为只写日志。

## 使用

```bash
python run.py
```

启动后：

1. 点击「选择文件」，选择 `configs/` 下的配置（也可以直接输入房间号）。
2. 勾选「监听弹幕/礼物」可同时拉取弹幕、礼物、上舰、SC、进场。
3. 点击「▶ 启动监听」。模型在点击后才加载，首次可能需要等待。
4. 字幕和弹幕显示在窗口，详细状态在控制台。

可选参数：

```bash
python run.py --backend mlx               # 强制 MLX（macOS）
python run.py --backend faster-whisper    # 强制 Faster-Whisper
python run.py --device cpu                # 强制 CPU
python run.py --model large-v3            # 覆盖默认模型
python run.py --no-danmaku                # 本次不监听弹幕
python run.py --login                     # 扫码登录并保存凭证
python run.py --autostart                 # 启动后自动开始监听默认配置
```

### 配置文件

`configs/*.json`：

```json
{
  "room_id": "1950858520",
  "streamer_name": "jiojio"
}
```

### 产物

- 录像：`records/live_record_<room>_<ts>.mp4`
- 切片：`records/Clip_<主播>_<时分秒>_from_<起始秒>s.mp4`
- 转写日志：`logs/<主播>_<room>_log_<ts>.txt`
- 弹幕日志：`logs/<主播>_<room>_danmaku_<ts>.txt`

## 弹幕数据库表

与 `bili_live_monitor` 兼容：

- `danmu_messages`：弹幕内容、粉丝牌、用户等级
- `gift_records`：礼物名称、数量、单价、总价、收礼人
  - 普通礼物来自 `SEND_GIFT` / `SEND_GIFT_V2`（V2 为 protobuf 编码）
  - 连麦/多人连线的“票”来自 `UNIVERSAL_EVENT_GIFT`，按累计票数增量记录，`gift_name` 为 `连麦投票`，送礼人未知
- `guard_records`：总督/提督/舰长、价格、起止时间
- `entry_user_records`：进场记录
- `super_chat_records`：醒目留言（SC）金额与内容

## 工具

先安装工具依赖：

```bash
pip install -r requirements-tools.txt
python -m playwright install chromium   # 用于把报告渲染成长图
```

### 直播复盘（DeepSeek）

把 DeepSeek API Key 写入 `tools/.dsSecretKey`（或设置环境变量 `DEEPSEEK_API_KEY`）。

```bash
python tools/summarize.py                              # 打开图形界面
python tools/summarize.py logs/a.txt logs/b.txt         # 多选文件合并总结
python tools/summarize.py --config configs/xiyin.json --date 2026-11-11
```

功能：

- 图形界面支持多选文件、拖拽文件（需 `tkinterdnd2`），也可以直接选择 `configs/` 下的直播间按日期总结
- 自动匹配 `logs/` 里的转写与弹幕、`records/` 里的录像，并汇总当天 MySQL 的礼物/舰长/SC/弹幕数据
- 自动获取当前大航海名单（总督/提督/舰长）、直播间财富等级和 B 站账号等级，避免把提督误判成普通粉丝；需要使用项目已有的 `.bili_credential.json`
- 根据核心观众的弹幕语气，输出「沟通风格画像 + 互动话术 + 避雷建议」，帮助主播更好地接住每个人
- 以「内容运营总监 + 数据增长分析师」的视角输出复盘、风险提示和下播行动清单，语气以鼓励和建议为主
- 输出 Markdown 到 `reports/`，并同时渲染两张图：
  - `<主播>_<日期>_<时间>.png`：带 Mermaid 思维导图的完整长图
  - `<主播>_<日期>_<时间>_摘要.png`：一页速览，适合直接发给主播

常用参数：`--no-db`（不查流水）、`--no-danmaku`（不加入弹幕）、`--no-png`（只出 Markdown）、
`--no-brief`（不出摘要图）、`--dry-run`（只生成 Prompt）、`--model deepseek-reasoner`（切换模型）。

会议实时英译中（macOS，需要 BlackHole、portaudio 与本地 Qwen）：

```bash
brew install portaudio && pip install pyaudio
python tools/meeting_translate.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

## 已修复的旧问题

- Windows CPU 回退时 `compute_type` 不兼容
- 固定 8 秒切块导致句子被截断（改为 VAD 静音断句）
- 切片时间戳包含推理延迟（改用音频段时间戳）
- 直播流中断后不再重连
- 停止时采集进程无法及时退出
- 停止/重连时录像转封装不完整

## 说明

- 仅用于个人学习与娱乐，请遵守平台条款与版权规定。
- 原 `main*.py` 原型脚本已删除，历史版本仍可在 git 历史中查看。
- 狼人杀相关代码已移到 `~/Documents/werewolf/`。
