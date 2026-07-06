"""Persistent, fault-tolerant appearance settings for Yang-gumi."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SETTINGS_PATH = DATA_DIR / "ui_settings.json"
BACKGROUNDS_DIR = ROOT / "backgrounds"
STATIC_DIR = ROOT / "static"
PLACEHOLDERS_DIR = STATIC_DIR / "placeholders"
UI_DIR = STATIC_DIR / "ui"

PAGE_LABELS = {
    "home": "首页", "library": "条目库", "add": "新增条目", "match": "Bangumi",
    "ranking": "排行榜", "compare": "评分对比",
    "tags": "标签筛选", "data": "数据管理",
}
BACKGROUND_MODES = [
    "none", "custom_image", "custom_url", "auto_poster_collage", "auto_poster_wall",
    "auto_poster_blur", "auto_top_rated_poster", "auto_recent_finished_poster",
]
POSTER_WIDGET_MODES = ["hero_strip", "side_rail", "poster_wall", "floating_cards", "blurred_banner", "none"]


def _page(background_mode: str, widget_mode: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "background_enabled": False,
        "background_mode": "none",
        "background_path": "",
        "background_url": "",
        "poster_widget_enabled": False,
        "poster_widget_mode": "none",
        "overlay_opacity": 0.70,
        "blur": 6,
        "brightness": 0.50,
        "fixed": True,
    }


DEFAULT_SETTINGS: dict[str, Any] = {
    "global": {
        "theme_style": "anime_midnight", "enable_motion": True, "enable_hover_animation": True,
        "enable_scroll_animation": True, "animation_strength": "light",
        "poster_source": "watched_anime", "poster_max_count": 24,
        "poster_refresh_mode": "stable", "poster_opacity": 0.18,
        "poster_blur": 1, "poster_brightness": 0.92, "content_glass_opacity": 0.86,
    },
    "home": _page("auto_poster_blur", "hero_strip"),
    "library": _page("auto_poster_collage", "side_rail"),
    "add": _page("auto_poster_blur", "none"),
    "match": _page("auto_poster_collage", "none"),
    "ranking": _page("auto_top_rated_poster", "poster_wall"),
    "compare": _page("auto_poster_collage", "none"),
    "tags": _page("auto_poster_collage", "side_rail"),
    "data": _page("auto_poster_blur", "none"),
}


def ensure_ui_directories() -> None:
    for directory in (DATA_DIR, BACKGROUNDS_DIR, STATIC_DIR, PLACEHOLDERS_DIR, UI_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _merge(default: dict[str, Any], current: Any) -> dict[str, Any]:
    result = copy.deepcopy(default)
    if not isinstance(current, dict):
        return result
    for key, value in current.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif key in result:
            result[key] = value
    return result


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    ensure_ui_directories()
    clean = _merge(DEFAULT_SETTINGS, settings)
    temp = SETTINGS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SETTINGS_PATH)
    return clean


def load_settings() -> dict[str, Any]:
    ensure_ui_directories()
    if not SETTINGS_PATH.exists():
        return save_settings(DEFAULT_SETTINGS)
    try:
        current = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("UI settings root must be an object")
        merged = _merge(DEFAULT_SETTINGS, current)
        for page_key in PAGE_LABELS:
            merged[page_key].update({
                "background_enabled": False, "background_mode": "none",
                "background_path": "", "background_url": "",
                "poster_widget_enabled": False, "poster_widget_mode": "none",
            })
        return merged
    except (OSError, ValueError, json.JSONDecodeError):
        return save_settings(DEFAULT_SETTINGS)


def reset_settings() -> dict[str, Any]:
    return save_settings(DEFAULT_SETTINGS)


def save_uploaded_background(uploaded: Any, page_key: str) -> str:
    ensure_ui_directories()
    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise ValueError("仅支持 JPG、PNG、WEBP 或 GIF 背景。")
    safe_page = re.sub(r"[^a-z0-9_-]", "", page_key.lower()) or "page"
    target = BACKGROUNDS_DIR / f"{safe_page}-background{suffix}"
    target.write_bytes(uploaded.getbuffer())
    return str(target)


ensure_ui_directories()
load_settings()
