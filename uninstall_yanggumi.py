"""Windows uninstaller for a local Yang-gumi installation."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "acgn.db"
APP_MARKERS = {"app.py", "database.py", "start_yanggumi.py", "requirements.txt"}


def validate_install_root(root: Path = ROOT) -> Path:
    root = root.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("拒绝卸载危险目录。")
    missing = [name for name in APP_MARKERS if not (root / name).is_file()]
    if missing:
        raise ValueError("当前目录不是完整的 Yang-gumi 安装目录。")
    return root


def backup_database_to(folder: Path, source: Path = DB_PATH) -> Path:
    folder = folder.expanduser().resolve()
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError("没有找到 Yang-gumi 数据库。")
    if folder == ROOT.resolve() or ROOT.resolve() in folder.parents:
        raise ValueError("数据必须保存到 Yang-gumi 安装目录之外，否则卸载时会一并删除。")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"yanggumi_uninstall_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source_conn.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("保存后的数据库完整性检查失败。")
    finally:
        destination.close()
        source_conn.close()
    return target


def cleanup_script(root: Path, parent_pid: int) -> str:
    escaped = str(root).replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
$root = '{escaped}'
try {{ Wait-Process -Id {int(parent_pid)} -Timeout 30 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 500
if ((Test-Path -LiteralPath $root) -and
    (Test-Path -LiteralPath (Join-Path $root 'app.py')) -and
    (Test-Path -LiteralPath (Join-Path $root 'start_yanggumi.py'))) {{
    $escapedRoot = [regex]::Escape($root)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {{
            ($_.ProcessId -ne $PID) -and
            (($_.ExecutablePath -like "$root\\*") -or ($_.CommandLine -match $escapedRoot))
        }} |
        ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
    Start-Sleep -Milliseconds 700
    Remove-Item -LiteralPath $root -Recurse -Force
}}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
""".strip()


def schedule_removal(root: Path = ROOT) -> None:
    script_path = Path(tempfile.gettempdir()) / f"yanggumi-uninstall-{os.getpid()}.ps1"
    script_path.write_text(cleanup_script(validate_install_root(root), os.getpid()), encoding="utf-8-sig")
    subprocess.Popen(
        [
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def main() -> int:
    if os.name != "nt":
        print("卸载程序目前仅支持 Windows。")
        return 2
    try:
        validate_install_root()
        import tkinter as tk
        from tkinter import filedialog, messagebox

        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        save = messagebox.askyesnocancel(
            "卸载 Yang-gumi",
            "卸载会删除本机的 Yang-gumi 程序与数据。\n\n是否先保存评分数据库？",
            parent=window,
        )
        if save is None:
            return 0
        saved_path: Path | None = None
        if save:
            selected = filedialog.askdirectory(
                title="选择 Yang-gumi 数据保存位置",
                mustexist=True,
                parent=window,
            )
            if not selected:
                return 0
            saved_path = backup_database_to(Path(selected))
        elif not messagebox.askyesno(
            "确认不保存数据",
            "未保存的数据在卸载后无法恢复。确定继续吗？",
            icon="warning",
            parent=window,
        ):
            return 0

        message = "Yang-gumi 即将卸载。"
        if saved_path:
            message += f"\n\n数据已安全保存到：\n{saved_path}"
        messagebox.showinfo("准备卸载", message, parent=window)
        schedule_removal()
        return 0
    except Exception as exc:
        try:
            from tkinter import messagebox
            messagebox.showerror("卸载失败", str(exc))
        except Exception:
            print(f"卸载失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
