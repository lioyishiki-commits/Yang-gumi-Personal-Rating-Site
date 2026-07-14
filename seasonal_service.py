"""Current-season Bangumi candidate pool and safe local status actions."""
from __future__ import annotations

import json
import html as html_lib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any

import bangumi_client as bgm
import database as db
import requests

ROOT = Path(__file__).resolve().parent
SEASONAL_POSTER_ROOT = ROOT / "static" / "seasonal_posters"
SEASONAL_SOURCE_PATH = ROOT / "data" / "seasonal_title_sources.json"
MISSING_COVER_REFRESH_PATH = ROOT / "data" / "missing_cover_refresh.json"
RATING_PRECISION_REFRESH_PATH = ROOT / "data" / "rating_precision_refresh.json"
KISSSUB_SCHEDULE_URL = "http://www.kisssub.org/"
YUC_SEASON_BASE_URL = "https://yuc.wiki"
KISSSUB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Yang-gumi/1.0",
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.7",
}
POSTER_HEADERS = {
    "User-Agent": "Yang-gumi/1.0 (+local Streamlit seasonal poster cache)",
    "Referer": "https://bgm.tv/",
}

_scheduler_lock = threading.Lock()
_scheduler_started = False
_refresh_lock = threading.Lock()


NON_JAPANESE_MARKERS = (
    "中国大陆", "中国动画", "国产动画", "国创", "donghua", "美国动画", "欧美动画",
    "american animation", "韩国动画", "韩国漫画", "webtoon", "法国动画", "英国动画",
    "netflix animation", "dreamworks", "disney", "pixar", "cartoon network",
)
NON_TV_MARKERS = ("剧场版", "映画", "movie", "ova", "oad", "web", "sp", "special", "短片", "pv", "总集篇")
ENDLESS_ANIME_MARKERS = ("海贼王", "名侦探柯南", "蜡笔小新", "哆啦A梦", "樱桃小丸子", "宝可梦")


def _subject_text(item: dict[str, Any]) -> str:
    subject = candidate_subject(item) if item.get("raw_json") is not None else item
    return json.dumps(subject, ensure_ascii=False).casefold()


def is_tv_seasonal_anime(item: dict[str, Any]) -> bool:
    """Conservatively identify TV anime from subtype, platform, infobox or tags."""
    subject = candidate_subject(item) if item.get("raw_json") is not None else item
    subtype = str(item.get("subtype") or subject.get("platform") or "").strip().casefold()
    if subtype == "tv" or re.fullmatch(r"tv\s*(动画|アニメ)?", subtype):
        return True
    if any(marker in subtype for marker in NON_TV_MARKERS):
        return False
    text = _subject_text(item)
    if any(f'"{marker}"' in text for marker in ("ova", "oad", "web", "movie", "sp")):
        return False
    return any(marker in text for marker in ('"platform": "tv"', '"放送平台": "tv"', '"类型": "tv"'))


def is_displayable_japanese_seasonal_anime(item: dict[str, Any]) -> bool:
    """Single main-area gate shared by every seasonal status/search/sort view."""
    subject = candidate_subject(item) if item.get("raw_json") is not None else item
    if int(subject.get("type") or 0) != 2:
        return False
    text = _subject_text(item)
    if any(marker in text for marker in NON_JAPANESE_MARKERS):
        return False
    return bgm.japanese_source_status(subject) == "confirmed"


def is_homepage_seasonal_anime(item: dict[str, Any], minimum_votes: int = 100) -> bool:
    """Show confirmed KissSub season seeds immediately; other entries keep the vote gate."""
    try:
        direct_rating = item.get("rating") if isinstance(item.get("rating"), dict) else {}
        cached_subject = candidate_subject(item) if item.get("raw_json") is not None or item.get("bangumi_id") else item
        votes = int(item.get("bangumi_total_votes") or direct_rating.get("total") or (cached_subject.get("rating") or {}).get("total") or 0)
    except (TypeError, ValueError):
        votes = 0
    subject = candidate_subject(item) if item.get("raw_json") is not None or item.get("bangumi_id") else item
    is_curated_seed = subject.get("_yanggumi_season_source") in {"kisssub", "yuc"}
    return (
        (is_curated_seed or votes >= minimum_votes)
        and _cached_item_matches_season(item)
        and not is_short_episode_anime(item)
        and is_displayable_japanese_seasonal_anime(item)
    )


def current_season(value: date | datetime | None = None) -> dict[str, Any]:
    value = value or datetime.now()
    month = int(value.month)
    start_month = 1 if month <= 3 else 4 if month <= 6 else 7 if month <= 9 else 10
    return {
        "year": int(value.year),
        "season_code": f"Q{((start_month - 1) // 3) + 1}",
        "start_month": start_month,
        "months": tuple(range(start_month, start_month + 3)),
        "month_label": f"{start_month}月番",
    }


def season_start_datetime(season: dict[str, Any]) -> datetime:
    return datetime(int(season["year"]), int(season["start_month"]), 1, 0, 0, 0)


def _season_date_bounds(season: dict[str, Any], *, allow_continuing: bool = False) -> tuple[date, date]:
    year = int(season["year"])
    start_month = int(season["start_month"])
    start = date(year, start_month, 1) - timedelta(days=400 if allow_continuing else 21)
    end = date(year + 1, 1, 1) if start_month == 10 else date(year, start_month + 3, 1)
    return start, end


def _subject_matches_season(
    subject: dict[str, Any], season: dict[str, Any], *, allow_missing: bool, allow_continuing: bool = False,
) -> bool:
    release_date = str(subject.get("date") or subject.get("release_date") or subject.get("air_date") or "")[:10]
    if not release_date:
        return allow_missing
    try:
        released = date.fromisoformat(release_date)
    except ValueError:
        return allow_missing
    start, end = _season_date_bounds(season, allow_continuing=allow_continuing)
    return start <= released < end


