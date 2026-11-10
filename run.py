#!/usr/bin/env python3
"""Bilibili 直播实时语音转写入口。"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bilibili 直播间实时语音转写与弹幕监听（图形界面）"
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "mlx", "faster-whisper"],
        default="auto",
        help="转写后端，默认 macOS 用 mlx，其他平台用 faster-whisper",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="推理设备: auto / cpu / cuda",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖默认模型（MLX 为 HuggingFace 路径，Faster-Whisper 为模型名）",
    )
    parser.add_argument(
        "--no-danmaku",
        action="store_true",
        help="启动时不监听弹幕/礼物",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="扫码登录 B 站并保存凭证，然后退出",
    )
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="启动 GUI 后自动开始监听默认/指定配置",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="指定启动时使用的直播间配置，例如 configs/jiojio.json",
    )
    return parser.parse_args(argv)


def _login() -> int:
    from bililive.bili_auth import qr_login, save_credential

    try:
        credential = qr_login()
    except Exception as exc:
        print(f"登录失败: {exc}", file=sys.stderr)
        return 1
    path = save_credential(credential)
    print(f"凭证已保存: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.login:
        return _login()

    try:
        from bililive.gui import run
    except ImportError as exc:
        print(f"无法加载 GUI: {exc}", file=sys.stderr)
        return 1

    try:
        run(
            backend=args.backend,
            device=args.device,
            model=args.model,
            danmaku=not args.no_danmaku,
            autostart=args.autostart,
            config_path=args.config,
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
