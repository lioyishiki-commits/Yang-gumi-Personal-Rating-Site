from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TOKEN_PATH = DATA_DIR / "public_share_token.txt"
CURRENT_URL_PATH = DATA_DIR / "current_share_url.txt"
BAT_PATH = ROOT / "启动只读分享.bat"
VISITOR_BAT_PATH = ROOT / "给访客打开 Yang-gumi.bat"
VISITOR_URL_PATH = ROOT / "给访客打开 Yang-gumi.url"
PORT = 8502


def share_token() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        value = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(value, encoding="utf-8")
    return value


def lan_address() -> str:
    """Choose the physical LAN address before virtual/tunnel adapters."""
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        addresses = []
    candidates = [ip for ip in addresses if ip and not ip.startswith(("127.", "169.254."))]
    for prefix in ("192.168.", "10."):
        match = next((ip for ip in candidates if ip.startswith(prefix)), None)
        if match:
            return match
    private_172 = next(
        (
            ip
            for ip in candidates
            if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31
        ),
        None,
    )
    if private_172:
        return private_172
    if candidates:
        return candidates[0]
    raise RuntimeError("没有找到可用于局域网分享的 IPv4 地址，请先连接 Wi-Fi 或网线。")


def wait_for_streamlit(timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    health = f"http://127.0.0.1:{PORT}/_stcore/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=1.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("只读网站启动超时，请确认 8502 端口没有被其他程序占用。")


def replace_lan_url(text: str, url: str) -> str:
    line = f'set "YANGGUMI_LAN_URL={url}"'
    pattern = re.compile(r'^set "YANGGUMI_LAN_URL=.*"$', re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else f"{line}\n{text}"


def update_launchers(url: str) -> None:
    CURRENT_URL_PATH.write_text(url, encoding="utf-8")
    if BAT_PATH.exists():
        source = BAT_PATH.read_text(encoding="utf-8-sig")
        BAT_PATH.write_text(replace_lan_url(source, url), encoding="utf-8-sig")
    visitor = (
        "@echo off\n"
        "chcp 65001 >nul\n"
        f'set "YANGGUMI_LAN_URL={url}"\n'
        "powershell -NoProfile -Command \"try { "
        "(Invoke-WebRequest -UseBasicParsing -TimeoutSec 6 -Uri '%YANGGUMI_LAN_URL%').StatusCode "
        "| Out-Null; exit 0 } catch { exit 1 }\"\n"
        "if errorlevel 1 (\n"
        "  echo 无法连接 Yang-gumi。请确认主人电脑的分享窗口保持开启，并连接同一 Wi-Fi 或局域网。\n"
        "  pause\n"
        "  exit /b 1\n"
        ")\n"
        "start \"\" \"%YANGGUMI_LAN_URL%\"\n"
        "exit /b 0\n"
    )
    VISITOR_BAT_PATH.write_text(visitor, encoding="utf-8-sig")
    VISITOR_URL_PATH.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8-sig")


def terminate(process: subprocess.Popen[bytes] | None) -> None:
    if not process or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()


def main() -> None:
    token = share_token()
    host = lan_address()
    url = f"http://{host}:{PORT}/?access={token}"
    env = dict(os.environ)
    env["YANGGUMI_SHARE_TOKEN"] = token
    env["YANGGUMI_READ_ONLY"] = "1"
    streamlit: subprocess.Popen[bytes] | None = None
    try:
        streamlit = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(ROOT / "app.py"),
                "--server.address",
                "0.0.0.0",
                "--server.port",
                str(PORT),
                "--server.headless",
                "true",
                "--server.enableCORS",
                "false",
                "--server.enableXsrfProtection",
                "false",
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=ROOT,
            env=env,
        )
        wait_for_streamlit()
        update_launchers(url)
        print("\nYang-gumi 只读分享已启动。", flush=True)
        print(f"访客地址：{url}", flush=True)
        print("把更新后的“启动只读分享.bat”直接发给访客即可；访客不需要 Python。", flush=True)
        print("请保持本窗口开启，并让访客连接与你相同的 Wi-Fi / 局域网。", flush=True)
        print("你保存的数据会被访客页面实时读取；访客不能修改。\n", flush=True)
        webbrowser.open(url)
        while streamlit.poll() is None:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        terminate(streamlit)


if __name__ == "__main__":
    main()
