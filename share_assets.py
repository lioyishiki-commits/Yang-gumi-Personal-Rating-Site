from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote
from urllib.request import Request, urlopen

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
CACHE_ROOT = STATIC_ROOT / "share_assets"
DATABASE_PATH = ROOT / "data" / "acgn.db"
DAILY_ART_MANIFEST_PATH = ROOT / "data" / "image_manifest.json"
BANGUMI_POSTER_ROOT = STATIC_ROOT / "bangumi_rank_posters"
SEASONAL_POSTER_ROOT = STATIC_ROOT / "seasonal_posters"

SHARE_ASSET_VERSION = 3
MAX_SOURCE_BYTES = 24 * 1024 * 1024
WORK_POSTER_SIZE = (720, 1080)
SEASONAL_POSTER_SIZE = (720, 1080)
REMOTE_HEADERS = {
    "User-Agent": "Yang-gumi/1.0 (+read-only share image cache)",
    "Referer": "https://bgm.tv/",
}

_cache_lock = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def enabled() -> bool:
    if os.getenv("YANGGUMI_SHARE_ASSETS", "0") == "1":
        return True
    try:
        import streamlit as st

        return str(st.context.headers.get("X-Yanggumi-Read-Only") or "") == "1"
    except (ImportError, RuntimeError):
        return False


def _safe_key(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "asset")).strip("-.")
    return cleaned[:72] or "asset"


def _local_source_path(source: str) -> Path | None:
    if source.startswith("/app/static/"):
        candidate = STATIC_ROOT / unquote(source.removeprefix("/app/static/"))
    elif source.startswith(("http://", "https://", "data:")):
        return None
    else:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _source_revision(source: str) -> str:
    path = _local_source_path(source)
    if path is None:
        return source
    try:
        stat = path.stat()
        return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        return str(path)


def _cache_path(bucket: str, key: Any, source: str, max_size: tuple[int, int], quality: int) -> Path:
    fingerprint = hashlib.sha256(
        f"v{SHARE_ASSET_VERSION}|{bucket}|{key}|{_source_revision(source)}|{max_size}|{quality}".encode("utf-8")
    ).hexdigest()[:18]
    return CACHE_ROOT / bucket / f"{_safe_key(key)}-{fingerprint}.webp"


def _static_url(path: Path) -> str:
    relative = path.resolve().relative_to(STATIC_ROOT.resolve()).as_posix()
    return f"/app/static/{relative}"


def _read_source(source: str, *, allow_remote: bool) -> bytes | None:
    path = _local_source_path(source)
    if path is not None:
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None
    if not allow_remote or not source.startswith(("http://", "https://")):
        return None
    try:
        request = Request(source, headers=REMOTE_HEADERS)
        with urlopen(request, timeout=10) as response:
            payload = response.read(MAX_SOURCE_BYTES + 1)
        return payload if 0 < len(payload) <= MAX_SOURCE_BYTES else None
    except Exception:
        return None


def optimized_image_url(
    source: str | None,
    *,
    bucket: str,
    key: Any,
    max_size: tuple[int, int],
    quality: int = 92,
    allow_remote: bool = False,
) -> str:
    source = str(source or "").strip()
    if not source:
        return ""
    target = _cache_path(bucket, key, source, max_size, quality)
    if target.is_file() and target.stat().st_size > 0:
        return _static_url(target)

    with _cache_lock:
        path_lock = _path_locks.setdefault(str(target), threading.Lock())
    with path_lock:
        if target.is_file() and target.stat().st_size > 0:
            return _static_url(target)
        payload = _read_source(source, allow_remote=allow_remote)
        if not payload:
            return source
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail(max_size, Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, "WEBP", quality=quality, method=4)
            encoded = output.getvalue()
            if not encoded:
                return source
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_bytes(encoded)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return _static_url(target)
        except (OSError, ValueError, Image.DecompressionBombError):
            return source


def _existing_local_work_poster(bangumi_id: Any) -> Path | None:
    if not str(bangumi_id or "").isdigit():
        return None
    subject_id = int(bangumi_id)
    for path in sorted(BANGUMI_POSTER_ROOT.glob(f"{subject_id}.*")):
        if path.is_file() and path.stat().st_size > 0:
            return path
    for path in sorted(SEASONAL_POSTER_ROOT.glob(f"*/{subject_id}.*")):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def work_cover_url(work: dict[str, Any], *, allow_remote: bool = False) -> str:
    bangumi_id = work.get("bangumi_id")
    local = _existing_local_work_poster(bangumi_id)
    source = str(local) if local else str(
        work.get("bangumi_image_url") or work.get("cover_url") or work.get("cover_path") or ""
    )
    if not source:
        return ""
    key = f"work-{work.get('id') or 'new'}-{bangumi_id or 'local'}"
    return optimized_image_url(
        source,
        bucket="covers",
        key=key,
        max_size=WORK_POSTER_SIZE,
        quality=92,
        allow_remote=allow_remote,
    )


