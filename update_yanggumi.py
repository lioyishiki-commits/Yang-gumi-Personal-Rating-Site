from __future__ import annotations

import hashlib
import json
import os
import py_compile
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


OWNER = "lioyishiki-commits"
REPOSITORY = "Yang-gumi-Personal-Rating-Site"
BRANCH = "main"
INITIAL_TAG = "v1.0.0"
INITIAL_VERSION = "1.0.0"
ROOT = Path(os.environ.get("YANGGUMI_UPDATE_ROOT") or Path(__file__).resolve().parent)
VERSION_FILE = ROOT / "VERSION"
STATE_FILE = ROOT / ".yanggumi-update-state.json"
RESTORE_ROOT = ROOT / "backups" / "update_restore_points"
API_BASE = os.environ.get("YANGGUMI_UPDATE_API_BASE") or f"https://api.github.com/repos/{OWNER}/{REPOSITORY}"
RAW_BASE = os.environ.get("YANGGUMI_UPDATE_RAW_BASE") or f"https://raw.githubusercontent.com/{OWNER}/{REPOSITORY}"

PROTECTED_PREFIXES = (
    ".git/", ".venv/", "backups/", "data/", "exports/", "logs/", "work/",
)
PROTECTED_EXACT = {
    ".yanggumi-update-state.json", ".streamlit/secrets.toml", "VERSION",
}
REQUIRED_AFTER_UPDATE = ("app.py", "database.py", "启动 Yang-gumi.bat", "update_yanggumi.py")


class UpdateError(RuntimeError):
    pass


def _request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Yang-gumi-Updater/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpdateError(f"无法连接 GitHub：{exc}") from exc


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Yang-gumi-Updater/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError(f"下载更新文件失败：{exc}") from exc


