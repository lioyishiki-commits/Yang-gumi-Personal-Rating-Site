"""Small, defensive client for Bangumi's public v0 API."""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

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
    "动画": "https://api.bgm.tv/v0/subjects?type=2&sort=rank",
    "漫画": "https://api.bgm.tv/v0/subjects?type=1&cat=1001&sort=rank",
    "小说": "https://api.bgm.tv/v0/subjects?type=1&cat=1002&sort=rank",
    "游戏": "https://api.bgm.tv/v0/subjects?type=4&cat=4001&sort=rank",
}
RANKING_API_FILTERS = {
    "动画": {"type": 2},
    "漫画": {"type": 1, "cat": 1001},
    "小说": {"type": 1, "cat": 1002},
    "游戏": {"type": 4, "cat": 4001},
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


ROOT = Path(__file__).resolve().parent
RANKING_CACHE_PATH = ROOT / "data" / "bangumi_ranking_cache.json"
RANKING_CACHE_VERSION = 7
_ranking_cache: dict[tuple[str, int, str], tuple[float, list[dict[str, Any]]]] = {}
_ranking_window_cache: dict[tuple[str, int, int, str], tuple[float, list[dict[str, Any]]]] = {}
_RANKING_CACHE_SECONDS = 60 * 60
RANKING_MAX_ITEMS = 7200


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


def _load_ranking_disk_cache(quarter: str | None = None, *, allow_stale: bool = False) -> dict[str, Any]:
    quarter = quarter or ranking_quarter_key()
    try:
        payload = json.loads(RANKING_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_ranking_disk_cache(quarter)
    if payload.get("version") != RANKING_CACHE_VERSION:
        return _empty_ranking_disk_cache(quarter)
    if not allow_stale and payload.get("quarter") != quarter:
        return _empty_ranking_disk_cache(quarter)
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        return _empty_ranking_disk_cache(quarter)
    return payload


def _save_ranking_disk_cache(payload: dict[str, Any]) -> None:
    RANKING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = RANKING_CACHE_VERSION
    payload["quarter"] = payload.get("quarter") or ranking_quarter_key()
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temporary = RANKING_CACHE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(RANKING_CACHE_PATH)


def clear_ranking_cache() -> None:
    _ranking_cache.clear()
    _ranking_window_cache.clear()
    try:
        RANKING_CACHE_PATH.unlink()
    except OSError:
        pass


def ranking_cache_count(category: str) -> int:
    """Return locally cached ranking rows without making a network request."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    current = _load_ranking_disk_cache(ranking_quarter_key())
    category_cache = (current.get("categories") or {}).get(selected) or {}
    rows = category_cache.get("items") or []
    windows = category_cache.get("windows") or {}
    if rows or windows:
        window_rows = [item for value in windows.values() if isinstance(value, dict) for item in (value.get("items") or [])]
        return len({int(item["id"]) for item in [*rows, *window_rows] if isinstance(item, dict) and str(item.get("id") or "").isdigit()})
    stale = _load_ranking_disk_cache(ranking_quarter_key(), allow_stale=True)
    stale_rows = ((stale.get("categories") or {}).get(selected) or {}).get("items") or []
    return sum(isinstance(item, dict) for item in stale_rows)


def _request(method: str, path: str, **kwargs: Any) -> Any:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BangumiError(f"Bangumi 请求失败：{exc}") from exc


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


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _parse_browser_ranking_page(source: str) -> list[dict[str, Any]]:
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
        if not title_match or not rank_match:
            continue
        image = html.unescape(image_match.group(1)) if image_match else ""
        if image.startswith("//"):
            image = "https:" + image
        items.append({
            "id": subject_id,
            "title": _strip_tags(title_match.group(1)),
            "original_title": _strip_tags(original_match.group(1)) if original_match else "",
            "rank": int(rank_match.group(1)),
            "score": float(score_match.group(1)) if score_match else None,
            "votes": int(votes_match.group(1).replace(",", "")) if votes_match else None,
            "image": image,
            "info": _strip_tags(info_match.group(1)) if info_match else "",
            "url": f"{WEB_BASE}/{subject_id}",
        })
    return items


def _ranking_category_matches(category: str, subject: dict[str, Any]) -> bool:
    source = japanese_source_status(subject)
    # Every public ranking category is Japan-only.  In particular, animation
    # must not treat an unknown origin as Japanese: Bangumi's type=2 endpoint
    # also contains Pixar, European and other non-Japanese animation.
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
    return {
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


def ranked_browser_subject_window(category: str, offset: int = 0, limit: int = 25) -> list[dict[str, Any]]:
    """Read one ranking window directly instead of downloading every preceding page."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    offset = min(max(int(offset), 0), RANKING_MAX_ITEMS - 1)
    limit = min(max(int(limit), 1), 100)
    quarter = ranking_quarter_key()
    cache_key = (selected, offset, limit, quarter)
    cached = _ranking_window_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RANKING_CACHE_SECONDS:
        return [dict(item) for item in cached[1]]

    disk_cache = _load_ranking_disk_cache(quarter)
    category_cache = disk_cache.setdefault("categories", {}).setdefault(selected, {})
    windows = category_cache.setdefault("windows", {})
    window_key = f"{offset}:{limit}"
    stored = windows.get(window_key) or {}
    stored_rows = [dict(item) for item in stored.get("items", []) if isinstance(item, dict)]
    if stored_rows:
        _ranking_window_cache[cache_key] = (time.time(), stored_rows)
        return stored_rows

    stale_cache = _load_ranking_disk_cache(quarter, allow_stale=True)
    stale_windows = (((stale_cache.get("categories") or {}).get(selected) or {}).get("windows") or {})
    stale_rows = [dict(item) for item in (stale_windows.get(window_key) or {}).get("items", []) if isinstance(item, dict)]
    results: list[dict[str, Any]] = []
    seen: set[int] = set()
    api_offset = offset
    api_page_size = min(100, max(50, limit * 2))

    for _ in range(3):
        params = {
            **RANKING_API_FILTERS[selected],
            "sort": "rank",
            "limit": api_page_size,
            "offset": api_offset,
        }
        try:
            payload = _request("GET", "/subjects", params=params)
        except BangumiError:
            if stale_rows:
                _ranking_window_cache[cache_key] = (time.time(), stale_rows)
                return stale_rows
            raise
        subjects = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(subjects, list) or not subjects:
            break
        api_offset += len(subjects)
        for subject in subjects:
            if not isinstance(subject, dict) or not str(subject.get("id") or "").isdigit():
                continue
            subject_id = int(subject["id"])
            if subject_id in seen or not _ranking_category_matches(selected, subject):
                continue
            seen.add(subject_id)
            results.append(_ranking_item_from_subject(subject))
            if len(results) >= limit:
                break
        if len(results) >= limit or len(subjects) < api_page_size:
            break

    results.sort(key=lambda item: (int(item.get("rank") or RANKING_MAX_ITEMS + 1), int(item.get("id") or 0)))
    results = results[:limit]
    windows[window_key] = {
        "offset": offset,
        "limit": limit,
        "source": "official-api-window",
        "items": [dict(item) for item in results],
    }
    category_cache["source"] = "official-api"
    _save_ranking_disk_cache(disk_cache)
    _ranking_window_cache[cache_key] = (time.time(), [dict(item) for item in results])
    return results


def ranked_browser_subjects(category: str, limit: int = 24) -> list[dict[str, Any]]:
    """Load ranked subjects from Bangumi's official public API and cache them locally."""
    selected = category if category in RANKING_BROWSER_URLS else "动画"
    limit = min(max(int(limit), 1), RANKING_MAX_ITEMS)
    quarter = ranking_quarter_key()
    cache_key = (selected, limit, quarter)
    cached = _ranking_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RANKING_CACHE_SECONDS:
        return [dict(item) for item in cached[1]]
    for (cached_category, cached_limit, cached_quarter), cached_value in sorted(_ranking_cache.items(), key=lambda pair: pair[0][1], reverse=True):
        if cached_category != selected or cached_quarter != quarter or cached_limit < limit:
            continue
        cached_at, cached_rows = cached_value
        if time.time() - cached_at < _RANKING_CACHE_SECONDS and len(cached_rows) >= limit:
            return [dict(item) for item in cached_rows[:limit]]

    disk_cache = _load_ranking_disk_cache(quarter)
    category_cache = disk_cache.setdefault("categories", {}).setdefault(selected, {})
    results = [dict(item) for item in category_cache.get("items", []) if isinstance(item, dict)]
    stale_cache = _load_ranking_disk_cache(quarter, allow_stale=True)
    stale_category = (stale_cache.get("categories") or {}).get(selected) or {}
    stale_results = [dict(item) for item in stale_category.get("items", []) if isinstance(item, dict)]
    if len(results) >= limit:
        _ranking_cache[cache_key] = (time.time(), [dict(item) for item in results[:limit]])
        return results[:limit]

    seen: set[int] = {int(item["id"]) for item in results if str(item.get("id") or "").isdigit()}
    api_page_size = 50
    offset = max(0, int(category_cache.get("loaded_offset") or 0))
    completed = bool(category_cache.get("complete"))
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
            if len(results) >= limit:
                break
        total = int(payload.get("total") or 0) if isinstance(payload, dict) else 0
        if len(subjects) < api_page_size or (total and offset >= total):
            category_cache["complete"] = True
            break
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
    # Keep CJK, kana, latin letters and digits; discard separators and punctuation.
    return "".join(char for char in value if char.isalnum() or "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff")


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
    """Classify origin conservatively without rejecting normal Japanese API results."""
    if subject.get("type") is None:
        return "unknown"
    if subject.get("type") not in {1, 2, 4}:
        return "excluded"
    text = _classification_text(subject)
    tag_names = {
        str(item.get("name", "") if isinstance(item, dict) else item).strip().casefold()
        for item in (subject.get("tags") or [])
    }
    foreign_origin_tags = {
        "非日本动画", "非日本動畫", "非日本動畫電影", "欧美", "歐美", "欧洲", "歐洲",
        "美国", "美國", "以色列", "法国", "法國", "英国", "英國", "韩国", "韓國",
        "中国", "中國", "国产", "國產", "pixar", "disney", "皮克斯", "迪士尼",
    }
    if tag_names & foreign_origin_tags:
        return "excluded"
    foreign_markers = (
        "中国动画", "国产动画", "国产游戏", "中国游戏", "donghua",
        "美国动画", "欧美动画", "american animation", "韩国漫画", "韩国动画", "webtoon",
        "非日本动画", "非日本動畫", "pixar", "disney",
        "国家 中国", "地区 中国", "国家/地区 中国", "原产地 中国",
        "国家 美国", "地区 美国", "国家/地区 美国", "原产地 美国",
        "国家 韩国", "地区 韩国", "国家/地区 韩国", "原产地 韩国",
        "国家 法国", "地区 法国", "国家 英国", "地区 英国",
        "网络剧", "电视剧", "真人剧",
    )
    if any(marker in text for marker in foreign_markers):
        return "excluded"
    japanese_markers = (
        "日本", "日本动画", "日本漫画", "日本游戏", "日文", "ライトノベル",
        "少年ジャンプ", "講談社", "集英社", "角川", "kadokawa", "テレビアニメ",
    )
    if any(marker in text for marker in japanese_markers):
        return "confirmed"
    # Kana in a translated alias or an incidental user tag does not prove
    # Japanese origin (for example Pixar's Soul has the alias ソウル).
    primary_name = str(subject.get("name") or "")
    if any("\u3040" <= char <= "\u30ff" for char in primary_name):
        return "confirmed"
    # A Japanese work can contain an overseas release note such as
    # "其他上映日期：中国大陆"; that is not the work's country of origin.
    if "中国大陆" in text:
        return "excluded"
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
    """Search Bangumi with the public subject type matching the local category."""
    selected = category if category in CATEGORY_SUBJECT_TYPES else "全部"
    return search_subjects(
        keyword,
        limit=limit,
        fallback_keywords=fallback_keywords,
        subject_types=CATEGORY_SUBJECT_TYPES[selected],
    )


def raw_type_name(subject: dict[str, Any]) -> str:
    return RAW_TYPE_NAMES.get(subject.get("type"), f"类型 {subject.get('type')}" if subject.get("type") else "未知")


def _classification_text(subject: dict[str, Any]) -> str:
    tags = subject.get("tags") or []
    tag_names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in tags]
    infobox = subject.get("infobox") or []
    info_text = " ".join(
        f"{item.get('key', '')} {item.get('value', '')}" for item in infobox if isinstance(item, dict)
    )
    meta_tags = subject.get("meta_tags") or []
    return " ".join([
        subject.get("name_cn") or "", subject.get("name") or "", subject.get("platform") or "",
        subject.get("subtype") or "", *[str(item) for item in meta_tags], *tag_names, info_text,
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
        "bangumi_score": rating.get("score"),
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
        "release_date": normalized["bangumi_date"],
        "year": int(normalized["bangumi_date"][:4]) if normalized["bangumi_date"][:4].isdigit() else None,
        **normalized,
    }
