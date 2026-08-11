"""Safely restart only the Yang-gumi Streamlit instance owned by this install."""
from __future__ import annotations

import base64
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
REPLACE_SIGNAL_PATH = ROOT / "data" / "yanggumi-replace.signal"


class RestartError(RuntimeError):
    pass


def _hidden_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _healthy(timeout: float = 1.0, port: int = PORT) -> bool:
    try:
        health_url = f"http://{HOST}:{int(port)}/_stcore/health"
        with urllib.request.urlopen(health_url, timeout=timeout) as response:
            return response.status == 200 and response.read(32).strip().lower() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def _listener_pid(port: int = PORT) -> int | None:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        raise RestartError("无法读取本机 8501 端口状态。")
    endpoint = f"{HOST}:{int(port)}"
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
        '-ErrorAction SilentlyContinue; if ($p) { '
        '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$p.CommandLine)) }'
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        raise RestartError(f"无法确认 8501 端口进程（PID {pid}）的身份。")
    encoded = completed.stdout.strip()
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RestartError(f"无法解析 8501 端口进程（PID {pid}）的身份。") from exc


def _is_owned_streamlit(command_line: str) -> bool:
    normalized = re.sub(r"[\\/]+", r"\\", command_line).casefold()
    root = re.sub(r"[\\/]+", r"\\", str(ROOT)).casefold()
    return root in normalized and "streamlit" in normalized and "app.py" in normalized


def mark_replacement(pid: int) -> None:
    REPLACE_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPLACE_SIGNAL_PATH.write_text(str(int(pid)), encoding="ascii")


def consume_replacement(pid: int) -> bool:
    try:
        requested = int(REPLACE_SIGNAL_PATH.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    if requested != int(pid):
        return False
    REPLACE_SIGNAL_PATH.unlink(missing_ok=True)
    return True


def _stop_listener(pid: int, *, allow_unowned: bool) -> None:
    if int(pid) in {0, 4} or int(pid) == os.getpid():
        raise RestartError(f"不能结束占用端口的系统或当前进程（PID {pid}）。")

    owned = False
    try:
        owned = _is_owned_streamlit(_process_command_line(pid))
    except RestartError:
        if not allow_unowned:
            raise
    if not owned and not allow_unowned:
        raise RestartError("8501 端口不是由当前目录的 Yang-gumi 占用，已拒绝结束该进程。")

    if owned:
        mark_replacement(pid)
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=_hidden_flags(), check=False,
    )
    if completed.returncode != 0:
        if owned:
            REPLACE_SIGNAL_PATH.unlink(missing_ok=True)
        raise RestartError(f"无法结束占用端口的旧进程（PID {pid}）。")


def _stop_owned_site(pid: int) -> None:
    _stop_listener(pid, allow_unowned=False)


def stop_owned_site(port: int = PORT, timeout: float = 15.0) -> int | None:
    """Stop only this installation's Streamlit listener and wait for release."""
    pid = _listener_pid(port)
    if pid is None:
        return None
    _stop_owned_site(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _listener_pid(port) is None:
            return pid
        time.sleep(0.2)
    raise RestartError(f"旧的 Yang-gumi 未在限定时间内释放端口 {port}。")


def replace_port_listener(port: int = PORT, timeout: float = 15.0) -> int | None:
    """Force-stop any listener on the fixed app port and wait for release.

    This is intentionally used only by the explicit desktop launcher. Updater
    restart paths continue to use ``stop_owned_site`` and its ownership check.
    """
    first_pid: int | None = None
    stopped_pids: set[int] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = _listener_pid(port)
        if pid is None:
            return first_pid
        if first_pid is None:
            first_pid = pid
        if pid not in stopped_pids:
            try:
                _stop_listener(pid, allow_unowned=True)
            except RestartError:
                # Windows taskkill /T can return a non-zero code when one
                # descendant disappears during termination even though the
                # actual listener has already gone.  Port ownership is the
                # authoritative result for a launcher replacement.
                remaining_pid = _listener_pid(port)
                if remaining_pid == pid:
                    raise
            stopped_pids.add(pid)
        time.sleep(0.2)
    raise RestartError(f"旧进程未在限定时间内释放端口 {port}。")


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
    pid = _listener_pid(PORT)
    if pid is None:
        raise RestartError("网站健康检查成功，但未找到 8501 端口监听进程。")
    stop_owned_site(PORT, timeout=min(15.0, timeout))
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