def seasonal_poster_url(
    source: str | None, *, key: Any, allow_remote: bool = False
) -> str:
    return optimized_image_url(
        source,
        bucket="seasonal",
        key=key,
        max_size=SEASONAL_POSTER_SIZE,
        quality=92,
        allow_remote=allow_remote,
    )


def daily_art_url(source: str | None, *, key: Any, kind: str) -> str:
    # daily_art already contains focus-aware 720x1080 / 1280x720 homepage assets.
    # Re-encoding them again only loses detail, so sharing serves those files directly.
    return str(source or "")


def _work_rows() -> list[dict[str, Any]]:
    if not DATABASE_PATH.is_file():
        return []
    connection = sqlite3.connect(f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id, bangumi_id, bangumi_image_url, cover_url, cover_path FROM works"
            ).fetchall()
        ]
    finally:
        connection.close()


def _current_season_paths(today: date | None = None) -> list[Path]:
    current = today or date.today()
    quarter = (current.month - 1) // 3 + 1
    folder = SEASONAL_POSTER_ROOT / f"{current.year}_Q{quarter}"
    return [path for path in folder.glob("*") if path.is_file()]


def _current_season_rows(today: date | None = None) -> list[dict[str, Any]]:
    if not DATABASE_PATH.is_file():
        return []
    current = today or date.today()
    season_code = f"Q{(current.month - 1) // 3 + 1}"
    connection = sqlite3.connect(
        f"file:{DATABASE_PATH.as_posix()}?mode=ro", uri=True, timeout=5
    )
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT bangumi_id, image_url
                FROM seasonal_anime_cache
                WHERE season_year=? AND season_code=? AND COALESCE(is_hidden, 0)=0
                """,
                (current.year, season_code),
            ).fetchall()
        ]
    finally:
        connection.close()


def _current_season_source(row: dict[str, Any], today: date | None = None) -> str:
    current = today or date.today()
    season_code = f"Q{(current.month - 1) // 3 + 1}"
    bangumi_id = row.get("bangumi_id")
    if str(bangumi_id or "").isdigit():
        folder = SEASONAL_POSTER_ROOT / f"{current.year}_{season_code}"
        for path in sorted(folder.glob(f"{int(bangumi_id)}.*")):
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
    return str(row.get("image_url") or "")


def _daily_art_items() -> list[dict[str, Any]]:
    try:
        payload = json.loads(DAILY_ART_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    return [item for item in payload.get("items", []) if isinstance(item, dict) and item.get("asset")]


def prepare_share_assets(max_workers: int = 6) -> dict[str, int]:
    """Incrementally prepare every image the current live share can expose."""
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, Callable[[], str]]] = []
    for work in _work_rows():
        tasks.append(("covers", lambda work=work: work_cover_url(work, allow_remote=True)))
    season_rows = _current_season_rows()
    if season_rows:
        for row in season_rows:
            source = _current_season_source(row)
            tasks.append((
                "seasonal",
                lambda row=row, source=source: seasonal_poster_url(
                    source,
                    key=f"current-{row.get('bangumi_id') or _safe_key(source)}",
                    allow_remote=True,
                ),
            ))
    else:
        for path in _current_season_paths():
            tasks.append((
                "seasonal",
                lambda path=path: seasonal_poster_url(str(path), key=f"current-{path.stem}"),
            ))
    stats = {"covers": 0, "seasonal": 0, "daily_art": 0, "failed": 0}
    if not tasks:
        return stats
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(tasks)))) as executor:
        pending = {executor.submit(callback): bucket for bucket, callback in tasks}
        for future in as_completed(pending):
            bucket = pending[future]
            try:
                result = future.result()
                if result.startswith("/app/static/share_assets/"):
                    stats[bucket] += 1
                else:
                    stats["failed"] += 1
            except Exception:
                stats["failed"] += 1
    return stats


def source_revision() -> tuple[int, int, int, int]:
    def mtime(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    season_paths = _current_season_paths()
    season_mtime = max((mtime(path) for path in season_paths), default=0)
    return mtime(DATABASE_PATH), mtime(DAILY_ART_MANIFEST_PATH), len(season_paths), season_mtime


def run_prewarmer(
    stop_requested: Callable[[], bool],
    interval: float = 15.0,
    initial_revision: tuple[int, int, int, int] | None = None,
) -> None:
    """Keep future works and season changes warm without restarting sharing."""
    previous = initial_revision
    while not stop_requested():
        revision = source_revision()
        if revision != previous:
            try:
                prepare_share_assets()
                previous = revision
            except Exception:
                # A failed refresh must never take down the live read-only share.
                previous = None
        deadline = time.monotonic() + max(interval, 1.0)
        while time.monotonic() < deadline and not stop_requested():
            time.sleep(0.5)