def _safe_relative(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise UpdateError(f"远端返回了不安全的文件路径：{value}")
    return path.as_posix()


def _protected(path: str) -> bool:
    normalized = _safe_relative(path)
    return normalized in PROTECTED_EXACT or normalized.startswith(PROTECTED_PREFIXES)


def _read_version() -> str:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if state.get("version"):
                return str(state["version"])
        except (OSError, json.JSONDecodeError):
            pass
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
        return value if value else INITIAL_VERSION
    except OSError:
        return INITIAL_VERSION


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if state.get("commit"):
                return state
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _tag_commit() -> str:
    payload = _request_json(f"{API_BASE}/git/ref/tags/{urllib.parse.quote(INITIAL_TAG, safe='')}")
    obj = payload.get("object") or {}
    sha = str(obj.get("sha") or "")
    if obj.get("type") == "tag" and sha:
        sha = str((_request_json(f"{API_BASE}/git/tags/{sha}").get("object") or {}).get("sha") or "")
    if not sha:
        raise UpdateError(f"GitHub 缺少初始版本标记 {INITIAL_TAG}。")
    return sha


def _head_commit() -> str:
    payload = _request_json(f"{API_BASE}/commits/{urllib.parse.quote(BRANCH, safe='')}")
    sha = str(payload.get("sha") or "")
    if not sha:
        raise UpdateError("无法读取 GitHub 主分支版本。")
    return sha


def _parse_version(version: str) -> tuple[int, int, int]:
    try:
        major, minor, patch = (int(part) for part in version.strip().split(".", 2))
        return major, minor, patch
    except (TypeError, ValueError):
        return 1, 0, 0


def _classify(compare: dict[str, Any]) -> tuple[str, str]:
    messages = "\n".join(
        str(((commit.get("commit") or {}).get("message") or ""))
        for commit in compare.get("commits") or []
    ).casefold()
    files = compare.get("files") or []
    changes = sum(int(item.get("changes") or 0) for item in files)
    paths = [str(item.get("filename") or "").casefold() for item in files]
    breaking = ("breaking change", "breaking:", "major:", "[major]", "不兼容")
    feature = ("feat:", "feat(", "feature:", "新增", "新功能", "增加功能")
    if any(marker in messages for marker in breaking):
        return "major", "检测到破坏性或不兼容更新"
    if any(marker in messages for marker in feature):
        return "minor", "检测到新功能"
    source_files = sum(path.endswith(".py") for path in paths)
    if len(files) >= 15 or changes >= 1200 or source_files >= 8:
        return "minor", "更新范围较大"
    return "patch", "修复或小幅调整"


def _bump(version: str, level: str) -> str:
    major, minor, patch = _parse_version(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _backup(paths: list[str], old_version: str, new_version: str, base: str, head: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = RESTORE_ROOT / f"{stamp}-v{old_version}-to-v{new_version}"
    files_root = backup / "files"
    data_root = backup / "data_snapshot"
    backup.mkdir(parents=True, exist_ok=False)
    entries = []
    for rel in sorted(set(paths) | {"VERSION", ".yanggumi-update-state.json"}):
        rel = _safe_relative(rel)
        source = ROOT / Path(*PurePosixPath(rel).parts)
        existed = source.is_file()
        entries.append({"path": rel, "existed": existed})
        if existed:
            target = files_root / Path(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    data_dir = ROOT / "data"
    if data_dir.exists():
        for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
            for source in data_dir.glob(pattern):
                data_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, data_root / source.name)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "from_version": old_version, "to_version": new_version,
        "base_commit": base, "head_commit": head, "entries": entries,
    }
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup


def _restore(backup: Path) -> None:
    manifest_path = backup / "manifest.json"
    if not manifest_path.exists():
        raise UpdateError("回滚点缺少 manifest.json。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_root = backup / "files"
    for entry in manifest.get("entries") or []:
        rel = _safe_relative(entry["path"])
        target = ROOT / Path(*PurePosixPath(rel).parts)
        saved = files_root / Path(*PurePosixPath(rel).parts)
        if entry.get("existed"):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
        elif target.exists() and target.is_file():
            target.unlink()
    data_root = backup / "data_snapshot"
    if data_root.exists():
        target_data = ROOT / "data"
        target_data.mkdir(parents=True, exist_ok=True)
        for source in data_root.iterdir():
            if source.is_file():
                shutil.copy2(source, target_data / source.name)


def _download_changes(files: list[dict[str, Any]], head: str, staging: Path) -> list[dict[str, Any]]:
    applicable = []
    for item in files:
        filename = _safe_relative(item.get("filename") or "")
        previous = item.get("previous_filename")
        if _protected(filename) or (previous and _protected(previous)):
            continue
        status = str(item.get("status") or "modified")
        normalized = {**item, "filename": filename, "previous_filename": _safe_relative(previous) if previous else None}
        if status != "removed":
            url = f"{RAW_BASE}/{urllib.parse.quote(head, safe='')}/{urllib.parse.quote(filename, safe='/')}"
            content = _download(url)
            expected = str(item.get("sha") or "")
            if expected and _git_blob_sha(content) != expected:
                raise UpdateError(f"文件校验失败：{filename}")
            target = staging / Path(*PurePosixPath(filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        applicable.append(normalized)
    return applicable


def _apply(applicable: list[dict[str, Any]], staging: Path) -> list[Path]:
    changed_python = []
    for item in applicable:
        filename = item["filename"]
        previous = item.get("previous_filename")
        status = item.get("status")
        if previous and previous != filename:
            old_target = ROOT / Path(*PurePosixPath(previous).parts)
            if old_target.exists() and old_target.is_file():
                old_target.unlink()
        target = ROOT / Path(*PurePosixPath(filename).parts)
        if status == "removed":
            if target.exists() and target.is_file():
                target.unlink()
            continue
        source = staging / Path(*PurePosixPath(filename).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        if target.suffix.lower() == ".py":
            changed_python.append(target)
    return changed_python


def _validate(changed_python: list[Path]) -> None:
    for required in REQUIRED_AFTER_UPDATE:
        if not (ROOT / required).exists():
            raise UpdateError(f"更新后缺少必要文件：{required}")
    for path in changed_python:
        py_compile.compile(str(path), doraise=True)


def _write_state(version: str, head: str, level: str) -> None:
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    STATE_FILE.write_text(json.dumps({
        "version": version, "commit": head, "level": level,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def check_and_update() -> int:
    current_version = _read_version()
    print(f"当前网站版本：{current_version}")
    print("正在检查 GitHub 仓库更新……")
    state = _load_state()
    base = str(state.get("commit") or _tag_commit())
    head = _head_commit()
    if base == head:
        print(f"GitHub 仓库没有更新。当前已是最新版本 {current_version}。")
        print("网站和更新程序均未进行任何修改。")
        return 0
    compare = _request_json(f"{API_BASE}/compare/{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}")
    if compare.get("status") == "behind":
        raise UpdateError("本地记录比 GitHub 主分支更新，已停止以避免覆盖。")
    files = compare.get("files") or []
    if not files:
        _write_state(current_version, head, "none")
        print("GitHub 提交已变化，但没有需要替换的文件。版本保持不变。")
        return 0
    if len(files) >= 300:
        raise UpdateError("本次更新文件过多，GitHub 比较结果可能不完整；请使用完整安装包。")
    level, reason = _classify(compare)
    target_version = _bump(current_version, level)
    applicable_paths = []
    for item in files:
        filename = _safe_relative(item.get("filename") or "")
        previous = item.get("previous_filename")
        if not _protected(filename) and not (previous and _protected(previous)):
            applicable_paths.append(filename)
            if previous:
                applicable_paths.append(_safe_relative(previous))
    print(f"发现更新：{current_version}  →  {target_version}")
    print(f"版本判断：{level.upper()}（{reason}）")
    print(f"将更新 {len(set(applicable_paths))} 个程序文件；本地数据库、备份和私人配置不会下载或覆盖。")
    choice = os.environ.get("YANGGUMI_UPDATE_SKIP_PROMPT", "").strip().upper()
    if choice not in {"Y", "N"}:
        choice = input("是否更新？请输入 Y 或 N：").strip().upper()
    if choice != "Y":
        print("已选择 N：取消更新，未修改任何文件。")
        return 0
    backup = _backup(applicable_paths, current_version, target_version, base, head)
    print(f"已创建更新前返回点：{backup}")
    try:
        with tempfile.TemporaryDirectory(prefix="yanggumi-update-") as temp_dir:
            staging = Path(temp_dir)
            applicable = _download_changes(files, head, staging)
            changed_python = _apply(applicable, staging)
            _validate(changed_python)
        _write_state(target_version, head, level)
    except Exception:
        _restore(backup)
        print("更新失败，已自动恢复到更新前状态。")
        raise
    print(f"更新成功。当前网站版本：{target_version}")
    print("请关闭并重新启动 Yang-gumi，使新版本完全生效。")
    return 0


def rollback_latest() -> int:
    backups = sorted((path for path in RESTORE_ROOT.glob("*") if (path / "manifest.json").exists()), reverse=True)
    if not backups:
        print("没有找到可用的更新返回点。")
        return 0
    backup = backups[0]
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    print(f"最近返回点：{backup.name}")
    print(f"将恢复到版本：{manifest.get('from_version', '未知')}")
    choice = os.environ.get("YANGGUMI_UPDATE_SKIP_PROMPT", "").strip().upper()
    if choice not in {"Y", "N"}:
        choice = input("是否恢复？请输入 Y 或 N：").strip().upper()
    if choice != "Y":
        print("已取消恢复。")
        return 0
    _restore(backup)
    print("已恢复最近一次更新前的程序与数据库快照。请重新启动 Yang-gumi。")
    return 0


def main() -> int:
    command = (sys.argv[1] if len(sys.argv) > 1 else "check").casefold()
    try:
        if command == "rollback":
            return rollback_latest()
        return check_and_update()
    except (UpdateError, OSError, ValueError, py_compile.PyCompileError) as exc:
        print(f"[错误] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
