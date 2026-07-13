from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "data" / "image_manifest.json"
SETTINGS_PATH = ROOT / "data" / "daily_art_settings.json"
ASSET_DIR = ROOT / "static" / "daily_art"
MANIFEST_VERSION = 9
DEFAULT_LOCAL_ROOTS = {
    "portrait": Path(
        os.getenv("YANGGUMI_PORTRAIT_DIR")
        or Path.home() / "Pictures" / "Yang-gumi" / "Portrait"
    ),
    "wallpaper": Path(
        os.getenv("YANGGUMI_WALLPAPER_DIR")
        or Path.home() / "Pictures" / "Yang-gumi" / "Wallpaper"
    ),
}


def load_source_folders() -> dict[str, Path]:
    roots = dict(DEFAULT_LOCAL_ROOTS)
    try:
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        for kind in roots:
            value = str(payload.get(kind) or "").strip()
            if value:
                roots[kind] = Path(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return roots


LOCAL_ROOTS = load_source_folders()
REFRESH_QUOTAS = {"portrait": 300, "wallpaper": 60}
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".avif", ".bmp", ".gif"}
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_INDEX_ITEMS = 500
MAX_SAMPLE_ITEMS = 500
MAX_CACHED_ASSETS = 900
MAX_SCAN_FILES = 12000
MAX_SCAN_DEPTH = 12
TARGET_ASPECTS = {"portrait": 2 / 3, "wallpaper": 16 / 9}

_refresh_lock = threading.Lock()
_scheduler_lock = threading.Lock()
_scheduler_started = False


