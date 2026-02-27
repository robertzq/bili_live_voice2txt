import requests
import json
import os
import sys

# --- 关键：强制关闭当前进程的代理设置 ---
os.environ['no_proxy'] = 'localhost,127.0.0.1'
if "http_proxy" in os.environ: del os.environ["http_proxy"]
if "https_proxy" in os.environ: del os.environ["https_proxy"]

# --- 配置区：指向全新的 llama-server ---
MODEL = "Qwen3.5-35B-A3B" # 名字可以随便写，因为服务器只加载了这一个模型
FILE_PATH = "柚锖子_1894720970_mlx_log_1772170784.txt" # 替换为你的真实文件名
LLAMA_SERVER_URL = "http://127.0.0.1:8000/v1/chat/completions" # OpenAI 兼容端点

try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    print(f"❌ 找不到文件: {FILE_PATH}")
    sys.exit()

prompt = f"""
你现在是一名资深的 B 站直播观察员。请针对以下【柚锖子】的直播录音文本，进行一次“手术级”的深度总结。

### 任务要求：
1. **时间线还原**：按照直播进行的顺序，梳理出至少 5-8 个关键的时间节点和对应发生的事件。
2. **核心槽点/梗**：提取直播间出现的特定黑话、梗（比如提到的“凉菜”、“沙尘暴”具体是怎么回事）。
3. **关键人物画像**：除了主播，提到了哪些重要的粉丝或观众（如“绝命山主”、“妙笔生花”），他们说了什么重要的话？
4. **情感曲线**：主播今天的情绪状态如何？（比如：疲惫、兴奋、还是在画饼？）
5. **硬核细节**：不要说“提到了一些敏感问题”，核心争议与实质回应（硬核细节）：杜绝使用“回应了近期争议”、“聊了敏感话题”这种废话。必须精确提炼：主播具体回应了什么节奏（起因）？她给出的最终处理基调是什么（强硬回怼、妥协道歉、还是画线立规矩）？并摘录 1-2 句最能代表她态度的原话或暴言。

### 待分析文本：
{content}

最后，请生成一段 Mermaid 格式的思维导图代码，概括本次直播的结构。
"""

# 转换为 OpenAI 兼容的 Payload 格式
payload = {
    "model": MODEL,
    "messages": [
        {"role": "user", "content": prompt}
    ],
    "stream": True,
    "temperature": 0.3
}

print(f"🚀 正在发送请求到本地 llama-server (端口 8000)...")

try:
    # 增加 proxies={'http': None, 'https': None} 双重保险
    response = requests.post(LLAMA_SERVER_URL, json=payload, stream=True, proxies={'http': None, 'https': None})
    
    # 如果 HTTP 状态码不是 200，直接打印出来
    if response.status_code != 200:
        print(f"❌ 服务器返回错误，代码: {response.status_code}")
        print(response.text)
        sys.exit()

    # 解析 Server-Sent Events (SSE) 数据流
    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            # SSE 协议的特征：数据以 "data: " 开头
            if line_str.startswith("data: "):
                data_str = line_str[6:] # 剥离前缀
                
                # 结束标志
                if data_str == "[DONE]":
                    print("\n\n✅ 总结完成！")
                    break
                
                try:
                    chunk = json.loads(data_str)
                    # 提取增量文本，防空指针
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if "content" in delta:
                        # 你会在这里实时看到 <think> 过程和最终输出
                        print(delta["content"], end="", flush=True)
                except json.JSONDecodeError:
                    print(f"\n⚠️ 收到非 JSON 数据: {data_str}")

except requests.exceptions.ConnectionError:
    print("❌ 无法连接到服务。请确保你运行了 './start_qwen.sh' 并且端口 8000 可用。")