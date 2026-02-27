import requests
import json
import os

# --- 关键：强制关闭当前进程的代理设置 ---
os.environ['no_proxy'] = 'localhost,127.0.0.1'
if "http_proxy" in os.environ: del os.environ["http_proxy"]
if "https_proxy" in os.environ: del os.environ["https_proxy"]

# 配置。qwen2.5-coder:32b. qwen3-coder:30b. deepseek-r1:32b
MODEL = "qwen3-coder:30b" # 建议确保你已经用 ollama pull 下好了
FILE_PATH = "柚锖子_1894720970_mlx_log_1772164506.txt" # 替换为你的真实文件名
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    print(f"❌ 找不到文件: {FILE_PATH}")
    exit()

prompt = f"""
你现在是一名资深的 B 站直播观察员。请针对以下【柚锖子】的直播录音文本，进行一次“手术级”的深度总结。

### 任务要求：
1. **时间线还原**：按照直播进行的顺序，梳理出至少 5-8 个关键的时间节点和对应发生的事件。
2. **核心槽点/梗**：提取直播间出现的特定黑话、梗（比如提到的“凉菜”、“沙尘暴”具体是怎么回事）。
3. **关键人物画像**：除了主播，提到了哪些重要的粉丝或观众（如“绝命山主”、“妙笔生花”），他们说了什么重要的话？
4. **情感曲线**：主播今天的情绪状态如何？（比如：疲惫、兴奋、还是在画饼？）
5. **硬核细节**：不要说“提到了一些敏感问题”，要写出“关于文件共享，主播具体是怎么澄清的，设置了哪些敏感词”。

### 待分析文本：
{content}

最后，请生成一段 Mermaid 格式的思维导图代码，概括本次直播的结构。
"""

payload = {
    "model": MODEL,
    "prompt": prompt,
    "stream": True,
    "options": {
        "num_ctx": 65536, # 48GB 内存够大，直接开 64k 窗口
        "temperature": 0.3
    }
}

print(f"🚀 正在发送请求到 Ollama (模型: {MODEL})...")

try:
    # 增加 proxies={'http': None, 'https': None} 双重保险
    response = requests.post(OLLAMA_URL, json=payload, stream=True, proxies={'http': None, 'https': None})
    
    # 如果 HTTP 状态码不是 200，直接打印出来
    if response.status_code != 200:
        print(f"❌ Ollama 返回错误，代码: {response.status_code}")
        print(response.text)
        exit()

    for line in response.iter_lines():
        if line:
            try:
                chunk = json.loads(line.decode('utf-8'))
                if "response" in chunk:
                    print(chunk["response"], end="", flush=True)
                if chunk.get("done"):
                    print("\n\n✅ 总结完成！")
            except json.JSONDecodeError:
                print(f"\n⚠️ 收到非 JSON 数据: {line}")
except requests.exceptions.ConnectionError:
    print("❌ 无法连接到 Ollama。请确保你运行了 'ollama serve' 并且端口 11434 可用。")