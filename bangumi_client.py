"""Small, defensive client for Bangumi's public v0 API."""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import requests

import bangumi_archive

API_BASE = "https://api.bgm.tv/v0"
WEB_BASE = "https://bgm.tv/subject"
USER_AGENT = "Yang-gumi/1.0 (+local personal rating site)"
TIMEOUT = 15
CATEGORY_LABELS = ("全部", "动画", "漫画", "轻小说", "游戏", "其他")
CATEGORY_SUBJECT_TYPES: dict[str, tuple[int, ...] | None] = {
    "全部": (1, 2, 4),
    "动画": (2,),
    "漫画": (1,),
    "轻小说": (1,),
    "游戏": (4,),
    "其他": (1, 2, 4),
}
RANKING_SUBJECT_TYPES = {"动画": 2, "漫画": 1, "小说": 1, "游戏": 4}
RANKING_CATEGORY_LABELS = ("动画", "漫画", "小说", "游戏")
RANKING_BROWSER_URLS = {
    "动画": "https://bgm.tv/anime/browser?sort=rank",
    "漫画": "https://bgm.tv/book/browser?cat=1001&sort=rank",
    "小说": "https://bgm.tv/book/browser?cat=1002&sort=rank",
    "游戏": "https://bgm.tv/game/browser?sort=rank",
}
RANKING_API_FILTERS = {
    "动画": {"type": 2},
    "漫画": {"type": 1, "cat": 1001},
    "小说": {"type": 1, "cat": 1002},
    "游戏": {"type": 4},
}
RAW_TYPE_NAMES = {1: "书籍", 2: "动画", 3: "音乐", 4: "游戏", 6: "三次元"}
RELEVANCE_ORDER = {
    "strict_exact": 4,
    "strict_contains": 3,
    "series_related": 2,
    "possible": 1,
    "irrelevant": 0,
}
RELEVANCE_LABELS = {
    "strict_exact": "完全匹配",
    "strict_contains": "标题包含",
    "series_related": "同系列",
    "possible": "可能相关",
}


class BangumiError(RuntimeError):
    pass


_readonly_access_token: ContextVar[str] = ContextVar(
    "yanggumi_bangumi_readonly_access_token", default="",
)


def set_readonly_access_token(token: str | None) -> None:
    """Set the current Streamlit session's optional read-only Bearer token."""
    _readonly_access_token.set(str(token or "").strip())


def readonly_access_token() -> str:
    return _readonly_access_token.get().strip()


def readonly_account_connected() -> bool:
    return bool(readonly_access_token())


ROOT = Path(__file__).resolve().parent
RANKING_CACHE_PATH = ROOT / "data" / "bangumi_ranking_cache.json"
RATING_PRECISION_CACHE_PATH = ROOT / "data" / "bangumi_rating_precision.json"
COVER_CACHE_PATH = ROOT / "data" / "bangumi_cover_cache.json"
PERSISTED_CONNECTION_PATH = ROOT / "data" / "bangumi_readonly_connection.bin"
R18_FALLBACK_COVER_DIR = ROOT / "static" / "r18_fallback_covers"
RANKING_CACHE_VERSION = 11
RATING_PRECISION_CACHE_VERSION = 1
_ranking_cache: dict[tuple[str, int, str], tuple[float, list[dict[str, Any]]]] = {}
_ranking_window_cache: dict[tuple[str, int, int, str], tuple[float, list[dict[str, Any]]]] = {}
_ranking_inventory_cache: dict[tuple[str, str, int, int], tuple[int, bool]] = {}
_ranking_subjects_cache: dict[
    tuple[str, str, int, int], list[dict[str, Any]]
] = {}
_RANKING_CACHE_SECONDS = 60 * 60
# Keep 300 pages available today, but never treat that reserve as the data
# ceiling. The official cache grows to exhaustion; this larger value is only
# a defensive guard against a malformed endless upstream response.
RANKING_INITIAL_CAPACITY = 7200
RANKING_MAX_ITEMS = 50_000
RANKING_UNRANKED_SENTINEL = 1_000_000_000
_ranking_prewarm_lock = threading.Lock()
_ranking_prewarm_inflight: set[tuple[str, str]] = set()
_ranking_daily_refresh_lock = threading.Lock()
_ranking_refresh_state_lock = threading.Lock()
_ranking_refresh_inflight = False
_rating_precision_lock = threading.Lock()
_rating_precision_inflight: set[int] = set()
_cover_cache_lock = threading.Lock()
_cover_cache_state: tuple[int, dict[str, str]] = (-1, {})
_r18_cover_sync_process: subprocess.Popen[Any] | None = None


