"""B 站登录凭证：cookie 文件 / 本地缓存 / 二维码登录。"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import PROJECT_ROOT

CREDENTIAL_FILE = Path(
    os.getenv("BILI_CREDENTIAL_FILE", str(PROJECT_ROOT / ".bili_credential.json"))
).expanduser()
COOKIE_FILE = Path(
    os.getenv("BILI_COOKIE_FILE", str(PROJECT_ROOT / "bilicookie.txt"))
).expanduser()

COOKIE_KEYS = ("SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID", "ac_time_value")


def configure_bilibili_client() -> bool:
    """让 bilibili-api 的 HTTP 与 WebSocket 都走 HTTPS_PROXY。

    aiohttp 默认不会把环境代理应用到 ws_connect，这里显式补上。
    curl_cffi 的 WS 在部分公司代理上会触发 BoringSSL 崩溃，因此固定用 aiohttp。
    """
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if not proxy:
        return False
    try:
        import aiohttp
        from bilibili_api import request_settings, select_client
    except ImportError:
        return False

    try:
        select_client("aiohttp")
    except Exception:
        pass
    request_settings.set_proxy(proxy)

    if not getattr(aiohttp.ClientSession, "_bili_proxy_patched", False):
        original_ws_connect = aiohttp.ClientSession.ws_connect

        def ws_connect_with_proxy(self, url, **kwargs):
            if not kwargs.get("proxy"):
                current = request_settings.get_proxy()
                if current:
                    kwargs["proxy"] = current
            return original_ws_connect(self, url, **kwargs)

        aiohttp.ClientSession.ws_connect = ws_connect_with_proxy
        aiohttp.ClientSession._bili_proxy_patched = True
    return True


def parse_cookie_string(raw: str) -> dict:
    """把浏览器请求头里的 cookie 字符串转成字典。"""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {str(key): str(value) for key, value in data.items()}

    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def _credential_from_cookies(cookies: dict):
    try:
        from bilibili_api import Credential
    except ImportError:
        return None
    if not cookies.get("SESSDATA"):
        return None
    return Credential.from_cookies(cookies)


def credential_from_env():
    cookies = {
        "SESSDATA": os.getenv("BILI_SESSDATA", ""),
        "bili_jct": os.getenv("BILI_JCT", ""),
        "buvid3": os.getenv("BILI_BUVID3", ""),
        "buvid4": os.getenv("BILI_BUVID4", ""),
        "DedeUserID": os.getenv("BILI_DEDEUSERID", ""),
        "ac_time_value": os.getenv("BILI_AC_TIME_VALUE", ""),
    }
    cookies = {key: value for key, value in cookies.items() if value}
    return _credential_from_cookies(cookies)


def load_credential():
    credential = credential_from_env()
    if credential is not None:
        return credential

    if CREDENTIAL_FILE.is_file():
        try:
            data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        credential = _credential_from_cookies(
            {str(key): str(value) for key, value in data.items()}
        )
        if credential is not None:
            return credential

    if COOKIE_FILE.is_file():
        try:
            raw = COOKIE_FILE.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        credential = _credential_from_cookies(parse_cookie_string(raw))
        if credential is not None:
            return credential

    return None


def save_credential(credential, path: Path = CREDENTIAL_FILE) -> Path:
    cookies = credential.get_cookies()
    data = {
        key: cookies[key]
        for key in COOKIE_KEYS
        if cookies.get(key)
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


QRCODE_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QRCODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"


def _login_session():
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError(
            "扫码登录需要 curl_cffi，请运行: pip install curl_cffi"
        ) from exc

    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    kwargs = {"impersonate": "chrome", "timeout": 20}
    if proxy:
        kwargs["proxies"] = {"all": proxy}
    return curl_requests.Session(**kwargs)


def _parse_login_cookies(data: dict, response) -> dict:
    cookies = {}
    url = data.get("url") or ""
    if url:
        query = parse_qs(urlparse(url).query)
        for key in (
            "SESSDATA",
            "bili_jct",
            "DedeUserID",
            "buvid3",
            "buvid4",
        ):
            if query.get(key):
                cookies[key] = query[key][0]

    try:
        cookies.update(response.cookies.get_dict())
    except Exception:
        pass

    try:
        for header in response.headers.get_list("set-cookie"):
            first = header.split(";", 1)[0]
            if "=" in first:
                key, value = first.split("=", 1)
                cookies.setdefault(key.strip(), value.strip())
    except Exception:
        pass

    if data.get("refresh_token"):
        cookies["ac_time_value"] = data["refresh_token"]
    return cookies


def qr_login() -> object:
    """终端二维码登录，成功返回 Credential。

    直接调用 B 站 passport 接口，避免 bilibili-api 对新版响应解析不全的问题。
    """
    try:
        from bilibili_api import Credential
    except ImportError as exc:
        raise RuntimeError(
            "未安装 bilibili-api-python，请运行: pip install bilibili-api-python"
        ) from exc
    try:
        from qrcode_terminal import qr_terminal_str
    except ImportError as exc:
        raise RuntimeError(
            "扫码登录需要 qrcode_terminal，请运行: pip install qrcode_terminal"
        ) from exc

    session = _login_session()
    try:
        while True:
            generated = session.get(QRCODE_GENERATE_URL).json()
            if generated.get("code") != 0:
                raise RuntimeError(f"生成二维码失败: {generated.get('message')}")
            data = generated["data"]
            qrcode_key = data["qrcode_key"]

            print(qr_terminal_str(data["url"]))
            print("请使用 B 站手机客户端扫码登录...")
            last_code = None

            while True:
                poll = session.get(
                    QRCODE_POLL_URL, params={"qrcode_key": qrcode_key}
                )
                payload = poll.json().get("data") or {}
                code = payload.get("code")

                if code == 86101:
                    if last_code != code:
                        print("等待扫码...", end="\r", flush=True)
                        last_code = code
                elif code == 86090:
                    if last_code != code:
                        print("已扫码，请在手机上确认...", end="\r", flush=True)
                        last_code = code
                elif code == 86038:
                    print("\n二维码已过期，正在重新生成...")
                    break
                elif code == 0 and payload.get("url"):
                    cookies = _parse_login_cookies(payload, poll)
                    if not cookies.get("SESSDATA"):
                        raise RuntimeError(
                            "登录成功但未拿到 SESSDATA，请重试或改用 bilicookie.txt"
                        )
                    print("\n登录成功")
                    return Credential(
                        sessdata=cookies.get("SESSDATA"),
                        bili_jct=cookies.get("bili_jct"),
                        buvid3=cookies.get("buvid3"),
                        buvid4=cookies.get("buvid4"),
                        dedeuserid=cookies.get("DedeUserID"),
                        ac_time_value=cookies.get("ac_time_value"),
                    )
                else:
                    raise RuntimeError(
                        f"登录失败: {payload.get('message') or payload}"
                    )
                time.sleep(0.5)
    finally:
        session.close()