def _cached_item_matches_season(item: dict[str, Any]) -> bool:
    year = item.get("season_year")
    season_code = str(item.get("season_code") or "")
    if not str(year or "").isdigit() or not re.fullmatch(r"Q[1-4]", season_code):
        return True
    subject = candidate_subject(item) if item.get("raw_json") is not None else dict(item)
    if not subject.get("date"):
        subject = {**subject, "date": item.get("release_date") or item.get("air_date") or ""}
    curated = subject.get("_yanggumi_season_source") in {"kisssub", "yuc"}
    continuing = subject.get("_yanggumi_broadcast_day") not in (None, "")
    expected = {
        "year": int(year),
        "season_code": season_code,
        "start_month": (int(season_code[1]) - 1) * 3 + 1,
    }
    return _subject_matches_season(subject, expected, allow_missing=curated, allow_continuing=continuing)


def _duration_minutes(value: Any) -> float | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", text)
    if clock:
        first, second, third = (int(part) if part is not None else None for part in clock.groups())
        if third is not None:
            return first * 60 + second + third / 60
        return first + second / 60
    minute = re.search(r"(?:约|約)?\s*(\d+(?:\.\d+)?)\s*(?:分钟|分鐘|分|min(?:ute)?s?)(?!\w)", text, flags=re.I)
    return float(minute.group(1)) if minute else None


def is_short_episode_anime(item: dict[str, Any], minimum_minutes: float = 12.0) -> bool:
    subject = candidate_subject(item) if item.get("raw_json") is not None else item
    if subject.get("_yanggumi_short_episode") is True:
        return True
    duration_keys = ("每话时长", "每話時長", "单集片长", "單集片長", "每集时长", "播放时长", "时长", "片长", "duration", "runtime")
    for field in subject.get("infobox") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip().casefold()
        if not any(marker.casefold() in key for marker in duration_keys):
            continue
        value = field.get("value")
        if isinstance(value, list):
            value = " ".join(str(part.get("v") if isinstance(part, dict) else part) for part in value)
        minutes = _duration_minutes(value)
        if minutes is not None:
            return minutes < float(minimum_minutes)
    return False


def _season_source_key(season: dict[str, Any]) -> str:
    return f"{int(season['year'])}-{season['season_code']}"


def _load_source_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(SEASONAL_SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = {}
    seasons = payload.get("seasons")
    return {"version": 1, "seasons": seasons if isinstance(seasons, dict) else {}}


def _save_source_manifest(payload: dict[str, Any]) -> None:
    SEASONAL_SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SEASONAL_SOURCE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SEASONAL_SOURCE_PATH)


def parse_kisssub_season_titles(page_html: str, start_month: int) -> list[str]:
    """Extract only titles between KissSub's 'N月新番→' and '←N月新番' markers."""
    if "visitor-test-form" in page_html or "captcha" in page_html.casefold():
        raise RuntimeError("KissSub 当前要求访客验证")
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", page_html, flags=re.I | re.S)
    value = re.sub(r"</(?:a|span|li|td|th|div|p|tr|br)\s*>", "\n", value, flags=re.I)
    value = html_lib.unescape(re.sub(r"<[^>]+>", " ", value))
    tokens = [re.sub(r"\s+", " ", part).strip() for part in value.splitlines()]
    start_pattern = re.compile(rf"{int(start_month)}\s*月\s*新番\s*[→➡]")
    end_pattern = re.compile(rf"[←⬅]\s*{int(start_month)}\s*月\s*新番")
    ignored = {"本季番", "番组报错", "短评投稿", "更多往期", "星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "剧场版", "特殊放送"}
    collecting = False
    titles: list[str] = []
    for token in tokens:
        if not token:
            continue
        start_match = start_pattern.search(token)
        end_match = end_pattern.search(token)
        if start_match:
            collecting = True
            token = token[start_match.end():].strip(" ·|｜-—")
        if end_match:
            before = token[:end_match.start()].strip(" ·|｜-—")
            if collecting and before:
                token = before
            else:
                token = ""
            collecting = False
        if not collecting and not (end_match and token):
            continue
        token = re.sub(r"^[•·]+|[•·]+$", "", token).strip()
        if token in ignored or len(token) < 2 or len(token) > 100 or token.isdigit():
            continue
        if token not in titles:
            titles.append(token)
    return titles


def fetch_kisssub_season_titles(season: dict[str, Any]) -> list[str]:
    response = requests.get(KISSSUB_SCHEDULE_URL, headers=KISSSUB_HEADERS, timeout=(5, 20), allow_redirects=True)
    response.raise_for_status()
    if "/public/html/start/" in response.url or "visitor-test-form" in response.text:
        raise RuntimeError("KissSub 当前要求访客验证")
    titles = parse_kisssub_season_titles(response.text, int(season["start_month"]))
    if not titles:
        raise RuntimeError("KissSub 当季番组表没有解析到标题")
    return titles


def yuc_season_url(season: dict[str, Any]) -> str:
    return f"{YUC_SEASON_BASE_URL}/{int(season['year'])}{int(season['start_month']):02d}/"


def parse_yuc_season_entries(page_html: str) -> list[dict[str, str]]:
    """Read Yuc Wiki's detailed title/poster pairs, excluding its compact schedule icons."""
    pattern = re.compile(
        r'<div[^>]*style=["\'][^"\']*float\s*:\s*left[^"\']*["\'][^>]*>'
        r'(?P<image_block>[\s\S]*?)</div>\s*<div[^>]*>\s*<table[\s\S]*?'
        r'<p[^>]*class=["\']title_cn_r\d*["\'][^>]*>(?P<title>[\s\S]*?)</p>',
        flags=re.I,
    )
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(page_html):
        image_match = re.search(r'(?:data-src|src)=["\']([^"\']+)["\']', match.group("image_block"), flags=re.I)
        title = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group("title")))
        title = re.sub(r"\s+", " ", title).strip()
        following = page_html[match.end():match.end() + 800]
        original_match = re.match(
            r'\s*<p[^>]*class=["\']title_jp_r\d*["\'][^>]*>([\s\S]*?)</p>', following, flags=re.I,
        )
        original_title = ""
        if original_match:
            original_title = html_lib.unescape(re.sub(r"<[^>]+>", " ", original_match.group(1)))
            original_title = re.sub(r"\s+", " ", original_title).strip()
        poster_url = html_lib.unescape(image_match.group(1)).strip() if image_match else ""
        if not title or not poster_url or title in seen:
            continue
        seen.add(title)
        entries.append({"title": title, "original_title": original_title, "poster_url": poster_url})
    return entries


