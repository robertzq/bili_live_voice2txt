#!/usr/bin/env python3
"""用本地大模型对直播转写日志做“手术级”复盘。

支持两种本地服务：
  - openai: llama.cpp / vLLM 等 OpenAI 兼容端点（默认 8000 端口）
  - ollama: Ollama（默认 11434 端口）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import requests

os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(key, None)

PROMPT_TEMPLATE = """\
你现在是一名资深的 B 站直播观察员。请针对以下【{streamer}】的直播录音文本，进行一次“手术级”的深度总结。

### 任务要求：
1. **时间线还原**：按照直播进行的顺序，梳理出至少 5-8 个关键的时间节点和对应发生的事件。
2. **核心槽点/梗**：提取直播间出现的特定黑话、梗，说明具体是怎么回事。
3. **关键人物画像**：除了主播，提到了哪些重要的粉丝或观众，他们说了什么重要的话？
4. **情感曲线**：主播今天的情绪状态如何？（比如：疲惫、兴奋、还是在画饼？）
5. **硬核细节**：杜绝“回应了近期争议”“聊了敏感话题”这种废话。必须精确提炼：主播具体回应了什么节奏（起因）？最终处理基调是什么（强硬回怼、妥协道歉、还是画线立规矩）？并摘录 1-2 句最能代表她态度的原话或暴言。

### 待分析文本：
{content}

最后，请生成一段 Mermaid 格式的思维导图代码，概括本次直播的结构。
"""


def build_prompt(streamer: str, content: str) -> str:
    return PROMPT_TEMPLATE.format(streamer=streamer or "主播", content=content)


def build_payload(backend: str, model: str, prompt: str) -> dict:
    if backend == "openai":
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "temperature": 0.3,
        }
    return {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"num_ctx": 65536, "temperature": 0.3},
    }


def iter_text(backend: str, response) -> Iterator[str]:
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8")

        if backend == "ollama":
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "response" in chunk:
                yield chunk["response"]
            if chunk.get("done"):
                return
            continue

        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        if "content" in delta:
            yield delta["content"]


def summarize(
    file_path: Path,
    streamer: str,
    backend: str,
    model: str,
    url: str,
) -> int:
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"❌ 无法读取日志: {exc}", file=sys.stderr)
        return 1

    prompt = build_prompt(streamer, content)
    payload = build_payload(backend, model, prompt)
    print(f"🚀 正在请求 {backend} (模型: {model})...")

    try:
        response = requests.post(
            url,
            json=payload,
            stream=True,
            proxies={"http": None, "https": None},
        )
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接 {url}，请确认本地模型服务已启动。", file=sys.stderr)
        return 1

    if response.status_code != 200:
        print(f"❌ 服务返回错误: {response.status_code}\n{response.text}", file=sys.stderr)
        return 1

    for text in iter_text(backend, response):
        print(text, end="", flush=True)
    print("\n\n✅ 总结完成！")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直播转写日志总结")
    parser.add_argument("file", type=Path, help="直播转写日志路径")
    parser.add_argument("--streamer", default="主播", help="主播名")
    parser.add_argument(
        "--backend", choices=["openai", "ollama"], default="openai"
    )
    parser.add_argument("--model", default="Qwen3.5-35B-A3B")
    parser.add_argument("--url", default=None)
    args = parser.parse_args(argv)

    url = args.url or (
        "http://127.0.0.1:8000/v1/chat/completions"
        if args.backend == "openai"
        else "http://127.0.0.1:11434/api/generate"
    )
    return summarize(args.file, args.streamer, args.backend, args.model, url)


if __name__ == "__main__":
    raise SystemExit(main())
