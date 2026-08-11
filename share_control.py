# Yang-gumi release: 1.3.0
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
STATUS_PATH = DATA_DIR / "remote_share_status.json"
STOP_PATH = DATA_DIR / "remote_share_stop.signal"
PID_PATH = DATA_DIR / "remote_share.pid"
KEEP_ALIVE_PATH = DATA_DIR / "remote_share_keep_alive.json"
SHIKISHARE_EXE = ROOT / "ShiKiShare.exe"
STATIC_SITE_DIR = DATA_DIR / "remote_share_site"
INTERACTIVE_SHARE_SCRIPT = ROOT / "share_public.py"

ACTIVE_STATES = {"starting", "running", "reconnecting", "degraded", "stopping"}
_start_lock = threading.Lock()
_last_supervisor_start = 0.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def process_is_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def read_status() -> dict[str, Any]:
    default = {
        "state": "stopped",
        "message": "公网只读分享尚未启动",
        "public_url": "",
        "local_state": "stopped",
        "tunnel_state": "stopped",
        "pid": None,
        "port": None,
        "started_at": "",
        "updated_at": _now(),
        "reconnect_attempt": 0,
        "last_error": "",
    }
    try:
        value = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            default.update(value)
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    if default["state"] in ACTIVE_STATES and not process_is_running(default.get("pid")):
        default.update(
            state="error",
            message="分享进程已意外停止；原公网链接已失效",
            public_url="",
            local_state="stopped",
            tunnel_state="disconnected",
            last_error="未检测到正在运行的只读分享进程",
            updated_at=_now(),
        )
        try:
            _atomic_json(STATUS_PATH, default)
        except OSError:
            pass
    return default


def keep_alive_enabled() -> bool:
    try:
        payload = json.loads(KEEP_ALIVE_PATH.read_text(encoding="utf-8"))
        return bool(payload.get("enabled"))
    except (OSError, TypeError, json.JSONDecodeError):
        return False


def _set_keep_alive(enabled: bool) -> None:
    if enabled:
        _atomic_json(
            KEEP_ALIVE_PATH,
            {
                "enabled": True,
                "enabled_at": _now(),
                "policy": "restart-while-owner-site-is-running",
                "minimum_target_seconds": 3600,
            },
        )
    else:
        KEEP_ALIVE_PATH.unlink(missing_ok=True)


def start_remote_share(*, keep_alive: bool = True) -> dict[str, Any]:
    if keep_alive:
        _set_keep_alive(True)
    with _start_lock:
        current = read_status()
        if current["state"] in ACTIVE_STATES and process_is_running(current.get("pid")):
            return current

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        STOP_PATH.unlink(missing_ok=True)
        if not INTERACTIVE_SHARE_SCRIPT.exists():
            raise FileNotFoundError("未找到交互式只读分享程序 share_public.py，请恢复完整网站文件。")
        command = [
            sys.executable,
            str(INTERACTIVE_SHARE_SCRIPT),
            "--managed",
        ]
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        started_at = _now()
        _atomic_json(
            STATUS_PATH,
            {
                "state": "starting",
                "message": "正在启动与主站同界面的交互式只读站…",
                "public_url": "",
                "local_state": "starting",
                "tunnel_state": "starting",
                "pid": None,
                "port": None,
                "started_at": started_at,
                "updated_at": started_at,
                "reconnect_attempt": 0,
                "last_error": "",
            },
        )
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=os.name != "nt",
        )
        payload = {
            "state": "starting",
            "message": "正在启动与主站同界面的交互式只读站…",
            "public_url": "",
            "local_state": "starting",
            "tunnel_state": "starting",
            "pid": process.pid,
            "port": None,
            "started_at": started_at,
            "updated_at": _now(),
            "reconnect_attempt": 0,
            "last_error": "",
        }
        PID_PATH.write_text(str(process.pid), encoding="ascii")
        progressed = read_status()
        if progressed.get("pid") == process.pid and progressed.get("port") is not None:
            return progressed
        _atomic_json(STATUS_PATH, payload)
        return payload


def ensure_remote_share_running(*, retry_interval: float = 30.0) -> dict[str, Any]:
    """Restart an opted-in share when its controller disappears unexpectedly."""
    global _last_supervisor_start
    status = read_status()
    if not keep_alive_enabled():
        return status
    if status["state"] in ACTIVE_STATES and process_is_running(status.get("pid")):
        return status
    now = time.monotonic()
    if now - _last_supervisor_start < max(1.0, float(retry_interval)):
        return status
    _last_supervisor_start = now
    return start_remote_share(keep_alive=False)


def stop_remote_share(timeout: float = 15.0) -> dict[str, Any]:
    _set_keep_alive(False)
    current = read_status()
    pid = current.get("pid")
    if not process_is_running(pid):
        payload = {**current, "state": "stopped", "message": "公网只读分享已停止", "public_url": "",
                   "local_state": "stopped", "tunnel_state": "stopped", "pid": None, "updated_at": _now()}
        _atomic_json(STATUS_PATH, payload)
        STOP_PATH.unlink(missing_ok=True)
        PID_PATH.unlink(missing_ok=True)
        return payload

    _atomic_json(STATUS_PATH, {**current, "state": "stopping", "message": "正在关闭公网链接和只读站…", "updated_at": _now()})
    STOP_PATH.write_text(_now(), encoding="utf-8")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process_is_running(pid):
        time.sleep(0.2)
    if process_is_running(pid) and os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    payload = {
        **current,
        "state": "stopped",
        "message": "公网只读分享已停止；原链接已经失效",
        "public_url": "",
        "local_state": "stopped",
        "tunnel_state": "stopped",
        "pid": None,
        "port": None,
        "updated_at": _now(),
        "reconnect_attempt": 0,
        "last_error": "",
    }
    _atomic_json(STATUS_PATH, payload)
    STOP_PATH.unlink(missing_ok=True)
    PID_PATH.unlink(missing_ok=True)
    return payload


def qr_code_png(text: str) -> bytes:
    if not text:
        return b""
    import qrcode

    code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=7, border=3)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="#111318", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def format_duration(started_at: str) -> str:
    if not started_at:
        return "—"
    try:
        seconds = max(0, int((datetime.now() - datetime.fromisoformat(started_at)).total_seconds()))
    except (TypeError, ValueError):
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