def _dpapi_transform(payload: bytes, *, protect: bool) -> bytes:
    """Protect small private data for the current Windows user with DPAPI."""
    if not payload:
        return b""
    import ctypes
    from ctypes import wintypes

    if not hasattr(ctypes, "windll"):
        raise OSError("Windows DPAPI is unavailable")

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(payload)
    source = _DataBlob(
        len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    crypt32 = ctypes.windll.crypt32
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Yang-gumi Bangumi read-only connection",
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def save_readonly_connection(access_token: str, account: dict[str, Any]) -> bool:
    """Persist a verified token encrypted for the current Windows user only."""
    token = str(access_token or "").strip()
    username = str((account or {}).get("username") or "").strip()
    if not token or not username:
        return False
    payload = json.dumps(
        {
            "version": 1,
            "access_token": token,
            "account": {
                "username": username,
                "nickname": str((account or {}).get("nickname") or "").strip(),
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        encrypted = _dpapi_transform(payload, protect=True)
        PERSISTED_CONNECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = PERSISTED_CONNECTION_PATH.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(PERSISTED_CONNECTION_PATH)
    except (OSError, ValueError, TypeError):
        return False
    return True


def load_readonly_connection() -> tuple[str, dict[str, str]]:
    """Load the DPAPI-protected connection without ever logging the token."""
    try:
        encrypted = PERSISTED_CONNECTION_PATH.read_bytes()
        decoded = _dpapi_transform(encrypted, protect=False)
        payload = json.loads(decoded.decode("utf-8"))
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return "", {}
    token = str(payload.get("access_token") or "").strip()
    raw_account = payload.get("account") if isinstance(payload, dict) else {}
    account = {
        "username": str((raw_account or {}).get("username") or "").strip(),
        "nickname": str((raw_account or {}).get("nickname") or "").strip(),
    }
    if not token or not account["username"]:
        return "", {}
    return token, account


def clear_readonly_connection() -> None:
    """Remove only Yang-gumi's locally persisted Bangumi connection."""
    try:
        PERSISTED_CONNECTION_PATH.unlink()
    except FileNotFoundError:
        pass


def start_r18_cover_sync_async_if_needed() -> bool:
    """Start one hidden, resumable full R18 cover sync for this Windows user."""
    global _r18_cover_sync_process
    if _r18_cover_sync_process is not None and _r18_cover_sync_process.poll() is None:
        return False
    token, account = load_readonly_connection()
    script = ROOT / "r18_cover_sync.py"
    if not token or not account.get("username") or not script.is_file():
        return False
    archive_ids = {
        int(row["id"])
        for row in bangumi_archive.archive_subjects()
        if int(row.get("id") or 0) > 0
    }
    covered_ids = {int(subject_id) for subject_id in _load_cover_cache()}
    fallback_ids = {
        int(path.stem)
        for path in R18_FALLBACK_COVER_DIR.glob("*.svg")
        if path.stem.isdigit()
    }
    if not (archive_ids - covered_ids - fallback_ids):
        return False
    try:
        _r18_cover_sync_process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        _r18_cover_sync_process = None
        return False
    return True


def _normalize_cover_url(value: Any) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if "no_icon_subject" in url or not url.startswith("https://lain.bgm.tv/"):
        return ""
    return url


def _load_cover_cache() -> dict[str, str]:
    global _cover_cache_state
    try:
        mtime_ns = int(COVER_CACHE_PATH.stat().st_mtime_ns)
    except OSError:
        return {}
    with _cover_cache_lock:
        if _cover_cache_state[0] == mtime_ns:
            return dict(_cover_cache_state[1])
        try:
            payload = json.loads(COVER_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            payload = {}
        raw_items = payload.get("items") if isinstance(payload, dict) else {}
        items = {
            str(subject_id): url
            for subject_id, value in (raw_items or {}).items()
            if str(subject_id).isdigit() and (url := _normalize_cover_url(value))
        }
        _cover_cache_state = (mtime_ns, items)
        return dict(items)


def _cached_cover_url(subject_id: int) -> str:
    return _load_cover_cache().get(str(int(subject_id)), "")


def _fallback_cover_url(subject_id: int) -> str:
    path = R18_FALLBACK_COVER_DIR / f"{int(subject_id)}.svg"
    if not path.is_file():
        return ""
    return f"/app/static/r18_fallback_covers/{int(subject_id)}.svg"


def _remember_cover_urls(items: dict[int, str]) -> None:
    normalized = {
        str(int(subject_id)): url
        for subject_id, value in items.items()
        if int(subject_id) > 0 and (url := _normalize_cover_url(value))
    }
    if not normalized:
        return
    global _cover_cache_state
    with _cover_cache_lock:
        current = _load_cover_cache_unlocked()
        changed = any(current.get(subject_id) != url for subject_id, url in normalized.items())
        if not changed:
            return
        current.update(normalized)
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "items": current,
        }
        COVER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = COVER_CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(COVER_CACHE_PATH)
        _cover_cache_state = (int(COVER_CACHE_PATH.stat().st_mtime_ns), dict(current))


def _load_cover_cache_unlocked() -> dict[str, str]:
    try:
        payload = json.loads(COVER_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = {}
    raw_items = payload.get("items") if isinstance(payload, dict) else {}
    return {
        str(subject_id): url
        for subject_id, value in (raw_items or {}).items()
        if str(subject_id).isdigit() and (url := _normalize_cover_url(value))
    }


def ranking_quarter_key(value: datetime | None = None) -> str:
    value = value or datetime.now()
    quarter = ((int(value.month) - 1) // 3) + 1
    return f"{int(value.year)}-Q{quarter}"


def _empty_ranking_disk_cache(quarter: str | None = None) -> dict[str, Any]:
    return {
        "version": RANKING_CACHE_VERSION,
        "quarter": quarter or ranking_quarter_key(),
        "updated_at": None,
        "categories": {},
    }


def _load_ranking_disk_cache(
    quarter: str | None = None, *, allow_stale: bool = False,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    quarter = quarter or ranking_quarter_key()
    path = cache_path or RANKING_CACHE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_ranking_disk_cache(quarter)
    payload_version = payload.get("version")
    if payload_version not in {RANKING_CACHE_VERSION, RANKING_CACHE_VERSION - 1}:
        return _empty_ranking_disk_cache(quarter)
    if not allow_stale and payload.get("quarter") != quarter:
        return _empty_ranking_disk_cache(quarter)
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        return _empty_ranking_disk_cache(quarter)
    if payload_version == RANKING_CACHE_VERSION - 1:
        # Reuse the already verified Japanese-animation set.  A cache version
        # bump may change ordering or metadata, but must never invent rows just
        # to fill the nominal 300-page browser capacity.
        payload["version"] = RANKING_CACHE_VERSION
        for category_cache in categories.values():
            if not isinstance(category_cache, dict):
                continue
            items = [item for item in category_cache.get("items") or [] if isinstance(item, dict)]
            items.sort(key=lambda item: (
                int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
                int(item.get("id") or 0),
            ))
            category_cache["items"] = items
    return payload


def _save_ranking_disk_cache(payload: dict[str, Any], *, cache_path: Path | None = None) -> None:
    path = cache_path or RANKING_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = RANKING_CACHE_VERSION
    payload["quarter"] = payload.get("quarter") or ranking_quarter_key()
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    if path == RANKING_CACHE_PATH:
        _ranking_inventory_cache.clear()


def _clear_ranking_memory_cache() -> None:
    _ranking_cache.clear()
    _ranking_window_cache.clear()
    _ranking_inventory_cache.clear()
    _ranking_subjects_cache.clear()


def _ranking_refresh_status_path() -> Path:
    return RANKING_CACHE_PATH.with_name("bangumi_ranking_refresh.json")


def _load_ranking_refresh_status() -> dict[str, Any]:
    try:
        payload = json.loads(_ranking_refresh_status_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_ranking_refresh_status(payload: dict[str, Any]) -> None:
    path = _ranking_refresh_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def clear_ranking_cache() -> None:
    _clear_ranking_memory_cache()
    try:
        RANKING_CACHE_PATH.unlink()
    except OSError:
        pass


def _ranking_cache_inventory(category: str) -> tuple[int, bool]:
    """Return cached row count and completion state with one disk parse."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    quarter = ranking_quarter_key()
    try:
        stat = RANKING_CACHE_PATH.stat()
        cache_key = (selected, quarter, int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        cache_key = (selected, quarter, 0, 0)
    cached = _ranking_inventory_cache.get(cache_key)
    if cached is not None:
        return cached

    current = _load_ranking_disk_cache(quarter)
    category_cache = (current.get("categories") or {}).get(selected) or {}
    rows = category_cache.get("items") or []
    windows = category_cache.get("windows") or {}
    if rows or windows:
        window_rows = [item for value in windows.values() if isinstance(value, dict) for item in (value.get("items") or [])]
        count = len({
            int(item["id"]) for item in [*rows, *window_rows]
            if isinstance(item, dict) and str(item.get("id") or "").isdigit()
        })
        result = (
            count,
            bool(category_cache.get("complete")) and bool(category_cache.get("r18_included")),
        )
    else:
        stale = _load_ranking_disk_cache(quarter, allow_stale=True)
        stale_category = (stale.get("categories") or {}).get(selected) or {}
        stale_rows = stale_category.get("items") or []
        result = (
            sum(isinstance(item, dict) for item in stale_rows),
            bool(stale_category.get("complete")) and bool(stale_category.get("r18_included")),
        )
    if len(_ranking_inventory_cache) >= 16:
        _ranking_inventory_cache.clear()
    _ranking_inventory_cache[cache_key] = result
    return result


def ranking_cache_count(category: str) -> int:
    """Return locally cached ranking rows without making a network request."""
    return _ranking_cache_inventory(category)[0]


def ranking_cache_complete(category: str) -> bool:
    """Return whether the current category cache reached the official API end."""
    return _ranking_cache_inventory(category)[1]


def ranking_browser_capacity(item_count: int) -> int:
    """Keep today's reserve while allowing future data to add more pages."""
    return max(RANKING_INITIAL_CAPACITY, max(0, int(item_count)))


def _request(method: str, path: str, **kwargs: Any) -> Any:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    token = readonly_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BangumiError(f"Bangumi 请求失败：{exc}") from exc


def _request_with_access_token(
    method: str, path: str, access_token: str, **kwargs: Any,
) -> Any:
    token = str(access_token or "").strip()
    if not token:
        raise BangumiError("尚未连接 Bangumi 账号。")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
    }
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
        if response.status_code in {401, 403}:
            raise BangumiError("Bangumi 账号连接已失效，请重新连接。")
        response.raise_for_status()
        return response.json()
    except BangumiError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise BangumiError(f"Bangumi 只读请求失败：{exc}") from exc


def verify_readonly_access_token(access_token: str) -> dict[str, Any]:
    """Validate a token through /v0/me without reading collections."""
    payload = _request_with_access_token("GET", "/me", access_token)
    if not isinstance(payload, dict) or not payload.get("username"):
        raise BangumiError("Bangumi 没有返回可识别的账号信息。")
    return {
        "username": str(payload.get("username") or ""),
        "nickname": str(payload.get("nickname") or payload.get("username") or ""),
    }


def _browser_page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _request_web_page(url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")
    except requests.RequestException as exc:
        raise BangumiError(f"Bangumi 排行榜读取失败：{exc}") from exc


def parse_rating_perspective(source: str) -> dict[str, Any]:
    """Read only the public rating summary from a Bangumi /stats page."""
    summary = source.split('id="chartCollectInterestType"', 1)[0]
    score_match = re.search(
        r'class="item orange"[\s\S]{0,240}?class="num">\s*([\d.]+)\s*</span>', summary
    )
    votes_match = re.search(
        r'class="item sky"[\s\S]{0,240}?class="num">\s*([\d,]+)\s*</span>', summary
    )
    if not score_match:
        raise BangumiError("Bangumi 评分透视没有返回公开评分")
    return {
        "score": round(float(score_match.group(1)), 2),
        "votes": int(votes_match.group(1).replace(",", "")) if votes_match else None,
    }


def _load_rating_precision_cache() -> dict[str, Any]:
    try:
        payload = json.loads(RATING_PRECISION_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = {}
    if payload.get("version") != RATING_PRECISION_CACHE_VERSION or not isinstance(payload.get("items"), dict):
        return {"version": RATING_PRECISION_CACHE_VERSION, "updated_at": None, "items": {}}
    return payload


def _save_rating_precision_cache(payload: dict[str, Any]) -> None:
    RATING_PRECISION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = RATING_PRECISION_CACHE_VERSION
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary = RATING_PRECISION_CACHE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(RATING_PRECISION_CACHE_PATH)


def _fetch_rating_perspective(subject_id: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            source = _request_web_page(f"{WEB_BASE}/{int(subject_id)}/stats")
            value = parse_rating_perspective(source)
            return {
                **value,
                "date": datetime.now().date().isoformat(),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        except BangumiError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    raise BangumiError(str(last_error or "Bangumi 评分透视读取失败"))


def enrich_precise_anime_ratings(
    items: Iterable[dict[str, Any]], *, force: bool = False, max_workers: int = 8,
    allow_network: bool = True,
) -> list[dict[str, Any]]:
    """Apply two-decimal scores from rating-perspective pages to Japanese anime rows.

    Only ``/subject/<id>/stats`` is read.  No reviews, comments, staff, accounts or
    other page data are requested or retained.
    """
    rows = [dict(item) for item in items]
    ids = list(dict.fromkeys(
        int(item["id"]) for item in rows if str(item.get("id") or "").isdigit()
    ))
    if not ids:
        return rows
    today = datetime.now().date().isoformat()
    missing: list[int] = []
    with _rating_precision_lock:
        payload = _load_rating_precision_cache()
        cache = payload.setdefault("items", {})
        if allow_network:
            missing = [
                subject_id for subject_id in ids
                if (force or str((cache.get(str(subject_id)) or {}).get("date") or "") != today)
                and subject_id not in _rating_precision_inflight
            ]
            _rating_precision_inflight.update(missing)
        snapshot = {subject_id: dict(cache.get(str(subject_id)) or {}) for subject_id in ids}

    if missing:
        fetched: dict[int, dict[str, Any]] = {}
        try:
            with ThreadPoolExecutor(max_workers=min(max(1, int(max_workers)), len(missing))) as executor:
                futures = {executor.submit(_fetch_rating_perspective, subject_id): subject_id for subject_id in missing}
                for future in as_completed(futures):
                    try:
                        fetched[futures[future]] = future.result()
                    except (BangumiError, ValueError, TypeError):
                        continue
        finally:
            with _rating_precision_lock:
                latest = _load_rating_precision_cache()
                latest_cache = latest.setdefault("items", {})
                for subject_id, value in fetched.items():
                    latest_cache[str(subject_id)] = value
                if fetched:
                    _save_rating_precision_cache(latest)
                _rating_precision_inflight.difference_update(missing)
                snapshot = {
                    subject_id: dict(latest_cache.get(str(subject_id)) or {})
                    for subject_id in ids
                }
    for item in rows:
        subject_id = int(item.get("id") or 0)
        precise = snapshot.get(subject_id) or {}
        if precise.get("score") is not None:
            item["score"] = round(float(precise["score"]), 2)
            item["precision_source"] = "bangumi-rating-perspective"
            item["precision_date"] = precise.get("date")
        if precise.get("votes") is not None:
            item["votes"] = int(precise["votes"])
    return rows


def _distribution_precision(subject: dict[str, Any]) -> tuple[float | None, int | None]:
    rating = subject.get("rating") or {}
    return rating_score_from_counts(rating), rating_total_votes(rating)


def enrich_precise_subject_ratings(
    subjects: Iterable[dict[str, Any]], *, force: bool = False, max_workers: int = 8,
    allow_network: bool = True,
) -> list[dict[str, Any]]:
    """Put real two-decimal perspective scores into API subject dictionaries.

    The search API exposes a rounded score. The public ``/stats`` page is the
    source of the two-decimal value; when it cannot be read, the original API
    value is preserved without marking it as precise.
    """
    rows = [dict(subject) for subject in subjects]
    distribution_ids: set[int] = set()
    for subject in rows:
        exact_score, exact_votes = _distribution_precision(subject)
        if exact_score is None:
            continue
        subject_id = int(subject.get("id") or 0)
        distribution_ids.add(subject_id)
        rating = dict(subject.get("rating") or {})
        rating["score"] = exact_score
        if exact_votes is not None:
            rating["total"] = exact_votes
        subject["rating"] = rating
        subject["precision_source"] = "bangumi-rating-distribution"
    probes = [
        {
            "id": subject.get("id"),
            "score": (subject.get("rating") or {}).get("score"),
            "votes": rating_total_votes(subject.get("rating") or {}),
        }
        for subject in rows
        if int(subject.get("id") or 0) not in distribution_ids
    ]
    enriched = enrich_precise_anime_ratings(
        probes, force=force, max_workers=max_workers, allow_network=allow_network,
    )
    precise_by_id = {
        int(item["id"]): item
        for item in enriched
        if str(item.get("id") or "").isdigit()
        and item.get("precision_source") == "bangumi-rating-perspective"
    }
    for subject in rows:
        subject_id = int(subject.get("id") or 0)
        precise = precise_by_id.get(subject_id)
        if not precise or subject_id in distribution_ids:
            continue
        rating = dict(subject.get("rating") or {})
        rating["score"] = round(float(precise["score"]), 2)
        if precise.get("votes") is not None:
            rating["total"] = int(precise["votes"])
        subject["rating"] = rating
        subject["precision_source"] = "bangumi-rating-perspective"
        subject["precision_date"] = precise.get("precision_date")
    return rows


def merge_precise_subject_rating(
    target: dict[str, Any], precise_subject: dict[str, Any],
) -> dict[str, Any]:
    """Carry a verified search-result rating into the detail saved locally."""
    result = dict(target)
    if not str(precise_subject.get("precision_source") or "").startswith("bangumi-rating-"):
        return result
    precise_rating = precise_subject.get("rating") or {}
    if precise_rating.get("score") is None:
        return result
    rating = dict(result.get("rating") or {})
    rating["score"] = round(float(precise_rating["score"]), 2)
    votes = rating_total_votes(precise_rating)
    if votes is not None:
        rating["total"] = votes
    result["rating"] = rating
    result["precision_source"] = precise_subject.get("precision_source")
    result["precision_date"] = precise_subject.get("precision_date")
    return result


def prewarm_precise_anime_ratings(
    items: Iterable[dict[str, Any]], *, max_workers: int = 8,
) -> None:
    """Refresh exact public scores in the background without delaying page paint."""
    rows = [dict(item) for item in items]
    if not rows:
        return

    def refresh() -> None:
        enrich_precise_anime_ratings(rows, max_workers=max_workers, allow_network=True)

    threading.Thread(
        target=refresh,
        name="yanggumi-bangumi-rating-precision",
        daemon=True,
    ).start()


def cached_ranking_subject_ids(category: str = "动画") -> list[int]:
    """Return known ranking IDs without any network request."""
    payload = _load_ranking_disk_cache(ranking_quarter_key(), allow_stale=True)
    category_cache = (payload.get("categories") or {}).get(category) or {}
    candidates = [item for item in category_cache.get("items", []) if isinstance(item, dict)]
    for window in (category_cache.get("windows") or {}).values():
        if isinstance(window, dict):
            candidates.extend(item for item in window.get("items", []) if isinstance(item, dict))
    return list(dict.fromkeys(
        int(item["id"]) for item in candidates if str(item.get("id") or "").isdigit()
    ))


def cached_ranking_subjects(category: str = "动画") -> list[dict[str, Any]]:
    """Return every locally cached public ranking row without network access.

    The Bangumi analysis controls must calculate against the cached category,
    not only the visible 24-card page.  A previous-quarter cache is accepted as
    an offline fallback in the same way as the ranking browser itself.
    """
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    quarter = ranking_quarter_key()
    try:
        stat = RANKING_CACHE_PATH.stat()
        cache_key = (selected, quarter, int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        cache_key = (selected, quarter, 0, 0)
    cached = _ranking_subjects_cache.get(cache_key)
    if cached is not None:
        return [dict(item) for item in cached]

    payload = _load_ranking_disk_cache(quarter)
    category_cache = (payload.get("categories") or {}).get(selected) or {}
    rows = [dict(item) for item in category_cache.get("items", []) if isinstance(item, dict)]
    if not rows:
        stale = _load_ranking_disk_cache(quarter, allow_stale=True)
        stale_category = (stale.get("categories") or {}).get(selected) or {}
        rows = [dict(item) for item in stale_category.get("items", []) if isinstance(item, dict)]
    rows = [_ranking_item_with_distribution_precision(item) for item in rows]
    rows.sort(key=lambda item: (
        int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
        int(item.get("id") or 0),
    ))
    if len(_ranking_subjects_cache) >= 12:
        _ranking_subjects_cache.clear()
    _ranking_subjects_cache[cache_key] = [dict(item) for item in rows]
    return [dict(item) for item in rows]


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _parse_browser_subject_list_page(source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in re.finditer(r'<li[^>]+id="item_(\d+)"[\s\S]*?</li>', source):
        subject_id = int(match.group(1))
        block = match.group(0)
        title_match = re.search(r'<a href="/subject/\d+" class="l">([\s\S]*?)</a>', block)
        rank_match = re.search(r'<span class="rank"><small>Rank </small>(\d+)</span>', block)
        score_match = re.search(r'<small class="fade">([\d.]+)</small>', block)
        votes_match = re.search(r'\(([\d,]+)人评分\)', block)
        image_match = re.search(r'<img src="([^"]+)"[^>]*class="cover"', block)
        original_match = re.search(r'<small class="grey">([\s\S]*?)</small>', block)
        info_match = re.search(r'<p class="info tip">([\s\S]*?)</p>', block)
        type_match = re.search(r'subject_type_(\d+)', block)
        if not title_match:
            continue
        image = html.unescape(image_match.group(1)) if image_match else ""
        if image.startswith("//"):
            image = "https:" + image
        items.append({
            "id": subject_id,
            "title": _strip_tags(title_match.group(1)),
            "original_title": _strip_tags(original_match.group(1)) if original_match else "",
            "rank": int(rank_match.group(1)) if rank_match else None,
            "score": float(score_match.group(1)) if score_match else None,
            "votes": int(votes_match.group(1).replace(",", "")) if votes_match else None,
            "image": image,
            "info": _strip_tags(info_match.group(1)) if info_match else "",
            "type": int(type_match.group(1)) if type_match else None,
            "url": f"{WEB_BASE}/{subject_id}",
        })
    return items


def _parse_browser_ranking_page(source: str) -> list[dict[str, Any]]:
    return [item for item in _parse_browser_subject_list_page(source) if item.get("rank")]


def _archive_subject_dictionary(
    record: dict[str, Any], browser_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert an official Archive record into the public v0 subject shape."""
    browser_item = browser_item or {}
    raw_counts = record.get("score_details") or {}
    counts = {
        str(score): int(count or 0)
        for score, count in raw_counts.items()
        if str(score).isdigit()
    } if isinstance(raw_counts, dict) else {}
    total = sum(counts.values())
    score = rating_score_from_counts({"count": counts})
    if score is None and record.get("score") not in (None, ""):
        archived_score = round(float(record["score"]), 2)
        score = archived_score if archived_score > 0 else None
    image = (
        _normalize_cover_url(browser_item.get("image"))
        or _cached_cover_url(int(record["id"]))
        or _fallback_cover_url(int(record["id"]))
    )
    if "no_icon_subject" in image:
        image = ""
    tags = record.get("tags") or []
    normalized_tags: list[dict[str, Any]] = []
    for tag in tags:
        if isinstance(tag, dict):
            normalized_tags.append(dict(tag))
        elif str(tag or "").strip():
            normalized_tags.append({"name": str(tag).strip(), "count": 0})
    subject = {
        "id": int(record["id"]),
        "type": int(record.get("type") or browser_item.get("type") or 0),
        "name": record.get("name") or browser_item.get("original_title") or browser_item.get("title") or "",
        "name_cn": record.get("name_cn") or browser_item.get("title") or "",
        "platform": record.get("platform") or "",
        "summary": record.get("summary") or "",
        "date": record.get("date") or "",
        "tags": normalized_tags,
        "meta_tags": record.get("meta_tags") or [],
        "infobox": [],
        "images": {"large": image, "common": image} if image else {},
        "rating": {
            "score": score,
            "rank": record.get("rank") or browser_item.get("rank"),
            "total": total or browser_item.get("votes"),
            "count": counts,
        },
        "nsfw": True,
        "_yanggumi_nsfw": True,
        "_archive_infobox": record.get("infobox") or "",
        "precision_source": "bangumi-rating-archive-distribution" if total > 0 else "",
    }
    return subject


def _subject_from_public_id(
    subject_id: int, browser_item: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    record = bangumi_archive.archive_subject(subject_id)
    if record:
        return _archive_subject_dictionary(record, browser_item)
    try:
        return get_subject(subject_id)
    except BangumiError:
        return None


def _ranking_category_matches(category: str, subject: dict[str, Any]) -> bool:
    source = japanese_source_status(subject)
    # Bangumi type=2 also contains non-Japanese animation. Unknown origin is
    # therefore not sufficient for this Japan-only public ranking.
    if source != "confirmed":
        return False
    inferred = infer_local_category(subject, "轻小说" if category == "小说" else category)
    if category == "小说":
        return inferred == "轻小说"
    return inferred == category


def _ranking_item_from_subject(subject: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_subject(subject)
    title = normalized.get("bangumi_name_cn") or normalized.get("bangumi_name") or "未命名"
    original = normalized.get("bangumi_name") or title
    item = {
        "id": int(subject["id"]),
        "title": title,
        "original_title": original,
        "rank": normalized.get("bangumi_rank"),
        "score": normalized.get("bangumi_score"),
        "votes": normalized.get("bangumi_total_votes"),
        "image": normalized.get("bangumi_image_url") or "",
        "info": " · ".join(value for value in (str(subject.get("date") or ""), str(subject.get("platform") or "")) if value),
        "url": f"{WEB_BASE}/{int(subject['id'])}",
        "subject": subject,
    }
    return _ranking_item_with_distribution_precision(item)


def _ranking_item_with_distribution_precision(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    if not str(result.get("image") or "").strip() and str(result.get("id") or "").isdigit():
        result["image"] = _cached_cover_url(int(result["id"]))
    subject = result.get("subject")
    if not isinstance(subject, dict):
        return result
    exact_score, exact_votes = _distribution_precision(subject)
    if exact_score is None:
        return result
    result["score"] = exact_score
    if exact_votes is not None:
        result["votes"] = exact_votes
    result["precision_source"] = "bangumi-rating-distribution"
    return result


def _merge_public_browser_ranking_rows(
    category: str,
    results: list[dict[str, Any]],
    api_seen_ids: set[int],
    *,
    api_total: int = 0,
    max_workers: int = 6,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    """Supplement API rankings with official Archive R18 rows.

    The weekly Bangumi Archive is preferred because it contains the complete
    public rating distribution and metadata for subjects omitted by the v0
    list API.  The HTML browser remains a fallback when no local Archive index
    is available.
    """
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    archive_rows = bangumi_archive.archive_subjects()
    if archive_rows:
        existing_ids = {
            int(item["id"]) for item in results if str(item.get("id") or "").isdigit()
        }
        added = 0
        for record in archive_rows:
            subject_id = int(record.get("id") or 0)
            if not subject_id or subject_id in existing_ids or not record.get("rank"):
                continue
            subject = _archive_subject_dictionary(record)
            if not _ranking_category_matches(selected, subject):
                continue
            results.append(_ranking_item_from_subject(subject))
            existing_ids.add(subject_id)
            added += 1
        results.sort(key=lambda item: (
            int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
            int(item.get("id") or 0),
        ))
        return results, True, 0, added

    workers = min(max(2, int(max_workers)), 8)
    estimated_pages = (max(0, int(api_total)) + 23) // 24
    max_pages = min(500, max(20, estimated_pages + max(8, estimated_pages // 5)))
    browser_rows: list[dict[str, Any]] = []
    pages_scanned = 0
    exhausted = False
    wave_size = workers * 2

    for wave_start in range(1, max_pages + 1, wave_size):
        page_numbers = list(range(wave_start, min(max_pages + 1, wave_start + wave_size)))

        def fetch_page(page: int) -> tuple[int, list[dict[str, Any]] | None]:
            try:
                source = _request_web_page(_browser_page_url(RANKING_BROWSER_URLS[selected], page))
                return page, _parse_browser_ranking_page(source)
            except BangumiError:
                return page, None

        fetched: dict[int, list[dict[str, Any]] | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_page, page): page for page in page_numbers}
            for future in as_completed(futures):
                page, rows = future.result()
                fetched[page] = rows
        if any(fetched.get(page) is None for page in page_numbers):
            return results, False, pages_scanned, 0
        for page in page_numbers:
            rows = fetched.get(page) or []
            pages_scanned = page
            if not rows:
                exhausted = True
                break
            browser_rows.extend(rows)
        if exhausted:
            break

    missing_ids = list(dict.fromkeys(
        int(item["id"])
        for item in browser_rows
        if str(item.get("id") or "").isdigit() and int(item["id"]) not in api_seen_ids
    ))
    if not missing_ids:
        return results, exhausted, pages_scanned, 0

    browser_by_id = {
        int(item["id"]): item
        for item in browser_rows if str(item.get("id") or "").isdigit()
    }
    fetched_subjects: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_subject_from_public_id, subject_id, browser_by_id.get(subject_id)): subject_id
            for subject_id in missing_ids
        }
        for future in as_completed(futures):
            subject_id = futures[future]
            try:
                subject = future.result()
            except (BangumiError, ValueError, TypeError):
                continue
            if isinstance(subject, dict):
                fetched_subjects[subject_id] = subject

    existing_ids = {
        int(item["id"]) for item in results if str(item.get("id") or "").isdigit()
    }
    added = 0
    for browser_item in browser_rows:
        subject_id = int(browser_item.get("id") or 0)
        subject = fetched_subjects.get(subject_id)
        if subject_id in existing_ids or subject is None:
            continue
        if not _ranking_category_matches(selected, subject):
            continue
        item = _ranking_item_from_subject(subject)
        item["rank"] = browser_item.get("rank") or item.get("rank")
        if not item.get("image") and browser_item.get("image"):
            item["image"] = browser_item.get("image")
        results.append(item)
        existing_ids.add(subject_id)
        added += 1
    results.sort(key=lambda item: (
        int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
        int(item.get("id") or 0),
    ))
    return results, exhausted, pages_scanned, added


def ranked_browser_subject_window(category: str, offset: int = 0, limit: int = 25) -> list[dict[str, Any]]:
    """Return a stable slice after category filtering.

    Bangumi's API offset is applied before Yang-gumi's Japan/category filter.
    Applying the filtered page number directly as an API offset made adjacent
    pages overlap.  The sequential cache below keeps one canonical filtered
    order and still fetches in 50-row batches, so the following page is usually
    already warm.
    """
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    offset = min(max(int(offset), 0), RANKING_MAX_ITEMS - 1)
    limit = min(max(int(limit), 1), 100)
    quarter = ranking_quarter_key()
    cache_key = (selected, offset, limit, quarter)
    cached = _ranking_window_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RANKING_CACHE_SECONDS:
        return [dict(item) for item in cached[1]]

    all_rows = ranked_browser_subjects(selected, min(RANKING_MAX_ITEMS, offset + limit))
    results = [dict(item) for item in all_rows[offset:offset + limit]]
    _ranking_window_cache[cache_key] = (time.time(), [dict(item) for item in results])
    return results


def _prewarm_ranking_capacity(
    category: str, *, max_workers: int = 6, cache_path: Path | None = None,
) -> int:
    """Build the complete filtered ranking cache in parallel.

    Interactive page reads stay small and fast.  This quarterly background pass
    fetches the remaining official API batches and commits a canonical sorted
    prefix after every wave. It stops at the official API end, not at today's
    300-page reserve, so future seasonal additions create new pages naturally.
    """
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    quarter = ranking_quarter_key()
    path = cache_path or RANKING_CACHE_PATH
    disk_cache = _load_ranking_disk_cache(quarter, cache_path=path)
    category_cache = disk_cache.setdefault("categories", {}).setdefault(selected, {})
    results = [
        _ranking_item_with_distribution_precision(item) for item in category_cache.get("items", [])
        if isinstance(item, dict)
    ]
    if len(results) >= RANKING_MAX_ITEMS:
        return RANKING_MAX_ITEMS

    seen: set[int] = {
        int(item["id"]) for item in results
        if str(item.get("id") or "").isdigit()
    }
    api_page_size = 50
    raw_offset = max(0, int(category_cache.get("loaded_offset") or 0))
    api_total = max(0, int(category_cache.get("api_total") or 0))
    exhausted = bool(category_cache.get("complete"))
    r18_included = bool(category_cache.get("r18_included"))
    if exhausted and r18_included:
        return min(len(results), RANKING_MAX_ITEMS)
    if exhausted and not r18_included:
        # Older caches reached the API end before public R18 supplementation
        # existed. Rebuild from offset zero so api_seen_ids also contains every
        # public non-R18 row and browser-only detection remains precise.
        results = []
        seen.clear()
        raw_offset = 0
        api_total = 0
        exhausted = False

    workers = min(max(1, int(max_workers)), 8)
    wave_size = workers * 3
    stopped_on_error = False
    while len(results) < RANKING_MAX_ITEMS and not exhausted and not stopped_on_error:
        scan_ceiling = api_total if api_total else raw_offset + (wave_size * api_page_size)
        offsets = list(range(raw_offset, scan_ceiling, api_page_size))[:wave_size]
        if not offsets:
            break

        def fetch(offset: int) -> tuple[int, dict[str, Any] | None]:
            params = {
                **RANKING_API_FILTERS[selected],
                "sort": "rank",
                "limit": api_page_size,
                "offset": offset,
            }
            try:
                payload = _request("GET", "/subjects", params=params)
            except BangumiError:
                return offset, None
            return offset, payload if isinstance(payload, dict) else None

        pages: dict[int, dict[str, Any] | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch, offset): offset for offset in offsets}
            for future in as_completed(futures):
                offset, payload = future.result()
                pages[offset] = payload

        failed_offsets = [offset for offset in offsets if not pages.get(offset)]
        if failed_offsets:
            # One bounded retry handles transient API failures without entering
            # an endless network loop.
            with ThreadPoolExecutor(max_workers=min(workers, len(failed_offsets))) as executor:
                futures = {executor.submit(fetch, offset): offset for offset in failed_offsets}
                for future in as_completed(futures):
                    offset, payload = future.result()
                    pages[offset] = payload

        for offset in offsets:
            payload = pages.get(offset)
            if not payload:
                stopped_on_error = True
                break
            subjects = payload.get("data")
            if not isinstance(subjects, list):
                stopped_on_error = True
                break
            api_total = max(api_total, int(payload.get("total") or 0))
            raw_offset = offset + len(subjects)
            for subject in subjects:
                if not isinstance(subject, dict) or not str(subject.get("id") or "").isdigit():
                    continue
                subject_id = int(subject["id"])
                if subject_id in seen:
                    continue
                seen.add(subject_id)
                if _ranking_category_matches(selected, subject):
                    results.append(_ranking_item_from_subject(subject))
            if len(subjects) < api_page_size or (api_total and raw_offset >= api_total):
                exhausted = True
                break
            if len(results) >= RANKING_MAX_ITEMS:
                break

        results.sort(key=lambda item: (
            int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
            int(item.get("id") or 0),
        ))
        stored_rows = [dict(item) for item in results[:RANKING_MAX_ITEMS]]
        category_cache.update({
            "items": stored_rows,
            "loaded_offset": raw_offset,
            "api_total": api_total,
            "complete": False,
            "source": "official-api",
        })
        # Interactive prewarming checkpoints the live cache after every wave.
        # A daily rebuild uses an isolated staging file and writes only once at
        # the end, avoiding repeated rewrites of the large complete snapshot.
        if path == RANKING_CACHE_PATH:
            _save_ranking_disk_cache(disk_cache, cache_path=path)
            _ranking_cache[(selected, len(stored_rows), quarter)] = (
                time.time(),
                [dict(item) for item in stored_rows],
            )
    browser_complete = False
    browser_pages_scanned = 0
    browser_added = 0
    if exhausted and not stopped_on_error and len(results) < RANKING_MAX_ITEMS:
        results, browser_complete, browser_pages_scanned, browser_added = _merge_public_browser_ranking_rows(
            selected,
            results,
            seen,
            api_total=api_total,
            max_workers=max_workers,
        )
        stored_rows = [dict(item) for item in results[:RANKING_MAX_ITEMS]]
        category_cache.update({
            "items": stored_rows,
            "loaded_offset": raw_offset,
            "api_total": api_total,
            "complete": bool(browser_complete),
            "r18_included": bool(browser_complete),
            "browser_pages_scanned": browser_pages_scanned,
            "browser_rows_added": browser_added,
            "source": "official-api+public-browser",
        })
    if path != RANKING_CACHE_PATH or browser_complete:
        _save_ranking_disk_cache(disk_cache, cache_path=path)
    return min(len(results), RANKING_MAX_ITEMS)


def ranking_cache_refresh_due(value: datetime | None = None) -> bool:
    """Return whether today's complete public ranking rebuild is still due."""
    now = value or datetime.now()
    status = _load_ranking_refresh_status()
    complete_categories = set(status.get("complete_categories") or [])
    r18_categories = set(status.get("r18_categories") or [])
    if (
        status.get("quarter") == ranking_quarter_key(now)
        and status.get("refreshed_on") == now.date().isoformat()
        and set(RANKING_CATEGORY_LABELS).issubset(complete_categories)
        and set(RANKING_CATEGORY_LABELS).issubset(r18_categories)
        and RANKING_CACHE_PATH.exists()
    ):
        return False
    payload = _load_ranking_disk_cache(ranking_quarter_key(now), allow_stale=True)
    refreshed_on = str(payload.get("refreshed_on") or "")
    if not refreshed_on:
        refreshed_on = str(payload.get("refresh_completed_at") or payload.get("updated_at") or "")[:10]
    categories = payload.get("categories") or {}
    complete = all(
        bool((categories.get(category) or {}).get("complete"))
        and bool((categories.get(category) or {}).get("r18_included"))
        for category in RANKING_CATEGORY_LABELS
    )
    return payload.get("quarter") != ranking_quarter_key(now) or refreshed_on != now.date().isoformat() or not complete


def refresh_ranking_cache(
    value: datetime | None = None, *, categories: Iterable[str] | None = None,
    max_workers: int = 2,
) -> dict[str, int]:
    """Rebuild public rankings in isolation and atomically publish only a complete result.

    The current cache remains readable throughout the refresh. Network errors,
    incomplete categories or process interruption never replace the last known
    good snapshot.
    """
    now = value or datetime.now()
    selected_categories = tuple(dict.fromkeys(
        category for category in (categories or RANKING_CATEGORY_LABELS)
        if category in RANKING_CATEGORY_LABELS
    ))
    if not selected_categories:
        raise ValueError("At least one supported ranking category is required")
    staging_path = RANKING_CACHE_PATH.with_name(
        f"{RANKING_CACHE_PATH.stem}.refreshing{RANKING_CACHE_PATH.suffix}"
    )
    with _ranking_daily_refresh_lock:
        try:
            staging_path.unlink(missing_ok=True)
            staging = _empty_ranking_disk_cache(ranking_quarter_key(now))
            staging["refresh_started_at"] = datetime.now().isoformat(timespec="seconds")
            _save_ranking_disk_cache(staging, cache_path=staging_path)
            counts: dict[str, int] = {}
            for category in selected_categories:
                counts[category] = _prewarm_ranking_capacity(
                    category, max_workers=max_workers, cache_path=staging_path,
                )
                staged = _load_ranking_disk_cache(
                    ranking_quarter_key(now), cache_path=staging_path,
                )
                category_cache = (staged.get("categories") or {}).get(category) or {}
                if not category_cache.get("complete") or not category_cache.get("r18_included"):
                    raise BangumiError(f"Bangumi {category} ranking refresh did not complete public R18 supplementation")
            completed_at = datetime.now().isoformat(timespec="seconds")
            staged = _load_ranking_disk_cache(ranking_quarter_key(now), cache_path=staging_path)
            staged.update({
                "refreshed_on": now.date().isoformat(),
                "refresh_completed_at": completed_at,
                "refresh_counts": counts,
            })
            _save_ranking_disk_cache(staged, cache_path=staging_path)
            staging_path.replace(RANKING_CACHE_PATH)
            _clear_ranking_memory_cache()
            _save_ranking_refresh_status({
                "quarter": ranking_quarter_key(now),
                "refreshed_on": now.date().isoformat(),
                "completed_at": completed_at,
                "complete_categories": list(selected_categories),
                "r18_categories": list(selected_categories),
                "counts": counts,
            })
            return counts
        except Exception:
            staging_path.unlink(missing_ok=True)
            raise


def refresh_ranking_cache_if_due(
    value: datetime | None = None, *, max_workers: int = 2,
) -> tuple[bool, dict[str, int]]:
    now = value or datetime.now()
    if not ranking_cache_refresh_due(now):
        return False, {}
    return True, refresh_ranking_cache(now, max_workers=max_workers)


def start_ranking_cache_refresh(*, force: bool = False, max_workers: int = 2) -> bool:
    """Start one invisible ranking refresh while the current cache stays available."""
    global _ranking_refresh_inflight
    with _ranking_refresh_state_lock:
        if _ranking_refresh_inflight or _ranking_daily_refresh_lock.locked():
            return False
        if not force and not ranking_cache_refresh_due():
            return False
        _ranking_refresh_inflight = True

    def refresh() -> None:
        global _ranking_refresh_inflight
        try:
            refresh_ranking_cache(max_workers=max_workers)
        finally:
            with _ranking_refresh_state_lock:
                _ranking_refresh_inflight = False

    threading.Thread(
        target=refresh, name="yanggumi-bangumi-ranking-daily", daemon=True,
    ).start()
    return True


def ranking_refresh_status(value: datetime | None = None) -> dict[str, Any]:
    """Return display-only freshness metadata for the public ranking page."""
    now = value or datetime.now()
    status = _load_ranking_refresh_status()
    completed_at = status.get("completed_at")
    if not completed_at:
        try:
            completed_at = datetime.fromtimestamp(RANKING_CACHE_PATH.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            completed_at = None
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "refreshed_on": status.get("refreshed_on"),
        "completed_at": completed_at,
        "next_refresh_at": next_midnight.isoformat(timespec="minutes"),
        "in_progress": _ranking_refresh_inflight or _ranking_daily_refresh_lock.locked(),
        "due": ranking_cache_refresh_due(now),
    }


def prewarm_ranking_subjects(category: str = "动画", *, max_workers: int = 6) -> None:
    """Make the complete dynamic ranking available without delaying current paint."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    quarter = ranking_quarter_key()
    key = (selected, quarter)
    with _ranking_prewarm_lock:
        if key in _ranking_prewarm_inflight or ranking_cache_complete(selected):
            return
        _ranking_prewarm_inflight.add(key)

    def refresh() -> None:
        try:
            _prewarm_ranking_capacity(selected, max_workers=max_workers)
        finally:
            with _ranking_prewarm_lock:
                _ranking_prewarm_inflight.discard(key)

    threading.Thread(
        target=refresh,
        name=f"yanggumi-bangumi-ranking-{selected}",
        daemon=True,
    ).start()


def ranked_browser_subjects(category: str, limit: int = 24) -> list[dict[str, Any]]:
    """Load ranked subjects from Bangumi's official public API and cache them locally."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    limit = min(max(int(limit), 1), RANKING_MAX_ITEMS)
    quarter = ranking_quarter_key()
    cache_key = (selected, limit, quarter)
    cached = _ranking_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RANKING_CACHE_SECONDS:
        return [dict(item) for item in cached[1][:limit]]
    for (cached_category, cached_limit, cached_quarter), cached_value in sorted(_ranking_cache.items(), key=lambda pair: pair[0][1], reverse=True):
        if cached_category != selected or cached_quarter != quarter:
            continue
        cached_at, cached_rows = cached_value
        if time.time() - cached_at < _RANKING_CACHE_SECONDS and len(cached_rows) >= limit:
            return [dict(item) for item in cached_rows[:limit]]

    disk_cache = _load_ranking_disk_cache(quarter)
    category_cache = disk_cache.setdefault("categories", {}).setdefault(selected, {})
    results = [
        _ranking_item_with_distribution_precision(item)
        for item in category_cache.get("items", []) if isinstance(item, dict)
    ]
    # Transparently upgrade an existing complete API cache after the Archive
    # supplement first becomes available.  This changes only the public cache;
    # the user's works, scores and settings are never touched.
    if bool(category_cache.get("complete")) and not bool(category_cache.get("r18_included")):
        results, merged, pages_scanned, added = _merge_public_browser_ranking_rows(
            selected,
            results,
            {int(item["id"]) for item in results if str(item.get("id") or "").isdigit()},
            api_total=int(category_cache.get("api_total") or len(results)),
        )
        if merged:
            category_cache.update({
                "items": [dict(item) for item in results[:RANKING_MAX_ITEMS]],
                "complete": True,
                "r18_included": True,
                "browser_pages_scanned": pages_scanned,
                "browser_rows_added": added,
                "source": "official-api+bangumi-archive",
            })
            _save_ranking_disk_cache(disk_cache)
    if bool(category_cache.get("complete")) and bool(category_cache.get("r18_included")):
        _ranking_cache[(selected, len(results), quarter)] = (
            time.time(),
            [dict(item) for item in results],
        )
        return results[:limit]
    if len(results) >= limit:
        _ranking_cache[cache_key] = (time.time(), [dict(item) for item in results[:limit]])
        return results[:limit]

    stale_cache = _load_ranking_disk_cache(quarter, allow_stale=True)
    stale_category = (stale_cache.get("categories") or {}).get(selected) or {}
    stale_results = [
        _ranking_item_with_distribution_precision(item)
        for item in stale_category.get("items", []) if isinstance(item, dict)
    ]
    seen: set[int] = {int(item["id"]) for item in results if str(item.get("id") or "").isdigit()}
    api_page_size = 50
    offset = max(0, int(category_cache.get("loaded_offset") or 0))
    completed = bool(category_cache.get("complete")) and bool(category_cache.get("r18_included"))
    if completed:
        _ranking_cache[cache_key] = (time.time(), [dict(item) for item in results])
        return results[:limit]

    while len(results) < limit and offset < RANKING_MAX_ITEMS:
        params = {
            **RANKING_API_FILTERS[selected],
            "sort": "rank",
            "limit": api_page_size,
            "offset": offset,
        }
        try:
            payload = _request("GET", "/subjects", params=params)
        except BangumiError:
            if stale_results:
                fallback_rows = stale_results[:limit]
                _ranking_cache[cache_key] = (time.time(), [dict(item) for item in fallback_rows])
                return fallback_rows
            raise
        subjects = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(subjects, list) or not subjects:
            category_cache["complete"] = True
            break
        offset += len(subjects)
        for subject in subjects:
            if not isinstance(subject, dict) or not str(subject.get("id") or "").isdigit():
                continue
            subject_id = int(subject["id"])
            if subject_id in seen:
                continue
            seen.add(subject_id)
            if not _ranking_category_matches(selected, subject):
                continue
            results.append(_ranking_item_from_subject(subject))
        total = int(payload.get("total") or 0) if isinstance(payload, dict) else 0
        if len(subjects) < api_page_size or (total and offset >= total):
            category_cache["complete"] = True
            break
    results.sort(key=lambda item: (
        int(item.get("rank") or RANKING_UNRANKED_SENTINEL),
        int(item.get("id") or 0),
    ))
    category_cache.update({
        "items": [dict(item) for item in results],
        "loaded_offset": offset,
        "source": "official-api",
    })
    _save_ranking_disk_cache(disk_cache)
    _ranking_cache[cache_key] = (time.time(), [dict(item) for item in results])
    return results


def _compact_keyword(keyword: str) -> str:
    """Remove whitespace while preserving CJK, kana, latin text and punctuation."""
    return "".join(char for char in keyword if not char.isspace())


def _plain_keyword(keyword: str) -> str:
    """Remove symbols/punctuation without transliterating or changing the language."""
    return "".join(
        char for char in keyword
        if not unicodedata.category(char).startswith(("P", "S", "Z"))
    )


def normalize_title(text: Any) -> str:
    """Normalize a title without translating or applying language-specific NLP."""
    value = unicodedata.normalize("NFKC", str(text or "")).strip().casefold()
    season_numbers = {"二": "2", "三": "3", "四": "4"}

    def chinese_season(match: re.Match[str]) -> str:
        raw = match.group(1)
        return f"season{season_numbers.get(raw, raw)}"

    value = re.sub(r"第\s*([二三四234])\s*[季期]", chinese_season, value)
    value = re.sub(r"\b(2)(?:nd)?\s*season\b|\bseason\s*2\b", "season2", value)
    value = re.sub(r"\b(3)(?:rd)?\s*season\b|\bseason\s*3\b", "season3", value)
    value = re.sub(r"\b(4)(?:th)?\s*season\b|\bseason\s*4\b", "season4", value)
    value = value.translate(str.maketrans({"監": "监", "獄": "狱"}))
    # Keep CJK, kana, latin letters and digits; discard separators and punctuation.
    normalized = "".join(char for char in value if char.isalnum() or "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff")
    for roman, digit in (("iii", "3"), ("ii", "2"), ("iv", "4")):
        normalized = re.sub(
            rf"(?<=[a-z0-9]){roman}(?=$|[^\x00-\x7f])",
            digit,
            normalized,
        )
    return normalized


def _flatten_text(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    return [str(value)]


def subject_title_candidates(subject: dict[str, Any]) -> list[str]:
    """Collect title-bearing fields and aliases while excluding summary prose."""
    values: list[str] = []
    for field in ("name_cn", "name", "title", "original_title", "aliases", "alias"):
        values.extend(_flatten_text(subject.get(field)))
    for item in subject.get("infobox") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").casefold()
        if any(marker in key for marker in ("别名", "別名", "alias", "中文名", "日文名", "原名")):
            values.extend(_flatten_text(item.get("value")))
    archive_info = str(subject.get("_archive_infobox") or "")
    if archive_info:
        values.extend(
            match.group(1).strip()
            for match in re.finditer(r"\[([^\]|]+)(?:\|[^\]]*)?\]", archive_info)
            if match.group(1).strip()
        )
    # Common title separators often delimit the exact short title from a
    # subtitle.  Retaining those segments lets searches such as BLACKSOULS2,
    # 监狱勇者 and 复仇催眠 match their longer Archive titles precisely.
    for value in list(values):
        values.extend(
            segment.strip()
            for segment in re.split(r"[～〜~—–：:]|\s+-\s+", value)
            if len(segment.strip()) >= 2
        )
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _series_base(normalized: str) -> str:
    value = re.sub(r"season[234]", "", normalized)
    for suffix in ("剧场版", "劇場版", "特别篇", "特別篇", "ova", "oad", "special", "movie"):
        value = value.replace(suffix, "")
    return value


def score_title_relevance(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a stable relevance level and score derived primarily from titles."""
    query_norm = normalize_title(query)
    titles = subject_title_candidates(candidate)
    normalized_titles = [(title, normalize_title(title)) for title in titles]
    normalized_titles = [(title, normalized) for title, normalized in normalized_titles if normalized]
    if not query_norm or not normalized_titles:
        return {"level": "irrelevant", "score": 0.0, "matched_title": ""}

    season = next((token for token in ("season2", "season3", "season4") if token in query_norm), "")
    matched_title = ""
    level = "irrelevant"
    score = 0.0
    for raw, title_norm in normalized_titles:
        candidate_season = next((token for token in ("season2", "season3", "season4") if token in title_norm), "")
        season_bonus = 24.0 if season and season == candidate_season else (-28.0 if season and candidate_season and season != candidate_season else 0.0)
        if query_norm == title_norm:
            current_level, current_score = "strict_exact", 400.0 + season_bonus
        elif len(query_norm) >= 2 and query_norm in title_norm:
            current_level, current_score = "strict_contains", 300.0 + min(len(query_norm), 30) + season_bonus
        else:
            query_base, title_base = _series_base(query_norm), _series_base(title_norm)
            if min(len(query_base), len(title_base)) >= 2 and (query_base in title_base or title_base in query_base):
                current_level, current_score = "series_related", 220.0 + min(len(query_base), len(title_base)) + season_bonus
            else:
                ratio = SequenceMatcher(None, query_norm, title_norm).ratio()
                current_level, current_score = ("possible", 100.0 * ratio) if ratio >= 0.48 else ("irrelevant", 0.0)
        if current_score > score:
            level, score, matched_title = current_level, current_score, raw

    summary_norm = normalize_title(candidate.get("summary"))
    if level == "irrelevant" and len(query_norm) >= 4 and query_norm in summary_norm:
        level, score = "possible", 42.0
    return {"level": level, "score": round(score, 3), "matched_title": matched_title}


def japanese_source_status(subject: dict[str, Any]) -> str:
    """Classify the animation's production origin, not its source material.

    Bangumi tags such as ``韩国`` or ``欧美`` often describe the original
    comic/game/IP.  Japanese TV/WEB productions adapted from those works must
    remain in the Japan-only animation ranking.  Explicit Chinese/US animation
    markers still win, while a kana primary title or an explicit Japanese
    animation tag is treated as strong production evidence.
    """
    if subject.get("type") is None:
        return "unknown"
    if subject.get("type") not in {1, 2, 4}:
        return "excluded"
    text = _classification_text(subject)
    tag_names = {
        str(item.get("name", "") if isinstance(item, dict) else item).strip().casefold()
        for item in (subject.get("tags") or [])
    }
    explicit_foreign_tags = {
        "非日本动画", "非日本動畫", "非日本動畫電影",
        "国产", "國產", "国产动画", "國產動畫", "国漫", "國漫",
    }
    if tag_names & explicit_foreign_tags:
        return "excluded"
    explicit_foreign_markers = (
        "中国动画", "国产动画", "国产游戏", "中国游戏", "donghua",
        "美国动画", "欧美动画", "american animation", "韩国动画",
        "非日本动画", "非日本動畫",
        "网络剧", "电视剧", "真人剧",
    )
    if any(marker in text for marker in explicit_foreign_markers):
        return "excluded"

    # Bangumi's primary ``name`` is normally the work's original title.  Kana,
    # or the explicit 日本动画 tag, is stronger production evidence than a
    # generic source-country/IP tag such as 韩国, 欧美 or Disney.
    primary_name = str(subject.get("name") or "")
    if (
        any("\u3040" <= char <= "\u30ff" for char in primary_name)
        or "日本动画" in tag_names
        or "日本動畫" in tag_names
    ):
        return "confirmed"

    foreign_origin_tags = {
        "欧美", "歐美", "美国动画", "美國動畫", "韩国动画", "韓國動畫",
        "pixar", "皮克斯",
    }
    if tag_names & foreign_origin_tags:
        return "excluded"
    foreign_origin_markers = (
        "国家 中国", "地区 中国", "国家/地区 中国", "原产地 中国",
        "国家 美国", "地区 美国", "国家/地区 美国", "原产地 美国",
        "国家 韩国", "地区 韩国", "国家/地区 韩国", "原产地 韩国",
        "国家 法国", "地区 法国", "国家 英国", "地区 英国",
    )
    if any(marker in text for marker in foreign_origin_markers):
        return "excluded"
    japanese_markers = (
        "日本", "日本动画", "日本漫画", "日本游戏", "日文", "ライトノベル",
        "少年ジャンプ", "講談社", "集英社", "角川", "kadokawa", "テレビアニメ",
    )
    if any(marker in text for marker in japanese_markers):
        return "confirmed"
    return "unknown"


def rank_search_results(query: str, subjects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for subject in subjects:
        source = japanese_source_status(subject)
        relevance = score_title_relevance(query, subject)
        if source == "excluded" or relevance["level"] == "irrelevant":
            continue
        item = dict(subject)
        item["_relevance_level"] = relevance["level"] if source in {"confirmed", "likely"} else "possible"
        item["_relevance_score"] = relevance["score"]
        item["_matched_title"] = relevance["matched_title"]
        item["_source_status"] = source
        ranked.append(item)
    return sorted(
        ranked,
        key=lambda item: (
            RELEVANCE_ORDER.get(item.get("_relevance_level"), 0),
            float(item.get("_relevance_score") or 0),
            str(item.get("date") or ""),
            float((item.get("rating") or {}).get("score") or 0),
        ),
        reverse=True,
    )


def search_subjects(
    keyword: str,
    limit: int = 10,
    offset: int = 0,
    fallback_keywords: Iterable[str | None] = (),
    subject_types: Iterable[int] | None = (1, 2, 4),
) -> list[dict[str, Any]]:
    """Search the exact UTF-8 term first, then conservative no-translation fallbacks."""
    keyword = (keyword or "").strip()
    if not keyword:
        return []

    candidates = [keyword, _compact_keyword(keyword), _plain_keyword(keyword)]
    for fallback in fallback_keywords:
        fallback = (fallback or "").strip()
        if fallback:
            candidates.extend([fallback, _compact_keyword(fallback), _plain_keyword(fallback)])

    seen: set[str] = set()
    possible_results: list[dict[str, Any]] = []
    seen_subject_ids: set[int] = set()
    for candidate in candidates:
        if not candidate or candidate.casefold() in seen:
            continue
        seen.add(candidate.casefold())
        payload: dict[str, Any] = {"keyword": candidate, "sort": "match"}
        if subject_types is not None:
            payload["filter"] = {"type": [int(value) for value in subject_types]}
        data = _request(
            "POST",
            "/search/subjects",
            params={"limit": min(max(limit, 1), 20), "offset": max(offset, 0)},
            json=payload,
        )
        results = rank_search_results(keyword, data.get("data", []))
        strict = [item for item in results if item.get("_relevance_level") != "possible"]
        possible = [item for item in results if item.get("_relevance_level") == "possible"]
        for item in possible:
            subject_id = int(item.get("id") or 0)
            if subject_id not in seen_subject_ids:
                possible_results.append(item)
                seen_subject_ids.add(subject_id)
        if strict:
            strict_ids = {int(item.get("id") or 0) for item in strict}
            return (strict + [item for item in possible_results if int(item.get("id") or 0) not in strict_ids])[: max(limit, 1)]
    return possible_results[: max(limit, 1)]


def search_subjects_by_category(
    keyword: str,
    category: str = "全部",
    limit: int = 10,
    fallback_keywords: Iterable[str | None] = (),
) -> list[dict[str, Any]]:
    """Search API and public HTML so adult public subjects are not omitted."""
    selected = category if category in CATEGORY_SUBJECT_TYPES else "全部"
    archive_subjects: list[dict[str, Any]] = []
    for record in bangumi_archive.search_archive_subjects(
        keyword,
        subject_types=CATEGORY_SUBJECT_TYPES[selected],
        limit=max(20, int(limit) * 3),
    ):
        subject = _archive_subject_dictionary(record)
        inferred = infer_local_category(subject, selected)
        if selected in {"动画", "漫画", "轻小说", "游戏"} and inferred != selected:
            continue
        archive_subjects.append(subject)
    ranked_archive = rank_search_results(keyword, archive_subjects)
    # Exact local Archive hits are complete public records and already carry
    # their true rating distribution.  Returning them immediately avoids an
    # unnecessary remote API + HTML round trip for hidden adult subjects.
    if (
        not readonly_account_connected()
        and any(item.get("_relevance_level") == "strict_exact" for item in ranked_archive)
    ):
        return enrich_precise_subject_ratings(
            ranked_archive, allow_network=False,
        )[: max(limit, 1)]
    api_results = search_subjects(
        keyword,
        limit=limit,
        fallback_keywords=fallback_keywords,
        subject_types=CATEGORY_SUBJECT_TYPES[selected],
    )
    web_categories = {
        "动画": (2,), "漫画": (1,), "轻小说": (1,), "游戏": (4,),
        "全部": (1, 2, 4), "其他": (1, 2, 4),
    }.get(selected, (1, 2, 4))
    public_items: list[dict[str, Any]] = []
    for web_category in web_categories:
        try:
            source = _request_web_page(
                f"https://bgm.tv/subject_search/{quote_plus(keyword)}?cat={web_category}"
            )
        except BangumiError:
            continue
        public_items.extend(_parse_browser_subject_list_page(source))
    public_by_id = {
        int(item["id"]): item
        for item in public_items if str(item.get("id") or "").isdigit()
    }
    existing_ids = {
        int(item.get("id") or 0) for item in api_results if str(item.get("id") or "").isdigit()
    }
    missing_ids = [
        subject_id for subject_id in dict.fromkeys(public_by_id)
        if subject_id not in existing_ids
    ][: max(20, int(limit) * 2)]
    public_subjects: list[dict[str, Any]] = []
    if missing_ids:
        with ThreadPoolExecutor(max_workers=min(6, len(missing_ids))) as executor:
            futures = {
                executor.submit(_subject_from_public_id, subject_id, public_by_id.get(subject_id)): subject_id
                for subject_id in missing_ids
            }
            for future in as_completed(futures):
                try:
                    subject = future.result()
                except (BangumiError, ValueError, TypeError):
                    continue
                if not isinstance(subject, dict):
                    continue
                inferred = infer_local_category(subject, selected)
                if selected in {"动画", "漫画", "轻小说", "游戏"} and inferred != selected:
                    continue
                public_subjects.append(subject)
    archive_subjects = [
        _archive_subject_dictionary(
            bangumi_archive.archive_subject(int(subject.get("id") or 0)) or {},
            public_by_id.get(int(subject.get("id") or 0)),
        )
        if public_by_id.get(int(subject.get("id") or 0))
        else subject
        for subject in archive_subjects
        if int(subject.get("id") or 0) not in existing_ids
    ]
    combined: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for subject in [*api_results, *public_subjects, *archive_subjects]:
        subject_id = int(subject.get("id") or 0)
        if not subject_id or subject_id in seen_ids:
            continue
        seen_ids.add(subject_id)
        combined.append(subject)
    token = readonly_access_token()
    if token:
        combined = enrich_authenticated_subjects(combined, token)
    combined = enrich_precise_subject_ratings(combined, allow_network=False)
    _remember_cover_urls({
        int(subject["id"]): (
            (subject.get("images") or {}).get("large")
            or (subject.get("images") or {}).get("common")
            or (subject.get("images") or {}).get("medium")
            or ""
        )
        for subject in combined
        if str(subject.get("id") or "").isdigit()
    })
    return rank_search_results(keyword, combined)[: max(limit, 1)]


def raw_type_name(subject: dict[str, Any]) -> str:
    return RAW_TYPE_NAMES.get(subject.get("type"), f"类型 {subject.get('type')}" if subject.get("type") else "未知")


def _classification_text(subject: dict[str, Any]) -> str:
    tags = subject.get("tags") or []
    tag_names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in tags]
    infobox = subject.get("infobox") or []
    if isinstance(infobox, str):
        info_text = infobox
    else:
        info_text = " ".join(
            f"{item.get('key', '')} {item.get('value', '')}" for item in infobox if isinstance(item, dict)
        )
    archive_info = str(subject.get("_archive_infobox") or "")
    meta_tags = subject.get("meta_tags") or []
    return " ".join([
        str(subject.get("name_cn") or ""), str(subject.get("name") or ""),
        str(subject.get("platform") or ""), str(subject.get("subtype") or ""),
        *[str(item) for item in meta_tags], *tag_names,
        info_text, archive_info,
    ]).casefold()


def infer_local_category(subject: dict[str, Any], preferred: str = "全部") -> str:
    subject_type = subject.get("type")
    if subject_type == 2:
        return "动画"
    if subject_type == 4:
        return "游戏"
    if subject_type in {3, 6}:
        return "其他"
    if subject_type == 1:
        try:
            platform_code = int(subject.get("platform") or 0)
        except (TypeError, ValueError):
            platform_code = 0
        if platform_code == 1002:
            return "轻小说"
        if platform_code == 1001:
            return "漫画"
        text = _classification_text(subject)
        light_novel_markers = ("轻小说", "輕小說", "ライトノベル", "light novel", "文库", "文庫", "小说", "小説")
        manga_markers = ("漫画", "コミック", "comic", "manga")
        if any(marker in text for marker in light_novel_markers):
            return "轻小说"
        if any(marker in text for marker in manga_markers):
            return "漫画"
        if preferred in {"漫画", "轻小说"}:
            return preferred
        return "漫画"
    return preferred if preferred in CATEGORY_LABELS and preferred != "全部" else "其他"


def infer_local_subtype(subject: dict[str, Any], category: str) -> str:
    text = _classification_text(subject)
    if category == "动画":
        platform = str(subject.get("platform") or subject.get("subtype") or "").strip().casefold()
        if "剧场版" in text or "劇場版" in text or "movie" in text:
            return "剧场版"
        if "ova" in text or "oad" in text:
            return "OVA"
        if platform == "web" or "网络动画" in text:
            return "WEB"
        if platform in {"sp", "special"} or "特别篇" in text or "特別篇" in text:
            return "SP"
        return "TV"
    if category == "漫画":
        return "漫画"
    if category == "轻小说":
        return "轻小说"
    if category == "游戏":
        if any(marker in text for marker in ("galgame", "visual novel", "美少女游戏", "恋爱冒险", "adv")):
            return "Galgame"
        if any(marker in text for marker in ("手游", "手机游戏", "android", "ios")):
            return "手游"
        if any(marker in text for marker in ("playstation", "xbox", "switch", "主机")):
            return "主机游戏"
        if any(marker in text for marker in ("windows", "pc")):
            return "PC游戏"
    return "其他"


def get_subject(subject_id: int) -> dict[str, Any]:
    return _request("GET", f"/subjects/{int(subject_id)}")


def get_subject_with_access_token(subject_id: int, access_token: str) -> dict[str, Any]:
    payload = _request_with_access_token(
        "GET", f"/subjects/{int(subject_id)}", access_token,
    )
    if not isinstance(payload, dict):
        raise BangumiError("Bangumi 没有返回条目详情。")
    return payload


def _subject_cover_url(subject: dict[str, Any]) -> str:
    images = subject.get("images") or {}
    value = str(
        images.get("large") or images.get("common") or images.get("medium")
        or images.get("grid") or images.get("small") or ""
    ).strip()
    return _normalize_cover_url(value) or (
        value if value.startswith("/app/static/r18_fallback_covers/") else ""
    )


def enrich_authenticated_subjects(
    subjects: Iterable[dict[str, Any]], access_token: str, *, max_workers: int = 6,
) -> list[dict[str, Any]]:
    """Fill every missing subject cover from the authenticated official API.

    Search results can come from the local official Archive when Bangumi hides
    an NSFW subject from anonymous requests.  Context variables do not cross
    worker threads, so the session token is passed explicitly for each detail
    request.  Only subject metadata is read; no collection endpoint is used.
    """
    rows = [dict(subject) for subject in subjects]
    token = str(access_token or "").strip()
    if not token:
        return rows
    missing_ids = list(dict.fromkeys(
        int(subject["id"])
        for subject in rows
        if str(subject.get("id") or "").isdigit()
        and not (_subject_cover_url(subject) or _cached_cover_url(int(subject["id"])))
    ))
    if not missing_ids:
        return rows

    details: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max(1, int(max_workers)), len(missing_ids))) as executor:
        futures = {
            executor.submit(get_subject_with_access_token, subject_id, token): subject_id
            for subject_id in missing_ids
        }
        for future in as_completed(futures):
            try:
                detail = future.result()
            except (BangumiError, ValueError, TypeError):
                continue
            if isinstance(detail, dict):
                details[futures[future]] = detail

    enriched: list[dict[str, Any]] = []
    discovered_covers: dict[int, str] = {}
    for subject in rows:
        subject_id = int(subject.get("id") or 0)
        detail = details.get(subject_id)
        if not detail:
            cached = (
                _cached_cover_url(subject_id) or _fallback_cover_url(subject_id)
            ) if subject_id else ""
            if cached and not _subject_cover_url(subject):
                subject["images"] = {"large": cached, "common": cached}
            enriched.append(subject)
            continue

        merged = merge_precise_subject_rating(detail, subject)
        for key, value in subject.items():
            if key.startswith("_") and key not in merged:
                merged[key] = value
        cover = _subject_cover_url(merged) or _subject_cover_url(subject)
        if cover:
            merged["images"] = {**(merged.get("images") or {}), "large": cover, "common": cover}
            discovered_covers[subject_id] = cover
        enriched.append(merged)
    _remember_cover_urls(discovered_covers)
    return enriched


def enrich_authenticated_ranking_rows(
    items: Iterable[dict[str, Any]], access_token: str, *, max_workers: int = 6,
) -> list[dict[str, Any]]:
    """Fill missing cover/detail fields for the visible ranking page only.

    This reads subject metadata through the authenticated official API. It does
    not call any collection endpoint and never persists the access token.
    """
    rows = [dict(item) for item in items]
    token = str(access_token or "").strip()
    if not token:
        return rows
    missing_ids = list(dict.fromkeys(
        int(item["id"])
        for item in rows
        if str(item.get("id") or "").isdigit() and not str(item.get("image") or "").strip()
    ))
    if not missing_ids:
        return rows
    details: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max(1, int(max_workers)), len(missing_ids))) as executor:
        futures = {
            executor.submit(get_subject_with_access_token, subject_id, token): subject_id
            for subject_id in missing_ids
        }
        for future in as_completed(futures):
            try:
                detail = future.result()
            except (BangumiError, ValueError, TypeError):
                continue
            if isinstance(detail, dict):
                details[futures[future]] = detail
    discovered_covers: dict[int, str] = {}
    for item in rows:
        subject_id = int(item.get("id") or 0)
        detail = details.get(subject_id)
        if not detail:
            continue
        image = _subject_cover_url(detail)
        if image:
            item["image"] = image
            discovered_covers[subject_id] = image
        item["subject"] = detail
        if not item.get("title"):
            item["title"] = detail.get("name_cn") or detail.get("name") or "未命名"
        if not item.get("original_title"):
            item["original_title"] = detail.get("name") or item.get("title") or ""
        if not item.get("info"):
            item["info"] = " · ".join(
                value for value in (
                    str(detail.get("date") or ""), str(detail.get("platform") or ""),
                ) if value
            )
    _remember_cover_urls(discovered_covers)
    return rows


def get_subject_persons(subject_id: int) -> list[dict[str, Any]]:
    """Return the public staff/person credits for a subject."""
    payload = _request("GET", f"/subjects/{int(subject_id)}/persons")
    return payload if isinstance(payload, list) else []


def get_subject_characters(subject_id: int) -> list[dict[str, Any]]:
    """Return public characters and their voice actors for a subject."""
    payload = _request("GET", f"/subjects/{int(subject_id)}/characters")
    return payload if isinstance(payload, list) else []


def list_subjects(
    subject_type: int, year: int, month: int, *, sort: str = "date", limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    """List public subjects through Bangumi's official year/month endpoint."""
    return _request("GET", "/subjects", params={
        "type": int(subject_type), "year": int(year), "month": int(month),
        "sort": sort, "limit": min(max(int(limit), 1), 100), "offset": max(int(offset), 0),
    })


def rating_total_votes(rating: dict[str, Any] | None) -> int | None:
    rating = rating or {}
    value = rating.get("total")
    if value in (None, ""):
        value = rating.get("count")
    if isinstance(value, dict):
        value = sum(int(item or 0) for item in value.values())
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def rating_score_from_counts(rating: dict[str, Any] | None) -> float | None:
    """Calculate Bangumi's real score from its public 1-10 vote distribution."""
    counts = (rating or {}).get("count")
    if not isinstance(counts, dict):
        return None
    weighted = 0
    total = 0
    for raw_score, raw_count in counts.items():
        try:
            score = int(raw_score)
            count = int(raw_count or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 10 and count >= 0:
            weighted += score * count
            total += count
    return None if total <= 0 else round(weighted / total, 2)


def normalize_subject(subject: dict[str, Any]) -> dict[str, Any]:
    rating = subject.get("rating") or {}
    images = subject.get("images") or {}
    subject_id = int(subject["id"])
    return {
        "bangumi_id": subject_id,
        "bangumi_url": f"{WEB_BASE}/{subject_id}",
        "bangumi_name": subject.get("name") or "",
        "bangumi_name_cn": subject.get("name_cn") or "",
        "bangumi_type": subject.get("type"),
        "bangumi_score": rating_score_from_counts(rating) if rating_score_from_counts(rating) is not None else rating.get("score"),
        "bangumi_rank": rating.get("rank") or None,
        "bangumi_total_votes": rating_total_votes(rating),
        "bangumi_date": subject.get("date") or "",
        "bangumi_summary": subject.get("summary") or "",
        "bangumi_image_url": images.get("large") or images.get("common") or images.get("medium") or "",
        "bangumi_tags_json": json.dumps(subject.get("tags") or [], ensure_ascii=False),
        "bangumi_rating_json": json.dumps(rating, ensure_ascii=False),
        "bangumi_raw_json": json.dumps(subject, ensure_ascii=False),
        "bangumi_last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def binding_fields(
    subject: dict[str, Any],
    fallback_title: str = "",
    fallback_original_title: str = "",
) -> dict[str, Any]:
    """Return binding data without ever erasing a usable local title."""
    normalized = normalize_subject(subject)
    title = normalized["bangumi_name_cn"] or normalized["bangumi_name"] or fallback_title.strip()
    original_title = (
        normalized["bangumi_name"]
        or fallback_original_title.strip()
        or fallback_title.strip()
        or title
    )
    return {**normalized, "title": title or original_title, "original_title": original_title}


def suggested_local_fields(
    subject: dict[str, Any], fallback_title: str = "", preferred_category: str = "全部"
) -> dict[str, Any]:
    normalized = binding_fields(subject, fallback_title, fallback_title)
    local_category = infer_local_category(subject, preferred_category)
    return {
        "title": normalized["title"],
        "original_title": normalized["original_title"],
        "type": local_category,
        "subtype": infer_local_subtype(subject, local_category),
        "status": "已看",
        "release_date": normalized["bangumi_date"],
        "year": int(normalized["bangumi_date"][:4]) if normalized["bangumi_date"][:4].isdigit() else None,
        **normalized,
    }
