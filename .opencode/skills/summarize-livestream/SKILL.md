---
name: summarize-livestream
description: 用 tools/summarize.py 总结 B 站直播间内容。用户说“总结/复盘今天这场直播”“用 summarize 总结直播内容”“总结青荼/苏狸狸今天的直播”“按时间段总结中午场”时使用；会自动匹配 configs/*.json、logs/、records/ 和 MySQL 流水，生成 Markdown + 完整图 + 一页速览图。
---

# 总结直播内容（summarize.py）

项目根目录：`/Users/ZQA4SGH/Documents/bili-whisper`

## 前置条件

- `tools/.dsSecretKey` 里有 DeepSeek Key；`.env` 里配好 MySQL。
- 已安装 `requirements-tools.txt`，并执行过 `python -m playwright install chromium`。
- `configs/` 下有对应直播间的 JSON 配置。

## 先确认三件事

1. **哪一位主播 / 哪个房间**：从用户的话里找名字，然后在 `configs/` 里找对应配置。
   - 例：青荼 → `configs/青荼.json`；苏狸狸 → `configs/sulili.json`。
   - 配置里包含 `room_id` 和 `streamer_name`，以配置为准。
   - 名字对不上或同时有多位主播时，先问清楚，不要猜。
2. **哪一天**：用户说“今天”就用系统当天日期（`date +%F`）；说“昨天”用前一天；说具体日期就用该日期。
3. **整场还是某个时段**：用户说“中午场”“下午场”“PK 那一段”时，先看 `logs/` 里这场直播的起止时间，再用 `--start/--end` 限定；没有明确时段就总结整天。

## 先检查有没有数据（避免白花 API 费用）

```bash
ls -lt logs/<主播>_<room_id>_* 2>/dev/null | head
ls -lt records/live_record_<room_id>_* 2>/dev/null | head
```

- 没有当天的转写日志：直接告诉用户“今天没有找到转写日志”，不要调用模型。
- 最新日志文件 mtime 在 1-2 分钟内还在变化：说明直播可能还在进行，先说明“这是截至 XX:XX 的快照”，建议下播后重跑。
- 日志有、录像没有也没关系；报告会标注“未匹配到录像”，总结仍可基于日志和数据库。

## 执行总结

**整天：**

```bash
python3 tools/summarize.py --config configs/<配置>.json --date YYYY-MM-DD
```

**某个时段（例如中午场 11:20-14:06）：**

```bash
python3 tools/summarize.py --config configs/<配置>.json \
  --date YYYY-MM-DD --start 11:20 --end 14:06
```

**常用选项：**

- `--no-png`：只要 Markdown，不渲染图片
- `--no-brief`：不生成一页速览图
- `--full-layout single`：完整图改回单栏长图（默认四栏报纸版）
- `--dry-run`：只生成 Prompt，不调用 DeepSeek（排查时段/数据时用）

运行后从 stdout 里取三个路径：

- `📄 Markdown: ...`
- `🖼️ 完整长图: ...`
- `🖼️ 一页速览图: ...`

产物默认在 `reports/`，文件名格式：
`<主播>_<日期>_<时间戳>.md/.png` 和 `<主播>_<日期>_<时间戳>_摘要.png`。

## 给用户的总结怎么写

不要只丢文件路径。读完 `_摘要.md`（需要细节再读完整 `.md`）后，用中文给：

1. **场次与时间**：例如“11:33-14:25，约 2 小时 52 分”。
2. **关键数据**：总流水、付费人数、弹幕/活跃人数、进场、峰值时段。
3. **做得好的 2-4 点**：带数据或原话。
4. **主要问题 2-4 点**：语气委婉、对事不对人。
5. **下一场优先做的 3 件事**：要具体、可执行。
6. **核心观众沟通建议**（如果报告里有）：挑 2-3 位，给一句可念的话术。
7. **文件路径**：完整图、速览图、Markdown。

## 数据口径与常见坑

- **数据库时间**：`gift_records/danmu_messages.timestamp` 是本地时间；`entry_user_records.created_at` 是数据库时区（通常 UTC）。工具已对进场统计做时区换算，但如果进场数字异常，先说明口径。
- **弹幕入库可能晚于转写**：例如日志从 11:33 开始，但数据库弹幕 13:12 才有。遇到这种情况，在总结里注明“弹幕统计从 XX:XX 开始，内容复盘覆盖全场”。
- **录像可能被清理**：`records/` 会自动轮换/清理；报告里出现“未匹配到录像”时，不要把它当成内容缺失，只说明录像已不在本地。
- **不要编造**：所有数字和原话以报告/数据库/日志为准；没有的数据就写“本期无数据”。
- **不要提交**：`reports/`、`configs/` 里的个人配置、`.dsSecretKey`、`.bili_credential.json` 都不要提交到 git。

## 如果用户还要切片

报告 `4.2 名场面与梗` 里会有时间点和二创方向。切片时：

1. 用文件名里的 epoch 秒确定录像起点：`live_record_<room>_<epoch>.mp4`。
2. 用 `ffprobe` 确认真实时长，目标时间减去录像起点得到偏移秒数。
3. 用 ffmpeg 截取（保留原画质）：

```bash
ffmpeg -y -ss <开始秒> -t <时长秒> -i records/<录像>.mp4 \
  -c copy -movflags +faststart records/Clip_<主播>_<日期>_<时间>_<标签>.mp4
```

一般每个切片 2-2.5 分钟，前后各留 20-30 秒上下文。

## 示例

用户：“总结青荼今天的直播”

```bash
date +%F
python3 tools/summarize.py --config configs/青荼.json --date "$(date +%F)"
```

用户：“用 summarize 总结苏狸狸今天中午这场”

```bash
python3 tools/summarize.py --config configs/sulili.json \
  --date "$(date +%F)" --start 11:20 --end 14:06
```

用户：“复盘昨天青荼的直播”

```bash
python3 tools/summarize.py --config configs/青荼.json \
  --date "$(python3 -c 'from datetime import date,timedelta; print(date.today()-timedelta(days=1))')"
```