def parse_yuc_schedule_entries(page_html: str) -> list[dict[str, Any]]:
    """Extract Yuc Wiki's Monday-Sunday TV schedule using a 06:00 broadcast-day boundary."""
    schedule_html = page_html.split('<p class="intro">', 1)[0]
    day_labels = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    day_positions: list[tuple[int, int]] = []
    for match in re.finditer(r'<td[^>]*class=["\']date2["\'][^>]*>([\s\S]*?)</td>', schedule_html, flags=re.I):
        label = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        day_index = next((index for index, day in enumerate(day_labels) if day in label), None)
        if day_index is not None:
            day_positions.append((match.start(), day_index))

    card_pattern = re.compile(
        r'<div[^>]*style=["\'][^"\']*float\s*:\s*left[^"\']*["\'][^>]*>\s*'
        r'<div[^>]*class=["\']div_date["\'][^>]*>(?P<meta>[\s\S]*?)</div>\s*'
        r'<div[^>]*>\s*<table[\s\S]*?<td[^>]*class=["\']date_title_["\'][^>]*>(?P<title>[\s\S]*?)</td>',
        flags=re.I,
    )
    entries: list[dict[str, Any]] = []
    for match in card_pattern.finditer(schedule_html):
        prior_days = [day_index for position, day_index in day_positions if position < match.start()]
        if not prior_days:
            continue
        day_index = prior_days[-1]
        meta = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group("meta")))
        time_match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})\s*~", meta)
        if not time_match:
            continue
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        broadcast_hour = hour
        if hour < 6:
            day_index = (day_index - 1) % 7
            broadcast_hour += 24
        duration_match = re.search(r"\d+(?:\.\d+)?\s*(?:分钟|分鐘|分|min(?:ute)?s?)(?!\w)", meta, flags=re.I)
        duration = _duration_minutes(duration_match.group(0)) if duration_match else None
        title = html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group("title")))
        title = re.sub(r"\s+", " ", title).strip()
        image_match = re.search(r'(?:data-src|src)=["\']([^"\']+)["\']', match.group("meta"), flags=re.I)
        note_match = re.search(r'<p[^>]*class=["\']imgep\d*["\'][^>]*>([\s\S]*?)</p>', match.group("meta"), flags=re.I)
        note = html_lib.unescape(re.sub(r"<[^>]+>", " ", note_match.group(1))).strip() if note_match else ""
        if title and all(row["title"] != title for row in entries):
            entries.append({
                "title": title,
                "poster_url": html_lib.unescape(image_match.group(1)).strip() if image_match else "",
                "broadcast_day": day_index,
                "broadcast_day_label": day_labels[day_index],
                "broadcast_time": f"{broadcast_hour:02d}:{minute:02d}",
                "broadcast_sort": day_index * 1440 + (broadcast_hour - 6) * 60 + minute,
                "broadcast_note": note,
                "is_short": "泡面" in meta or "泡麵" in meta or (duration is not None and duration < 12),
                "is_endless": any(marker in title for marker in ENDLESS_ANIME_MARKERS),
            })
    return entries


def parse_yuc_short_entries(page_html: str) -> list[dict[str, Any]]:
    return [entry for entry in parse_yuc_schedule_entries(page_html) if entry.get("is_short")]


def parse_yuc_short_titles(page_html: str) -> list[str]:
    return [entry["title"] for entry in parse_yuc_short_entries(page_html)]


def _yuc_titles_are_aliases(schedule_title: str, detail_title: str) -> bool:
    if bgm.normalize_title(schedule_title) == bgm.normalize_title(detail_title):
        return True
    relevance = bgm.score_title_relevance(
        schedule_title, {"name_cn": detail_title, "name": detail_title, "infobox": []},
    )
    if relevance["level"] in {"strict_exact", "strict_contains", "series_related"}:
        return True
    if float(relevance["score"]) >= 80:
        return True
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", detail_title)
    acronym = "".join(word[0] for word in words if len(word) > 1).upper()
    return len(acronym) >= 3 and acronym in schedule_title.upper()


def fetch_yuc_season_entries(season: dict[str, Any]) -> list[dict[str, Any]]:
    response = requests.get(yuc_season_url(season), headers=KISSSUB_HEADERS, timeout=(5, 30))
    response.raise_for_status()
    response.encoding = "utf-8"
    details = parse_yuc_season_entries(response.text)
    details_by_key = {bgm.normalize_title(entry["title"]): entry for entry in details}
    details_by_poster = {entry["poster_url"]: entry for entry in details if entry.get("poster_url")}
    entries: list[dict[str, Any]] = []
    for schedule_entry in parse_yuc_schedule_entries(response.text):
        if schedule_entry.get("is_endless"):
            continue
        key = bgm.normalize_title(schedule_entry["title"])
        detail = details_by_key.get(key)
        if detail is None:
            poster_detail = details_by_poster.get(schedule_entry.get("poster_url") or "")
            if poster_detail and _yuc_titles_are_aliases(schedule_entry["title"], poster_detail["title"]):
                detail = poster_detail
        merged = dict(schedule_entry)
        aliases = [schedule_entry["title"]]
        if detail:
            merged["original_title"] = detail.get("original_title") or ""
            merged["poster_url"] = detail.get("poster_url") or merged.get("poster_url") or ""
            aliases.extend([detail["title"], detail.get("original_title") or ""])
        merged["aliases"] = list(dict.fromkeys(value for value in aliases if value))
        entries.append(merged)
    if not entries:
        raise RuntimeError("Yuc Wiki 当季页面没有解析到标题和海报")
    return entries


