"""Reliable Windows launcher for the local Yang-gumi Streamlit app."""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from datetime import datetime

from frontend_compat import ensure_streamlit_frontend_compatibility
import restart_yanggumi
import share_control


ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"
HOST = "127.0.0.1"
PORT = 8501
URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{URL}/_stcore/health"
LOG_PATH = ROOT / "data" / "yanggumi-launch.log"


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def streamlit_is_healthy(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200 and response.read(32).strip().lower() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex((HOST, PORT)) == 0


def _share_should_resume() -> bool:
    status = share_control.read_status()
    return share_control.keep_alive_enabled() or (
        status.get("state") in share_control.ACTIVE_STATES
        and share_control.process_is_running(status.get("pid"))
    )


def prepare_single_instance() -> bool:
    """Release the fixed port by replacing only this install's old instance."""
    resume_share = _share_should_resume()
    try:
        if resume_share:
            share_control.stop_remote_share(timeout=20)

        listener_pid = restart_yanggumi._listener_pid(PORT)
        if listener_pid is not None:
            restart_yanggumi.stop_owned_site(PORT, timeout=15)

        deadline = time.monotonic() + 8.0
        while port_is_open() and time.monotonic() < deadline:
            time.sleep(0.2)
        if port_is_open():
            raise restart_yanggumi.RestartError(
                f"端口 {PORT} 由其他程序占用，已拒绝误结束；请关闭该程序后重试。"
            )
    except Exception:
        if resume_share:
            try:
                share_control.start_remote_share(keep_alive=True)
            except (OSError, RuntimeError):
                pass
        raise
    return resume_share


def resume_remote_share(enabled: bool) -> None:
    if not enabled:
        return
    try:
        share_control.start_remote_share(keep_alive=True)
    except (OSError, RuntimeError) as exc:
        log(f"主站已启动，但公网分享恢复失败：{exc}")
        print("\n主站已正常启动，但公网分享需要手动重新启动。")


def main() -> int:
    open_browser = "--no-browser" not in sys.argv[1:]
    log("启动器开始运行")
    print("=" * 58)
    print(" Yang-gumi 本地评分库")
    print(f" 固定地址：{URL}")
    print("=" * 58, flush=True)

    try:
        import streamlit  # noqa: F401
    except ImportError:
        log("启动失败：缺少 Streamlit 或项目依赖")
        print("\n缺少 Streamlit 或项目依赖。")
        print("请先安装 requirements.txt 中的依赖，再重新双击启动文件。")
        print("高级安装命令：python -m pip install -r requirements.txt")
        return 3

    try:
        ensure_streamlit_frontend_compatibility()
    except RuntimeError as exc:
        log(f"前端兼容检查失败：{exc}")
        print(f"\n前端兼容检查失败：{exc}")
        return 7

    resume_share = False
    try:
        resume_share = prepare_single_instance()
    except restart_yanggumi.RestartError as exc:
        log(f"单实例替换失败：{exc}")
        print(f"\n无法安全释放 8501 端口：{exc}")
        return 2

    command = [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.address", HOST,
        "--server.port", str(PORT),
        "--server.headless", "true",
        "--server.enableWebsocketCompression", "true",
        "--server.fileWatcherType", "none",
        "--browser.serverAddress", HOST,
        "--browser.gatherUsageStats", "false",
    ]
    print("\n正在启动，请稍候……", flush=True)
    try:
        process = subprocess.Popen(command, cwd=ROOT)
    except OSError as exc:
        resume_remote_share(resume_share)
        log(f"启动子进程失败：{exc}")
        print(f"\n启动失败：{exc}")
        return 4

    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            if restart_yanggumi.consume_replacement(process.pid):
                log(f"旧实例已被新启动请求替换：PID={process.pid}")
                return 0
            resume_remote_share(resume_share)
            log(f"Streamlit 提前退出：{process.returncode}")
            print(f"\nStreamlit 启动失败，退出代码：{process.returncode}")
            return process.returncode or 5
        if streamlit_is_healthy():
            resume_remote_share(resume_share)
            log(f"启动成功：{URL}，PID={process.pid}")
            print(f"\n启动成功，正在打开 {URL}", flush=True)
            if open_browser:
                webbrowser.open(URL)
            break
        time.sleep(0.5)
    else:
        resume_remote_share(resume_share)
        log("启动超时")
        print("\n启动超时。请查看上方错误信息，或确认安全软件没有拦截 Python。")
        process.terminate()
        return 6

    try:
        return_code = process.wait()
        if restart_yanggumi.consume_replacement(process.pid):
            log(f"旧实例已被新启动请求替换：PID={process.pid}")
            return 0
        return return_code
    except KeyboardInterrupt:
        log("用户请求关闭 Yang-gumi")
        print("\n正在关闭 Yang-gumi……")
        process.terminate()
        return process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