def set_source_folder(kind: str, folder: str | Path) -> Path:
    if kind not in DEFAULT_LOCAL_ROOTS:
        raise ValueError(f"Unsupported daily art source: {kind}")
    selected = Path(folder).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError("Selected daily art source is not a folder")
    roots = load_source_folders()
    roots[kind] = selected
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({key: str(value) for key, value in roots.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(SETTINGS_PATH)
    LOCAL_ROOTS.clear()
    LOCAL_ROOTS.update(roots)
    return selected


def choose_source_folder(kind: str) -> Path | None:
    """Open the local desktop folder chooser and persist the selected source."""
    if kind not in DEFAULT_LOCAL_ROOTS:
        raise ValueError(f"Unsupported daily art source: {kind}")
    initial = LOCAL_ROOTS.get(kind, DEFAULT_LOCAL_ROOTS[kind])
    if not initial.exists():
        initial = initial.parent if initial.parent.exists() else Path.home()
    label = "竖屏" if kind == "portrait" else "壁纸"

    if os.name == "nt":
        def ps_literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        script = f"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = {ps_literal(f'选择{label}美图文件夹')}
$dialog.SelectedPath = {ps_literal(str(initial))}
$dialog.ShowNewFolderButton = $true
try {{
    $owner.Show()
    $owner.Activate()
    $result = $dialog.ShowDialog($owner)
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
        [Console]::Write($dialog.SelectedPath)
    }}
}} finally {{
    $dialog.Dispose()
    $owner.Close()
    $owner.Dispose()
}}
"""
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=900,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Windows folder chooser is unavailable") from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Windows folder chooser failed"
            raise RuntimeError(message)
        selected = completed.stdout.strip()
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog

            owner = tk.Tk()
            owner.withdraw()
            owner.attributes("-topmost", True)
            try:
                selected = filedialog.askdirectory(
                    parent=owner,
                    title=f"选择{label}美图文件夹",
                    initialdir=str(initial),
                    mustexist=True,
                )
            finally:
                owner.destroy()
        except Exception as exc:
            raise RuntimeError(
                "Desktop folder chooser is unavailable; configure YANGGUMI_PORTRAIT_DIR "
                "or YANGGUMI_WALLPAPER_DIR before starting Yang-gumi"
            ) from exc
    return set_source_folder(kind, selected) if selected else None


def _hour_slot(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%dT%H")


def _iter_shallow(root: Path):
    """Yield files recursively from the folder explicitly selected by the user.

    Image libraries copied from another computer are often grouped several folders
    deep.  The previous one-level scan silently missed those files.  Traversal stays
    bounded and never follows directory links, so a mistaken selection cannot turn
    into an unbounded disk scan.
    """
    stack: list[tuple[Path, int]] = [(root, 0)]
    examined = 0
    while stack and examined < MAX_SCAN_FILES:
        current, depth = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if examined >= MAX_SCAN_FILES:
                break
            try:
                if child.is_file():
                    examined += 1
                    yield child
                elif depth < MAX_SCAN_DEPTH and child.is_dir() and not child.is_symlink():
                    stack.append((child, depth + 1))
            except OSError:
                continue


def _asset_name(path: Path, mtime: float) -> str:
    digest = hashlib.sha256(f"v{MANIFEST_VERSION}|{path}|{mtime}".encode("utf-8", "ignore")).hexdigest()[:24]
    return f"{digest}.webp"


def _stable_key(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()[:24]


def _group_key(path: Path, root: Path) -> str:
    try:
        relative = path.parent.relative_to(root)
        group = str(relative) if str(relative) not in {"", "."} else path.stem
    except ValueError:
        group = path.stem
    return _stable_key(group)


def _focus_position(image: Image.Image) -> str:
    """Approximate anime/person focus from saturation and edge detail for safer cover crops."""
    thumb = image.copy()
    thumb.thumbnail((96, 96), Image.Resampling.BILINEAR)
    rgb = thumb.convert("RGB")
    hsv = rgb.convert("HSV")
    edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    width, height = rgb.size
    total = x_sum = y_sum = 0.0
    for y in range(height):
        vertical_bias = 1.1 if 0.15 <= y / max(height, 1) <= 0.72 else 0.72
        for x in range(width):
            _, saturation, value = hsv.getpixel((x, y))
            edge = edges.getpixel((x, y))
            weight = (saturation * 0.9 + edge * 1.15 + value * 0.18) * vertical_bias
            if weight <= 30:
                continue
            total += weight
            x_sum += x * weight
            y_sum += y * weight
    if total <= 0:
        return "50% 45%"
    focus_x = min(68, max(32, round((x_sum / total) / max(width - 1, 1) * 100)))
    focus_y = min(70, max(24, round((y_sum / total) / max(height - 1, 1) * 100)))
    return f"{focus_x}% {focus_y}%"


def _parse_focus(focus: str) -> tuple[float, float]:
    try:
        raw_x, raw_y = focus.replace("%", "").split()[:2]
        return float(raw_x) / 100.0, float(raw_y) / 100.0
    except (ValueError, IndexError):
        return 0.5, 0.45


def _trim_plain_border(image: Image.Image) -> Image.Image:
    """Remove obvious black/white/plain margins before making the homepage crop."""
    width, height = image.size
    if width < 80 or height < 80:
        return image
    sample_points = [
        image.getpixel((0, 0)), image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)), image.getpixel((width - 1, height - 1)),
    ]
    background = tuple(sum(pixel[channel] for pixel in sample_points) // len(sample_points) for channel in range(3))
    diff = ImageChops.difference(image, Image.new("RGB", image.size, background)).convert("L")
    mask = diff.point(lambda value: 255 if value > 24 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    cropped_w = right - left
    cropped_h = bottom - top
    if cropped_w * cropped_h < width * height * 0.22:
        return image
    pad_x = max(8, int(cropped_w * 0.04))
    pad_y = max(8, int(cropped_h * 0.04))
    box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )
    if (box[2] - box[0]) < width * 0.35 or (box[3] - box[1]) < height * 0.35:
        return image
    return image.crop(box)


def _crop_to_aspect(image: Image.Image, aspect: float, focus: str) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        return image
    current_aspect = width / height
    focus_x, focus_y = _parse_focus(focus)
    if current_aspect > aspect:
        crop_w = int(height * aspect)
        center_x = int(width * focus_x)
        left = min(max(0, center_x - crop_w // 2), max(0, width - crop_w))
        return image.crop((left, 0, left + crop_w, height))
    crop_h = int(width / aspect)
    center_y = int(height * focus_y)
    top = min(max(0, center_y - crop_h // 2), max(0, height - crop_h))
    return image.crop((0, top, width, top + crop_h))


def _homepage_asset(image: Image.Image, kind: str, focus: str) -> Image.Image:
    trimmed = _trim_plain_border(image)
    target_size = (720, 1080) if kind == "portrait" else (1280, 720)
    cropped = _crop_to_aspect(trimmed, TARGET_ASPECTS.get(kind, 1.42), focus)
    return cropped.resize(target_size, Image.Resampling.LANCZOS)


def rebuild_manifest(only_kind: str | None = None) -> dict[str, Any]:
    """Build the full cache, or refresh only one selected source folder."""
    if only_kind is not None and only_kind not in LOCAL_ROOTS:
        raise ValueError(f"Unsupported daily art source: {only_kind}")
    _refresh_lock.acquire()
    try:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        scan_stats: dict[str, dict[str, int]] = {}
        previous: dict[str, Any] = {}
        if only_kind is not None:
            try:
                previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
                scan_stats.update({
                    key: {
                        "files_checked": int(value.get("files_checked") or 0),
                        "supported": int(value.get("supported") or 0),
                        "accepted": int(value.get("accepted") or 0),
                        "unreadable": int(value.get("unreadable") or 0),
                    }
                    for key, value in (previous.get("scan_stats") or {}).items()
                    if key in LOCAL_ROOTS and isinstance(value, dict)
                })
                for item in previous.get("items", [])[:MAX_INDEX_ITEMS]:
                    if item.get("type") == only_kind or item.get("type") not in LOCAL_ROOTS:
                        continue
                    asset = ASSET_DIR / Path(str(item.get("asset") or "")).name
                    if item.get("asset") and asset.exists():
                        entries.append(item)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        for kind, root in LOCAL_ROOTS.items():
            if only_kind is not None and kind != only_kind:
                continue
            quota = REFRESH_QUOTAS[kind]
            if not root.exists():
                scan_stats[kind] = {"files_checked": 0, "supported": 0, "accepted": 0, "unreadable": 0}
                continue
            scanned_paths = list(_iter_shallow(root) or [])
            paths = [path for path in scanned_paths if path.suffix.casefold() in ALLOWED_SUFFIXES]
            random.SystemRandom().shuffle(paths)
            accepted = 0
            unreadable = 0
            for path in paths:
                if accepted >= quota:
                    break
                try:
                    stat = path.stat()
                    if stat.st_size <= 0 or stat.st_size > MAX_FILE_SIZE:
                        continue
                    asset_name = _asset_name(path, stat.st_mtime)
                    cached_asset = ASSET_DIR / asset_name
                    with Image.open(path) as source:
                        source.verify()
                    with Image.open(path) as source:
                        image = ImageOps.exif_transpose(source).convert("RGB")
                        width, height = image.size
                        focus = _focus_position(_trim_plain_border(image))
                        if not cached_asset.exists():
                            asset_image = _homepage_asset(image, kind, focus)
                            asset_image.save(cached_asset, "WEBP", quality=80, method=4)
                    entries.append({
                        "path": str(path), "size": stat.st_size, "width": width, "height": height,
                        "type": kind, "mtime": stat.st_mtime, "asset": f"daily_art/{asset_name}",
                        "key": _stable_key(asset_name), "group": _group_key(path, root), "focus": focus,
                    })
                    accepted += 1
                except (OSError, ValueError, Image.DecompressionBombError):
                    unreadable += 1
                    continue
            scan_stats[kind] = {
                "files_checked": len(scanned_paths),
                "supported": len(paths),
                "accepted": accepted,
                "unreadable": unreadable,
            }
        referenced = {Path(item["asset"]).name for item in entries}
        cached_assets = sorted(
            ASSET_DIR.glob("*.webp"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        removable = [item for item in cached_assets if item.name not in referenced]
        keep_extra = max(0, MAX_CACHED_ASSETS - len(referenced))
        for cached in removable[keep_extra:]:
            cached.unlink(missing_ok=True)
        now = datetime.now()
        payload = {
            "version": MANIFEST_VERSION,
            "updated_at": now.isoformat(timespec="seconds"),
            "refresh_slot": _hour_slot(now),
            "scan_stats": scan_stats,
            "items": entries,
        }
        temporary = MANIFEST_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(MANIFEST_PATH)
        return payload
    finally:
        _refresh_lock.release()


def _read_manifest(validate: bool = True) -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        items = []
        for item in payload.get("items", [])[:MAX_INDEX_ITEMS]:
            if item.get("type") not in LOCAL_ROOTS or not item.get("asset"):
                continue
            asset = ASSET_DIR / Path(str(item["asset"])).name
            if (not validate or asset.exists()) and int(item.get("size") or 0) <= MAX_FILE_SIZE:
                normalized = dict(item)
                normalized["key"] = str(item.get("key") or _stable_key(item.get("asset")))
                normalized["group"] = str(item.get("group") or normalized["key"])
                normalized["focus"] = str(item.get("focus") or "50% 45%")
                normalized.pop("path", None)
                items.append(normalized)
        return {
            "version": int(payload.get("version") or 0),
            "updated_at": payload.get("updated_at"),
            "refresh_slot": payload.get("refresh_slot"),
            "scan_stats": {
                key: {
                    "files_checked": int(value.get("files_checked") or 0),
                    "supported": int(value.get("supported") or 0),
                    "accepted": int(value.get("accepted") or 0),
                    "unreadable": int(value.get("unreadable") or 0),
                }
                for key, value in (payload.get("scan_stats") or {}).items()
                if key in LOCAL_ROOTS and isinstance(value, dict)
            },
            "items": items,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": 0, "updated_at": None, "refresh_slot": None, "scan_stats": {}, "items": []}


def _refresh_if_stale() -> None:
    manifest = _read_manifest(validate=False)
    if manifest.get("version") != MANIFEST_VERSION or manifest.get("refresh_slot") != _hour_slot():
        rebuild_manifest()


def _scheduler() -> None:
    _refresh_if_stale()
    while True:
        now = datetime.now()
        next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        time.sleep(max(1.0, (next_hour - now).total_seconds()))
        rebuild_manifest()


def start_hourly_refresh() -> None:
    """Start one daemon that refreshes at 00:00, 01:00 … 23:00 without blocking page renders."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        threading.Thread(target=_scheduler, name="yanggumi-daily-art-hourly", daemon=True).start()


def load_manifest() -> dict[str, Any]:
    """Read only the cheap current JSON. The hourly daemon refreshes it in the background."""
    return _read_manifest(validate=True)


def refresh_manifest_async_if_needed(manifest: dict[str, Any]) -> bool:
    if manifest.get("version") == MANIFEST_VERSION and manifest.get("refresh_slot") == _hour_slot():
        return False
    if _refresh_lock.locked():
        return True
    threading.Thread(target=_refresh_if_stale, name="yanggumi-daily-art-refresh-once", daemon=True).start()
    return True


def browser_candidates(items: list[dict[str, Any]], kind: str) -> list[dict[str, str]]:
    values = [item for item in items if item.get("type") == kind]
    random.SystemRandom().shuffle(values)
    return [
        {
            "src": f"/app/static/{item['asset']}",
            "key": str(item.get("key") or _stable_key(item.get("asset"))),
            "group": str(item.get("group") or item.get("key") or _stable_key(item.get("asset"))),
            "focus": str(item.get("focus") or "50% 45%"),
        }
        for item in values[:MAX_SAMPLE_ITEMS]
    ]