def _season_source_entry(
    season: dict[str, Any], *, try_live: bool, payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    payload = payload or _load_source_manifest()
    key = _season_source_key(season)
    entry = payload["seasons"].setdefault(key, {"titles": [], "matches": {}})
    error = ""
    if try_live or not entry.get("titles"):
        entry["last_attempt"] = datetime.now().isoformat(timespec="seconds")
        try:
            entry["titles"] = fetch_kisssub_season_titles(season)
            entry["source"] = "kisssub"
            entry["live_fetched_at"] = datetime.now().isoformat(timespec="seconds")
            entry["last_error"] = ""
        except Exception as exc:
            error = str(exc)
            entry["last_error"] = error
        _save_source_manifest(payload)
    return payload, entry, error


def _update_yuc_source_entry(
    season: dict[str, Any], payload: dict[str, Any], entry: dict[str, Any], *, try_live: bool,
) -> str:
    error = ""
    if try_live or not entry.get("yuc_titles"):
        entry["yuc_last_attempt"] = datetime.now().isoformat(timespec="seconds")
        try:
            rows = fetch_yuc_season_entries(season)
            entry["yuc_titles"] = [row["title"] for row in rows]
            entry["yuc_posters"] = {row["title"]: row["poster_url"] for row in rows}
            entry["yuc_short_titles"] = [row["title"] for row in rows if row.get("is_short")]
            entry["yuc_aliases"] = {
                row["title"]: list(row.get("aliases") or [row["title"]]) for row in rows
            }
            entry["yuc_broadcasts"] = {
                row["title"]: {
                    "day": int(row["broadcast_day"]),
                    "day_label": str(row["broadcast_day_label"]),
                    "time": str(row["broadcast_time"]),
                    "sort": int(row["broadcast_sort"]),
                    "note": str(row.get("broadcast_note") or ""),
                }
                for row in rows if row.get("broadcast_day") is not None and row.get("broadcast_time")
            }
            entry["yuc_live_fetched_at"] = datetime.now().isoformat(timespec="seconds")
            entry["yuc_last_error"] = ""
        except Exception as exc:
            error = str(exc)
            entry["yuc_last_error"] = error
        _save_source_manifest(payload)
    return error


def _poster_ext(url: str, content_type: str = "") -> str:
    suffix = Path(str(url).split("?", 1)[0]).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    content_type = content_type.lower()
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    return ".jpg"


def _poster_dir(year: int, season_code: str) -> Path:
    return SEASONAL_POSTER_ROOT / f"{int(year)}_{season_code}"


def seasonal_poster_static_url(year: int, season_code: str, bangumi_id: int) -> str:
    poster_dir = _poster_dir(year, season_code)
    for path in poster_dir.glob(f"{int(bangumi_id)}.*"):
        if path.is_file() and path.stat().st_size > 0:
            relative = path.resolve().relative_to((ROOT / "static").resolve()).as_posix()
            return f"/app/static/{relative}"
    return ""


def preload_seasonal_posters(year: int, season_code: str, items: list[dict[str, Any]]) -> int:
    """Download current-quarter posters into Streamlit static cache for smooth carousel use."""
    poster_dir = _poster_dir(year, season_code)
    poster_dir.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[int, list[str]]] = []
    for item in items:
        try:
            bangumi_id = int(item.get("bangumi_id") or item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        image_url = str(item.get("image_url") or item.get("bangumi_image_url") or "").strip()
        subject = candidate_subject(item) if item.get("raw_json") is not None else item
        yuc_poster_url = str(subject.get("_yanggumi_yuc_poster") or "").strip()
        image_urls = list(dict.fromkeys(url for url in (image_url, yuc_poster_url) if url))
        if not bangumi_id or not image_urls or seasonal_poster_static_url(year, season_code, bangumi_id):
            continue
        pending.append((bangumi_id, image_urls))

    def download(values: tuple[int, list[str]]) -> int:
        bangumi_id, image_urls = values
        for image_url in image_urls:
            try:
                response = requests.get(image_url, headers=POSTER_HEADERS, timeout=(3, 10))
                response.raise_for_status()
                content_type = response.headers.get("content-type") or ""
                if "image" not in content_type.lower() and not response.content[:16]:
                    continue
                suffix = _poster_ext(image_url, content_type)
                target = poster_dir / f"{bangumi_id}{suffix}"
                temp = poster_dir / f"{bangumi_id}{suffix}.tmp"
                temp.write_bytes(response.content)
                temp.replace(target)
                return 1
            except Exception:
                continue
        return 0

    if not pending:
        return 0
    with ThreadPoolExecutor(max_workers=min(8, len(pending))) as executor:
        return sum(executor.map(download, pending))


def _candidate(subject: dict[str, Any]) -> dict[str, Any]:
    normalized = bgm.normalize_subject(subject)
    name_cn = normalized["bangumi_name_cn"]
    name = normalized["bangumi_name"]
    source = bgm.japanese_source_status(subject)
    platform = str(subject.get("platform") or "").casefold()
    excluded_platforms = {"其他", "mv", "cm", "pv", "宣传片", "music video"}
    if platform in excluded_platforms:
        source = "excluded"
    elif source != "confirmed":
        source = "unconfirmed"
    return {
        "bangumi_id": normalized["bangumi_id"],
        "title": name_cn or name or "未命名动画",
        "original_title": name or name_cn,
        "name_cn": name_cn,
        "name": name,
        "release_date": normalized["bangumi_date"],
        "air_date": normalized["bangumi_date"],
        "image_url": normalized["bangumi_image_url"],
        "bangumi_score": normalized["bangumi_score"],
        "bangumi_rank": normalized["bangumi_rank"],
        "bangumi_total_votes": normalized["bangumi_total_votes"],
        "summary": normalized["bangumi_summary"],
        "tags_json": normalized["bangumi_tags_json"],
        "raw_json": json.dumps(subject, ensure_ascii=False),
        "source_status": source,
    }


def _mark_season_subject(
    subject: dict[str, Any], source_title: str, source_name: str, poster_url: str = "",
    is_short: bool = False, broadcast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    marked = dict(subject)
    marked["_yanggumi_season_source"] = source_name
    marked["_yanggumi_season_title"] = source_title
    if source_name == "kisssub":
        marked["_yanggumi_kisssub_title"] = source_title
    if poster_url:
        marked["_yanggumi_yuc_poster"] = poster_url
    if is_short:
        marked["_yanggumi_short_episode"] = True
    if broadcast:
        marked["_yanggumi_broadcast_day"] = int(broadcast["day"])
        marked["_yanggumi_broadcast_day_label"] = str(broadcast["day_label"])
        marked["_yanggumi_broadcast_time"] = str(broadcast["time"])
        marked["_yanggumi_broadcast_sort"] = int(broadcast["sort"])
        marked["_yanggumi_broadcast_note"] = str(broadcast.get("note") or "")
    return marked


def _mark_kisssub_subject(subject: dict[str, Any], source_title: str) -> dict[str, Any]:
    return _mark_season_subject(subject, source_title, "kisssub")


def _best_kisssub_match(
    title: str, season: dict[str, Any], subjects: list[dict[str, Any]], *, allow_continuing: bool = False,
) -> dict[str, Any] | None:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for subject in subjects:
        if int(subject.get("type") or 0) != 2:
            continue
        if allow_continuing and not is_tv_seasonal_anime(subject):
            continue
        relevance = bgm.score_title_relevance(title, subject)
        if not _subject_matches_season(
            subject, season, allow_missing=True, allow_continuing=allow_continuing,
        ):
            continue
        release_date = str(subject.get("date") or "")[:10]
        current_quarter = False
        try:
            released = date.fromisoformat(release_date)
            current_quarter = released.year == int(season["year"]) and released.month in season["months"]
        except ValueError:
            pass
        accepted_level = relevance["level"] in {"strict_exact", "strict_contains", "series_related"}
        possible_threshold = 50 if current_quarter else 60
        if not accepted_level and not (
            relevance["level"] == "possible" and float(relevance["score"]) >= possible_threshold
        ):
            continue
        score = float(relevance["score"])
        try:
            released = date.fromisoformat(release_date)
            if released.year == int(season["year"]) and released.month in season["months"]:
                score += 500
            elif released >= season_start_datetime(season).date() - timedelta(days=45):
                score += 80
        except ValueError:
            pass
        ranked.append((score, subject))
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None


def match_kisssub_titles(
    titles: list[str], season: dict[str, Any], known_matches: dict[str, Any] | None = None,
    source_names: dict[str, str] | None = None, poster_urls: dict[str, str] | None = None,
    short_titles: set[str] | None = None,
    broadcasts: dict[str, dict[str, Any]] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Resolve curated season names to Bangumi subjects without failing the whole batch."""
    current_titles = set(titles)
    matches = {
        str(title): int(subject_id) for title, subject_id in (known_matches or {}).items()
        if str(title) in current_titles and str(subject_id).isdigit()
    }
    source_names = source_names or {}
    poster_urls = poster_urls or {}
    short_titles = short_titles or set()
    broadcasts = broadcasts or {}
    aliases = aliases or {}
    candidates: list[dict[str, Any]] = []
    failures: list[str] = []

    def resolve(title: str) -> tuple[str, dict[str, Any] | None, int | None]:
        try:
            broadcast = broadcasts.get(title)
            allow_continuing = broadcast is not None
            subject_id = matches.get(title)
            subject: dict[str, Any] | None = None
            if subject_id:
                try:
                    subject = bgm.get_subject(subject_id)
                except bgm.BangumiError:
                    subject_id = None
                if subject is not None and (
                    not _subject_matches_season(
                        subject, season, allow_missing=True, allow_continuing=allow_continuing,
                    )
                    or (allow_continuing and not is_tv_seasonal_anime(subject))
                ):
                    subject_id = None
                    subject = None
            if subject is None:
                selected = None
                search_titles = list(dict.fromkeys([title, *(aliases.get(title) or [])]))
                for search_title in search_titles:
                    results = bgm.search_subjects(search_title, limit=12, subject_types=(2,))
                    selected = _best_kisssub_match(
                        search_title, season, results, allow_continuing=allow_continuing,
                    )
                    if selected:
                        break
                if not selected:
                    return title, None, None
                subject_id = int(selected["id"])
                try:
                    subject = bgm.get_subject(subject_id)
                except bgm.BangumiError:
                    subject = selected
            item = _candidate(_mark_season_subject(
                subject, title, source_names.get(title, "kisssub"), poster_urls.get(title, ""),
                title in short_titles, broadcast,
            ))
            return title, item if item["source_status"] != "excluded" else None, subject_id
        except Exception:
            return title, None, None

    if not titles:
        return candidates, matches, failures
    with ThreadPoolExecutor(max_workers=min(8, len(titles))) as executor:
        resolved = executor.map(resolve, titles)
        for title, item, subject_id in resolved:
            if item is None or subject_id is None:
                failures.append(title)
                matches.pop(title, None)
                continue
            matches[title] = int(subject_id)
            candidates.append(item)
    return candidates, matches, failures


def fetch_seasonal_candidates(season: dict[str, Any], page_size: int = 100) -> list[dict[str, Any]]:
    """Fetch one quarter from Bangumi's official public /v0/subjects endpoint."""
    found: dict[int, dict[str, Any]] = {}
    for month in season["months"]:
        offset = 0
        while True:
            payload = bgm.list_subjects(2, season["year"], month, limit=page_size, offset=offset)
            rows = payload.get("data") or []
            for subject in rows:
                if int(subject.get("type") or 0) != 2:
                    continue
                release_date = str(subject.get("date") or "")[:10]
                try:
                    released = date.fromisoformat(release_date)
                except ValueError:
                    continue
                if released.year != season["year"] or released.month not in season["months"]:
                    continue
                item = _candidate(subject)
                if item["source_status"] != "excluded":
                    found[item["bangumi_id"]] = item
            offset += len(rows)
            if not rows or offset >= int(payload.get("total") or 0):
                break
    return sorted(found.values(), key=lambda item: (item.get("air_date") or "9999-99-99", item["bangumi_id"]))


def refresh_current_season(value: date | datetime | None = None) -> tuple[dict[str, Any], int]:
    with _refresh_lock:
        return _refresh_current_season(value)


def _refresh_current_season(value: date | datetime | None = None) -> tuple[dict[str, Any], int]:
    now = value or datetime.now()
    season = current_season(now)
    errors: list[str] = []
    found: dict[int, dict[str, Any]] = {}
    fresh_ids: set[int] = set()
    official_ids: set[int] = set()
    existing = db.list_seasonal_anime(season["year"], season["season_code"], include_unconfirmed=True)
    for cached in existing:
        if not _cached_item_matches_season(cached):
            continue
        subject = candidate_subject(cached)
        found[int(cached["bangumi_id"])] = _candidate(subject)

    try:
        for item in fetch_seasonal_candidates(season):
            bangumi_id = int(item["bangumi_id"])
            found[bangumi_id] = item
            fresh_ids.add(bangumi_id)
            official_ids.add(bangumi_id)
    except Exception as exc:
        errors.append(f"Bangumi 月度列表：{exc}")

    source_payload = _load_source_manifest()
    source_entry = (source_payload.get("seasons") or {}).get(_season_source_key(season)) or {}
    quarter_open = now.date() == season_start_datetime(season).date()
    source_payload, source_entry, source_error = _season_source_entry(
        season, try_live=quarter_open or not source_entry_live(source_entry, season), payload=source_payload,
    )
    if source_error:
        errors.append(source_error)
    yuc_error = _update_yuc_source_entry(
        season, source_payload, source_entry,
        try_live=quarter_open or not bool(source_entry.get("yuc_live_fetched_at")),
    )
    if yuc_error:
        errors.append(yuc_error)
    kisssub_titles = [str(title).strip() for title in source_entry.get("titles", []) if str(title).strip()]
    yuc_titles = [str(title).strip() for title in source_entry.get("yuc_titles", []) if str(title).strip()]
    titles = list(dict.fromkeys(kisssub_titles + yuc_titles))
    yuc_posters = {str(title): str(url) for title, url in (source_entry.get("yuc_posters") or {}).items() if str(url)}
    source_names = {title: "kisssub" for title in kisssub_titles}
    source_names.update({title: "yuc" for title in yuc_titles})
    short_titles = {str(title) for title in source_entry.get("yuc_short_titles", []) if str(title)}
    broadcasts = {
        str(title): value for title, value in (source_entry.get("yuc_broadcasts") or {}).items()
        if isinstance(value, dict) and value.get("day") is not None and value.get("time")
    }
    yuc_aliases = {
        str(title): [str(value) for value in values if str(value)]
        for title, values in (source_entry.get("yuc_aliases") or {}).items()
        if isinstance(values, list)
    }
    seeded, matches, failed_titles = match_kisssub_titles(
        titles, season, source_entry.get("matches") or {}, source_names, yuc_posters, short_titles, broadcasts,
        yuc_aliases,
    )
    source_entry["matches"] = matches
    source_entry["last_match_attempt"] = datetime.now().isoformat(timespec="seconds")
    source_entry["pending_titles"] = failed_titles
    regular_yuc_titles = [title for title in yuc_titles if title not in short_titles]
    source_entry["quarter_audit"] = {
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "status": "complete" if all(title in matches for title in regular_yuc_titles) else "incomplete",
        "expected_yuc_regular": len(regular_yuc_titles),
        "matched_yuc_regular": sum(1 for title in regular_yuc_titles if title in matches),
        "pending_yuc_regular": [title for title in regular_yuc_titles if title not in matches],
        "kisssub_titles": len(kisssub_titles),
        "matched_kisssub_titles": sum(1 for title in kisssub_titles if title in matches),
        "bangumi_official_candidates": len(official_ids),
        "matched_union_subjects": len({int(subject_id) for subject_id in matches.values()}),
    }
    _save_source_manifest(source_payload)
    for item in seeded:
        bangumi_id = int(item["bangumi_id"])
        found[bangumi_id] = item
        fresh_ids.add(bangumi_id)

    def refresh_cached(values: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any] | None]:
        bangumi_id, cached_item = values
        try:
            old_subject = candidate_subject(cached_item)
            refreshed = bgm.get_subject(bangumi_id)
            source_name = str(old_subject.get("_yanggumi_season_source") or "")
            if source_name in {"kisssub", "yuc"}:
                broadcast = None
                if old_subject.get("_yanggumi_broadcast_day") not in (None, ""):
                    broadcast = {
                        "day": old_subject.get("_yanggumi_broadcast_day"),
                        "day_label": old_subject.get("_yanggumi_broadcast_day_label") or "",
                        "time": old_subject.get("_yanggumi_broadcast_time") or "",
                        "sort": old_subject.get("_yanggumi_broadcast_sort") or 0,
                        "note": old_subject.get("_yanggumi_broadcast_note") or "",
                    }
                refreshed = _mark_season_subject(
                    refreshed,
                    str(old_subject.get("_yanggumi_season_title") or old_subject.get("_yanggumi_kisssub_title") or ""),
                    source_name,
                    str(old_subject.get("_yanggumi_yuc_poster") or ""),
                    bool(old_subject.get("_yanggumi_short_episode")),
                    broadcast,
                )
            return bangumi_id, _candidate(refreshed)
        except Exception:
            return bangumi_id, None

    stale_items = [(bangumi_id, item) for bangumi_id, item in found.items() if bangumi_id not in fresh_ids]
    if stale_items:
        with ThreadPoolExecutor(max_workers=min(8, len(stale_items))) as executor:
            for bangumi_id, refreshed_item in executor.map(refresh_cached, stale_items):
                if refreshed_item is not None:
                    found[bangumi_id] = refreshed_item

    def candidate_order(item: dict[str, Any]) -> tuple[Any, ...]:
        subject = candidate_subject(item)
        broadcast_sort = subject.get("_yanggumi_broadcast_sort")
        if str(broadcast_sort).isdigit():
            return (0, int(broadcast_sort), item["bangumi_id"])
        return (1, item.get("air_date") or "9999-99-99", item["bangumi_id"])

    eligible = found.values()
    if broadcasts:
        eligible = (
            item for item in eligible
            if str(candidate_subject(item).get("_yanggumi_broadcast_day")).isdigit()
        )
    candidates = sorted((item for item in eligible if not is_short_episode_anime(item)), key=candidate_order)
    if not candidates:
        message = "；".join(errors) or "本季没有可保存的 Bangumi 条目"
        db.mark_seasonal_sync(season["year"], season["season_code"], "error", message)
        raise RuntimeError(message)
    count = db.upsert_seasonal_anime(candidates, season["year"], season["season_code"], season["month_label"])
    preload_seasonal_posters(season["year"], season["season_code"], candidates)
    db.mark_seasonal_sync(season["year"], season["season_code"], "success", "；".join(errors))
    return season, count


def source_entry_live(source_entry: dict[str, Any] | None, season: dict[str, Any]) -> bool:
    if source_entry is None:
        source_entry = (_load_source_manifest().get("seasons") or {}).get(_season_source_key(season)) or {}
    return bool(source_entry.get("live_fetched_at"))


def refresh_current_season_if_due(value: date | datetime | None = None) -> tuple[bool, dict[str, Any], int]:
    """Refresh after the quarter opens at 00:00, then at most once per local day."""
    now = value or datetime.now()
    season = current_season(now)
    meta = db.seasonal_cache_meta(season["year"], season["season_code"])
    last_sync = str((meta or {}).get("last_sync") or "")
    if last_sync:
        try:
            if datetime.fromisoformat(last_sync) < season_start_datetime(season):
                refreshed_season, count = refresh_current_season(now)
                return True, refreshed_season, count
        except ValueError:
            pass
    if last_sync[:10] == now.date().isoformat():
        return False, season, 0
    refreshed_season, count = refresh_current_season(now)
    return True, refreshed_season, count


def refresh_missing_anime_covers() -> int:
    """Fill only empty anime covers from the corresponding Bangumi subject."""
    updated = 0
    for work in db.list_works():
        if work.get("type") != "动画":
            continue
        if any(str(work.get(key) or "").strip() for key in ("bangumi_image_url", "cover_url", "cover_path")):
            continue
        subject = None
        subject_id = work.get("bangumi_id")
        if subject_id:
            try:
                subject = bgm.get_subject(int(subject_id))
            except Exception:
                continue
        else:
            title = str(work.get("title") or work.get("original_title") or "").strip()
            if not title:
                continue
            try:
                candidates = bgm.search_subjects_by_category(title, "动画", limit=10)
            except Exception:
                continue
            normalized_title = bgm.normalize_title(title)
            subject = next((candidate for candidate in candidates if normalized_title and normalized_title in {
                bgm.normalize_title(candidate.get("name")), bgm.normalize_title(candidate.get("name_cn")),
            }), None)
        if not subject or bgm.japanese_source_status(subject) != "confirmed":
            continue
        image_url = bgm.normalize_subject(subject).get("bangumi_image_url") or ""
        if not image_url:
            continue
        db.update_bangumi(int(work["id"]), {"bangumi_image_url": image_url}, include_local_titles=False)
        updated += 1
    MISSING_COVER_REFRESH_PATH.parent.mkdir(parents=True, exist_ok=True)
    MISSING_COVER_REFRESH_PATH.write_text(json.dumps({
        "date": datetime.now().date().isoformat(), "updated": updated,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def refresh_missing_anime_covers_if_due(value: date | datetime | None = None) -> tuple[bool, int]:
    today = (value or datetime.now()).date().isoformat()
    try:
        if json.loads(MISSING_COVER_REFRESH_PATH.read_text(encoding="utf-8")).get("date") == today:
            return False, 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return True, refresh_missing_anime_covers()


def refresh_precise_anime_ratings() -> int:
    """Refresh two-decimal scores for known, confirmed Japanese animations only."""
    works_by_subject: dict[int, list[dict[str, Any]]] = {}
    for work in db.list_works():
        if work.get("type") != "动画" or not str(work.get("bangumi_id") or "").isdigit():
            continue
        try:
            subject = json.loads(str(work.get("bangumi_raw_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            subject = {}
        if not subject or bgm.japanese_source_status(subject) != "confirmed":
            continue
        works_by_subject.setdefault(int(work["bangumi_id"]), []).append(work)
    subject_ids = list(dict.fromkeys([*bgm.cached_ranking_subject_ids("动画"), *works_by_subject]))
    refreshed = bgm.enrich_precise_anime_ratings(
        [{"id": subject_id} for subject_id in subject_ids], force=True, max_workers=8,
    )
    updated = 0
    for item in refreshed:
        if item.get("precision_source") != "bangumi-rating-perspective":
            continue
        for work in works_by_subject.get(int(item["id"]), []):
            db.update_bangumi(int(work["id"]), {
                "bangumi_score": round(float(item["score"]), 2),
                "bangumi_total_votes": int(item.get("votes") or 0),
            }, include_local_titles=False)
            updated += 1
    RATING_PRECISION_REFRESH_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATING_PRECISION_REFRESH_PATH.write_text(json.dumps({
        "date": datetime.now().date().isoformat(), "known_subjects": len(subject_ids),
        "updated_local_works": updated, "finished_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(subject_ids)


def refresh_precise_anime_ratings_if_due(value: date | datetime | None = None) -> tuple[bool, int]:
    today = (value or datetime.now()).date().isoformat()
    try:
        if json.loads(RATING_PRECISION_REFRESH_PATH.read_text(encoding="utf-8")).get("date") == today:
            return False, 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return True, refresh_precise_anime_ratings()


def _midnight_refresh_scheduler() -> None:
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep(max(1.0, (next_midnight - now).total_seconds()))
        try:
            refresh_current_season_if_due(datetime.now())
        except Exception:
            pass
        try:
            refresh_missing_anime_covers_if_due(datetime.now())
        except Exception:
            pass
        try:
            refresh_precise_anime_ratings_if_due(datetime.now())
        except Exception:
            pass


def start_midnight_refresh_scheduler() -> None:
    """Keep a running local site synchronized at 00:00; first-open catch-up remains in app.py."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        threading.Thread(
            target=lambda: refresh_missing_anime_covers_if_due(datetime.now()),
            name="yanggumi-cover-catchup", daemon=True,
        ).start()
        threading.Thread(
            target=lambda: refresh_precise_anime_ratings_if_due(datetime.now()),
            name="yanggumi-rating-precision-catchup", daemon=True,
        ).start()
        threading.Thread(target=_midnight_refresh_scheduler, name="yanggumi-season-midnight", daemon=True).start()


def reclassify_cached_season(year: int, season_code: str) -> int:
    """Re-evaluate old cached rows without deleting cache or local works."""
    changed = 0
    for candidate in db.list_seasonal_anime(year, season_code, include_unconfirmed=True):
        item = _candidate(candidate_subject(candidate))
        status = item["source_status"]
        if status == "excluded":
            status = "unconfirmed"
        if status != candidate.get("source_status"):
            db.update_seasonal_source(int(candidate["id"]), status)
            changed += 1
    return changed


def candidate_subject(candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(candidate.get("raw_json") or "{}")
    except json.JSONDecodeError:
        raw = {}
    if raw.get("id"):
        return raw
    return {
        "id": candidate["bangumi_id"], "type": 2,
        "name": candidate.get("name") or candidate.get("original_title") or "",
        "name_cn": candidate.get("name_cn") or candidate.get("title") or "",
        "date": candidate.get("release_date") or candidate.get("air_date") or "",
        "summary": candidate.get("summary") or "",
        "images": {"large": candidate.get("image_url") or ""},
        "rating": {
            "score": candidate.get("bangumi_score"), "rank": candidate.get("bangumi_rank"),
            "total": candidate.get("bangumi_total_votes"),
        },
        "tags": json.loads(candidate.get("tags_json") or "[]"),
    }


def set_candidate_status(cache_id: int, status: str) -> tuple[int, bool]:
    """Create/update a work while preserving all personal scoring and review fields."""
    if status not in {"在看", "想看", "已看", "弃置"}:
        raise ValueError("不支持的新番状态")
    candidate = db.get_seasonal_anime(cache_id)
    if not candidate:
        raise ValueError("当季新番候选不存在")
    existing = db.get_work_by_bangumi_id(candidate["bangumi_id"])
    was_abandoned = bool(existing and existing.get("status") == "弃置")
    subject = candidate_subject(candidate)
    fields = bgm.suggested_local_fields(subject, candidate.get("title") or "", "动画")
    if existing:
        db.update_bangumi(existing["id"], bgm.binding_fields(
            subject, existing.get("title") or candidate.get("title") or "",
            existing.get("original_title") or candidate.get("original_title") or "",
        ), include_local_titles=False)
        db.update_work_status(existing["id"], status)
        work_id = int(existing["id"])
    else:
        work_id = db.save_work({**fields, "status": status, "score_total": None})
    db.link_seasonal_work(cache_id, work_id, status)
    return work_id, status == "弃置" and not was_abandoned


def open_candidate_for_scoring(cache_id: int) -> int:
    """Open a candidate for editing; create it as watching only when it is new."""
    candidate = db.get_seasonal_anime(cache_id)
    if not candidate:
        raise ValueError("当季新番候选不存在")
    existing = db.get_work_by_bangumi_id(candidate["bangumi_id"])
    if existing:
        db.link_seasonal_work(cache_id, int(existing["id"]), existing.get("status") or "在看")
        return int(existing["id"])
    work_id, _ = set_candidate_status(cache_id, "在看")
    return work_id
