"""Safely restart only the Yang-gumi Streamlit instance owned by this install."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8501
HEALTH_URL = f"http://{HOST}:{PORT}/_stcore/health"


class RestartError(RuntimeError):
    pass


def _hidden_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _healthy(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200 and response.read(32).strip().lower() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def _listener_pid() -> int | None:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        raise RestartError("无法读取本机 8501 端口状态。")
    endpoint = f"{HOST}:{PORT}"
    for line in completed.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 5 and columns[1] == endpoint and columns[3].upper() == "LISTENING":
            try:
                return int(columns[4])
            except ValueError:
                continue
    return None


def _process_command_line(pid: int) -> str:
    script = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {int(pid)}" '
        '-ErrorAction SilentlyContinue; if ($p) { $p.CommandLine }'
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        raise RestartError(f"无法确认 8501 端口进程（PID {pid}）的身份。")
    return completed.stdout.strip()


def _is_owned_streamlit(command_line: str) -> bool:
    normalized = re.sub(r"[\\/]+", r"\\", command_line).casefold()
    root = re.sub(r"[\\/]+", r"\\", str(ROOT)).casefold()
    return root in normalized and "streamlit" in normalized and "app.py" in normalized


def _stop_owned_site(pid: int) -> None:
    command_line = _process_command_line(pid)
    if not _is_owned_streamlit(command_line):
        raise RestartError("8501 端口不是由当前目录的 Yang-gumi 占用，已拒绝结束该进程。")
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        raise RestartError(f"无法结束旧版 Yang-gumi 进程（PID {pid}）。")


def _launch_hidden() -> int:
    launcher = ROOT / "start_yanggumi.py"
    if not launcher.is_file():
        raise RestartError("缺少 start_yanggumi.py，无法重新启动。")
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    output_path = data / "update-restart.out.log"
    error_path = data / "update-restart.err.log"
    creationflags = _hidden_flags()
    if os.name == "nt":
        creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
    with output_path.open("a", encoding="utf-8") as stdout, error_path.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, str(launcher), "--no-browser"], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            creationflags=creationflags, close_fds=True,
            start_new_session=os.name != "nt",
        )
    return int(process.pid)


def restart_running_site(timeout: float = 45.0) -> dict[str, Any]:
    """Restart a healthy owned site; never touch an unrelated listener or start a closed site."""
    if not _healthy():
        return {"restarted": False, "reason": "not_running"}
    pid = _listener_pid()
    if pid is None:
        raise RestartError("网站健康检查成功，但未找到 8501 端口监听进程。")
    _stop_owned_site(pid)
    stop_deadline = time.monotonic() + min(15.0, timeout)
    while time.monotonic() < stop_deadline and _healthy(timeout=0.4):
        time.sleep(0.2)
    if _healthy(timeout=0.4):
        raise RestartError("旧版 Yang-gumi 未在限定时间内停止。")
    launcher_pid = _launch_hidden()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(timeout=0.6):
            return {"restarted": True, "old_pid": pid, "launcher_pid": launcher_pid}
        time.sleep(0.25)
    raise RestartError("新版 Yang-gumi 启动超时，请查看 data/update-restart.err.log。")


if __name__ == "__main__":
    try:
        outcome = restart_running_site()
    except RestartError as exc:
        print(f"[错误] {exc}")
        raise SystemExit(1)
    print("已重启。" if outcome.get("restarted") else "网站当前未运行，无需重启。")
